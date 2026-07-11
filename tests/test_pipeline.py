import json
import sqlite3
from dataclasses import FrozenInstanceError
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

import paper_radar.pipeline as pipeline_module
from paper_radar.config import FeedConfig, TopicConfig
from paper_radar.database import (
    connect_database,
    initialize_database,
    mark_journal_status,
    register_journal,
    replace_article_tags,
    upsert_article,
)
from paper_radar.feeds import FeedParseError
from paper_radar.models import (
    ArticleRecord,
    ClassificationSummary,
    FeedFetchResult,
    RawFeedItem,
    RunSummary,
)
from paper_radar.pipeline import (
    ContractError,
    PipelineConfigurationError,
    PipelineContractError,
    update_database,
)


def _feed(feed_id: str, *, enabled: bool = True, host: str = "example.test") -> FeedConfig:
    return FeedConfig(
        id=feed_id,
        name=feed_id.title(),
        publisher="nature",
        feed_url=f"https://{host}/{feed_id}.xml",
        enabled=enabled,
    )


def _topic(
    topic_id: str = "saw", group: str = "acoustic-rf", *keywords: str
) -> TopicConfig:
    return TopicConfig(
        id=topic_id,
        label=topic_id.upper(),
        keywords=keywords or ("SAW",),
        group=group,
    )


def _classification(
    articles_scanned: int = 0,
    articles_tagged: int = 0,
    tag_assignments: int = 0,
    active_tags: int = 0,
) -> ClassificationSummary:
    return ClassificationSummary(
        articles_scanned,
        articles_tagged,
        tag_assignments,
        active_tags,
    )


def _article(*, title: str, uid: str = "article-1") -> ArticleRecord:
    return ArticleRecord(
        uid=uid,
        doi=None,
        journal_id="feed",
        title=title,
        abstract=None,
        authors=(),
        published_at=None,
        article_type="other",
        article_url=f"https://example.test/{uid}",
        normalized_url=f"https://example.test/{uid}",
        oa_status="unknown",
        source_feed_url="https://example.test/feed.xml",
        metadata_status="rss_only",
    )


def _seed_article(database_path: Path, article: ArticleRecord) -> None:
    connection = connect_database(database_path)
    try:
        initialize_database(connection)
        register_journal(connection, _feed("feed"))
        upsert_article(connection, article)
    finally:
        connection.close()


def _rss(*items: tuple[str, str]) -> bytes:
    entries = "".join(
        f"<item><title>{title}</title><link>{link}</link></item>" for title, link in items
    )
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<rss version='2.0'><channel><title>Test</title>"
        "<link>https://example.test/</link><description>Test feed</description>"
        f"{entries}</channel></rss>"
    ).encode()


def _result(*items: tuple[str, str], effective_url: str | None = None) -> FeedFetchResult:
    return FeedFetchResult(
        content=_rss(*items),
        etag='"new"',
        last_modified="Fri, 10 Jul 2026 12:00:00 GMT",
        not_modified=False,
        effective_url=effective_url,
    )


def _identity_enricher(
    client: httpx.Client,
    article: ArticleRecord,
    *,
    unpaywall_email: str | None = None,
) -> ArticleRecord:
    return article


def _rows(database_path: Path, sql: str) -> list[sqlite3.Row]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def test_run_summary_is_immutable_and_slotted() -> None:
    summary = RunSummary("ok", 1, 2, 3, 0, ("good",), (), _classification())

    assert not hasattr(summary, "__dict__")
    with pytest.raises(FrozenInstanceError):
        summary.status = "partial"  # type: ignore[misc]

    assert issubclass(PipelineContractError, ContractError)


