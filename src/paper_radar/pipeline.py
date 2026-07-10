import json
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import httpx

from paper_radar.classify import classify_article
from paper_radar.config import FeedConfig, TopicConfig
from paper_radar.database import (
    connect_database,
    create_run,
    finish_run,
    get_feed_state,
    initialize_database,
    mark_journal_status,
    register_journal,
    replace_article_tags,
    resolve_article_uid,
    upsert_article,
)
from paper_radar.enrich import enrich_article, validate_unpaywall_email
from paper_radar.feeds import FeedFetchError, fetch_feed, parse_feed_bytes
from paper_radar.http_client import PoliteClient, retry_operation
from paper_radar.models import ArticleRecord, FeedFetchResult, RunSummary
from paper_radar.normalize import normalize_item


_MAX_ITEM_ERRORS = 20
_MAX_NOTE_FEEDS = 100
_MAX_NOTE_IDENTIFIER_LENGTH = 128

FeedFetcher = Callable[..., FeedFetchResult]
ArticleEnricher = Callable[..., ArticleRecord]


def update_database(
    database_path: Path | str,
    feeds: Iterable[FeedConfig],
    topics: Iterable[TopicConfig],
    *,
    unpaywall_email: str | None = None,
    client: httpx.Client | None = None,
    fetcher: FeedFetcher = fetch_feed,
    enricher: ArticleEnricher = enrich_article,
    sleeper: Callable[[float], None] = time.sleep,
    min_interval: float = 0.5,
    clock: Callable[[], float] = time.monotonic,
) -> RunSummary:
    """Update a schema-v3 database while isolating operational failures by feed and item."""

    email = validate_unpaywall_email(unpaywall_email)
    connection = None
    owned_client: PoliteClient | None = None
    primary_error: BaseException | None = None
    run_id: int | None = None
    finish_attempted = False
    inserted = 0
    updated = 0
    skipped = 0
    failed = 0
    successful_feeds: list[str] = []
    failed_feeds: list[str] = []
    item_errors: list[dict[str, object]] = []
    omitted_item_errors = 0

    try:
        connection = connect_database(Path(database_path))
        initialize_database(connection)
        run_id = create_run(connection)
        topic_list = tuple(topics)
        enabled_feeds = tuple(feed for feed in feeds if feed.enabled)

        request_client = client
        if request_client is None:
            owned_client = PoliteClient(
                min_interval=min_interval,
                clock=clock,
                sleeper=sleeper,
                follow_redirects=False,
            )
            request_client = owned_client

        for feed in enabled_feeds:
            feed_item_failures = 0
            try:
                register_journal(connection, feed)
                etag, last_modified = get_feed_state(connection, feed.id)
                result = retry_operation(
                    lambda: fetcher(
                        request_client,
                        feed,
                        etag=etag,
                        last_modified=last_modified,
                    ),
                    sleeper=sleeper,
                )

                if result.not_modified:
                    mark_journal_status(
                        connection,
                        feed.id,
                        status="not_modified",
                        etag=result.etag if result.etag is not None else etag,
                        last_modified=(
                            result.last_modified
                            if result.last_modified is not None
                            else last_modified
                        ),
                    )
                    successful_feeds.append(feed.id)
                    continue

                if result.content is None:
                    raise FeedFetchError("successful feed response did not include content")
                items = parse_feed_bytes(
                    result.content,
                    feed.id,
                    feed.feed_url,
                    effective_url=result.effective_url,
                )
                for item_index, item in enumerate(items):
                    outcome: str | None = None
                    try:
                        article = normalize_item(item, feed)
                        article = enricher(
                            request_client,
                            article,
                            unpaywall_email=email,
                        )
                        matched_topics = classify_article(article, topic_list)
                        outcome = upsert_article(connection, article)
                        if outcome == "inserted":
                            inserted += 1
                        elif outcome == "updated":
                            updated += 1
                        elif outcome == "skipped":
                            skipped += 1
                        else:
                            raise RuntimeError(f"unsupported persistence outcome: {outcome!r}")
                        persisted_uid = resolve_article_uid(connection, article)
                        if persisted_uid is None:
                            raise RuntimeError("persisted article identity could not be resolved")
                        replace_article_tags(connection, persisted_uid, matched_topics)
                    except AssertionError:
                        raise
                    except Exception as exc:
                        failed += 1
                        feed_item_failures += 1
                        if len(item_errors) < _MAX_ITEM_ERRORS:
                            item_errors.append(
                                {
                                    "feed_id": _bounded_identifier(feed.id),
                                    "item_index": item_index,
                                    "error": _safe_diagnostic(exc),
                                }
                            )
                        else:
                            omitted_item_errors += 1

                if feed_item_failures:
                    mark_journal_status(
                        connection,
                        feed.id,
                        status="partial",
                        error=f"{feed_item_failures} item error(s)",
                        etag=result.etag,
                        last_modified=result.last_modified,
                    )
                else:
                    mark_journal_status(
                        connection,
                        feed.id,
                        status="ok",
                        etag=result.etag,
                        last_modified=result.last_modified,
                    )
                successful_feeds.append(feed.id)
            except AssertionError:
                raise
            except Exception as exc:
                failed += 1
                failed_feeds.append(feed.id)
                try:
                    mark_journal_status(
                        connection,
                        feed.id,
                        status="error",
                        error=_safe_diagnostic(exc),
                    )
                except Exception:
                    pass

        status, diagnostic = _terminal_status(
            enabled_count=len(enabled_feeds),
            successful_count=len(successful_feeds),
            failed_count=failed,
        )
        notes = _run_notes(
            successful_feeds,
            failed_feeds,
            item_errors,
            omitted_item_errors,
            diagnostic=diagnostic,
        )
        summary = RunSummary(
            status=status,
            inserted=inserted,
            updated=updated,
            skipped=skipped,
            failed=failed,
            successful_feeds=tuple(successful_feeds),
            failed_feeds=tuple(failed_feeds),
        )
        finish_attempted = True
        finish_run(
            connection,
            run_id,
            status=status,
            inserted=inserted,
            updated=updated,
            skipped=skipped,
            failed=failed,
            notes=notes,
        )
        return summary
    except BaseException as exc:
        primary_error = exc
        if connection is not None and run_id is not None and not finish_attempted:
            finish_attempted = True
            notes = _run_notes(
                successful_feeds,
                failed_feeds,
                item_errors,
                omitted_item_errors,
                diagnostic=_safe_diagnostic(exc),
            )
            try:
                finish_run(
                    connection,
                    run_id,
                    status="error",
                    inserted=inserted,
                    updated=updated,
                    skipped=skipped,
                    failed=failed,
                    notes=notes,
                )
            except BaseException as finish_error:
                if hasattr(exc, "add_note"):
                    exc.add_note(
                        "The update run could not be finalized after the original operation failed "
                        f"({_safe_diagnostic(finish_error)})."
                    )
        raise
    finally:
        cleanup_error: BaseException | None = None
        for resource in (owned_client, connection):
            if resource is None:
                continue
            try:
                resource.close()
            except BaseException as exc:
                if primary_error is not None and hasattr(primary_error, "add_note"):
                    primary_error.add_note(
                        f"Cleanup also failed ({_safe_diagnostic(exc)}); the original error "
                        "was preserved."
                    )
                elif cleanup_error is None:
                    cleanup_error = exc
                elif hasattr(cleanup_error, "add_note"):
                    cleanup_error.add_note(f"Additional cleanup failed ({_safe_diagnostic(exc)}).")
        if primary_error is None and cleanup_error is not None:
            raise cleanup_error


