import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from itertools import count
from pathlib import Path
from time import monotonic, sleep

from paper_radar.config import FeedConfig, TopicConfig
from paper_radar.models import (
    ENRICHABLE_FIELD_ORDER,
    ArticleRecord,
    enriched_field_has_meaningful_value,
    higher_metadata_status,
    normalize_enriched_fields,
)


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_SAVEPOINT_IDS = count()
_ARTICLE_COLUMNS = (
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
    "enriched_fields_json",
)
_WRITE_TRANSACTION_BUDGET_SECONDS = 5.0
_WRITE_TRANSACTION_ATTEMPT_TIMEOUT_MS = 100
_WRITE_TRANSACTION_RETRY_DELAY_SECONDS = 0.02
_JOURNAL_STATUSES = frozenset({"ok", "not_modified", "partial", "error"})
_SUCCESSFUL_JOURNAL_STATUSES = frozenset({"ok", "not_modified"})


class RepositoryConflictError(ValueError):
    """Raised when two persisted identities cannot be reconciled safely."""


class RepositoryNotFoundError(LookupError):
    """Raised when a requested persisted object does not exist."""


class RepositoryBusyError(TimeoutError):
    """Raised when a repository write transaction cannot be acquired."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def register_journal(connection: sqlite3.Connection, feed: FeedConfig) -> None:
    with _atomic(connection):
        connection.execute(
            """
            INSERT INTO journals (id, name, publisher, feed_url, enabled)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                publisher = excluded.publisher,
                feed_url = excluded.feed_url,
                enabled = excluded.enabled
            """,
            (feed.id, feed.name, feed.publisher, feed.feed_url, int(feed.enabled)),
        )


def get_feed_state(connection: sqlite3.Connection, feed_id: str) -> tuple[str | None, str | None]:
    row = connection.execute(
        "SELECT etag, last_modified FROM journals WHERE id = ?", (feed_id,)
    ).fetchone()
    if row is None:
        return None, None
    return row["etag"], row["last_modified"]


def mark_journal_status(
    connection: sqlite3.Connection,
    feed_id: str,
    *,
    status: str,
    error: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
) -> None:
    if not isinstance(status, str) or status not in _JOURNAL_STATUSES or status != status.strip():
        allowed = ", ".join(sorted(_JOURNAL_STATUSES))
        raise ValueError(f"journal status must be exactly one of: {allowed}")
    is_success = status in _SUCCESSFUL_JOURNAL_STATUSES
    if not is_success and (not isinstance(error, str) or not error.strip()):
        raise ValueError(f"journal {status} status requires a nonblank diagnostic")

    timestamp = utc_now()
    with _atomic(connection):
        row = connection.execute(
            """
            SELECT etag, last_modified, last_success_at
            FROM journals WHERE id = ?
            """,
            (feed_id,),
        ).fetchone()
        if row is None:
            raise RepositoryNotFoundError(f"journal not found: {feed_id}")
        stored_etag = etag if status == "ok" or etag is not None else row["etag"]
        stored_last_modified = (
            last_modified if status == "ok" or last_modified is not None else row["last_modified"]
        )
        connection.execute(
            """
            UPDATE journals
            SET etag = ?,
                last_modified = ?,
                last_checked_at = ?,
                last_success_at = ?,
                last_status = ?,
                last_error = ?
            WHERE id = ?
            """,
            (
                stored_etag,
                stored_last_modified,
                timestamp,
                timestamp if is_success else row["last_success_at"],
                status,
                None if is_success else error,
                feed_id,
            ),
        )


def upsert_article(connection: sqlite3.Connection, article: ArticleRecord) -> str:
    """Persist an article while retaining the first matching persisted UID.

    Identity lookup is DOI first, normalized URL second, then candidate UID.  An
    existing UID remains the survivor so foreign-key relationships stay stable.
    """

    _validate_article_enriched_fields(article)
    with _atomic(connection):
        if not _row_exists(connection, "journals", article.journal_id):
            raise RepositoryNotFoundError(f"journal not found: {article.journal_id}")

        identity_rows = _article_identity_rows(connection, article)
        ordered_rows = _ordered_unique_rows(identity_rows)
        if not ordered_rows:
            timestamp = utc_now()
            values = _article_insert_values(article, timestamp)
            connection.execute(
                """
                INSERT INTO articles (
                    uid, doi, journal_id, title, abstract, authors_json,
                    published_at, article_type, article_url, normalized_url,
                    oa_status, source_feed_url, metadata_status,
                    enriched_fields_json, first_seen_at, last_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            _record_article_alias(connection, article.normalized_url, article.uid)
            return "inserted"

        survivor = ordered_rows[0]
        survivor_uid = survivor["uid"]
        for loser in ordered_rows[1:]:
            connection.execute(
                "UPDATE article_url_aliases SET article_uid = ? WHERE article_uid = ?",
                (survivor_uid, loser["uid"]),
            )
        for existing_row in ordered_rows:
            _record_article_alias(
                connection,
                existing_row["normalized_url"],
                survivor_uid,
            )
        _record_article_alias(connection, article.normalized_url, survivor_uid)

        desired = _merged_article_values(survivor, ordered_rows[1:], article)
        has_losers = len(ordered_rows) > 1
        changed = has_losers or any(
            survivor[column] != desired[column] for column in _ARTICLE_COLUMNS
        )
        if not changed and survivor["first_seen_at"] == desired["first_seen_at"]:
            return "skipped"

        timestamp = utc_now()
        for loser in ordered_rows[1:]:
            connection.execute(
                """
                INSERT OR IGNORE INTO article_tags (article_uid, tag_id)
                SELECT ?, tag_id FROM article_tags WHERE article_uid = ?
                """,
                (survivor_uid, loser["uid"]),
            )
            connection.execute("DELETE FROM articles WHERE uid = ?", (loser["uid"],))

        connection.execute(
            """
            UPDATE articles
            SET doi = ?, journal_id = ?, title = ?, abstract = ?, authors_json = ?,
                published_at = ?, article_type = ?, article_url = ?, normalized_url = ?,
                oa_status = ?, source_feed_url = ?, metadata_status = ?,
                enriched_fields_json = ?,
                first_seen_at = ?, last_updated_at = ?
            WHERE uid = ?
            """,
            tuple(desired[column] for column in _ARTICLE_COLUMNS)
            + (desired["first_seen_at"], timestamp, survivor_uid),
        )
        return "updated"


def resolve_article_uid(connection: sqlite3.Connection, article: ArticleRecord) -> str | None:
    rows = _article_identity_rows(connection, article)
    ordered_rows = _ordered_unique_rows(rows)
    return ordered_rows[0]["uid"] if ordered_rows else None


def get_article(connection: sqlite3.Connection, uid: str) -> ArticleRecord | None:
    """Return the canonical persisted article, rejecting corrupt serialized fields."""

    row = connection.execute("SELECT * FROM articles WHERE uid = ?", (uid,)).fetchone()
    if row is None:
        return None
    return _article_from_row(row)


def list_articles(connection: sqlite3.Connection) -> tuple[ArticleRecord, ...]:
    """Return every persisted article in stable UID order."""

    rows = connection.execute("SELECT * FROM articles ORDER BY uid").fetchall()
    return tuple(_article_from_row(row) for row in rows)


def _article_from_row(row: sqlite3.Row) -> ArticleRecord:
    """Convert a stored article row while rejecting corrupt serialized fields."""

    authors = _authors_from_json(row["authors_json"])
    if any(not isinstance(author, str) for author in authors):
        raise RepositoryConflictError("stored authors must contain only strings")
    enriched_fields = _stored_enriched_fields(row)
    return ArticleRecord(
        uid=row["uid"],
        doi=row["doi"],
        journal_id=row["journal_id"],
        title=row["title"],
        abstract=row["abstract"],
        authors=tuple(authors),
        published_at=row["published_at"],
        article_type=row["article_type"],
        article_url=row["article_url"],
        normalized_url=row["normalized_url"],
        oa_status=row["oa_status"],
        source_feed_url=row["source_feed_url"],
        metadata_status=row["metadata_status"],
        enriched_fields=enriched_fields,
    )


def replace_article_tags(
    connection: sqlite3.Connection, article_uid: str, topics: Iterable[TopicConfig]
) -> None:
    topics_by_id = _validated_topics(topics)

    with _atomic(connection):
        if not _row_exists(connection, "articles", article_uid):
            raise RepositoryNotFoundError(f"article not found: {article_uid}")

        connection.execute("DELETE FROM article_tags WHERE article_uid = ?", (article_uid,))
        for current_topic in topics_by_id.values():
            _upsert_or_migrate_tag(connection, current_topic)
        connection.executemany(
            "INSERT INTO article_tags (article_uid, tag_id) VALUES (?, ?)",
            ((article_uid, topic_id) for topic_id in topics_by_id),
        )
        connection.execute(
            """
            DELETE FROM tags
            WHERE NOT EXISTS (
                SELECT 1 FROM article_tags WHERE article_tags.tag_id = tags.id
            )
            """
        )


def replace_all_article_tags(
    connection: sqlite3.Connection,
    assignments: Mapping[str, Iterable[TopicConfig]],
) -> None:
    """Replace every article-tag relationship in one atomic operation.

    Assignment keys must exactly match the persisted article UIDs. This guard
    prevents a partial classifier result from accidentally clearing unrelated
    articles.
    """

    with _atomic(connection):
        topics_by_article: dict[str, dict[str, TopicConfig]] = {}
        all_topics: list[TopicConfig] = []
        for article_uid, article_topics in assignments.items():
            validated = _validated_topics(article_topics)
            topics_by_article[article_uid] = validated
            all_topics.extend(validated.values())
        topics_by_id = _validated_topics(all_topics)

        stored_uids = {
            row["uid"] for row in connection.execute("SELECT uid FROM articles")
        }
        supplied_uids = set(topics_by_article)
        unknown_uids = supplied_uids - stored_uids
        omitted_uids = stored_uids - supplied_uids
        if unknown_uids:
            raise RepositoryNotFoundError(
                "unknown article assignment uid(s): " + ", ".join(sorted(unknown_uids))
            )
        if omitted_uids:
            raise RepositoryConflictError(
                "missing or omitted article assignment uid(s): "
                + ", ".join(sorted(omitted_uids))
            )

        connection.execute("DELETE FROM article_tags")
        for current_topic in topics_by_id.values():
            _upsert_or_migrate_tag(connection, current_topic)
        connection.executemany(
            "INSERT INTO article_tags (article_uid, tag_id) VALUES (?, ?)",
            (
                (article_uid, topic_id)
                for article_uid, article_topics in topics_by_article.items()
                for topic_id in article_topics
            ),
        )
        connection.execute(
            """
            DELETE FROM tags
            WHERE NOT EXISTS (
                SELECT 1 FROM article_tags WHERE article_tags.tag_id = tags.id
            )
            """
        )


def _validated_topics(topics: Iterable[TopicConfig]) -> dict[str, TopicConfig]:
    topics_by_id: dict[str, TopicConfig] = {}
    topic_id_by_label: dict[str, str] = {}
    for current_topic in topics:
        previous = topics_by_id.get(current_topic.id)
        if previous is not None and previous.label != current_topic.label:
            raise RepositoryConflictError(f"conflicting labels supplied for tag {current_topic.id}")
        previous_id = topic_id_by_label.get(current_topic.label)
        if previous_id is not None and previous_id != current_topic.id:
            raise RepositoryConflictError(
                f"duplicate tag label {current_topic.label!r} supplied for "
                f"{previous_id} and {current_topic.id}"
            )
        topics_by_id[current_topic.id] = current_topic
        topic_id_by_label[current_topic.label] = current_topic.id
    return topics_by_id


def create_run(connection: sqlite3.Connection) -> int:
    with _atomic(connection):
        cursor = connection.execute(
            "INSERT INTO runs_log (started_at, status) VALUES (?, 'running')", (utc_now(),)
        )
        if cursor.lastrowid is None:
            raise RuntimeError("database did not return a run id")
        return cursor.lastrowid


def finish_run(
    connection: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    inserted: int,
    updated: int,
    skipped: int,
    failed: int,
    notes: str,
) -> None:
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        raise ValueError("run id must be a positive integer")
    if status not in {"ok", "partial", "error"}:
        raise ValueError("run terminal status must be one of: ok, partial, error")
    counts = {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
    }
    for name, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} count must be a nonnegative integer")
    if not isinstance(notes, str):
        raise ValueError("run notes must be a string")

    with _atomic(connection):
        row = connection.execute("SELECT status FROM runs_log WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise RepositoryNotFoundError(f"run not found: {run_id}")
        if row["status"] != "running":
            raise RepositoryConflictError(f"run {run_id} is already finished")
        connection.execute(
            """
            UPDATE runs_log
            SET finished_at = ?, status = ?, inserted_count = ?, updated_count = ?,
                skipped_count = ?, failed_count = ?, notes = ?
            WHERE id = ?
            """,
            (utc_now(), status, inserted, updated, skipped, failed, notes, run_id),
        )


@contextmanager
def _atomic(connection: sqlite3.Connection) -> Iterator[None]:
    """Run a write atomically without committing a caller-owned transaction.

    Owned writes begin IMMEDIATE to serialize identity reads and writes. Callers
    composing concurrent writes in an outer transaction should also start that
    outer transaction with BEGIN IMMEDIATE before invoking repository functions.
    """
    owns_transaction = not connection.in_transaction
    savepoint = f"paper_radar_repository_{next(_SAVEPOINT_IDS)}"
    if owns_transaction:
        _begin_immediate(connection)
    else:
        connection.execute(f"SAVEPOINT {savepoint}")

    try:
        yield
        if owns_transaction:
            connection.commit()
        else:
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
    except BaseException as original_error:
        try:
            if owns_transaction:
                if connection.in_transaction:
                    connection.rollback()
            elif connection.in_transaction:
                connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        except BaseException as cleanup_error:
            original_error.add_note(f"transaction cleanup failed: {cleanup_error}")
            raise original_error from cleanup_error
        raise


def _begin_immediate(connection: sqlite3.Connection) -> None:
    """Acquire a write transaction within one monotonic total time budget."""
    configured_timeout_ms = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
    total_budget_seconds = min(
        _WRITE_TRANSACTION_BUDGET_SECONDS,
        configured_timeout_ms / 1000,
    )
    deadline = monotonic() + total_budget_seconds
    last_error: sqlite3.OperationalError | None = None
    try:
        while True:
            remaining_seconds = max(0.0, deadline - monotonic())
            remaining_ms = max(0, int(remaining_seconds * 1000))
            attempt_timeout_ms = min(
                _WRITE_TRANSACTION_ATTEMPT_TIMEOUT_MS,
                configured_timeout_ms,
                remaining_ms,
            )
            connection.execute(f"PRAGMA busy_timeout = {attempt_timeout_ms}")
            try:
                connection.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as error:
                if not _is_sqlite_busy(error):
                    raise
                last_error = error
                remaining_seconds = deadline - monotonic()
                if remaining_seconds <= 0:
                    break
                sleep(min(_WRITE_TRANSACTION_RETRY_DELAY_SECONDS, remaining_seconds))
    finally:
        connection.execute(f"PRAGMA busy_timeout = {configured_timeout_ms}")

    raise RepositoryBusyError(
        f"could not acquire immediate write transaction within {total_budget_seconds:.3f} seconds"
    ) from last_error


def _is_sqlite_busy(error: sqlite3.OperationalError) -> bool:
    error_code = getattr(error, "sqlite_errorcode", None)
    return isinstance(error_code, int) and error_code & 0xFF == sqlite3.SQLITE_BUSY


def _row_exists(connection: sqlite3.Connection, table: str, row_id: str) -> bool:
    if table not in {"journals", "articles"}:
        raise ValueError(f"unsupported repository table: {table}")
    return (
        connection.execute(
            f"SELECT 1 FROM {table} WHERE id = ?"
            if table == "journals"
            else f"SELECT 1 FROM {table} WHERE uid = ?",
            (row_id,),
        ).fetchone()
        is not None
    )


def _article_identity_rows(
    connection: sqlite3.Connection, article: ArticleRecord
) -> tuple[
    sqlite3.Row | None,
    sqlite3.Row | None,
    sqlite3.Row | None,
]:
    doi_row = (
        connection.execute("SELECT * FROM articles WHERE doi = ?", (article.doi,)).fetchone()
        if article.doi is not None
        else None
    )
    alias_row = (
        connection.execute(
            """
            SELECT articles.*
            FROM article_url_aliases
            JOIN articles ON articles.uid = article_url_aliases.article_uid
            WHERE article_url_aliases.normalized_url = ?
            """,
            (article.normalized_url,),
        ).fetchone()
        if article.normalized_url is not None
        else None
    )
    direct_url_row = (
        connection.execute(
            "SELECT * FROM articles WHERE normalized_url = ?", (article.normalized_url,)
        ).fetchone()
        if article.normalized_url is not None and alias_row is None
        else None
    )
    url_row = alias_row if alias_row is not None else direct_url_row
    uid_row = connection.execute("SELECT * FROM articles WHERE uid = ?", (article.uid,)).fetchone()
    rows = (doi_row, url_row, uid_row)
    _validate_identity_rows(article, rows)
    return rows


def _validate_identity_rows(article: ArticleRecord, rows: tuple[sqlite3.Row | None, ...]) -> None:
    doi_row, url_row, _ = rows
    if doi_row is not None and doi_row["journal_id"] != article.journal_id:
        raise RepositoryConflictError(
            f"DOI {article.doi} already belongs to conflicting journal {doi_row['journal_id']}"
        )
    if (
        url_row is not None
        and article.doi is not None
        and url_row["doi"] is not None
        and url_row["doi"] != article.doi
    ):
        raise RepositoryConflictError(
            f"normalized URL {article.normalized_url} already belongs to DOI {url_row['doi']}"
        )

    unique_rows = _ordered_unique_rows(rows)
    journals = {row["journal_id"] for row in unique_rows}
    if any(journal != article.journal_id for journal in journals):
        raise RepositoryConflictError("article identity points to a conflicting journal")
    dois = {row["doi"] for row in unique_rows if row["doi"] is not None}
    if article.doi is not None:
        dois.add(article.doi)
    if len(dois) > 1:
        raise RepositoryConflictError("article identity has contradictory DOI values")


def _record_article_alias(
    connection: sqlite3.Connection, normalized_url: object, article_uid: str
) -> None:
    if normalized_url is None:
        return
    connection.execute(
        """
        INSERT INTO article_url_aliases (normalized_url, article_uid)
        VALUES (?, ?)
        ON CONFLICT(normalized_url) DO UPDATE SET article_uid = excluded.article_uid
        """,
        (normalized_url, article_uid),
    )


def _upsert_or_migrate_tag(connection: sqlite3.Connection, current_topic: TopicConfig) -> None:
    label_owner = connection.execute(
        "SELECT id FROM tags WHERE label = ?", (current_topic.label,)
    ).fetchone()
    migrated_article_uids: list[str] = []
    if label_owner is not None and label_owner["id"] != current_topic.id:
        migrated_article_uids = [
            row["article_uid"]
            for row in connection.execute(
                "SELECT article_uid FROM article_tags WHERE tag_id = ?",
                (label_owner["id"],),
            )
        ]
        connection.execute("DELETE FROM tags WHERE id = ?", (label_owner["id"],))

    connection.execute(
        """
        INSERT INTO tags (id, label) VALUES (?, ?)
        ON CONFLICT(id) DO UPDATE SET label = excluded.label
        """,
        (current_topic.id, current_topic.label),
    )
    connection.executemany(
        "INSERT OR IGNORE INTO article_tags (article_uid, tag_id) VALUES (?, ?)",
        ((article_uid, current_topic.id) for article_uid in migrated_article_uids),
    )


def _ordered_unique_rows(
    rows: Iterable[sqlite3.Row | None],
) -> list[sqlite3.Row]:
    result: list[sqlite3.Row] = []
    seen_uids: set[str] = set()
    for row in rows:
        if row is None or row["uid"] in seen_uids:
            continue
        result.append(row)
        seen_uids.add(row["uid"])
    return result


def _article_insert_values(article: ArticleRecord, timestamp: str) -> tuple[object, ...]:
    return (
        article.uid,
        article.doi,
        article.journal_id,
        article.title,
        article.abstract,
        _authors_json(article.authors),
        article.published_at,
        article.article_type,
        article.article_url,
        article.normalized_url,
        article.oa_status,
        article.source_feed_url,
        article.metadata_status,
        _enriched_fields_json(article.enriched_fields),
        timestamp,
        timestamp,
    )


def _merged_article_values(
    survivor: sqlite3.Row, losers: Iterable[sqlite3.Row], article: ArticleRecord
) -> dict[str, object]:
    merged = dict(survivor)
    enriched_fields = set(_stored_enriched_fields(merged))
    for loser in losers:
        loser_enriched_fields = set(_stored_enriched_fields(loser))
        if merged["doi"] is None and loser["doi"] is not None:
            merged["doi"] = loser["doi"]
        for field in ENRICHABLE_FIELD_ORDER:
            column = _enrichable_column(field)
            if _prefer_split_candidate(
                field,
                merged[column],
                loser[column],
                current_is_enriched=field in enriched_fields,
                candidate_is_enriched=field in loser_enriched_fields,
            ):
                merged[column] = loser[column]
        enriched_fields.update(loser_enriched_fields)
        merged["metadata_status"] = higher_metadata_status(
            str(merged["metadata_status"]), str(loser["metadata_status"])
        )
        merged["first_seen_at"] = min(merged["first_seen_at"], loser["first_seen_at"])

    incoming_enriched_fields = set(normalize_enriched_fields(article.enriched_fields))
    for field in ENRICHABLE_FIELD_ORDER:
        column = _enrichable_column(field)
        candidate = _incoming_enrichable_value(article, field)
        candidate_is_enriched = field in incoming_enriched_fields
        if _prefer_incoming_candidate(
            field,
            merged[column],
            candidate,
            current_is_enriched=field in enriched_fields,
            candidate_is_enriched=candidate_is_enriched,
        ):
            merged[column] = candidate
            if candidate_is_enriched:
                enriched_fields.add(field)
            else:
                enriched_fields.discard(field)

    merged.update(
        {
            "doi": article.doi if article.doi is not None else merged["doi"],
            "journal_id": article.journal_id,
            "article_url": article.article_url,
            "normalized_url": (
                article.normalized_url
                if article.normalized_url is not None
                else merged["normalized_url"]
            ),
            "source_feed_url": article.source_feed_url,
            "metadata_status": higher_metadata_status(
                str(merged["metadata_status"]), article.metadata_status
            ),
            "enriched_fields_json": _enriched_fields_json(enriched_fields),
        }
    )
    return merged


def _prefer_split_candidate(
    field: str,
    current: object,
    candidate: object,
    *,
    current_is_enriched: bool,
    candidate_is_enriched: bool,
) -> bool:
    if not _enrichable_value_is_known(field, candidate):
        return False
    if not _enrichable_value_is_known(field, current):
        return True
    return candidate_is_enriched and not current_is_enriched


def _prefer_incoming_candidate(
    field: str,
    current: object,
    candidate: object,
    *,
    current_is_enriched: bool,
    candidate_is_enriched: bool,
) -> bool:
    if not _enrichable_value_is_known(field, candidate):
        return False
    if not _enrichable_value_is_known(field, current):
        return True
    return candidate_is_enriched or not current_is_enriched


def _enrichable_column(field: str) -> str:
    return "authors_json" if field == "authors" else field


def _incoming_enrichable_value(article: ArticleRecord, field: str) -> object:
    return _authors_json(article.authors) if field == "authors" else getattr(article, field)


def _enrichable_value_is_known(field: str, value: object) -> bool:
    if field == "authors":
        return enriched_field_has_meaningful_value(field, _authors_from_json(value))
    return enriched_field_has_meaningful_value(field, value)


def _authors_json(authors: Iterable[str]) -> str:
    return json.dumps(list(authors), ensure_ascii=False, separators=(",", ":"))


def _enriched_fields_json(fields: Iterable[str]) -> str:
    normalized = normalize_enriched_fields(fields)
    return json.dumps(list(normalized), ensure_ascii=False, separators=(",", ":"))


def _json_enriched_fields(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise RepositoryConflictError("stored enriched fields must be JSON text")
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise RepositoryConflictError("stored enriched fields contain invalid JSON") from exc
    if not isinstance(parsed, list):
        raise RepositoryConflictError("stored enriched fields must be a JSON list")
    try:
        return normalize_enriched_fields(parsed)
    except ValueError as exc:
        raise RepositoryConflictError(
            "stored enriched fields contain an unsupported field"
        ) from exc


def _validate_article_enriched_fields(article: ArticleRecord) -> tuple[str, ...]:
    enriched_fields = normalize_enriched_fields(article.enriched_fields)
    for field in enriched_fields:
        if not enriched_field_has_meaningful_value(field, getattr(article, field)):
            raise ValueError(f"enriched field {field!r} must have a corresponding meaningful value")
    return enriched_fields


def _stored_enriched_fields(row: sqlite3.Row | dict[str, object]) -> tuple[str, ...]:
    enriched_fields = _json_enriched_fields(row["enriched_fields_json"])
    for field in enriched_fields:
        value = (
            _authors_from_json(row["authors_json"])
            if field == "authors"
            else row[_enrichable_column(field)]
        )
        if not enriched_field_has_meaningful_value(field, value):
            raise RepositoryConflictError(
                f"stored enriched field {field!r} must have a corresponding meaningful value"
            )
    return enriched_fields


def _authors_from_json(value: object) -> list[object]:
    if not isinstance(value, str):
        raise RepositoryConflictError("stored authors must be JSON text")
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise RepositoryConflictError("stored authors contain invalid JSON") from exc
    if not isinstance(parsed, list):
        raise RepositoryConflictError("stored authors must be a JSON list")
    return parsed


def _schema_statements(script: str) -> Iterator[str]:
    start = 0
    for position, character in enumerate(script):
        if character != ";":
            continue

        candidate = script[start : position + 1]
        if sqlite3.complete_statement(candidate):
            statement = candidate.strip()
            if statement:
                yield statement
            start = position + 1

    if script[start:].strip():
        raise RuntimeError("schema SQL contains an incomplete statement")


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise RuntimeError("cannot initialize database with a pending transaction")

    connection.execute("PRAGMA foreign_keys = ON")
    try:
        _begin_immediate(connection)
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3):
            raise RuntimeError(f"unsupported database schema version: {version}")

        script = SCHEMA_PATH.read_text(encoding="utf-8")
        for statement in _schema_statements(script):
            connection.execute(statement)
        _ensure_enriched_fields_column(connection, source_version=version)
        connection.execute("PRAGMA user_version = 3")
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def _ensure_enriched_fields_column(connection: sqlite3.Connection, *, source_version: int) -> None:
    article_columns = {row["name"] for row in connection.execute("PRAGMA table_info('articles')")}
    if "enriched_fields_json" not in article_columns:
        connection.execute(
            "ALTER TABLE articles ADD COLUMN enriched_fields_json TEXT NOT NULL DEFAULT '[]'"
        )
        if source_version in (1, 2):
            _backfill_legacy_enriched_fields(connection)


def _backfill_legacy_enriched_fields(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT uid, title, abstract, authors_json, published_at, article_type, oa_status
        FROM articles
        WHERE metadata_status IN ('partial', 'enriched')
        """
    ).fetchall()
    for row in rows:
        enriched_fields: list[str] = []
        for field in ENRICHABLE_FIELD_ORDER:
            value = (
                _authors_from_json(row["authors_json"])
                if field == "authors"
                else row[_enrichable_column(field)]
            )
            if enriched_field_has_meaningful_value(field, value):
                enriched_fields.append(field)
        connection.execute(
            "UPDATE articles SET enriched_fields_json = ? WHERE uid = ?",
            (_enriched_fields_json(enriched_fields), row["uid"]),
        )
