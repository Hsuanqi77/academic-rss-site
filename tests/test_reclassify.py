from pathlib import Path
import sqlite3

import pytest

from paper_radar.config import FeedConfig, TopicConfig
from paper_radar.database import (
    RepositoryConflictError,
    RepositoryNotFoundError,
    connect_database,
    initialize_database,
    register_journal,
    replace_all_article_tags,
    replace_article_tags,
    upsert_article,
)
from paper_radar.models import ArticleRecord, ClassificationSummary
from paper_radar.reclassify import ReclassificationError, reclassify_all_articles


@pytest.fixture
def connection(tmp_path: Path) -> sqlite3.Connection:
    database = connect_database(tmp_path / "reclassify.sqlite3")
    initialize_database(database)
    register_journal(
        database,
        FeedConfig(
            id="journal-1",
            name="Journal One",
            publisher="nature",
            feed_url="https://example.com/feed.xml",
        ),
    )
    try:
        yield database
    finally:
        database.close()


def article(uid: str, title: str, abstract: str | None = None) -> ArticleRecord:
    return ArticleRecord(
        uid=uid,
        doi=None,
        journal_id="journal-1",
        title=title,
        abstract=abstract,
        authors=("Ada Lovelace",),
        published_at="2026-07-01T00:00:00Z",
        article_type="research",
        article_url=f"https://example.com/{uid}",
        normalized_url=f"https://example.com/{uid}",
        oa_status="unknown",
        source_feed_url="https://example.com/feed.xml",
        metadata_status="rss_only",
    )


def topic(topic_id: str, label: str, *keywords: str) -> TopicConfig:
    return TopicConfig(
        id=topic_id,
        label=label,
        keywords=keywords,
        group="acoustic-rf",
    )


def links(connection: sqlite3.Connection) -> set[tuple[str, str]]:
    return {
        tuple(row)
        for row in connection.execute(
            "SELECT article_uid, tag_id FROM article_tags ORDER BY article_uid, tag_id"
        )
    }


def insert_two_articles(connection: sqlite3.Connection) -> tuple[ArticleRecord, ArticleRecord]:
    matching = article(
        "url:matching",
        "Piezoelectric bulk acoustic wave resonator",
    )
    unrelated = article("url:unrelated", "Marine biology observations")
    assert upsert_article(connection, matching) == "inserted"
    assert upsert_article(connection, unrelated) == "inserted"
    return matching, unrelated


def test_reclassify_replaces_obsolete_links_and_returns_summary(
    connection: sqlite3.Connection,
) -> None:
    matching, unrelated = insert_two_articles(connection)
    replace_article_tags(connection, unrelated.uid, [topic("obsolete", "Obsolete", "old")])
    topics = (
        topic("baw", "BAW", "bulk acoustic wave"),
        topic("piezo", "Piezoelectric", "piezoelectric"),
    )

    result = reclassify_all_articles(connection, topics)

    assert result == ClassificationSummary(
        articles_scanned=2,
        articles_tagged=1,
        tag_assignments=2,
        active_tags=2,
    )
    assert links(connection) == {(matching.uid, "baw"), (matching.uid, "piezo")}
    assert connection.execute("SELECT COUNT(*) FROM tags WHERE id = 'obsolete'").fetchone()[0] == 0


def test_reclassify_is_idempotent(connection: sqlite3.Connection) -> None:
    matching, _ = insert_two_articles(connection)
    topics = (topic("baw", "BAW", "bulk acoustic wave"),)

    first = reclassify_all_articles(connection, topics)
    second = reclassify_all_articles(connection, topics)

    assert first == second
    assert links(connection) == {(matching.uid, "baw")}


def test_reclassify_deduplicates_identical_topic_ids_before_reporting_summary(
    connection: sqlite3.Connection,
) -> None:
    record = article("url:one", "Bulk acoustic wave resonator")
    assert upsert_article(connection, record) == "inserted"
    repeated = topic("baw", "BAW", "bulk acoustic wave")

    summary = reclassify_all_articles(connection, (repeated, repeated))

    assert summary == ClassificationSummary(1, 1, 1, 1)
    assert connection.execute("SELECT COUNT(*) FROM article_tags").fetchone()[0] == 1
    assert links(connection) == {(record.uid, "baw")}


def test_reclassify_rejects_conflicting_duplicate_id_without_changing_old_links(
    connection: sqlite3.Connection,
) -> None:
    record = article("url:one", "Bulk acoustic wave resonator")
    assert upsert_article(connection, record) == "inserted"
    replace_article_tags(connection, record.uid, [topic("old", "Old", "old")])

    with pytest.raises(RepositoryConflictError, match="conflicting labels.*baw"):
        reclassify_all_articles(
            connection,
            (
                topic("baw", "BAW", "bulk acoustic wave"),
                topic("baw", "Bulk acoustic", "bulk acoustic wave"),
            ),
        )

    assert links(connection) == {(record.uid, "old")}


def test_reclassify_empty_database(connection: sqlite3.Connection) -> None:
    assert reclassify_all_articles(connection, ()) == ClassificationSummary(0, 0, 0, 0)
    assert links(connection) == set()


