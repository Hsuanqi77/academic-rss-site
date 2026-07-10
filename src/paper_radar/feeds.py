import re
from collections.abc import Mapping
from time import monotonic
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import feedparser
import httpx

from paper_radar.config import FeedConfig
from paper_radar.models import FeedFetchResult, RawFeedItem


USER_AGENT = "paper-radar/0.1 (+personal academic RSS reader)"
MAX_FEED_BYTES = 8 * 1024 * 1024
FETCH_DEADLINE_SECONDS = 30.0
_DOI_PREFIX_PATTERN = re.compile(r"10\.\d{4,9}/", re.IGNORECASE)
_DOI_LABEL_PATTERN = re.compile(r"^doi\s*:\s*", re.IGNORECASE)
_DOI_PROSE_PUNCTUATION = ".,!?\"'"
_DOI_DELIMITERS = {"(": ")", "<": ">", "[": "]", "{": "}"}
_DOI_RESOLVER_HOSTS = {"doi.org", "dx.doi.org"}
_SUPPORTED_FEED_VERSIONS = {"atom10", "rss10", "rss20"}


class FeedParseError(ValueError):
    pass


class FeedFetchError(RuntimeError):
    pass


def parse_feed(
    content: bytes,
    feed_id: str,
    feed_url: str,
    *,
    effective_url: str | None = None,
) -> list[RawFeedItem]:
    return parse_feed_bytes(
        content,
        feed_id,
        feed_url,
        effective_url=effective_url,
    )


def parse_feed_bytes(
    content: bytes,
    feed_id: str,
    feed_url: str,
    *,
    effective_url: str | None = None,
) -> list[RawFeedItem]:
    base_url = effective_url or feed_url
    try:
        parsed = feedparser.parse(
            content,
            response_headers={
                "content-location": base_url,
                "content-type": "application/xml",
            },
        )
    except Exception as exc:
        raise FeedParseError(f"could not parse feed {feed_id} ({feed_url}): {exc}") from exc

    items: list[RawFeedItem] = []
    for entry in parsed.entries:
        title = _text(entry.get("title"))
        link = _text(entry.get("link"))
        if title is None or link is None:
            continue
        link = urljoin(base_url, link)

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
    if getattr(parsed, "version", "") not in _SUPPORTED_FEED_VERSIONS:
        raise FeedParseError(
            f"could not parse feed {feed_id} ({feed_url}): not a recognized RSS or Atom feed"
        )

    return items


