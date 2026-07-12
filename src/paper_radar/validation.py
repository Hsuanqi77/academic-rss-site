from __future__ import annotations

import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path


EXPECTED_SCHEMA_VERSION = 4
MINIMUM_PREVIOUS_FRACTION = 0.5
_PUBLISHABLE_RUN_STATUSES = frozenset({"ok", "partial"})
_REQUIRED_TABLES = frozenset(
    {
        "journals",
        "articles",
        "article_url_aliases",
        "tags",
        "article_tags",
        "runs_log",
    }
)
_REQUIRED_COLUMNS = {
    "journals": frozenset(
        {
            "id",
            "name",
            "publisher",
            "feed_url",
            "enabled",
            "etag",
            "last_modified",
            "last_checked_at",
            "last_success_at",
            "last_status",
            "last_error",
        }
    ),
    "articles": frozenset(
        {
            "uid",
            "doi",
            "journal_id",
            "title",
            "abstract",
            "authors_json",
            "published_at",
            "article_type",
            "article_url",
            "normalized_url",
            "oa_status",
            "source_feed_url",
            "metadata_status",
            "first_seen_at",
            "last_updated_at",
            "enriched_fields_json",
        }
    ),
    "article_url_aliases": frozenset({"normalized_url", "article_uid"}),
    "tags": frozenset({"id", "label"}),
    "article_tags": frozenset({"article_uid", "tag_id"}),
    "runs_log": frozenset(
        {
            "id",
            "started_at",
            "finished_at",
            "status",
            "inserted_count",
            "updated_count",
            "skipped_count",
            "failed_count",
            "notes",
        }
    ),
}


class ValidationError(RuntimeError):
    """Raised when a database is unsafe to publish."""


@dataclass(frozen=True, slots=True)
class ValidationReport:
    article_count: int
    journal_count: int
    earliest_date: str | None
    latest_date: str | None
    schema_version: int


def _open_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def validate_database(
    path: Path | str, *, previous_path: Path | str | None = None
) -> ValidationReport:
    """Validate a schema-v4 publication candidate without modifying it."""

    database_path = Path(path)
    report = _validate_single_database(database_path)

    if previous_path is not None:
        previous_database = Path(previous_path)
        try:
            previous_exists = previous_database.exists()
        except OSError as exc:
            raise ValidationError(
                f"could not inspect previous published database {previous_database}: {exc}"
            ) from exc
        if previous_exists:
            try:
                previous_report = _validate_single_database(previous_database)
            except ValidationError as exc:
                raise ValidationError(f"previous published database is invalid: {exc}") from exc
            if (
                previous_report.article_count
                and report.article_count < previous_report.article_count * MINIMUM_PREVIOUS_FRACTION
            ):
                raise ValidationError(
                    "article count dropped from "
                    f"{previous_report.article_count} to {report.article_count}"
                )

    return report


def _validate_single_database(path: Path) -> ValidationReport:
    try:
        if not path.exists():
            raise ValidationError(f"database does not exist: {path}")
        if not path.is_file():
            raise ValidationError(f"database is not a file: {path}")
    except OSError as exc:
        raise ValidationError(f"could not inspect database {path}: {exc}") from exc

    connection: sqlite3.Connection | None = None
    try:
        connection = _open_readonly(path)
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        integrity_messages = [str(row[0]) for row in integrity_rows]
        if integrity_messages != ["ok"]:
            details = "; ".join(integrity_messages) or "no result"
            raise ValidationError(f"integrity check failed: {details}")

        foreign_key_row = connection.execute("PRAGMA foreign_key_check").fetchone()
        if foreign_key_row is not None:
            raise ValidationError("foreign key check failed")

        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if schema_version != EXPECTED_SCHEMA_VERSION:
            raise ValidationError(
                f"schema version is {schema_version}; expected {EXPECTED_SCHEMA_VERSION}"
            )

        table_names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing_tables = _REQUIRED_TABLES - table_names
        if missing_tables:
            raise ValidationError(
                "database is missing required tables: " + ", ".join(sorted(missing_tables))
            )
        for table_name, required_columns in _REQUIRED_COLUMNS.items():
            actual_columns = {
                str(row[1])
                for row in connection.execute(
                    f"PRAGMA table_info({_quote_identifier(table_name)})"
                ).fetchall()
            }
            missing_columns = required_columns - actual_columns
            if missing_columns:
                raise ValidationError(
                    f"table {table_name} is missing required columns: "
                    + ", ".join(sorted(missing_columns))
                )

        article_count = int(connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
        if article_count == 0:
            raise ValidationError("database has no articles")

        journal_count = int(connection.execute("SELECT COUNT(*) FROM journals").fetchone()[0])
        latest_run = connection.execute(
            "SELECT status, finished_at FROM runs_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if latest_run is None:
            raise ValidationError("latest run is missing")
        if (
            latest_run[0] not in _PUBLISHABLE_RUN_STATUSES
            or not isinstance(latest_run[1], str)
            or not latest_run[1].strip()
        ):
            raise ValidationError("latest run did not complete successfully")

        earliest_date, latest_date = connection.execute(
            "SELECT MIN(published_at), MAX(published_at) FROM articles"
        ).fetchone()
        return ValidationReport(
            article_count=article_count,
            journal_count=journal_count,
            earliest_date=earliest_date,
            latest_date=latest_date,
            schema_version=schema_version,
        )
    except ValidationError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        raise ValidationError(f"invalid SQLite database {path}: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


def publish_database(working_path: Path | str, published_path: Path | str) -> ValidationReport:
    """Validate and atomically replace the published database with a snapshot."""

    working = Path(working_path)
    published = Path(published_path)
    if working.resolve() == published.resolve():
        raise ValidationError("working and published database paths must differ")

    validate_database(working, previous_path=published)
    try:
        published.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{published.name}.", suffix=".tmp", dir=published.parent
        )
        os.close(descriptor)
    except OSError as exc:
        raise ValidationError(f"could not create publication temporary file: {exc}") from exc

    temporary = Path(temporary_name)
    stage = "backup"
    try:
        source = _open_readonly(working)
        try:
            destination = sqlite3.connect(temporary)
            try:
                _backup_database(source, destination)
            finally:
                destination.close()
        finally:
            source.close()

        snapshot_report = validate_database(temporary, previous_path=published)
        stage = "replace"
        os.replace(temporary, published)
        return snapshot_report
    except ValidationError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise ValidationError(f"database {stage} failed: {exc}") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            # A cleanup error must never turn a successful atomic replacement into a failure.
            pass


def _backup_database(source: sqlite3.Connection, destination: sqlite3.Connection) -> None:
    source.backup(destination)


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
