from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml


VALID_PUBLISHERS = {"nature", "aip", "ieee", "wiley"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FeedConfig:
    id: str
    name: str
    publisher: str
    feed_url: str
    enabled: bool = True
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TopicConfig:
    id: str
    label: str
    keywords: tuple[str, ...]


def load_feeds(path: Path) -> list[FeedConfig]:
    rows = _load_rows(path, "feeds")
    feeds: list[FeedConfig] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()

    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise ConfigError(f"feed row {index} must be a mapping")

        feed_id = _required_text(row, "id", f"feed row {index}")
        if feed_id in seen_ids:
            raise ConfigError(f"duplicate feed id: {feed_id}")

        name = _required_text(row, "name", f"feed {feed_id}")
        publisher = _required_text(row, "publisher", f"feed {feed_id}").lower()
        if publisher not in VALID_PUBLISHERS:
            raise ConfigError(f"unknown publisher for feed {feed_id}: {publisher}")

        feed_url = _required_text(row, "feed_url", f"feed {feed_id}")
        try:
            parsed_url = urlsplit(feed_url)
        except ValueError as exc:
            raise ConfigError(f"feed {feed_id} must use an HTTPS URL") from exc
        if parsed_url.scheme.lower() != "https" or not parsed_url.netloc:
            raise ConfigError(f"feed {feed_id} must use an HTTPS URL")
        if feed_url in seen_urls:
            raise ConfigError(f"duplicate feed URL: {feed_url}")

        enabled = row.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(f"feed {feed_id} enabled must be a boolean")
        aliases = _text_tuple(row.get("aliases", []), f"feed {feed_id} aliases")

        feeds.append(
            FeedConfig(
                id=feed_id,
                name=name,
                publisher=publisher,
                feed_url=feed_url,
                enabled=enabled,
                aliases=aliases,
            )
        )
        seen_ids.add(feed_id)
        seen_urls.add(feed_url)

    return feeds


def load_topics(path: Path) -> list[TopicConfig]:
    rows = _load_rows(path, "topics")
    topics: list[TopicConfig] = []
    seen_ids: set[str] = set()

    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise ConfigError(f"topic row {index} must be a mapping")

        topic_id = _required_text(row, "id", f"topic row {index}")
        if topic_id in seen_ids:
            raise ConfigError(f"duplicate topic id: {topic_id}")

        label = _required_text(row, "label", f"topic {topic_id}")
        raw_keywords = row.get("keywords")
        if not isinstance(raw_keywords, list):
            raise ConfigError(f"topic {topic_id} must define keywords")
        keywords = tuple(
            keyword.strip()
            for keyword in raw_keywords
            if isinstance(keyword, str) and keyword.strip()
        )
        if not keywords:
            raise ConfigError(f"topic {topic_id} must define keywords")
        if len(keywords) != len(raw_keywords):
            raise ConfigError(f"topic {topic_id} keywords must be nonblank strings")

        topics.append(TopicConfig(id=topic_id, label=label, keywords=keywords))
        seen_ids.add(topic_id)

    return topics


def _load_rows(path: Path, key: str) -> list[Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"could not read YAML configuration {path}: {exc}") from exc

    if not isinstance(document, Mapping):
        raise ConfigError("configuration root must be a mapping")

    rows = document.get(key)
    if not isinstance(rows, list) or not rows:
        raise ConfigError(f"{key} must be a non-empty list")
    return rows


def _required_text(row: Mapping[str, Any], field: str, context: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{context} must define {field}")
    return value.strip()


def _text_tuple(value: Any, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigError(f"{context} must be a list")
    normalized = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(normalized) != len(value):
        raise ConfigError(f"{context} must contain only nonblank strings")
    return normalized
