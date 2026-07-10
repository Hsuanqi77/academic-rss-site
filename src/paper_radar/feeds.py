import re
from collections.abc import Mapping
from typing import Any

import feedparser
import httpx

from paper_radar.config import FeedConfig
from paper_radar.models import FeedFetchResult, RawFeedItem


USER_AGENT = "paper-radar/0.1 (+personal academic RSS reader)"
_DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_DOI_TRAILING_PUNCTUATION = ".,;:!?)]}>\"'"


class FeedParseError(ValueError):
    pass


def parse_feed(content: bytes, feed_id: str, feed_url: str) -> list[RawFeedItem]:
    try:
        parsed = feedparser.parse(content)
    except Exception as exc:
        raise FeedParseError(f"could not parse feed {feed_id} ({feed_url}): {exc}") from exc

    items: list[RawFeedItem] = []
    for entry in parsed.entries:
        title = _text(entry.get("title"))
        link = _text(entry.get("link"))
        if title is None or link is None:
            continue

        summary = _first_text(entry, "summary", "description")
        items.append(
            RawFeedItem(
                feed_id=feed_id,
                feed_url=feed_url,
                title=title,
                link=link,
                published=_first_text(entry, "published", "updated", "dc_date"),
                doi=_extract_doi(entry, summary, link),
                authors=_extract_authors(entry),
                summary=summary,
                raw_type=_first_text(entry, "prism_section", "type"),
            )
        )

    if parsed.bozo and not items:
        error = getattr(parsed, "bozo_exception", "unknown parser error")
        raise FeedParseError(f"could not parse feed {feed_id} ({feed_url}): {error}")

    return items


def fetch_feed(
    client: httpx.Client,
    feed: FeedConfig,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FeedFetchResult:
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    response = client.get(
        feed.feed_url,
        headers=headers,
        timeout=25.0,
        follow_redirects=True,
    )
    response_etag = response.headers.get("ETag")
    response_last_modified = response.headers.get("Last-Modified")

    if response.status_code == httpx.codes.NOT_MODIFIED:
        return FeedFetchResult(
            content=None,
            etag=response_etag or etag,
            last_modified=response_last_modified or last_modified,
            not_modified=True,
        )

    response.raise_for_status()
    return FeedFetchResult(
        content=response.content,
        etag=response_etag,
        last_modified=response_last_modified,
        not_modified=False,
    )


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _first_text(entry: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _text(entry.get(key))
        if value is not None:
            return value
    return None


def _extract_authors(entry: Mapping[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    authors = entry.get("authors")
    if isinstance(authors, (list, tuple)):
        for author in authors:
            if isinstance(author, Mapping):
                name = _text(author.get("name"))
            else:
                name = _text(author)
            if name is not None:
                names.append(name)

    if not names:
        author = _text(entry.get("author"))
        if author is not None:
            names.append(author)
    return tuple(names)


def _extract_doi(entry: Mapping[str, Any], summary: str | None, link: str) -> str | None:
    candidates = (
        entry.get("prism_doi"),
        entry.get("dc_identifier"),
        entry.get("id"),
        entry.get("guid"),
        summary,
        link,
    )
    for candidate in candidates:
        value = _text(candidate)
        if value is None:
            continue
        match = _DOI_PATTERN.search(value)
        if match is not None:
            doi = match.group(0).rstrip(_DOI_TRAILING_PUNCTUATION)
            if doi:
                return doi
    return None
