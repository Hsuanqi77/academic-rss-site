import hashlib
import ipaddress
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from unicodedata import normalize as normalize_unicode
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from paper_radar.config import FeedConfig
from paper_radar.identifiers import normalize_doi
from paper_radar.models import ArticleRecord, RawFeedItem


_WHITESPACE_PATTERN = re.compile(r"\s+")
_TRACKING_PARAMETERS = {"fbclid", "gclid", "spm"}
_HOST_LABEL_PATTERN = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$", re.IGNORECASE)
_PERCENT_ESCAPE_PATTERN = re.compile(r"%([0-9a-f]{2})", re.IGNORECASE)
_URL_UNRESERVED = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~")
_MIN_PUBLICATION_YEAR = 1900
_MAX_PUBLICATION_YEAR = 2100
_ARTICLE_TYPE_PATTERNS = (
    ("correction", re.compile(r"\b(?:correction|erratum|corrigendum)\b", re.IGNORECASE)),
    ("review", re.compile(r"\breview(?:\s+article)?\b", re.IGNORECASE)),
    (
        "editorial",
        re.compile(
            r"\b(?:editorial|comment(?:ary)?|perspective|letter\s+to\s+the\s+editor)\b",
            re.IGNORECASE,
        ),
    ),
    ("research", re.compile(r"\b(?:research|article|letter)\b", re.IGNORECASE)),
)
_HIDDEN_TAGS = {"script", "style", "template", "noscript"}
_SEPARATOR_TAGS = {
    "br",
    "dd",
    "div",
    "dt",
    "figcaption",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "p",
    "pre",
    "td",
    "th",
    "tr",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in _HIDDEN_TAGS:
            self.hidden_tags.append(tag)
        elif not self.hidden_tags and tag in _SEPARATOR_TAGS:
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if not self.hidden_tags and tag in _SEPARATOR_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _HIDDEN_TAGS:
            for index in range(len(self.hidden_tags) - 1, -1, -1):
                if self.hidden_tags[index] == tag:
                    del self.hidden_tags[index]
                    break
        elif not self.hidden_tags and tag in _SEPARATOR_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden_tags:
            self.parts.append(data)


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    source = str(value)
    for _ in range(2):
        source = unescape(source)
    parser = _TextExtractor()
    parser.feed(source)
    parser.close()
    cleaned = _WHITESPACE_PATTERN.sub(" ", "".join(parser.parts)).strip()
    return normalize_unicode("NFC", cleaned) or None


def normalize_url(value: Any) -> str | None:
    if value is None:
        return None
    candidate = str(value).strip()
    if not candidate or any(
        character.isspace() or ord(character) < 32 or 0x7F <= ord(character) <= 0x9F
        for character in candidate
    ):
        return None

    try:
        parsed = urlsplit(candidate)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password
    except (UnicodeError, ValueError):
        return None
    if (
        scheme not in {"http", "https"}
        or hostname is None
        or username is not None
        or password is not None
    ):
        return None

    canonical_host = _canonicalize_host(hostname)
    if canonical_host is None:
        return None
    if ":" in canonical_host:
        canonical_host = f"[{canonical_host}]"
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        canonical_host = f"{canonical_host}:{port}"

    path = _normalize_percent_escapes(parsed.path or "/")
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    query = _remove_tracking_parameters(_normalize_percent_escapes(parsed.query))
    return urlunsplit((scheme, canonical_host, path, query, ""))


def _canonicalize_host(hostname: str) -> str | None:
    try:
        return ipaddress.ip_address(hostname).compressed.lower()
    except ValueError:
        pass

    try:
        ascii_host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if not ascii_host or len(ascii_host) > 253:
        return None
    labels = ascii_host.rstrip(".").split(".")
    if not labels or not all(_HOST_LABEL_PATTERN.fullmatch(label) for label in labels):
        return None
    if all(character.isdigit() or character == "." for character in ascii_host):
        return None
    return ascii_host.rstrip(".")


def _normalize_percent_escapes(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        character = chr(int(match.group(1), 16))
        if character in _URL_UNRESERVED:
            return character
        return f"%{match.group(1).upper()}"

    return _PERCENT_ESCAPE_PATTERN.sub(replace, value)


def _remove_tracking_parameters(query: str) -> str:
    if not query:
        return ""
    kept: list[str] = []
    for parameter in query.split("&"):
        raw_name = parameter.partition("=")[0]
        name = unquote_plus(raw_name).casefold()
        if name.startswith("utm_") or name in _TRACKING_PARAMETERS:
            continue
        kept.append(parameter)
    return "&".join(kept)


def normalize_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        candidate = str(value).strip()
        if not candidate:
            return None
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(candidate)
            except (TypeError, ValueError, OverflowError):
                return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        normalized = parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        return None
    # RSS dates outside this deliberately broad publication window are almost
    # certainly parser errors and would destabilize fallback identities.
    if not _MIN_PUBLICATION_YEAR <= normalized.year <= _MAX_PUBLICATION_YEAR:
        return None
    return normalized.isoformat()


def normalize_article_type(value: Any) -> str:
    candidate = clean_text(value)
    if candidate is None:
        return "other"
    is_news_article = re.search(r"\bnews\s+article\b", candidate, re.IGNORECASE) is not None
    for article_type, pattern in _ARTICLE_TYPE_PATTERNS:
        if pattern.search(candidate):
            if article_type == "research" and is_news_article:
                return "other"
            return article_type
    return "other"


def make_uid(
    doi: Any,
    normalized_url: Any,
    journal_id: Any,
    title: Any,
    published: Any,
) -> str:
    """Return an import-candidate UID, not an unconditional persistence key.

    Persistence must reconcile incoming records by DOI and then normalized URL
    before insert. When enrichment changes a URL UID to a DOI UID, repositories
    must retain the existing UID or migrate it and dependent tags transactionally.
    """
    canonical_doi = normalize_doi(doi)
    if canonical_doi is not None:
        return f"doi:{canonical_doi}"

    canonical_url = normalize_url(normalized_url)
    if canonical_url is not None:
        return f"url:{_short_sha256(canonical_url)}"

    canonical_date = normalize_date(published)
    published_date = canonical_date[:10] if canonical_date is not None else ""
    identity = "\x1f".join(
        (
            clean_text(journal_id) or "",
            (clean_text(title) or "").casefold(),
            published_date,
        )
    )
    return f"hash:{_short_sha256(identity)}"


def _short_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def normalize_item(item: RawFeedItem, feed: FeedConfig) -> ArticleRecord:
    if item.feed_id != feed.id:
        raise ValueError(
            f"raw item feed id {item.feed_id!r} does not match supplied feed {feed.id!r}"
        )
    if item.feed_url.strip() != feed.feed_url.strip():
        raise ValueError(
            f"raw item feed URL {item.feed_url!r} does not match supplied feed URL "
            f"{feed.feed_url!r}"
        )

    article_url = item.link.strip() if isinstance(item.link, str) else ""
    normalized_url = normalize_url(article_url)
    if normalized_url is None:
        raise ValueError(f"raw item has an unusable article URL: {item.link!r}")

    title = clean_text(item.title) or "Untitled"
    abstract = clean_text(item.summary)
    authors = _normalize_authors(item.authors)
    doi = normalize_doi(item.doi)
    published_at = normalize_date(item.published)
    article_type = normalize_article_type(item.raw_type)
    return ArticleRecord(
        uid=make_uid(doi, normalized_url, feed.id, title, published_at),
        doi=doi,
        journal_id=feed.id,
        title=title,
        abstract=abstract,
        authors=authors,
        published_at=published_at,
        article_type=article_type,
        article_url=article_url,
        normalized_url=normalized_url,
        oa_status="unknown",
        source_feed_url=feed.feed_url,
        metadata_status="rss_only",
    )


def _normalize_authors(authors: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for author in authors:
        name = clean_text(author)
        if name is None:
            continue
        identity = name.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        normalized.append(name)
    return tuple(normalized)
