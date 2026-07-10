import sqlite3
from pathlib import Path

import pytest

import paper_radar.database as database_module
from paper_radar.database import SCHEMA_PATH, connect_database, initialize_database


def insert_journal(
    connection: sqlite3.Connection,
    *,
    journal_id: str = "journal-1",
    feed_url: str = "https://example.com/feed.xml",
) -> None:
    connection.execute(
        "INSERT INTO journals (id, name, publisher, feed_url) VALUES (?, ?, ?, ?)",
        (journal_id, "Example Journal", "nature", feed_url),
    )


def insert_article(
    connection: sqlite3.Connection,
    *,
    uid: str,
    journal_id: str | None = "journal-1",
    doi: str | None = None,
    normalized_url: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO articles (
            uid, doi, journal_id, title, article_url, normalized_url,
            source_feed_url, first_seen_at, last_updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uid,
            doi,
            journal_id,
            f"Article {uid}",
            f"https://example.com/articles/{uid}",
            normalized_url,
            "https://example.com/feed.xml",
            "2026-07-10T09:00:00Z",
            "2026-07-10T09:00:00Z",
        ),
    )


def test_schema_path_targets_packaged_sql_file() -> None:
    assert SCHEMA_PATH.name == "schema.sql"
    assert SCHEMA_PATH.parent.name == "paper_radar"
    assert SCHEMA_PATH.is_file()
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS article_url_aliases" in schema
    assert "PRAGMA user_version = 2" in schema


def test_connect_database_creates_parent_and_applies_connection_settings(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "paper-radar.sqlite3"

    connection = connect_database(database_path)
    try:
        row = connection.execute("SELECT 42 AS answer").fetchone()

        assert database_path.is_file()
        assert connection.row_factory is sqlite3.Row
        assert row is not None
        assert row["answer"] == 42
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        connection.close()


def test_initialize_database_creates_version_two_schema(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "paper-radar.sqlite3")
    connection.row_factory = sqlite3.Row
    try:
        initialize_database(connection)

        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]

        assert tables == {
            "journals",
            "articles",
            "article_url_aliases",
            "tags",
            "article_tags",
            "runs_log",
        }
        assert version == 2
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()


def test_initialize_database_is_idempotent_and_preserves_data(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "paper-radar.sqlite3")
    try:
        initialize_database(connection)
        insert_journal(connection)
        connection.commit()

        initialize_database(connection)

        row = connection.execute("SELECT id, name, publisher, feed_url FROM journals").fetchone()
        assert row is not None
        assert dict(row) == {
            "id": "journal-1",
            "name": "Example Journal",
            "publisher": "nature",
            "feed_url": "https://example.com/feed.xml",
        }
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_initialize_database_rejects_and_preserves_pending_transaction(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "paper-radar.sqlite3")
    try:
        initialize_database(connection)
        insert_journal(connection)
        assert connection.in_transaction is True

        with pytest.raises(RuntimeError, match="pending transaction"):
            initialize_database(connection)

        assert connection.in_transaction is True
        assert connection.execute("SELECT COUNT(*) FROM journals").fetchone()[0] == 1
        connection.rollback()
        assert connection.execute("SELECT COUNT(*) FROM journals").fetchone()[0] == 0
    finally:
        connection.close()


