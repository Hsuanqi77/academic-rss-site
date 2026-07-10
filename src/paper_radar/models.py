from collections.abc import Iterable
from dataclasses import dataclass
from unicodedata import category


ENRICHABLE_FIELD_ORDER = (
    "title",
    "authors",
    "abstract",
    "published_at",
    "article_type",
    "oa_status",
)
ENRICHABLE_FIELDS = frozenset(ENRICHABLE_FIELD_ORDER)
METADATA_STATUS_RANK = {"rss_only": 0, "partial": 1, "enriched": 2}
_TITLE_PLACEHOLDERS = frozenset(
    {"na", "no title", "not available", "title unavailable", "unknown", "untitled"}
)
_AUTHOR_PLACEHOLDERS = frozenset(
    {
        "anonymous",
        "et al",
        "na",
        "not available",
        "unknown",
        "unknown author",
        "unknown authors",
    }
)
_ABSTRACT_PLACEHOLDERS = frozenset(
    {
        "abstract unavailable",
        "na",
        "no abstract",
        "no abstract available",
        "not available",
        "unknown",
        "unknown abstract",
    }
)


def normalize_enriched_fields(fields: Iterable[str]) -> tuple[str, ...]:
    if isinstance(fields, (str, bytes)):
        raise ValueError("enriched fields must be an iterable of field names")
    supplied: set[str] = set()
    for field in fields:
        if not isinstance(field, str) or field not in ENRICHABLE_FIELDS:
            raise ValueError(f"unsupported enriched field: {field!r}")
        supplied.add(field)
    return tuple(field for field in ENRICHABLE_FIELD_ORDER if field in supplied)


def enriched_field_has_meaningful_value(field: str, value: object) -> bool:
    if field not in ENRICHABLE_FIELDS:
        raise ValueError(f"unsupported enriched field: {field!r}")
    if field == "title":
        return _meaningful_text(value, _TITLE_PLACEHOLDERS)
    if field == "authors":
        if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
            return False
        return any(_meaningful_text(author, _AUTHOR_PLACEHOLDERS) for author in value)
    if field == "abstract":
        return _meaningful_text(value, _ABSTRACT_PLACEHOLDERS)
    if field == "published_at":
        return isinstance(value, str) and bool(value.strip())
    if field == "article_type":
        return isinstance(value, str) and bool(value.strip()) and value.casefold() != "other"
    return value in {"open", "closed"}


def _meaningful_text(value: object, placeholders: frozenset[str]) -> bool:
    if not isinstance(value, str):
        return False
    key = _placeholder_key(value)
    return bool(key) and key not in placeholders


def _placeholder_key(value: str) -> str:
    without_punctuation = "".join(
        character for character in value.casefold() if not category(character).startswith("P")
    )
    return " ".join(without_punctuation.split())


def higher_metadata_status(current: str, candidate: str) -> str:
    current_rank = METADATA_STATUS_RANK[current]
    candidate_rank = METADATA_STATUS_RANK[candidate]
    return candidate if candidate_rank > current_rank else current


@dataclass(frozen=True, slots=True)
class ArticleRecord:
    uid: str
    doi: str | None
    journal_id: str
    title: str
    abstract: str | None
    authors: tuple[str, ...]
    published_at: str | None
    article_type: str
    article_url: str
    normalized_url: str | None
    oa_status: str
    source_feed_url: str
    metadata_status: str
    enriched_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RawFeedItem:
    feed_id: str
    feed_url: str
    title: str
    link: str
    published: str | None
    doi: str | None
    authors: tuple[str, ...]
    summary: str | None
    raw_type: str | None


@dataclass(frozen=True, slots=True)
class FeedFetchResult:
    content: bytes | None
    etag: str | None
    last_modified: str | None
    not_modified: bool
    effective_url: str | None = None
