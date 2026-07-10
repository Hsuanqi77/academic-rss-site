import json
import re
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import paper_radar.database as database_module
from paper_radar.config import FeedConfig, TopicConfig
from paper_radar.database import (
    RepositoryConflictError,
    RepositoryNotFoundError,
    create_run,
    finish_run,
    get_feed_state,
    mark_journal_status,
    register_journal,
    replace_article_tags,
    resolve_article_uid,
    upsert_article,
    utc_now,
)
from paper_radar.models import ArticleRecord


@pytest.fixture
def connection(tmp_path: Path) -> sqlite3.Connection:
    database = database_module.connect_database(tmp_path / "repository.sqlite3")
    database_module.initialize_database(database)
    try:
        yield database
    finally:
        database.close()


def feed(
    *,
    feed_id: str = "journal-1",
    name: str = "Journal One",
    publisher: str = "nature",
    feed_url: str = "https://example.com/feed.xml",
    enabled: bool = True,
) -> FeedConfig:
    return FeedConfig(
        id=feed_id,
        name=name,
        publisher=publisher,
        feed_url=feed_url,
        enabled=enabled,
    )


def article(**changes: object) -> ArticleRecord:
    record = ArticleRecord(
        uid="url:article-1",
        doi=None,
        journal_id="journal-1",
        title="A useful paper",
        abstract="A detailed abstract",
        authors=("Ada Lovelace", "张三"),
        published_at="2026-07-01T00:00:00Z",
        article_type="research",
        article_url="https://example.com/articles/1?utm_source=rss",
        normalized_url="https://example.com/articles/1",
        oa_status="open",
        source_feed_url="https://example.com/feed.xml",
        metadata_status="enriched",
    )
    return replace(record, **changes)


def topic(topic_id: str, label: str) -> TopicConfig:
    return TopicConfig(id=topic_id, label=label, keywords=(label.lower(),))


def register_default_journal(connection: sqlite3.Connection) -> None:
    register_journal(connection, feed())


def test_repository_api_is_importable() -> None:
    assert all(
        callable(function)
        for function in (
            utc_now,
            register_journal,
            get_feed_state,
            mark_journal_status,
            upsert_article,
            resolve_article_uid,
            replace_article_tags,
            create_run,
            finish_run,
        )
    )
    assert issubclass(RepositoryConflictError, Exception)
    assert issubclass(RepositoryNotFoundError, Exception)


def test_utc_now_is_utc_iso_seconds() -> None:
    timestamp = utc_now()

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", timestamp)
    parsed = datetime.fromisoformat(timestamp.removesuffix("Z") + "+00:00")
    assert parsed.tzinfo == timezone.utc
    assert parsed.microsecond == 0


def test_register_journal_upserts_configuration_without_erasing_feed_state(
    connection: sqlite3.Connection,
) -> None:
    register_journal(connection, feed())
    connection.execute(
        """
        UPDATE journals
        SET etag = ?, last_modified = ?, last_checked_at = ?,
            last_success_at = ?, last_status = ?, last_error = ?
        WHERE id = ?
        """,
        (
            '"cached"',
            "Wed, 01 Jul 2026 00:00:00 GMT",
            "2026-07-01T00:00:00Z",
            "2026-07-01T00:00:00Z",
            "ok",
            "old error",
            "journal-1",
        ),
    )
    connection.commit()

    register_journal(
        connection,
        feed(
            name="Renamed Journal",
            feed_url="https://example.com/new-feed.xml",
            enabled=False,
        ),
    )

    row = connection.execute("SELECT * FROM journals WHERE id = ?", ("journal-1",)).fetchone()
    assert row is not None
    assert (row["name"], row["publisher"], row["feed_url"], row["enabled"]) == (
        "Renamed Journal",
        "nature",
        "https://example.com/new-feed.xml",
        0,
    )
    assert (
        row["etag"],
        row["last_modified"],
        row["last_checked_at"],
        row["last_success_at"],
        row["last_status"],
        row["last_error"],
    ) == (
        '"cached"',
        "Wed, 01 Jul 2026 00:00:00 GMT",
        "2026-07-01T00:00:00Z",
        "2026-07-01T00:00:00Z",
        "ok",
        "old error",
    )
    assert connection.in_transaction is False