def test_reclassify_no_matches_clears_old_tags(connection: sqlite3.Connection) -> None:
    record = article("url:one", "Unrelated")
    assert upsert_article(connection, record) == "inserted"
    replace_article_tags(connection, record.uid, [topic("obsolete", "Obsolete", "old")])

    summary = reclassify_all_articles(
        connection,
        (topic("baw", "BAW", "bulk acoustic wave"),),
    )

    assert summary == ClassificationSummary(1, 0, 0, 0)
    assert links(connection) == set()
    assert connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0


@pytest.mark.parametrize("assignments", [{"url:one": (), "unknown": ()}, {}])
def test_replace_all_rejects_unknown_or_omitted_uids_without_mutation(
    connection: sqlite3.Connection,
    assignments: dict[str, tuple[TopicConfig, ...]],
) -> None:
    record = article("url:one", "One")
    assert upsert_article(connection, record) == "inserted"
    replace_article_tags(connection, record.uid, [topic("existing", "Existing", "one")])

    with pytest.raises((RepositoryNotFoundError, RepositoryConflictError), match="unknown|missing|omitted"):
        replace_all_article_tags(connection, assignments)

    assert links(connection) == {(record.uid, "existing")}


@pytest.mark.parametrize(
    ("conflicts", "message"),
    [
        (
            (topic("same", "First", "first"), topic("same", "Second", "second")),
            "conflicting labels",
        ),
        (
            (topic("first", "Same", "first"), topic("second", "Same", "second")),
            "duplicate tag label",
        ),
    ],
)
def test_replace_all_rejects_conflicting_id_or_label_and_preserves_links(
    connection: sqlite3.Connection,
    conflicts: tuple[TopicConfig, ...],
    message: str,
) -> None:
    record = article("url:one", "One")
    assert upsert_article(connection, record) == "inserted"
    replace_article_tags(connection, record.uid, [topic("existing", "Existing", "one")])
    with pytest.raises(RepositoryConflictError, match=message):
        replace_all_article_tags(connection, {record.uid: conflicts})

    assert links(connection) == {(record.uid, "existing")}


def test_replace_all_rolls_back_if_relationship_insert_fails(
    connection: sqlite3.Connection,
) -> None:
    first, second = insert_two_articles(connection)
    existing = topic("existing", "Existing", "existing")
    replace_article_tags(connection, first.uid, [existing])
    connection.execute(
        """
        CREATE TRIGGER reject_materials_link
        BEFORE INSERT ON article_tags
        WHEN NEW.tag_id = 'materials'
        BEGIN
            SELECT RAISE(ABORT, 'injected relationship failure');
        END
        """
    )
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected relationship failure"):
        replace_all_article_tags(
            connection,
            {
                first.uid: (topic("baw", "BAW", "baw"),),
                second.uid: (topic("materials", "Materials", "materials"),),
            },
        )

    assert links(connection) == {(first.uid, "existing")}
    assert [tuple(row) for row in connection.execute("SELECT id, label FROM tags")] == [
        ("existing", "Existing")
    ]
    assert connection.in_transaction is False


def test_classification_error_does_not_change_database(
    connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = article("url:one", "One")
    assert upsert_article(connection, record) == "inserted"
    replace_article_tags(connection, record.uid, [topic("existing", "Existing", "one")])

    def fail(*args: object, **kwargs: object) -> list[TopicConfig]:
        raise RuntimeError("classification failed")

    monkeypatch.setattr("paper_radar.reclassify.classify_article", fail)
    with pytest.raises(ReclassificationError, match="url:one") as captured:
        reclassify_all_articles(connection, ())

    assert isinstance(captured.value.__cause__, RuntimeError)
    assert str(captured.value.__cause__) == "classification failed"
    assert links(connection) == {(record.uid, "existing")}


def test_replace_all_deduplicates_same_topic_for_an_article(
    connection: sqlite3.Connection,
) -> None:
    record = article("url:one", "One")
    assert upsert_article(connection, record) == "inserted"
    repeated = topic("same", "Same", "same")

    replace_all_article_tags(connection, {record.uid: (repeated, repeated)})

    assert links(connection) == {(record.uid, "same")}


def test_replace_all_preserves_caller_owned_transaction(
    connection: sqlite3.Connection,
) -> None:
    record = article("url:one", "One")
    assert upsert_article(connection, record) == "inserted"
    connection.execute(
        "INSERT INTO journals (id, name, publisher, feed_url) VALUES (?, ?, ?, ?)",
        ("uncommitted", "Uncommitted", "nature", "https://example.com/uncommitted.xml"),
    )

    replace_all_article_tags(
        connection,
        {record.uid: (topic("same", "Same", "same"),)},
    )

    assert connection.in_transaction is True
    assert links(connection) == {(record.uid, "same")}
    connection.rollback()
    assert links(connection) == set()
    assert connection.execute("SELECT COUNT(*) FROM journals WHERE id = 'uncommitted'").fetchone()[0] == 0