def test_good_and_bad_feeds_are_isolated_with_exact_persisted_summary(tmp_path: Path) -> None:
    database_path = tmp_path / "radar.db"
    good = _feed("good")
    bad = _feed("bad")

    def fetcher(client: httpx.Client, feed: FeedConfig, **kwargs: object) -> FeedFetchResult:
        if feed.id == "bad":
            raise FeedParseError("secret-token=do-not-log")
        return _result(("A SAW paper", "https://example.test/article"))

    summary = update_database(
        database_path,
        [good, bad],
        [_topic()],
        fetcher=fetcher,
        enricher=_identity_enricher,
        min_interval=0,
    )

    assert summary == RunSummary(
        "partial", 1, 0, 0, 1, ("good",), ("bad",), _classification(1, 1, 1, 1)
    )
    journals = {row["id"]: row for row in _rows(database_path, "SELECT * FROM journals")}
    assert journals["good"]["last_status"] == "ok"
    assert journals["good"]["last_error"] is None
    assert journals["bad"]["last_status"] == "error"
    assert journals["bad"]["last_error"] == "FeedParseError"
    run = _rows(database_path, "SELECT * FROM runs_log")[0]
    assert tuple(
        run[name] for name in ("inserted_count", "updated_count", "skipped_count", "failed_count")
    ) == (1, 0, 0, 1)
    notes = json.loads(run["notes"])
    assert notes["successful_feeds"] == ["good"]
    assert notes["failed_feeds"] == ["bad"]
    assert "secret-token" not in run["notes"]


def test_pipeline_retries_transient_fetch_with_injected_backoff(tmp_path: Path) -> None:
    attempts = 0
    sleeps: list[float] = []

    def fetcher(client: httpx.Client, feed: FeedConfig, **kwargs: object) -> FeedFetchResult:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ReadTimeout("temporary")
        return _result()

    summary = update_database(
        tmp_path / "radar.db",
        [_feed("feed")],
        [],
        fetcher=fetcher,
        enricher=_identity_enricher,
        sleeper=sleeps.append,
        min_interval=0,
    )

    assert summary.status == "ok"
    assert attempts == 3
    assert sleeps == [0.5, 1.0]


def test_not_modified_preserves_missing_validator_and_updates_present_one(tmp_path: Path) -> None:
    database_path = tmp_path / "radar.db"
    feed = _feed("feed")
    connection = connect_database(database_path)
    try:
        initialize_database(connection)
        register_journal(connection, feed)
        mark_journal_status(
            connection,
            feed.id,
            status="ok",
            etag='"old"',
            last_modified="Thu, 9 Jul 2026 12:00:00 GMT",
        )
    finally:
        connection.close()

    def fetcher(
        client: httpx.Client,
        current_feed: FeedConfig,
        *,
        etag: str | None,
        last_modified: str | None,
    ) -> FeedFetchResult:
        assert (etag, last_modified) == ('"old"', "Thu, 9 Jul 2026 12:00:00 GMT")
        return FeedFetchResult(
            content=None,
            etag=None,
            last_modified="Fri, 10 Jul 2026 12:00:00 GMT",
            not_modified=True,
            effective_url=current_feed.feed_url,
        )

    summary = update_database(
        database_path,
        [feed],
        [],
        fetcher=fetcher,
        enricher=_identity_enricher,
        min_interval=0,
    )

    assert summary == RunSummary("ok", 0, 0, 0, 0, ("feed",), (), _classification())
    journal = _rows(database_path, "SELECT * FROM journals")[0]
    assert journal["last_status"] == "not_modified"
    assert (journal["etag"], journal["last_modified"]) == (
        '"old"',
        "Fri, 10 Jul 2026 12:00:00 GMT",
    )


def test_effective_redirect_url_resolves_relative_article_links(tmp_path: Path) -> None:
    database_path = tmp_path / "radar.db"

    summary = update_database(
        database_path,
        [_feed("feed")],
        [],
        fetcher=lambda *args, **kwargs: _result(
            ("Relative paper", "article/1"),
            effective_url="https://redirected.test/path/feed.xml",
        ),
        enricher=_identity_enricher,
        min_interval=0,
    )

    assert summary.inserted == 1
    article = _rows(database_path, "SELECT article_url FROM articles")[0]
    assert article["article_url"] == "https://redirected.test/path/article/1"