def fetch_feed(
    client: httpx.Client,
    feed: FeedConfig,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FeedFetchResult:
    """Fetch a bounded feed, following redirects but rejecting a final HTTP URL."""
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    started_at = monotonic()
    with client.stream(
        "GET",
        feed.feed_url,
        headers=headers,
        timeout=25.0,
        follow_redirects=True,
    ) as response:
        _require_https_effective_url(response.url)
        response_etag = response.headers.get("ETag")
        response_last_modified = response.headers.get("Last-Modified")

        if response.status_code == httpx.codes.NOT_MODIFIED:
            _enforce_fetch_deadline(started_at, response.url)
            return FeedFetchResult(
                content=None,
                etag=response_etag or etag,
                last_modified=response_last_modified or last_modified,
                not_modified=True,
                effective_url=str(response.url),
            )

        response.raise_for_status()
        _enforce_fetch_deadline(started_at, response.url)
        _reject_declared_oversize(response)

        content = bytearray()
        decoded_size = 0
        for chunk in response.iter_bytes():
            _enforce_fetch_deadline(started_at, response.url)
            decoded_size += len(chunk)
            if decoded_size > MAX_FEED_BYTES:
                raise FeedFetchError(
                    f"decoded feed size exceeds maximum of {MAX_FEED_BYTES} bytes: {response.url}"
                )
            content.extend(chunk)
        _enforce_fetch_deadline(started_at, response.url)

        return FeedFetchResult(
            content=bytes(content),
            etag=response_etag,
            last_modified=response_last_modified,
            not_modified=False,
            effective_url=str(response.url),
        )


def _reject_declared_oversize(response: httpx.Response) -> None:
    content_length = response.headers.get("Content-Length")
    if content_length is None:
        return
    try:
        declared_size = int(content_length)
    except ValueError:
        return
    if declared_size > MAX_FEED_BYTES:
        raise FeedFetchError(
            f"declared feed size {declared_size} exceeds maximum of {MAX_FEED_BYTES} bytes: "
            f"{response.url}"
        )


def _require_https_effective_url(url: httpx.URL) -> None:
    if url.scheme != "https":
        raise FeedFetchError(f"final feed URL must use HTTPS: {url}")


def _enforce_fetch_deadline(started_at: float, url: httpx.URL) -> None:
    if monotonic() - started_at > FETCH_DEADLINE_SECONDS:
        raise FeedFetchError(
            f"total fetch deadline of {FETCH_DEADLINE_SECONDS:g} seconds exceeded: {url}"
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
        (entry.get("prism_doi"), False),
        (entry.get("dc_identifier"), False),
        (entry.get("id"), False),
        (entry.get("guid"), False),
        (summary, True),
        (link, True),
    )
    for candidate, free_text in candidates:
        doi = _normalize_doi(candidate, free_text=free_text)
        if doi is not None:
            return doi
    return None


def _normalize_doi(value: Any, *, free_text: bool) -> str | None:
    candidate = _text(value)
    if candidate is None:
        return None

    parsed_url = urlsplit(candidate)
    is_resolver = (
        parsed_url.scheme.lower() in {"http", "https"}
        and parsed_url.hostname in _DOI_RESOLVER_HOSTS
    )
    if is_resolver:
        candidate = unquote(parsed_url.path.lstrip("/"))
    else:
        candidate = _DOI_LABEL_PATTERN.sub("", candidate, count=1)
        match = _DOI_PREFIX_PATTERN.search(candidate)
        if match is None:
            return None
        if free_text:
            candidate = candidate[match.start() :].split(maxsplit=1)[0]
        elif match.start() > 0:
            return None

    candidate = _strip_unmatched_prose_delimiters(
        candidate,
        free_text=free_text and not is_resolver,
    )
    if not _is_complete_doi(candidate):
        return None
    return candidate


def _strip_unmatched_prose_delimiters(candidate: str, *, free_text: bool) -> str:
    candidate = candidate.strip()
    if free_text:
        closing_to_opening = {closing: opening for opening, closing in _DOI_DELIMITERS.items()}
        while candidate:
            if candidate[-1] in _DOI_PROSE_PUNCTUATION:
                candidate = candidate[:-1]
                continue
            closing = candidate[-1]
            opening = closing_to_opening.get(closing)
            if opening is not None and candidate.count(closing) > candidate.count(opening):
                candidate = candidate[:-1]
                continue
            break
        return candidate

    unmatched_index = _first_unmatched_closing_index(candidate)
    if unmatched_index is not None:
        removable = set(_DOI_PROSE_PUNCTUATION) | set(_DOI_DELIMITERS.values())
        if set(candidate[unmatched_index:]) <= removable:
            candidate = candidate[:unmatched_index].rstrip(_DOI_PROSE_PUNCTUATION)
    return candidate


def _first_unmatched_closing_index(candidate: str) -> int | None:
    stack: list[str] = []
    closing_to_opening = {closing: opening for opening, closing in _DOI_DELIMITERS.items()}
    for index, character in enumerate(candidate):
        if character in _DOI_DELIMITERS:
            stack.append(character)
        elif character in closing_to_opening:
            if not stack or stack[-1] != closing_to_opening[character]:
                return index
            stack.pop()
    return None


def _is_complete_doi(candidate: str) -> bool:
    if _DOI_PREFIX_PATTERN.match(candidate) is None:
        return False
    prefix_end = candidate.find("/") + 1
    suffix = candidate[prefix_end:]
    if not suffix or any(
        character.isspace() or not character.isprintable() for character in suffix
    ):
        return False

    stack: list[str] = []
    closing_to_opening = {closing: opening for opening, closing in _DOI_DELIMITERS.items()}
    for character in suffix:
        if character in _DOI_DELIMITERS:
            stack.append(character)
        elif character in closing_to_opening:
            if not stack or stack.pop() != closing_to_opening[character]:
                return False
    return not stack
