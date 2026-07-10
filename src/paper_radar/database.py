import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from itertools import count
from pathlib import Path
from time import sleep

from paper_radar.config import FeedConfig, TopicConfig
from paper_radar.models import ArticleRecord


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
)
_METADATA_STATUS_RANK = {"rss_only": 0, "partial": 1, "enriched": 2}
_WRITE_TRANSACTION_ATTEMPTS = 4
_WRITE_TRANSACTION_RETRY_DELAY_SECONDS = 0.02


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
    if not isinstance(status, str) or not status.strip():
        raise ValueError("journal status must be a nonblank string")

    timestamp = utc_now()
    is_error = status == "error"
    with _atomic(connection):
        if not _row_exists(connection, "journals", feed_id):
            raise RepositoryNotFoundError(f"journal not found: {feed_id}")
        connection.execute(
            """
            UPDATE journals
            SET etag = COALESCE(?, etag),
                last_modified = COALESCE(?, last_modified),
                last_checked_at = ?,
                last_success_at = CASE WHEN ? THEN last_success_at ELSE ? END,
                last_status = ?,
                last_error = ?
            WHERE id = ?
            """,
            (
                etag,
                last_modified,
                timestamp,
                is_error,
                timestamp,
                status,
                error,
                feed_id,
            ),
        )


def upsert_article(connection: sqlite3.Connection, article: ArticleRecord) -> str:
    """Persist an article while retaining the first matching persisted UID.

    Identity lookup is DOI first, normalized URL second, then candidate UID.  An
    existing UID remains the survivor so foreign-key relationships stay stable.
    """

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
                    first_seen_at, last_updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def replace_article_tags(
    connection: sqlite3.Connection, article_uid: str, topics: Iterable[TopicConfig]
) -> None:
    topics_by_id: dict[str, TopicConfig] = {}
    for current_topic in topics:
        previous = topics_by_id.get(current_topic.id)
        if previous is not None and previous.label != current_topic.label:
            raise RepositoryConflictError(f"conflicting labels supplied for tag {current_topic.id}")
        topics_by_id[current_topic.id] = current_topic

    with _atomic(connection):
        if not _row_exists(connection, "articles", article_uid):
            raise RepositoryNotFoundError(f"article not found: {article_uid}")

        connection.execute("DELETE FROM article_tags WHERE article_uid = ?", (article_uid,))
        for current_topic in topics_by_id.values():
            connection.execute(
                """
                INSERT INTO tags (id, label) VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET label = excluded.label
                """,
                (current_topic.id, current_topic.label),
            )
        connection.executemany(
            "INSERT INTO article_tags (article_uid, tag_id) VALUES (?, ?)",
            ((article_uid, topic_id) for topic_id in topics_by_id),
        )


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
    except BaseException:
        if owns_transaction:
            if connection.in_transaction:
                connection.rollback()
        elif connection.in_transaction:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def _begin_immediate(connection: sqlite3.Connection) -> None:
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(1, _WRITE_TRANSACTION_ATTEMPTS + 1):
        try:
            connection.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as error:
            if not _is_sqlite_busy(error):
                raise
            last_error = error
            if attempt < _WRITE_TRANSACTION_ATTEMPTS:
                sleep(_WRITE_TRANSACTION_RETRY_DELAY_SECONDS * attempt)

    raise RepositoryBusyError(
        "could not acquire immediate write transaction "
        f"after {_WRITE_TRANSACTION_ATTEMPTS} attempts"
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
    uid_row = connection.execute("SELECT * FROM articles WHERE uid = ?", (article.uid,)).fetchone()
    direct_url_row = (
        connection.execute(
            "SELECT * FROM articles WHERE normalized_url = ?", (article.normalized_url,)
        ).fetchone()
        if article.normalized_url is not None and alias_row is None
        else None
    )
    rows = (doi_row, alias_row, uid_row, direct_url_row)
    _validate_identity_rows(article, rows)
    return rows


def _validate_identity_rows(article: ArticleRecord, rows: tuple[sqlite3.Row | None, ...]) -> None:
    doi_row, alias_row, _, direct_url_row = rows
    url_row = alias_row if alias_row is not None else direct_url_row
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
        timestamp,
        timestamp,
    )