def test_get_feed_state_returns_validators_and_handles_missing_id(
    connection: sqlite3.Connection,
) -> None:
    register_default_journal(connection)
    connection.execute(
        "UPDATE journals SET etag = ?, last_modified = ? WHERE id = ?",
        ('"v1"', "Thu, 02 Jul 2026 00:00:00 GMT", "journal-1"),
    )
    connection.commit()

    assert get_feed_state(connection, "journal-1") == (
        '"v1"',
        "Thu, 02 Jul 2026 00:00:00 GMT",
    )
    assert get_feed_state(connection, "missing") == (None, None)


def test_mark_journal_status_tracks_errors_success_and_validators(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_default_journal(connection)
    clock = {"now": "2026-07-10T10:00:00Z"}
    monkeypatch.setattr(database_module, "utc_now", lambda: clock["now"])

    mark_journal_status(
        connection,
        "journal-1",
        status="error",
        error="timeout",
        etag='"v1"',
        last_modified="Thu, 09 Jul 2026 00:00:00 GMT",
    )
    failed_row = connection.execute(
        "SELECT * FROM journals WHERE id = ?", ("journal-1",)
    ).fetchone()
    assert failed_row is not None
    assert failed_row["last_checked_at"] == "2026-07-10T10:00:00Z"
    assert failed_row["last_success_at"] is None
    assert failed_row["last_status"] == "error"
    assert failed_row["last_error"] == "timeout"
    assert failed_row["etag"] == '"v1"'

    clock["now"] = "2026-07-10T11:00:00Z"
    mark_journal_status(
        connection,
        "journal-1",
        status="not_modified",
        last_modified="Fri, 10 Jul 2026 00:00:00 GMT",
    )
    success_row = connection.execute(
        "SELECT * FROM journals WHERE id = ?", ("journal-1",)
    ).fetchone()
    assert success_row is not None
    assert success_row["last_checked_at"] == "2026-07-10T11:00:00Z"
    assert success_row["last_success_at"] == "2026-07-10T11:00:00Z"
    assert success_row["last_status"] == "not_modified"
    assert success_row["last_error"] is None
    assert success_row["etag"] == '"v1"'
    assert success_row["last_modified"] == "Fri, 10 Jul 2026 00:00:00 GMT"
    assert connection.in_transaction is False


def test_mark_journal_status_rejects_missing_journal_and_blank_status(
    connection: sqlite3.Connection,
) -> None:
    with pytest.raises(RepositoryNotFoundError, match="missing"):
        mark_journal_status(connection, "missing", status="error", error="gone")
    with pytest.raises(ValueError, match="status"):
        mark_journal_status(connection, "missing", status=" ")
    assert connection.in_transaction is False


def test_mark_journal_partial_status_keeps_diagnostics_until_success(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_default_journal(connection)
    clock = {"now": "2026-07-10T10:00:00Z"}
    monkeypatch.setattr(database_module, "utc_now", lambda: clock["now"])

    mark_journal_status(
        connection,
        "journal-1",
        status="partial",
        error="2 item failures",
    )
    partial = connection.execute(
        "SELECT last_success_at, last_status, last_error FROM journals WHERE id = ?",
        ("journal-1",),
    ).fetchone()
    assert tuple(partial) == (
        "2026-07-10T10:00:00Z",
        "partial",
        "2 item failures",
    )

    clock["now"] = "2026-07-10T11:00:00Z"
    mark_journal_status(connection, "journal-1", status="ok")
    successful = connection.execute(
        "SELECT last_success_at, last_status, last_error FROM journals WHERE id = ?",
        ("journal-1",),
    ).fetchone()
    assert tuple(successful) == ("2026-07-10T11:00:00Z", "ok", None)


def test_upsert_article_inserts_all_fields_and_unicode_authors(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_default_journal(connection)
    monkeypatch.setattr(database_module, "utc_now", lambda: "2026-07-10T12:00:00Z")
    record = article()

    result = upsert_article(connection, record)

    assert result == "inserted"
    assert type(result) is str
    row = connection.execute("SELECT * FROM articles").fetchone()
    assert row is not None
    assert row["uid"] == record.uid
    assert row["doi"] == record.doi
    assert row["journal_id"] == record.journal_id
    assert row["title"] == record.title
    assert row["abstract"] == record.abstract
    assert json.loads(row["authors_json"]) == list(record.authors)
    assert row["published_at"] == record.published_at
    assert row["article_type"] == record.article_type
    assert row["article_url"] == record.article_url
    assert row["normalized_url"] == record.normalized_url
    assert row["oa_status"] == record.oa_status
    assert row["source_feed_url"] == record.source_feed_url
    assert row["metadata_status"] == record.metadata_status
    assert row["first_seen_at"] == "2026-07-10T12:00:00Z"
    assert row["last_updated_at"] == "2026-07-10T12:00:00Z"
    assert resolve_article_uid(connection, record) == record.uid
    assert connection.in_transaction is False


def test_upsert_article_skips_unchanged_and_updates_only_actual_changes(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_default_journal(connection)
    clock = {"now": "2026-07-10T12:00:00Z"}
    monkeypatch.setattr(database_module, "utc_now", lambda: clock["now"])
    record = article()
    assert upsert_article(connection, record) == "inserted"

    clock["now"] = "2026-07-10T13:00:00Z"
    assert upsert_article(connection, record) == "skipped"
    skipped_row = connection.execute(
        "SELECT first_seen_at, last_updated_at FROM articles"
    ).fetchone()
    assert tuple(skipped_row) == ("2026-07-10T12:00:00Z", "2026-07-10T12:00:00Z")

    clock["now"] = "2026-07-10T14:00:00Z"
    assert upsert_article(connection, replace(record, title="A corrected title")) == "updated"
    updated_row = connection.execute(
        "SELECT title, first_seen_at, last_updated_at FROM articles"
    ).fetchone()
    assert tuple(updated_row) == (
        "A corrected title",
        "2026-07-10T12:00:00Z",
        "2026-07-10T14:00:00Z",
    )


def test_url_only_article_is_enriched_without_uid_churn_or_tag_loss(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_default_journal(connection)
    clock = {"now": "2026-07-10T12:00:00Z"}
    monkeypatch.setattr(database_module, "utc_now", lambda: clock["now"])
    url_only = article(
        uid="url:stable",
        doi=None,
        abstract=None,
        authors=(),
        article_type="other",
        oa_status="unknown",
        metadata_status="rss_only",
    )
    assert upsert_article(connection, url_only) == "inserted"
    replace_article_tags(connection, url_only.uid, [topic("acoustics", "Acoustics")])

    clock["now"] = "2026-07-11T12:00:00Z"
    enriched = article(uid="doi:10.1234/example", doi="10.1234/example")
    assert upsert_article(connection, enriched) == "updated"

    rows = connection.execute("SELECT * FROM articles").fetchall()
    assert len(rows) == 1
    assert rows[0]["uid"] == "url:stable"
    assert rows[0]["doi"] == "10.1234/example"
    assert rows[0]["first_seen_at"] == "2026-07-10T12:00:00Z"
    assert rows[0]["last_updated_at"] == "2026-07-11T12:00:00Z"
    assert (
        connection.execute("SELECT article_uid, tag_id FROM article_tags").fetchall()[0][
            "article_uid"
        ]
        == "url:stable"
    )
    assert resolve_article_uid(connection, enriched) == "url:stable"


def test_historical_url_alias_replay_resolves_survivor_without_duplicate(
    connection: sqlite3.Connection,
) -> None:
    register_default_journal(connection)
    old_url = "https://example.com/articles/old"
    new_url = "https://example.com/articles/new"
    url_only = article(uid="url:stable", doi=None, normalized_url=old_url)
    assert upsert_article(connection, url_only) == "inserted"
    enriched = article(
        uid="doi:10.1234/alias",
        doi="10.1234/alias",
        normalized_url=old_url,
    )
    assert upsert_article(connection, enriched) == "updated"
    moved = replace(enriched, normalized_url=new_url, article_url=new_url)
    assert upsert_article(connection, moved) == "updated"

    replay = article(
        uid="url:replay-candidate",
        doi=None,
        normalized_url=old_url,
        article_url=old_url,
        metadata_status="rss_only",
    )
    assert resolve_article_uid(connection, replay) == url_only.uid
    assert upsert_article(connection, replay) == "updated"

    assert connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
    assert {
        tuple(row)
        for row in connection.execute("SELECT normalized_url, article_uid FROM article_url_aliases")
    } == {(old_url, url_only.uid), (new_url, url_only.uid)}


def test_direct_normalized_url_fallback_repairs_missing_alias(
    connection: sqlite3.Connection,
) -> None:
    register_default_journal(connection)
    record = article(uid="url:stable")
    assert upsert_article(connection, record) == "inserted"
    connection.execute("DELETE FROM article_url_aliases")
    connection.commit()
    replay = replace(record, uid="url:defensive-replay")

    assert upsert_article(connection, replay) == "skipped"

    alias = connection.execute(
        "SELECT normalized_url, article_uid FROM article_url_aliases"
    ).fetchone()
    assert tuple(alias) == (record.normalized_url, record.uid)


def test_lower_quality_url_record_does_not_erase_enriched_metadata(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_default_journal(connection)
    clock = {"now": "2026-07-10T12:00:00Z"}
    monkeypatch.setattr(database_module, "utc_now", lambda: clock["now"])
    enriched = article(uid="doi:10.1234/example", doi="10.1234/example")
    assert upsert_article(connection, enriched) == "inserted"

    clock["now"] = "2026-07-11T12:00:00Z"
    low_quality = article(
        uid="url:new-candidate",
        doi=None,
        title="Updated feed title",
        abstract="A truncated RSS snippet",
        authors=("Ada Lovelace",),
        published_at="2026-01-01T00:00:00Z",
        article_type="review",
        article_url="https://example.com/articles/1?source=new-feed",
        oa_status="unknown",
        source_feed_url="https://example.com/new-feed.xml",
        metadata_status="rss_only",
    )
    assert upsert_article(connection, low_quality) == "updated"

    row = connection.execute("SELECT * FROM articles").fetchone()
    assert row is not None
    assert row["uid"] == enriched.uid
    assert row["doi"] == enriched.doi
    assert row["title"] == "Updated feed title"
    assert row["article_url"] == low_quality.article_url
    assert row["source_feed_url"] == low_quality.source_feed_url
    assert row["abstract"] == enriched.abstract
    assert json.loads(row["authors_json"]) == list(enriched.authors)
    assert row["published_at"] == enriched.published_at
    assert row["article_type"] == enriched.article_type
    assert row["oa_status"] == enriched.oa_status
    assert row["metadata_status"] == enriched.metadata_status
    assert resolve_article_uid(connection, low_quality) == enriched.uid


@pytest.mark.parametrize(
    ("enriched_oa", "rss_oa"),
    (("open", "closed"), ("closed", "open")),
)
def test_lower_rank_record_cannot_change_known_enriched_oa_status(
    connection: sqlite3.Connection,
    enriched_oa: str,
    rss_oa: str,
) -> None:
    register_default_journal(connection)
    enriched = article(
        uid="doi:10.1234/oa-rank",
        doi="10.1234/oa-rank",
        oa_status=enriched_oa,
        metadata_status="enriched",
    )
    assert upsert_article(connection, enriched) == "inserted"
    rss_update = article(
        uid="url:oa-rank",
        doi=None,
        title="Updated RSS title",
        oa_status=rss_oa,
        metadata_status="rss_only",
    )

    assert upsert_article(connection, rss_update) == "updated"

    row = connection.execute("SELECT title, oa_status, metadata_status FROM articles").fetchone()
    assert tuple(row) == ("Updated RSS title", enriched_oa, "enriched")


def test_same_rank_meaningful_oa_status_may_update_but_unknown_cannot_erase_it(
    connection: sqlite3.Connection,
) -> None:
    register_default_journal(connection)
    original = article(oa_status="open", metadata_status="enriched")
    assert upsert_article(connection, original) == "inserted"
    changed = replace(original, oa_status="closed")

    assert upsert_article(connection, changed) == "updated"
    assert connection.execute("SELECT oa_status FROM articles").fetchone()[0] == "closed"

    assert upsert_article(connection, replace(changed, oa_status="unknown")) == "skipped"
    assert connection.execute("SELECT oa_status FROM articles").fetchone()[0] == "closed"


def test_conflicting_doi_for_same_url_rolls_back_without_partial_changes(
    connection: sqlite3.Connection,
) -> None:
    register_default_journal(connection)
    original = article(uid="doi:first", doi="10.1234/first")
    assert upsert_article(connection, original) == "inserted"

    with pytest.raises(RepositoryConflictError, match="normalized URL"):
        upsert_article(
            connection,
            article(uid="doi:second", doi="10.1234/second", title="Should not persist"),
        )

    rows = connection.execute("SELECT uid, doi, title FROM articles").fetchall()
    assert [tuple(row) for row in rows] == [(original.uid, original.doi, original.title)]
    assert connection.in_transaction is False


def test_same_doi_in_conflicting_journal_is_rejected(
    connection: sqlite3.Connection,
) -> None:
    register_default_journal(connection)
    register_journal(
        connection,
        feed(
            feed_id="journal-2",
            name="Journal Two",
            publisher="aip",
            feed_url="https://example.org/feed.xml",
        ),
    )
    original = article(uid="doi:shared", doi="10.1234/shared")
    assert upsert_article(connection, original) == "inserted"
    conflicting = article(
        uid="doi:shared-new",
        doi="10.1234/shared",
        journal_id="journal-2",
        normalized_url="https://example.org/articles/shared",
        article_url="https://example.org/articles/shared",
        source_feed_url="https://example.org/feed.xml",
    )

    with pytest.raises(RepositoryConflictError, match="journal"):
        upsert_article(connection, conflicting)

    assert connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1


def test_split_identity_rows_merge_tags_and_preserve_earliest_first_seen(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_default_journal(connection)
    clock = {"now": "2026-07-10T12:00:00Z"}
    monkeypatch.setattr(database_module, "utc_now", lambda: clock["now"])
    doi_row = article(
        uid="doi:survivor",
        doi="10.1234/split",
        normalized_url="https://example.com/old-url",
    )
    assert upsert_article(connection, doi_row) == "inserted"
    replace_article_tags(connection, doi_row.uid, [topic("doi-tag", "DOI tag")])

    clock["now"] = "2026-07-09T12:00:00Z"
    url_row = article(
        uid="url:loser",
        doi=None,
        normalized_url="https://example.com/canonical",
    )
    assert upsert_article(connection, url_row) == "inserted"
    replace_article_tags(connection, url_row.uid, [topic("url-tag", "URL tag")])

    clock["now"] = "2026-07-11T12:00:00Z"
    combined = article(
        uid="doi:new-candidate",
        doi="10.1234/split",
        normalized_url="https://example.com/canonical",
        title="Combined identity",
    )
    assert upsert_article(connection, combined) == "updated"

    rows = connection.execute("SELECT * FROM articles").fetchall()
    assert len(rows) == 1
    assert rows[0]["uid"] == doi_row.uid
    assert rows[0]["first_seen_at"] == "2026-07-09T12:00:00Z"
    assert rows[0]["normalized_url"] == combined.normalized_url
    assert {row["tag_id"] for row in connection.execute("SELECT tag_id FROM article_tags")} == {
        "doi-tag",
        "url-tag",
    }
    assert resolve_article_uid(connection, combined) == doi_row.uid


def test_split_identity_merge_repoints_all_historical_aliases(
    connection: sqlite3.Connection,
) -> None:
    register_default_journal(connection)
    doi_row = article(
        uid="doi:alias-survivor",
        doi="10.1234/split-alias",
        normalized_url="https://example.com/doi-current",
    )
    url_row = article(
        uid="url:alias-loser",
        doi=None,
        normalized_url="https://example.com/url-current",
    )
    assert upsert_article(connection, doi_row) == "inserted"
    assert upsert_article(connection, url_row) == "inserted"
    connection.executemany(
        "INSERT OR IGNORE INTO article_url_aliases (normalized_url, article_uid) VALUES (?, ?)",
        (
            (doi_row.normalized_url, doi_row.uid),
            ("https://example.com/doi-historical", doi_row.uid),
            (url_row.normalized_url, url_row.uid),
            ("https://example.com/url-historical", url_row.uid),
        ),
    )
    connection.commit()
    combined = article(
        uid="doi:new-alias-candidate",
        doi=doi_row.doi,
        normalized_url=url_row.normalized_url,
    )

    assert upsert_article(connection, combined) == "updated"

    assert connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
    aliases = connection.execute(
        "SELECT normalized_url, article_uid FROM article_url_aliases"
    ).fetchall()
    assert {row["normalized_url"] for row in aliases} == {
        doi_row.normalized_url,
        "https://example.com/doi-historical",
        url_row.normalized_url,
        "https://example.com/url-historical",
    }
    assert {row["article_uid"] for row in aliases} == {doi_row.uid}


def test_conflicting_historical_alias_and_doi_roll_back(
    connection: sqlite3.Connection,
) -> None:
    register_default_journal(connection)
    doi_row = article(
        uid="doi:first",
        doi="10.1234/first",
        normalized_url="https://example.com/first",
    )
    alias_owner = article(
        uid="doi:second",
        doi="10.1234/second",
        normalized_url="https://example.com/second-current",
    )
    assert upsert_article(connection, doi_row) == "inserted"
    assert upsert_article(connection, alias_owner) == "inserted"
    historical_url = "https://example.com/second-historical"
    connection.execute(
        "INSERT INTO article_url_aliases (normalized_url, article_uid) VALUES (?, ?)",
        (historical_url, alias_owner.uid),
    )
    connection.commit()
    incoming = replace(doi_row, normalized_url=historical_url, title="Must roll back")

    with pytest.raises(RepositoryConflictError, match="normalized URL|contradictory DOI"):
        upsert_article(connection, incoming)

    assert {
        tuple(row)
        for row in connection.execute("SELECT uid, doi, title FROM articles ORDER BY uid")
    } == {
        (doi_row.uid, doi_row.doi, doi_row.title),
        (alias_owner.uid, alias_owner.doi, alias_owner.title),
    }
    assert (
        connection.execute(
            "SELECT article_uid FROM article_url_aliases WHERE normalized_url = ?",
            (historical_url,),
        ).fetchone()[0]
        == alias_owner.uid
    )
    assert connection.in_transaction is False


def test_split_identity_merge_keeps_metadata_from_higher_quality_row(
    connection: sqlite3.Connection,
) -> None:
    register_default_journal(connection)
    doi_row = article(
        uid="doi:survivor",
        doi="10.1234/split-quality",
        normalized_url="https://example.com/old-url",
        abstract="A truncated RSS snippet",
        authors=("RSS Author",),
        published_at="2026-01-01T00:00:00Z",
        article_type="other",
        metadata_status="rss_only",
    )
    url_row = article(
        uid="url:enriched-loser",
        doi=None,
        normalized_url="https://example.com/canonical",
        abstract="The complete enriched abstract",
        authors=("Ada Lovelace", "Grace Hopper"),
        published_at="2026-07-01T00:00:00Z",
        article_type="research",
        metadata_status="enriched",
    )
    assert upsert_article(connection, doi_row) == "inserted"
    assert upsert_article(connection, url_row) == "inserted"
    combined = article(
        uid="doi:new-candidate",
        doi="10.1234/split-quality",
        normalized_url="https://example.com/canonical",
        abstract=None,
        authors=(),
        published_at=None,
        article_type="other",
        metadata_status="rss_only",
    )

    assert upsert_article(connection, combined) == "updated"

    row = connection.execute("SELECT * FROM articles").fetchone()
    assert row is not None
    assert row["uid"] == doi_row.uid
    assert row["abstract"] == url_row.abstract
    assert json.loads(row["authors_json"]) == list(url_row.authors)
    assert row["published_at"] == url_row.published_at
    assert row["article_type"] == url_row.article_type
    assert row["metadata_status"] == "enriched"


def test_split_identity_merge_keeps_oa_from_higher_quality_row(
    connection: sqlite3.Connection,
) -> None:
    register_default_journal(connection)
    doi_row = article(
        uid="doi:oa-survivor",
        doi="10.1234/split-oa",
        normalized_url="https://example.com/old-url",
        oa_status="closed",
        metadata_status="rss_only",
    )
    url_row = article(
        uid="url:oa-loser",
        doi=None,
        normalized_url="https://example.com/canonical",
        oa_status="open",
        metadata_status="enriched",
    )
    assert upsert_article(connection, doi_row) == "inserted"
    assert upsert_article(connection, url_row) == "inserted"
    combined = article(
        uid="doi:new-oa-candidate",
        doi="10.1234/split-oa",
        normalized_url="https://example.com/canonical",
        oa_status="closed",
        metadata_status="rss_only",
    )

    assert upsert_article(connection, combined) == "updated"

    row = connection.execute("SELECT uid, oa_status, metadata_status FROM articles").fetchone()
    assert tuple(row) == (doi_row.uid, "open", "enriched")


def test_identity_merge_failure_restores_both_rows_and_tag_links(
    connection: sqlite3.Connection,
) -> None:
    register_default_journal(connection)
    doi_row = article(
        uid="doi:survivor",
        doi="10.1234/split",
        normalized_url="https://example.com/old-url",
    )
    url_row = article(
        uid="url:loser",
        doi=None,
        normalized_url="https://example.com/canonical",
    )
    assert upsert_article(connection, doi_row) == "inserted"
    assert upsert_article(connection, url_row) == "inserted"
    replace_article_tags(connection, doi_row.uid, [topic("doi-tag", "DOI tag")])
    replace_article_tags(connection, url_row.uid, [topic("url-tag", "URL tag")])
    connection.execute(
        """
        CREATE TRIGGER reject_article_update
        BEFORE UPDATE ON articles
        BEGIN
            SELECT RAISE(ABORT, 'injected update failure');
        END
        """
    )
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected update failure"):
        upsert_article(
            connection,
            article(
                uid="doi:new-candidate",
                doi="10.1234/split",
                normalized_url="https://example.com/canonical",
            ),
        )

    assert {row["uid"] for row in connection.execute("SELECT uid FROM articles")} == {
        doi_row.uid,
        url_row.uid,
    }
    assert {
        tuple(row) for row in connection.execute("SELECT article_uid, tag_id FROM article_tags")
    } == {(doi_row.uid, "doi-tag"), (url_row.uid, "url-tag")}
    assert connection.in_transaction is False


def test_replace_article_tags_is_idempotent_updates_labels_and_removes_stale_links(
    connection: sqlite3.Connection,
) -> None:
    register_default_journal(connection)
    record = article()
    assert upsert_article(connection, record) == "inserted"
    first_topics = [topic("acoustics", "Acoustics"), topic("materials", "Materials")]

    replace_article_tags(connection, record.uid, first_topics)
    replace_article_tags(connection, record.uid, first_topics)
    replace_article_tags(connection, record.uid, [topic("acoustics", "Wave acoustics")])

    links = connection.execute(
        """
        SELECT article_tags.article_uid, tags.id, tags.label
        FROM article_tags JOIN tags ON tags.id = article_tags.tag_id
        """
    ).fetchall()
    assert [tuple(row) for row in links] == [(record.uid, "acoustics", "Wave acoustics")]
    stale_tag = connection.execute("SELECT label FROM tags WHERE id = ?", ("materials",)).fetchone()
    assert stale_tag["label"] == "Materials"
    assert connection.in_transaction is False


def test_replace_article_tags_rolls_back_all_changes_on_label_conflict(
    connection: sqlite3.Connection,
) -> None:
    register_default_journal(connection)
    record = article()
    assert upsert_article(connection, record) == "inserted"
    replace_article_tags(
        connection,
        record.uid,
        [topic("acoustics", "Acoustics"), topic("materials", "Materials")],
    )

    with pytest.raises(sqlite3.IntegrityError):
        replace_article_tags(connection, record.uid, [topic("acoustics", "Materials")])

    assert {
        row["tag_id"]
        for row in connection.execute(
            "SELECT tag_id FROM article_tags WHERE article_uid = ?", (record.uid,)
        )
    } == {"acoustics", "materials"}
    assert (
        connection.execute("SELECT label FROM tags WHERE id = ?", ("acoustics",)).fetchone()[
            "label"
        ]
        == "Acoustics"
    )
    assert connection.in_transaction is False


def test_replace_article_tags_rejects_missing_article(connection: sqlite3.Connection) -> None:
    with pytest.raises(RepositoryNotFoundError, match="missing"):
        replace_article_tags(connection, "missing", [topic("acoustics", "Acoustics")])


def test_repository_operation_inside_outer_transaction_does_not_commit_caller_work(
    connection: sqlite3.Connection,
) -> None:
    register_default_journal(connection)
    record = article()
    assert upsert_article(connection, record) == "inserted"
    connection.execute(
        "INSERT INTO journals (id, name, publisher, feed_url) VALUES (?, ?, ?, ?)",
        ("uncommitted", "Uncommitted", "aip", "https://example.org/feed.xml"),
    )
    assert connection.in_transaction is True

    replace_article_tags(connection, record.uid, [topic("acoustics", "Acoustics")])

    assert connection.in_transaction is True
    assert connection.execute("SELECT COUNT(*) FROM article_tags").fetchone()[0] == 1
    connection.rollback()
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM journals WHERE id = ?", ("uncommitted",)
        ).fetchone()[0]
        == 0
    )
    assert connection.execute("SELECT COUNT(*) FROM article_tags").fetchone()[0] == 0


def test_repository_rolls_back_if_its_own_commit_fails(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TRIGGER inject_deferred_foreign_key_failure
        AFTER INSERT ON journals
        BEGIN
            INSERT INTO articles (
                uid, journal_id, title, article_url, source_feed_url,
                first_seen_at, last_updated_at
            ) VALUES (
                'injected-orphan', 'missing-journal', 'Injected',
                'https://example.com/injected', 'https://example.com/feed.xml',
                '2026-07-10T00:00:00Z', '2026-07-10T00:00:00Z'
            );
        END
        """
    )
    connection.commit()
    connection.execute("PRAGMA defer_foreign_keys = ON")

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        register_default_journal(connection)

    assert connection.in_transaction is False
    assert connection.execute("SELECT COUNT(*) FROM journals").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0


def test_nested_repository_operation_preserves_error_after_transaction_wide_rollback(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        CREATE TRIGGER inject_transaction_wide_rollback
        BEFORE INSERT ON journals
        BEGIN
            SELECT RAISE(ROLLBACK, 'injected transaction rollback');
        END
        """
    )
    connection.commit()
    connection.execute("BEGIN")

    with pytest.raises(sqlite3.IntegrityError, match="injected transaction rollback"):
        register_default_journal(connection)

    assert connection.in_transaction is False
    assert connection.execute("SELECT COUNT(*) FROM journals").fetchone()[0] == 0


def test_create_and_finish_run_lifecycle(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"now": "2026-07-10T12:00:00Z"}
    monkeypatch.setattr(database_module, "utc_now", lambda: clock["now"])

    run_id = create_run(connection)
    started = connection.execute("SELECT * FROM runs_log WHERE id = ?", (run_id,)).fetchone()
    assert started is not None
    assert started["started_at"] == "2026-07-10T12:00:00Z"
    assert started["finished_at"] is None
    assert started["status"] == "running"
    assert tuple(
        started[key] for key in ("inserted_count", "updated_count", "skipped_count", "failed_count")
    ) == (0, 0, 0, 0)

    clock["now"] = "2026-07-10T12:05:00Z"
    finish_run(
        connection,
        run_id,
        status="partial",
        inserted=3,
        updated=2,
        skipped=4,
        failed=1,
        notes="one feed failed",
    )
    finished = connection.execute("SELECT * FROM runs_log WHERE id = ?", (run_id,)).fetchone()
    assert finished is not None
    assert finished["finished_at"] == "2026-07-10T12:05:00Z"
    assert finished["status"] == "partial"
    assert tuple(
        finished[key]
        for key in ("inserted_count", "updated_count", "skipped_count", "failed_count")
    ) == (3, 2, 4, 1)
    assert finished["notes"] == "one feed failed"
    assert connection.in_transaction is False


@pytest.mark.parametrize("status", ["running", "done", "", " "])
def test_finish_run_rejects_nonterminal_status(connection: sqlite3.Connection, status: str) -> None:
    run_id = create_run(connection)
    with pytest.raises(ValueError, match="terminal status"):
        finish_run(
            connection,
            run_id,
            status=status,
            inserted=0,
            updated=0,
            skipped=0,
            failed=0,
            notes="",
        )


@pytest.mark.parametrize("field", ["inserted", "updated", "skipped", "failed"])
def test_finish_run_rejects_negative_or_noninteger_counts(
    connection: sqlite3.Connection, field: str
) -> None:
    run_id = create_run(connection)
    counts: dict[str, object] = {"inserted": 0, "updated": 0, "skipped": 0, "failed": 0}
    counts[field] = -1
    with pytest.raises(ValueError, match=field):
        finish_run(connection, run_id, status="error", notes="bad count", **counts)  # type: ignore[arg-type]

    counts[field] = 1.5
    with pytest.raises(ValueError, match=field):
        finish_run(connection, run_id, status="error", notes="bad count", **counts)  # type: ignore[arg-type]


def test_finish_run_rejects_missing_or_already_finished_run(
    connection: sqlite3.Connection,
) -> None:
    with pytest.raises(RepositoryNotFoundError, match="999"):
        finish_run(
            connection,
            999,
            status="error",
            inserted=0,
            updated=0,
            skipped=0,
            failed=1,
            notes="missing",
        )

    run_id = create_run(connection)
    finish_run(
        connection,
        run_id,
        status="ok",
        inserted=1,
        updated=0,
        skipped=0,
        failed=0,
        notes="",
    )
    with pytest.raises(RepositoryConflictError, match="already finished"):
        finish_run(
            connection,
            run_id,
            status="ok",
            inserted=1,
            updated=0,
            skipped=0,
            failed=0,
            notes="again",
        )


def test_run_creation_inside_outer_transaction_is_rolled_back_with_caller_work(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        "INSERT INTO journals (id, name, publisher, feed_url) VALUES (?, ?, ?, ?)",
        ("outer", "Outer", "nature", "https://example.com/outer.xml"),
    )

    run_id = create_run(connection)

    assert connection.in_transaction is True
    assert connection.execute("SELECT id FROM runs_log WHERE id = ?", (run_id,)).fetchone()
    connection.rollback()
    assert connection.execute("SELECT COUNT(*) FROM runs_log").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM journals").fetchone()[0] == 0