def _terminal_status(
    *, enabled_count: int, successful_count: int, failed_count: int
) -> tuple[str, str | None]:
    if enabled_count == 0:
        return "error", "no enabled feeds configured"
    if successful_count == 0:
        return "error", None
    if failed_count:
        return "partial", None
    return "ok", None


def _run_notes(
    successful_feeds: list[str],
    failed_feeds: list[str],
    item_errors: list[dict[str, object]],
    omitted_item_errors: int,
    *,
    diagnostic: str | None,
) -> str:
    payload: dict[str, Any] = {
        "successful_feeds": [
            _bounded_identifier(feed_id) for feed_id in successful_feeds[:_MAX_NOTE_FEEDS]
        ],
        "failed_feeds": [
            _bounded_identifier(feed_id) for feed_id in failed_feeds[:_MAX_NOTE_FEEDS]
        ],
        "item_errors": item_errors,
        "omitted_item_errors": omitted_item_errors,
    }
    omitted_successful = max(0, len(successful_feeds) - _MAX_NOTE_FEEDS)
    omitted_failed = max(0, len(failed_feeds) - _MAX_NOTE_FEEDS)
    if omitted_successful:
        payload["omitted_successful_feeds"] = omitted_successful
    if omitted_failed:
        payload["omitted_failed_feeds"] = omitted_failed
    if diagnostic is not None:
        payload["diagnostic"] = diagnostic
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _bounded_identifier(value: object) -> str:
    return str(value)[:_MAX_NOTE_IDENTIFIER_LENGTH]


def _safe_diagnostic(error: BaseException) -> str:
    return type(error).__name__[:_MAX_NOTE_IDENTIFIER_LENGTH]