def _merged_article_values(
    survivor: sqlite3.Row, losers: Iterable[sqlite3.Row], article: ArticleRecord
) -> dict[str, object]:
    merged = dict(survivor)
    for loser in losers:
        current_rank = _METADATA_STATUS_RANK[str(merged["metadata_status"])]
        loser_rank = _METADATA_STATUS_RANK[str(loser["metadata_status"])]
        loser_is_higher_quality = loser_rank > current_rank
        if merged["doi"] is None and loser["doi"] is not None:
            merged["doi"] = loser["doi"]
        merged["abstract"] = _prefer_protected_text(
            merged["abstract"],
            loser["abstract"],
            prefer_candidate=loser_is_higher_quality,
        )
        if _json_has_authors(loser["authors_json"]) and (
            loser_is_higher_quality or not _json_has_authors(merged["authors_json"])
        ):
            merged["authors_json"] = loser["authors_json"]
        merged["published_at"] = _prefer_protected_text(
            merged["published_at"],
            loser["published_at"],
            prefer_candidate=loser_is_higher_quality,
        )
        if loser["article_type"] != "other" and (
            loser_is_higher_quality or merged["article_type"] == "other"
        ):
            merged["article_type"] = loser["article_type"]
        merged["oa_status"] = _prefer_known_oa_status(
            str(merged["oa_status"]),
            str(loser["oa_status"]),
            prefer_candidate=loser_is_higher_quality,
        )
        merged["metadata_status"] = _higher_metadata_status(
            str(merged["metadata_status"]), str(loser["metadata_status"])
        )
        merged["first_seen_at"] = min(merged["first_seen_at"], loser["first_seen_at"])

    incoming_is_not_lower_quality = (
        _METADATA_STATUS_RANK[article.metadata_status]
        >= _METADATA_STATUS_RANK[str(merged["metadata_status"])]
    )
    merged.update(
        {
            "doi": article.doi if article.doi is not None else merged["doi"],
            "journal_id": article.journal_id,
            "title": article.title,
            "abstract": _prefer_protected_text(
                merged["abstract"],
                article.abstract,
                prefer_candidate=incoming_is_not_lower_quality,
            ),
            "authors_json": (
                _authors_json(article.authors)
                if _has_authors(article.authors)
                and (incoming_is_not_lower_quality or not _json_has_authors(merged["authors_json"]))
                else merged["authors_json"]
            ),
            "published_at": _prefer_protected_text(
                merged["published_at"],
                article.published_at,
                prefer_candidate=incoming_is_not_lower_quality,
            ),
            "article_type": (
                article.article_type
                if article.article_type != "other"
                and (incoming_is_not_lower_quality or merged["article_type"] == "other")
                else merged["article_type"]
            ),
            "article_url": article.article_url,
            "normalized_url": (
                article.normalized_url
                if article.normalized_url is not None
                else merged["normalized_url"]
            ),
            "oa_status": _prefer_known_oa_status(
                str(merged["oa_status"]),
                article.oa_status,
                prefer_candidate=incoming_is_not_lower_quality,
            ),
            "source_feed_url": article.source_feed_url,
            "metadata_status": _higher_metadata_status(
                str(merged["metadata_status"]), article.metadata_status
            ),
        }
    )
    return merged


def _prefer_protected_text(current: object, candidate: object, *, prefer_candidate: bool) -> object:
    if _has_text(candidate) and (prefer_candidate or not _has_text(current)):
        return candidate
    return current


def _prefer_known_oa_status(current: str, candidate: str, *, prefer_candidate: bool) -> str:
    """Prefer stronger OA evidence; keep the stable survivor when split rows tie."""
    if candidate == "unknown":
        return current
    if current == "unknown" or prefer_candidate:
        return candidate
    return current


def _has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_authors(authors: Iterable[str]) -> bool:
    return any(author.strip() for author in authors)


def _authors_json(authors: Iterable[str]) -> str:
    return json.dumps(list(authors), ensure_ascii=False, separators=(",", ":"))


def _json_has_authors(value: str) -> bool:
    parsed = json.loads(value)
    return isinstance(parsed, list) and _has_authors(parsed)


def _higher_metadata_status(current: str, candidate: str) -> str:
    return (
        candidate if _METADATA_STATUS_RANK[candidate] > _METADATA_STATUS_RANK[current] else current
    )


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
        if version not in (0, 1, 2):
            raise RuntimeError(f"unsupported database schema version: {version}")

        script = SCHEMA_PATH.read_text(encoding="utf-8")
        for statement in _schema_statements(script):
            connection.execute(statement)
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