def test_initialize_database_rolls_back_partial_schema_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failing_schema = tmp_path / "failing-schema.sql"
    failing_schema.write_text(
        """
CREATE TABLE partial_table (id INTEGER PRIMARY KEY);
PRAGMA user_version = 1;
CREATE TABL invalid_syntax (id INTEGER PRIMARY KEY);
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(database_module, "SCHEMA_PATH", failing_schema)
    connection = connect_database(tmp_path / "paper-radar.sqlite3")
    try:
        with pytest.raises(sqlite3.OperationalError):
            initialize_database(connection)

        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == set()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_initialize_database_rejects_unsupported_version_without_altering_it(
    tmp_path: Path,
) -> None:
    connection = sqlite3.connect(tmp_path / "future.sqlite3")
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sentinel VALUES ('preserve me')")
        connection.execute("PRAGMA user_version = 3")
        connection.commit()

        with pytest.raises(RuntimeError, match="unsupported database schema version: 3"):
            initialize_database(connection)

        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == {"sentinel"}
        assert connection.execute("SELECT value FROM sentinel").fetchone()[0] == "preserve me"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_schema_defaults_and_indexes(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "paper-radar.sqlite3")
    try:
        initialize_database(connection)
        insert_journal(connection)
        insert_article(connection, uid="article-1")
        connection.execute(
            "INSERT INTO runs_log (started_at, status) VALUES (?, ?)",
            ("2026-07-10T09:00:00Z", "running"),
        )

        journal = connection.execute(
            "SELECT enabled, last_status FROM journals WHERE id = 'journal-1'"
        ).fetchone()
        article = connection.execute(
            """
            SELECT authors_json, article_type, oa_status, metadata_status
            FROM articles WHERE uid = 'article-1'
            """
        ).fetchone()
        run = connection.execute(
            """
            SELECT inserted_count, updated_count, skipped_count, failed_count, notes
            FROM runs_log
            """
        ).fetchone()

        assert tuple(journal) == (1, "never")
        assert tuple(article) == ("[]", "other", "unknown", "rss_only")
        assert tuple(run) == (0, 0, 0, 0, "")

        article_indexes = {
            row["name"]: row for row in connection.execute("PRAGMA index_list('articles')")
        }
        assert {
            "idx_articles_doi_unique",
            "idx_articles_normalized_url_unique",
            "idx_articles_published_at",
            "idx_articles_journal_id",
            "idx_articles_article_type",
            "idx_articles_oa_status",
        }.issubset(article_indexes)
        assert article_indexes["idx_articles_doi_unique"]["unique"] == 1
        assert article_indexes["idx_articles_doi_unique"]["partial"] == 1
        assert article_indexes["idx_articles_normalized_url_unique"]["unique"] == 1
        assert article_indexes["idx_articles_normalized_url_unique"]["partial"] == 1

        published_columns = [
            (row["name"], row["desc"])
            for row in connection.execute("PRAGMA index_xinfo('idx_articles_published_at')")
            if row["key"] == 1
        ]
        tag_columns = [
            row["name"]
            for row in connection.execute(
                "PRAGMA index_info('idx_article_tags_tag_id_article_uid')"
            )
        ]
        assert published_columns == [("published_at", 1)]
        assert tag_columns == ["tag_id", "article_uid"]

        alias_indexes = {
            row["name"]: row
            for row in connection.execute("PRAGMA index_list('article_url_aliases')")
        }
        assert "idx_article_url_aliases_article_uid" in alias_indexes
        alias_columns = [
            row["name"]
            for row in connection.execute(
                "PRAGMA index_info('idx_article_url_aliases_article_uid')"
            )
        ]
        assert alias_columns == ["article_uid"]
    finally:
        connection.close()


def test_schema_enforces_required_values_and_checks(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "paper-radar.sqlite3")
    try:
        initialize_database(connection)

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO journals (id, name, publisher, feed_url) VALUES (?, ?, ?, ?)",
                ("bad-publisher", "Bad", "unknown", "https://example.com/bad.xml"),
            )

        insert_journal(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO journals (id, name, publisher, feed_url) VALUES (?, ?, ?, ?)",
                ("duplicate-feed", "Duplicate", "aip", "https://example.com/feed.xml"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE journals SET enabled = 2 WHERE id = 'journal-1'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO articles (
                    uid, journal_id, title, article_url, source_feed_url,
                    first_seen_at, last_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "orphan",
                    "missing-journal",
                    "Orphan",
                    "https://example.com/orphan",
                    "https://example.com/feed.xml",
                    "2026-07-10T09:00:00Z",
                    "2026-07-10T09:00:00Z",
                ),
            )

        insert_article(connection, uid="article-1")
        for column, invalid_value in (
            ("article_type", "news"),
            ("oa_status", "embargoed"),
            ("metadata_status", "complete"),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    f"UPDATE articles SET {column} = ? WHERE uid = 'article-1'",
                    (invalid_value,),
                )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO articles (
                    uid, article_url, source_feed_url, first_seen_at, last_updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "missing-title",
                    "https://example.com/missing-title",
                    "https://example.com/feed.xml",
                    "2026-07-10T09:00:00Z",
                    "2026-07-10T09:00:00Z",
                ),
            )
    finally:
        connection.close()


def test_articles_require_a_journal_id(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "paper-radar.sqlite3")
    try:
        initialize_database(connection)
        insert_journal(connection)

        with pytest.raises(sqlite3.IntegrityError):
            insert_article(connection, uid="missing-journal-id", journal_id=None)
    finally:
        connection.close()


def test_text_primary_keys_reject_null_values(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "paper-radar.sqlite3")
    try:
        initialize_database(connection)

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO journals (id, name, publisher, feed_url) VALUES (?, ?, ?, ?)",
                (None, "Missing ID", "nature", "https://example.com/missing-id.xml"),
            )
        insert_journal(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO articles (
                    uid, journal_id, title, article_url, source_feed_url,
                    first_seen_at, last_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    None,
                    "journal-1",
                    "Missing ID",
                    "https://example.com/missing-id",
                    "https://example.com/feed.xml",
                    "2026-07-10T09:00:00Z",
                    "2026-07-10T09:00:00Z",
                ),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO tags (id, label) VALUES (?, ?)", (None, "Missing ID"))
    finally:
        connection.close()


def test_partial_unique_article_identifiers_allow_multiple_nulls(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "paper-radar.sqlite3")
    try:
        initialize_database(connection)
        insert_journal(connection)
        insert_article(connection, uid="null-1")
        insert_article(connection, uid="null-2")
        insert_article(
            connection,
            uid="identified",
            doi="10.1234/example",
            normalized_url="https://example.com/canonical",
        )

        with pytest.raises(sqlite3.IntegrityError):
            insert_article(
                connection,
                uid="duplicate-doi",
                doi="10.1234/example",
                normalized_url="https://example.com/other",
            )
        with pytest.raises(sqlite3.IntegrityError):
            insert_article(
                connection,
                uid="duplicate-url",
                doi="10.1234/other",
                normalized_url="https://example.com/canonical",
            )

        assert (
            connection.execute("SELECT COUNT(*) FROM articles WHERE doi IS NULL").fetchone()[0] == 2
        )
    finally:
        connection.close()


def test_article_tag_foreign_keys_and_cascades(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "paper-radar.sqlite3")
    try:
        initialize_database(connection)
        insert_journal(connection)
        insert_article(connection, uid="article-1")
        connection.executemany(
            "INSERT INTO tags (id, label) VALUES (?, ?)",
            (("tag-1", "Acoustics"), ("tag-2", "Materials")),
        )
        connection.executemany(
            "INSERT INTO article_tags (article_uid, tag_id) VALUES (?, ?)",
            (("article-1", "tag-1"), ("article-1", "tag-2")),
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO article_tags (article_uid, tag_id) VALUES (?, ?)",
                ("missing-article", "tag-1"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM journals WHERE id = 'journal-1'")

        connection.execute("DELETE FROM tags WHERE id = 'tag-1'")
        remaining_tag_ids = [
            row["tag_id"]
            for row in connection.execute("SELECT tag_id FROM article_tags ORDER BY tag_id")
        ]
        assert remaining_tag_ids == ["tag-2"]

        connection.execute("DELETE FROM articles WHERE uid = 'article-1'")
        assert connection.execute("SELECT COUNT(*) FROM article_tags").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 1
    finally:
        connection.close()


def test_initialize_database_migrates_v1_and_backfills_url_aliases(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "v1.sqlite3")
    try:
        initialize_database(connection)
        insert_journal(connection)
        insert_article(
            connection,
            uid="with-url",
            normalized_url="https://example.com/articles/canonical",
        )
        insert_article(connection, uid="without-url")
        connection.execute("DROP TABLE article_url_aliases")
        connection.execute("PRAGMA user_version = 1")
        connection.commit()

        initialize_database(connection)

        aliases = connection.execute(
            "SELECT normalized_url, article_uid FROM article_url_aliases"
        ).fetchall()
        assert [tuple(row) for row in aliases] == [
            ("https://example.com/articles/canonical", "with-url")
        ]
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_url_aliases_cascade_when_article_is_deleted(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "aliases.sqlite3")
    try:
        initialize_database(connection)
        insert_journal(connection)
        insert_article(
            connection,
            uid="article-1",
            normalized_url="https://example.com/articles/canonical",
        )
        connection.execute(
            "INSERT INTO article_url_aliases (normalized_url, article_uid) VALUES (?, ?)",
            ("https://example.com/articles/old", "article-1"),
        )

        connection.execute("DELETE FROM articles WHERE uid = ?", ("article-1",))

        assert connection.execute("SELECT COUNT(*) FROM article_url_aliases").fetchone()[0] == 0
    finally:
        connection.close()