def test_bad_item_is_counted_and_feed_is_partial_while_later_item_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "radar.db"
    feed = _feed("feed")
    items = [
        RawFeedItem(feed.id, feed.feed_url, "Bad", "mailto:bad", None, None, (), None, None),
        RawFeedItem(
            feed.id,
            feed.feed_url,
            "Good SAW",
            "https://example.test/good",
            None,
            None,
            (),
            None,
            None,
        ),
    ]

    monkeypatch.setattr(pipeline_module, "parse_feed_bytes", lambda *args, **kwargs: items)
    summary = update_database(
        database_path,
        [feed],
        [_topic()],
        fetcher=lambda *args, **kwargs: _result(),
        enricher=_identity_enricher,
        min_interval=0,
    )

    assert summary == RunSummary(
        "partial", 1, 0, 0, 1, ("feed",), (), _classification(1, 1, 1, 1)
    )
    journal = _rows(database_path, "SELECT * FROM journals")[0]
    assert journal["last_status"] == "partial"
    notes = json.loads(_rows(database_path, "SELECT notes FROM runs_log")[0]["notes"])
    assert notes["item_errors"] == [{"error": "ValueError", "feed_id": "feed", "item_index": 0}]
    assert notes["omitted_item_errors"] == 0


def test_not_modified_update_reclassifies_every_stored_article(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "papers.db"
    _seed_article(database_path, _article(title="AlScN BAW resonator"))
    result = FeedFetchResult(
        content=None,
        etag='"same"',
        last_modified="Fri, 10 Jul 2026 00:00:00 GMT",
        not_modified=True,
    )
    topics = (
        _topic("baw", "acoustic-rf", "BAW"),
        _topic("alscn", "piezo-ferroelectric", "AlScN"),
    )
    original_reclassify = pipeline_module.reclassify_all_articles
    reclassification_calls = 0

    def counting_reclassify(
        connection: sqlite3.Connection, configured_topics: tuple[TopicConfig, ...]
    ) -> ClassificationSummary:
        nonlocal reclassification_calls
        reclassification_calls += 1
        return original_reclassify(connection, configured_topics)

    monkeypatch.setattr(pipeline_module, "reclassify_all_articles", counting_reclassify)

    summary = update_database(
        database_path,
        [_feed("feed")],
        topics,
        fetcher=lambda *_args, **_kwargs: result,
        enricher=_identity_enricher,
        min_interval=0,
    )

    assert summary.classification == ClassificationSummary(1, 1, 2, 2)
    assert reclassification_calls == 1
    assert [
        row["tag_id"]
        for row in _rows(database_path, "SELECT tag_id FROM article_tags ORDER BY tag_id")
    ] == ["alscn", "baw"]


def test_reclassification_failure_is_run_fatal_and_preserves_existing_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "radar.db"
    article = _article(title="SAW resonator")
    _seed_article(database_path, article)
    connection = connect_database(database_path)
    try:
        replace_article_tags(connection, article.uid, [_topic("existing")])
    finally:
        connection.close()
    monkeypatch.setattr(
        pipeline_module,
        "reclassify_all_articles",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret tag detail")),
    )

    with pytest.raises(RuntimeError, match="secret tag detail"):
        update_database(
            database_path,
            [_feed("feed")],
            [_topic("replacement")],
            fetcher=lambda *args, **kwargs: FeedFetchResult(
                content=None,
                etag='"same"',
                last_modified=None,
                not_modified=True,
            ),
            enricher=_identity_enricher,
            min_interval=0,
        )

    assert [
        row["tag_id"] for row in _rows(database_path, "SELECT tag_id FROM article_tags")
    ] == ["existing"]
    run = _rows(database_path, "SELECT status, failed_count, notes FROM runs_log")[0]
    assert (run["status"], run["failed_count"]) == ("error", 0)
    notes = json.loads(run["notes"])
    assert notes["diagnostic"] == "RuntimeError"
    assert "secret tag detail" not in run["notes"]


def test_partial_update_still_reclassifies_all_stored_articles(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "radar.db"
    _seed_article(database_path, _article(title="AlScN BAW resonator"))

    summary = update_database(
        database_path,
        [_feed("good"), _feed("bad")],
        (
            _topic("baw", "acoustic-rf", "BAW"),
            _topic("alscn", "piezo-ferroelectric", "AlScN"),
        ),
        fetcher=lambda client, feed, **kwargs: (
            FeedFetchResult(None, None, None, True)
            if feed.id == "good"
            else (_ for _ in ()).throw(FeedParseError("failed"))
        ),
        enricher=_identity_enricher,
        min_interval=0,
    )

    assert summary.status == "partial"
    assert summary.classification == ClassificationSummary(1, 1, 2, 2)


def test_tags_use_persisted_survivor_uid_after_url_only_to_doi_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "radar.db"
    feed = _feed("feed")
    doi = {"value": None}

    def parser(*args: object, **kwargs: object) -> list[RawFeedItem]:
        return [
            RawFeedItem(
                feed.id,
                feed.feed_url,
                "SAW paper",
                "https://example.test/stable",
                None,
                doi["value"],
                (),
                None,
                None,
            )
        ]

    monkeypatch.setattr(pipeline_module, "parse_feed_bytes", parser)
    first = update_database(
        database_path,
        [feed],
        [_topic("first-tag")],
        fetcher=lambda *args, **kwargs: _result(),
        enricher=_identity_enricher,
        min_interval=0,
    )
    original_uid = _rows(database_path, "SELECT uid FROM articles")[0]["uid"]
    doi["value"] = "10.1000/stable"
    second = update_database(
        database_path,
        [feed],
        [_topic("second-tag")],
        fetcher=lambda *args, **kwargs: _result(),
        enricher=_identity_enricher,
        min_interval=0,
    )

    assert (first.inserted, second.updated) == (1, 1)
    article = _rows(database_path, "SELECT uid, doi FROM articles")[0]
    assert (article["uid"], article["doi"]) == (original_uid, "10.1000/stable")
    tag_link = _rows(database_path, "SELECT article_uid, tag_id FROM article_tags")[0]
    assert (tag_link["article_uid"], tag_link["tag_id"]) == (original_uid, "second-tag")


def test_classification_uses_canonical_merged_article(tmp_path: Path) -> None:
    database_path = tmp_path / "radar.db"
    feed = _feed("feed")
    titles = iter(("SAW article", "Untitled"))

    def fetcher(*args: object, **kwargs: object) -> FeedFetchResult:
        return _result((next(titles), "https://example.test/stable"))

    first = update_database(
        database_path,
        [feed],
        [_topic()],
        fetcher=fetcher,
        enricher=_identity_enricher,
        min_interval=0,
    )
    second = update_database(
        database_path,
        [feed],
        [_topic()],
        fetcher=fetcher,
        enricher=_identity_enricher,
        min_interval=0,
    )

    assert (first.inserted, second.skipped) == (1, 1)
    assert _rows(database_path, "SELECT tag_id FROM article_tags")[0]["tag_id"] == "saw"


def test_classification_sees_abstract_merged_by_upsert(tmp_path: Path) -> None:
    database_path = tmp_path / "radar.db"
    feed = _feed("feed")
    calls = 0

    def enricher(client: httpx.Client, record: ArticleRecord, **kwargs: object) -> ArticleRecord:
        nonlocal calls
        calls += 1
        if calls == 1:
            return record
        return replace(
            record,
            abstract="A SAW result",
            metadata_status="enriched",
            enriched_fields=("abstract",),
        )

    for _ in range(2):
        update_database(
            database_path,
            [feed],
            [_topic()],
            fetcher=lambda *args, **kwargs: _result(("Untitled", "https://example.test/stable")),
            enricher=enricher,
            min_interval=0,
        )

    assert _rows(database_path, "SELECT tag_id FROM article_tags")[0]["tag_id"] == "saw"


def test_partial_feed_does_not_advance_validators_and_next_run_repairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "radar.db"
    feed = _feed("feed")
    observed: list[tuple[str | None, str | None]] = []
    run_number = 0

    def fetcher(
        client: httpx.Client,
        current_feed: FeedConfig,
        *,
        etag: str | None,
        last_modified: str | None,
    ) -> FeedFetchResult:
        nonlocal run_number
        observed.append((etag, last_modified))
        run_number += 1
        return _result()

    bad = RawFeedItem(feed.id, feed.feed_url, "Bad", "mailto:bad", None, None, (), None, None)
    good = RawFeedItem(
        feed.id,
        feed.feed_url,
        "Good SAW",
        "https://example.test/good",
        None,
        None,
        (),
        None,
        None,
    )
    monkeypatch.setattr(
        pipeline_module,
        "parse_feed_bytes",
        lambda *args, **kwargs: [bad] if run_number == 1 else [good],
    )

    first = update_database(
        database_path,
        [feed],
        [_topic()],
        fetcher=fetcher,
        enricher=_identity_enricher,
        min_interval=0,
    )
    second = update_database(
        database_path,
        [feed],
        [_topic()],
        fetcher=fetcher,
        enricher=_identity_enricher,
        min_interval=0,
    )

    assert (first.status, second.status) == ("partial", "ok")
    assert observed == [(None, None), (None, None)]
    journal = _rows(database_path, "SELECT etag, last_status FROM journals")[0]
    assert (journal["etag"], journal["last_status"]) == ('"new"', "ok")
    assert _rows(database_path, "SELECT tag_id FROM article_tags")[0]["tag_id"] == "saw"


@pytest.mark.parametrize(
    ("keyword", "value"),
    [
        ("fetcher", None),
        ("enricher", 3),
        ("sleeper", "sleep"),
        ("clock", object()),
        ("client", object()),
        ("min_interval", -0.1),
    ],
)
def test_invalid_pipeline_configuration_fails_before_database_creation(
    tmp_path: Path, keyword: str, value: object
) -> None:
    database_path = tmp_path / "radar.db"
    kwargs: dict[str, object] = {keyword: value}

    with pytest.raises(PipelineConfigurationError):
        update_database(database_path, [], [], **kwargs)  # type: ignore[arg-type]

    assert not database_path.exists()


@pytest.mark.parametrize("callback", ["fetcher", "enricher"])
def test_callback_wrong_signature_is_contract_error_not_feed_isolation(
    tmp_path: Path, callback: str
) -> None:
    database_path = tmp_path / "radar.db"

    def wrong_signature() -> object:
        return object()

    kwargs: dict[str, object] = {
        "fetcher": lambda *args, **kwargs: _result(("Article", "https://example.test/article")),
        "enricher": _identity_enricher,
        callback: wrong_signature,
    }
    with pytest.raises(PipelineContractError, match=callback):
        update_database(database_path, [_feed("feed")], [], min_interval=0, **kwargs)  # type: ignore[arg-type]

    assert not database_path.exists()


def test_fetcher_and_enricher_return_contracts_bypass_isolation(tmp_path: Path) -> None:
    with pytest.raises(PipelineContractError, match="fetcher.*FeedFetchResult"):
        update_database(
            tmp_path / "fetch.db",
            [_feed("feed")],
            [],
            fetcher=lambda *args, **kwargs: object(),  # type: ignore[arg-type]
            min_interval=0,
        )

    with pytest.raises(PipelineContractError, match="enricher.*ArticleRecord"):
        update_database(
            tmp_path / "enrich.db",
            [_feed("feed")],
            [],
            fetcher=lambda *args, **kwargs: _result(("Article", "https://example.test/article")),
            enricher=lambda *args, **kwargs: object(),  # type: ignore[arg-type]
            min_interval=0,
        )


def test_terminal_write_retries_once_and_does_not_double_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "radar.db"
    original_finish = pipeline_module.finish_run
    attempts = 0
    sleeps: list[float] = []

    def flaky_finish(*args: object, **kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("busy")
        original_finish(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline_module, "finish_run", flaky_finish)
    summary = update_database(
        database_path,
        [],
        [],
        sleeper=sleeps.append,
        min_interval=0,
    )

    assert summary.status == "error"
    assert attempts == 2
    assert len(sleeps) == 1
    assert _rows(database_path, "SELECT status FROM runs_log")[0]["status"] == "error"


def test_exhausted_normal_finish_uses_one_error_finalization_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "radar.db"
    original_finish = pipeline_module.finish_run
    attempts = 0

    def fail_normal_then_finalize(*args: object, **kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts <= 3:
            raise sqlite3.OperationalError("busy")
        original_finish(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline_module, "finish_run", fail_normal_then_finalize)
    with pytest.raises(sqlite3.OperationalError, match="busy"):
        update_database(database_path, [], [], sleeper=lambda seconds: None, min_interval=0)

    assert attempts == 4
    assert _rows(database_path, "SELECT status FROM runs_log")[0]["status"] == "error"


def test_successful_terminal_write_is_called_only_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_finish = pipeline_module.finish_run
    attempts = 0

    def counting_finish(*args: object, **kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        original_finish(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline_module, "finish_run", counting_finish)
    update_database(tmp_path / "radar.db", [], [], min_interval=0)

    assert attempts == 1


def test_primary_error_is_preserved_when_all_finalization_retries_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "radar.db"
    attempts = 0

    def unavailable_finish(*args: object, **kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        raise sqlite3.OperationalError("busy")

    monkeypatch.setattr(pipeline_module, "finish_run", unavailable_finish)
    primary = KeyboardInterrupt("stop")

    with pytest.raises(KeyboardInterrupt) as captured:
        update_database(
            database_path,
            [_feed("feed")],
            [],
            fetcher=lambda *args, **kwargs: (_ for _ in ()).throw(primary),
            sleeper=lambda seconds: None,
            min_interval=0,
        )

    assert captured.value is primary
    assert attempts == 3
    assert any("could not be finalized" in note for note in captured.value.__notes__)
    assert _rows(database_path, "SELECT status FROM runs_log")[0]["status"] == "running"


@pytest.mark.parametrize("exception", [AssertionError("bug"), KeyboardInterrupt()])
def test_unexpected_control_or_programming_error_is_finalized_then_propagated(
    tmp_path: Path, exception: BaseException
) -> None:
    database_path = tmp_path / "radar.db"

    def fetcher(*args: object, **kwargs: object) -> FeedFetchResult:
        raise exception

    with pytest.raises(type(exception)):
        update_database(
            database_path,
            [_feed("feed")],
            [],
            fetcher=fetcher,
            enricher=_identity_enricher,
            min_interval=0,
        )

    run = _rows(database_path, "SELECT status, failed_count, notes FROM runs_log")[0]
    assert run["status"] == "error"
    assert json.loads(run["notes"])["diagnostic"] == type(exception).__name__


def test_journal_status_programming_error_propagates_with_feed_error_cause_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "radar.db"
    real_connection = connect_database(database_path)

    class ConnectionProxy:
        def __init__(self) -> None:
            self.closed = False

        def __getattr__(self, name: str) -> object:
            return getattr(real_connection, name)

        def close(self) -> None:
            self.closed = True
            real_connection.close()

    proxy = ConnectionProxy()
    monkeypatch.setattr(pipeline_module, "connect_database", lambda path: proxy)
    original_mark = pipeline_module.mark_journal_status

    def broken_mark(*args: object, status: str, **kwargs: object) -> None:
        if status == "error":
            raise AssertionError("journal status programming bug")
        original_mark(*args, status=status, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline_module, "mark_journal_status", broken_mark)
    feed_error = FeedParseError("feed parse failed")

    with pytest.raises(AssertionError, match="journal status programming bug") as captured:
        update_database(
            database_path,
            [_feed("bad")],
            [],
            fetcher=lambda *args, **kwargs: (_ for _ in ()).throw(feed_error),
            enricher=_identity_enricher,
            min_interval=0,
        )

    assert captured.value.__cause__ is feed_error
    assert proxy.closed is True
    run = _rows(database_path, "SELECT status, failed_count, notes FROM runs_log")[0]
    assert (run["status"], run["failed_count"]) == ("error", 1)
    assert json.loads(run["notes"])["diagnostic"] == "AssertionError"


def test_journal_status_sqlite_error_is_not_silent_and_next_feed_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "radar.db"
    original_mark = pipeline_module.mark_journal_status

    def intermittently_broken_mark(*args: object, status: str, **kwargs: object) -> None:
        if status == "error":
            raise sqlite3.OperationalError("secret journal detail")
        original_mark(*args, status=status, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline_module, "mark_journal_status", intermittently_broken_mark)

    def fetcher(client: httpx.Client, feed: FeedConfig, **kwargs: object) -> FeedFetchResult:
        if feed.id == "bad":
            raise FeedParseError("original secret feed detail")
        return _result()

    summary = update_database(
        database_path,
        [_feed("bad"), _feed("good")],
        [],
        fetcher=fetcher,
        enricher=_identity_enricher,
        min_interval=0,
    )

    assert summary == RunSummary(
        "partial", 0, 0, 0, 1, ("good",), ("bad",), _classification()
    )
    run = _rows(database_path, "SELECT status, failed_count, notes FROM runs_log")[0]
    assert (run["status"], run["failed_count"]) == ("partial", 1)
    notes = json.loads(run["notes"])
    assert notes["journal_status_errors"] == [
        {
            "error": "OperationalError",
            "feed_error": "FeedParseError",
            "feed_id": "bad",
            "stage": "mark_failed_feed_status",
        }
    ]
    assert notes["omitted_journal_status_errors"] == 0
    assert "secret" not in run["notes"]
    journals = {
        row["id"]: row["last_status"] for row in _rows(database_path, "SELECT * FROM journals")
    }
    assert journals == {"bad": "never", "good": "ok"}


def test_all_disabled_is_error_without_fetching_or_false_failure_count(tmp_path: Path) -> None:
    database_path = tmp_path / "radar.db"

    summary = update_database(
        database_path,
        [_feed("disabled", enabled=False)],
        [],
        fetcher=lambda *args, **kwargs: pytest.fail("disabled feed fetched"),
        enricher=_identity_enricher,
        min_interval=0,
    )

    assert summary == RunSummary("error", 0, 0, 0, 0, (), (), _classification())
    run = _rows(database_path, "SELECT * FROM runs_log")[0]
    assert json.loads(run["notes"])["diagnostic"] == "no enabled feeds configured"
    assert _rows(database_path, "SELECT * FROM journals") == []


def test_only_failed_feed_produces_error_status(tmp_path: Path) -> None:
    summary = update_database(
        tmp_path / "radar.db",
        [_feed("bad")],
        [],
        fetcher=lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad feed")),
        enricher=_identity_enricher,
        min_interval=0,
    )

    assert summary == RunSummary("error", 0, 0, 0, 1, (), ("bad",), _classification())


def test_invalid_nonblank_unpaywall_email_fails_before_database_or_fetch(tmp_path: Path) -> None:
    database_path = tmp_path / "radar.db"

    with pytest.raises(ValueError, match="Unpaywall email"):
        update_database(
            database_path,
            [_feed("feed")],
            [],
            unpaywall_email="invalid-address",
            fetcher=lambda *args, **kwargs: pytest.fail("fetch should not run"),
            enricher=_identity_enricher,
            min_interval=0,
        )

    assert not database_path.exists()


def test_external_client_is_not_closed_but_owned_client_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    try:
        update_database(
            tmp_path / "external.db",
            [],
            [],
            client=external,
            fetcher=lambda *args, **kwargs: pytest.fail("no feeds"),
            enricher=_identity_enricher,
        )
        assert external.is_closed is False
    finally:
        external.close()

    created: list[httpx.Client] = []
    original_client = pipeline_module.PoliteClient

    class RecordingClient(original_client):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(
                transport=httpx.MockTransport(lambda request: httpx.Response(200)), **kwargs
            )
            created.append(self)

    monkeypatch.setattr(pipeline_module, "PoliteClient", RecordingClient)
    update_database(
        tmp_path / "owned.db",
        [],
        [],
        fetcher=lambda *args, **kwargs: pytest.fail("no feeds"),
        enricher=_identity_enricher,
    )
    assert len(created) == 1
    assert created[0].is_closed is True


def test_connection_is_still_closed_when_owned_client_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_connection = connect_database(tmp_path / "cleanup.db")

    class ConnectionProxy:
        def __init__(self) -> None:
            self.closed = False

        def __getattr__(self, name: str) -> object:
            return getattr(real_connection, name)

        def close(self) -> None:
            self.closed = True
            real_connection.close()

    proxy = ConnectionProxy()
    monkeypatch.setattr(pipeline_module, "connect_database", lambda path: proxy)
    original_client = pipeline_module.PoliteClient

    class CloseFailingClient(original_client):
        def close(self) -> None:
            super().close()
            raise RuntimeError("client cleanup failed")

    monkeypatch.setattr(pipeline_module, "PoliteClient", CloseFailingClient)

    with pytest.raises(RuntimeError, match="client cleanup failed"):
        update_database(tmp_path / "ignored.db", [], [])

    assert proxy.closed is True
