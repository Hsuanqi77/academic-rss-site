from dataclasses import dataclass


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
