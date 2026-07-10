import os
import sqlite3
from pathlib import Path

import pytest

import paper_radar.validation as validation_module
from paper_radar.database import connect_database, initialize_database
from paper_radar.validation import ValidationError, publish_database, validate_database


def _database(
    path: Path,
    *,
    articles: int = 1,
    run_status: str | None = "ok",
    schema_version: int = 3,
) -> None:
    connection = connect_database(path)
    initialize_database(connection)
    connection.execute(
        """
        INSERT INTO journals (id, name, publisher, feed_url)
        VALUES ('journal', 'Journal', 'nature', 'https://example.test/feed')
        """
    )
    for index in range(articles):
        connection.execute(
            """
            INSERT INTO articles (
                uid, journal_id, title, authors_json, published_at, article_url,
                source_feed_url, first_seen_at, last_updated_at
            ) VALUES (?, 'journal', ?, '[]', ?, ?, 'https://example.test/feed', ?, ?)
            """,
            (
                f"article-{index}",
                f"Article {index}",
                f"2026-07-{index + 1:02d}T00:00:00Z",
                f"https://example.test/article/{index}",
                "2026-07-01T00:00:00Z",
                "2026-07-01T00:00:00Z",
            ),
        )
    if run_status is not None:
        connection.execute(
            """
            INSERT INTO runs_log (started_at, finished_at, status)
            VALUES ('2026-07-01T00:00:00Z', '2026-07-01T00:01:00Z', ?)
            """,
            (run_status,),
        )
    connection.execute(f"PRAGMA user_version = {schema_version}")
    connection.commit()
    connection.close()


def test_validate_database_returns_a_report_for_a_publishable_database(tmp_path: Path) -> None:
    database = tmp_path / "working.db"
    _database(database, articles=2)

    report = validate_database(database)

    assert report.article_count == 2
    assert report.journal_count == 1
    assert report.earliest_date == "2026-07-01T00:00:00Z"
    assert report.latest_date == "2026-07-02T00:00:00Z"
    assert report.schema_version == 3


@pytest.mark.parametrize("kind", ["missing", "directory", "corrupt"])
def test_validate_database_rejects_unreadable_database(tmp_path: Path, kind: str) -> None:
    database = tmp_path / "working.db"
    if kind == "directory":
        database.mkdir()
    elif kind == "corrupt":
        database.write_bytes(b"not sqlite")

    with pytest.raises(ValidationError):
        validate_database(database)


def test_empty_database_is_not_publishable(tmp_path: Path) -> None:
    database = tmp_path / "working.db"
    _database(database, articles=0)

    with pytest.raises(ValidationError, match="no articles"):
        validate_database(database)


@pytest.mark.parametrize("status", [None, "running", "error"])
def test_latest_run_must_be_a_completed_success(tmp_path: Path, status: str | None) -> None:
    database = tmp_path / "working.db"
    _database(database, run_status=status)

    with pytest.raises(ValidationError, match="latest run"):
        validate_database(database)


def test_completed_partial_run_is_publishable(tmp_path: Path) -> None:
    database = tmp_path / "working.db"
    _database(database, run_status="partial")

    assert validate_database(database).article_count == 1


def test_unknown_latest_run_status_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "working.db"
    _database(database)
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA ignore_check_constraints = ON")
    connection.execute("UPDATE runs_log SET status = 'unknown'")
    connection.commit()
    connection.close()

    with pytest.raises(ValidationError, match="latest run"):
        validate_database(database)


def test_wrong_schema_version_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "working.db"
    _database(database, schema_version=2)

    with pytest.raises(ValidationError, match="schema version"):
        validate_database(database)


def test_forged_schema_version_with_missing_required_column_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "working.db"
    _database(database)
    connection = sqlite3.connect(database)
    connection.execute("ALTER TABLE articles DROP COLUMN enriched_fields_json")
    connection.commit()
    connection.close()

    with pytest.raises(ValidationError, match="missing required columns"):
        validate_database(database)


def test_foreign_key_violation_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "working.db"
    _database(database)
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("UPDATE articles SET journal_id = 'missing'")
    connection.commit()
    connection.close()

    with pytest.raises(ValidationError, match="foreign key"):
        validate_database(database)


def test_catastrophic_article_count_drop_is_rejected(tmp_path: Path) -> None:
    previous = tmp_path / "published.db"
    working = tmp_path / "working.db"
    _database(previous, articles=10)
    _database(working, articles=4)

    with pytest.raises(ValidationError, match="dropped from 10 to 4"):
        validate_database(working, previous_path=previous)


def test_exactly_half_the_previous_article_count_is_allowed(tmp_path: Path) -> None:
    previous = tmp_path / "published.db"
    working = tmp_path / "working.db"
    _database(previous, articles=10)
    _database(working, articles=5)

    assert validate_database(working, previous_path=previous).article_count == 5


def test_corrupt_previous_database_blocks_publication(tmp_path: Path) -> None:
    previous = tmp_path / "published.db"
    working = tmp_path / "working.db"
    previous.write_bytes(b"broken")
    _database(working)

    with pytest.raises(ValidationError, match="previous"):
        validate_database(working, previous_path=previous)


def test_failed_publish_keeps_existing_database(tmp_path: Path) -> None:
    working = tmp_path / "working.db"
    published = tmp_path / "docs" / "data" / "papers.db"
    _database(working, articles=0)
    published.parent.mkdir(parents=True)
    published.write_bytes(b"known-good")

    with pytest.raises(ValidationError):
        publish_database(working, published)

    assert published.read_bytes() == b"known-good"
    assert not list(published.parent.glob("*.tmp"))


def test_publish_uses_a_validated_snapshot_and_replaces_atomically(tmp_path: Path) -> None:
    working = tmp_path / "working.db"
    published = tmp_path / "nested" / "papers.db"
    _database(working, articles=2)

    report = publish_database(working, published)

    assert report.article_count == 2
    assert validate_database(published).article_count == 2
    assert not list(published.parent.glob("*.tmp"))


def test_publish_accepts_a_validated_partial_run(tmp_path: Path) -> None:
    working = tmp_path / "working.db"
    published = tmp_path / "published.db"
    _database(working, run_status="partial")

    report = publish_database(working, published)

    assert report.article_count == 1
    assert validate_database(published).article_count == 1


@pytest.mark.parametrize("failure_stage", ["backup", "replace"])
def test_publish_failure_preserves_previous_database_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_stage: str
) -> None:
    working = tmp_path / "working.db"
    published = tmp_path / "papers.db"
    _database(working, articles=2)
    _database(published, articles=1)
    before = published.read_bytes()

    if failure_stage == "backup":

        def fail_backup(source: sqlite3.Connection, destination: sqlite3.Connection) -> None:
            raise OSError("backup failed")

        monkeypatch.setattr(validation_module, "_backup_database", fail_backup)
    else:

        def fail_replace(source: Path, destination: Path) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(ValidationError, match=failure_stage):
        publish_database(working, published)

    assert published.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_working_and_published_paths_must_differ(tmp_path: Path) -> None:
    database = tmp_path / "papers.db"
    _database(database)

    with pytest.raises(ValidationError, match="must differ"):
        publish_database(database, database)
