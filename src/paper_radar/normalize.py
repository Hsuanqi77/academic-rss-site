import hashlib
import ipaddress
import re
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, unquote_plus, urlsplit, urlunsplit

from paper_radar.config import FeedConfig
from paper_radar.models import ArticleRecord, RawFeedItem


_WHITESPACE_PATTERN = re.compile(r"\s+")
_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_DOI_LABEL_PATTERN = re.compile(r"^doi\s*:\s*", re.IGNORECASE)
_DOI_RESOLVER_HOSTS = {"doi.org", "dx.doi.org"}
_DOI_DELIMITERS = {"(": ")", "[": "]", "{": "}", "<": ">"}
_DOI_PROSE_PUNCTUATION = ".,!?\"'"
_TRACKING_PARAMETERS = {"fbclid", "gclid", "spm"}
_HOST_LABEL_PATTERN = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$", re.IGNORECASE)
_ARTICLE_TYPE_PATTERNS = (
    ("review", re.compile(r"\breview(?:\s+article)?\b", re.IGNORECASE)),
    ("correction", re.compile(r"\b(?:correction|erratum|corrigendum)\b", re.IGNORECASE)),
    (
        "editorial",
        re.compile(r"\b(?:editorial|comment(?:ary)?|perspective)\b", re.IGNORECASE),
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
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in _HIDDEN_TAGS:
            self.hidden_depth += 1
        elif self.hidden_depth == 0 and tag in _SEPARATOR_TAGS:
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if self.hidden_depth == 0 and tag in _SEPARATOR_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _HIDDEN_TAGS and self.hidden_depth:
            self.hidden_depth -= 1
        elif self.hidden_depth == 0 and tag in _SEPARATOR_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self.hidden_depth == 0:
            self.parts.append(data)


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    parser = _TextExtractor()
    parser.feed(unescape(str(value)))
    parser.close()
    cleaned = _WHITESPACE_PATTERN.sub(" ", "".join(parser.parts)).strip()
    return cleaned or None


def normalize_doi(value: Any) -> str | None:
    if value is None:
        return None
    candidate = unescape(str(value)).strip()
    if not candidate:
        return None

    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return None
    is_resolver = (
        parsed.scheme.lower() in {"http", "https"}
        and hostname is not None
        and hostname.lower() in _DOI_RESOLVER_HOSTS
    )
    if is_resolver:
        candidate = unquote(parsed.path.lstrip("/"))
    else:
        candidate = _DOI_LABEL_PATTERN.sub("", candidate, count=1)

    candidate = _strip_doi_prose_suffix(candidate.strip())
    if not _is_complete_doi(candidate):
        return None
    return candidate.lower()


def _strip_doi_prose_suffix(candidate: str) -> str:
    closing_to_opening = {closing: opening for opening, closing in _DOI_DELIMITERS.items()}
    while candidate:
        if candidate[-1] in _DOI_PROSE_PUNCTUATION:
            candidate = candidate[:-1]
            continue
        opening = closing_to_opening.get(candidate[-1])
        if opening is not None and candidate.count(candidate[-1]) > candidate.count(opening):
            candidate = candidate[:-1]
            continue
        break
    return candidate


def _is_complete_doi(candidate: str) -> bool:
    if _DOI_PATTERN.fullmatch(candidate) is None:
        return False
    suffix = candidate[candidate.find("/") + 1 :]
    if any(not character.isprintable() or character.isspace() for character in suffix):
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
    except (UnicodeError, ValueError):
        return None
    if scheme not in {"http", "https"} or hostname is None:
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

    path = parsed.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    query = _remove_tracking_parameters(parsed.query)
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
    return ascii_host


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
        return parsed.astimezone(timezone.utc).isoformat()
    except (OverflowError, ValueError):
        return None


def normalize_article_type(value: Any) -> str:
    candidate = clean_text(value)
    if candidate is None:
        return "other"
    for article_type, pattern in _ARTICLE_TYPE_PATTERNS:
        if pattern.search(candidate):
            return article_type
    return "other"


def make_uid(
    doi: Any,
    normalized_url: Any,
    journal_id: Any,
    title: Any,
    published: Any,
) -> str:
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
