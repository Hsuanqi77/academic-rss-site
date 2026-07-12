import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from paper_radar.matching import normalize_match_separators


VALID_PUBLISHERS = {"nature", "aps", "aip", "ieee", "wiley", "elsevier", "aaas", "springer"}
_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$", re.IGNORECASE)
_FEED_FIELDS = frozenset({"id", "name", "publisher", "feed_url", "enabled", "aliases"})
_TOPIC_CATALOG_FIELDS = frozenset({"topic_groups", "topics"})
_TOPIC_GROUP_FIELDS = frozenset({"id", "label", "order"})
_TOPIC_FIELDS = frozenset({"id", "label", "keywords", "group", "requires_any_group"})


class ConfigError(ValueError):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable YAML key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate YAML key: {key}",
                    key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


@dataclass(frozen=True, slots=True)
class FeedConfig:
    id: str
    name: str
    publisher: str
    feed_url: str
    enabled: bool = True
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TopicGroupConfig:
    id: str
    label: str
    order: int


@dataclass(frozen=True, slots=True)
class TopicConfig:
    id: str
    label: str
    keywords: tuple[str, ...]
    group: str
    requires_any_group: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TopicCatalog:
    groups: tuple[TopicGroupConfig, ...]
    topics: tuple[TopicConfig, ...]


def load_feeds(path: Path) -> list[FeedConfig]:
    rows = _load_rows(path, "feeds")
    feeds: list[FeedConfig] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()

    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise ConfigError(f"feed row {index} must be a mapping")

        feed_id = _required_text(row, "id", f"feed row {index}")
        _reject_unknown_fields(row, _FEED_FIELDS, f"feed {feed_id}")
        if feed_id in seen_ids:
            raise ConfigError(f"duplicate feed id: {feed_id}")

        name = _required_text(row, "name", f"feed {feed_id}")
        publisher = _required_text(row, "publisher", f"feed {feed_id}").lower()
        if publisher not in VALID_PUBLISHERS:
            raise ConfigError(f"unknown publisher for feed {feed_id}: {publisher}")

        feed_url = _required_text(row, "feed_url", f"feed {feed_id}")
        try:
            parsed_url = httpx.URL(feed_url)
        except httpx.InvalidURL as exc:
            raise ConfigError(f"feed {feed_id} has invalid feed_url: {exc}") from exc
        if parsed_url.scheme != "https":
            raise ConfigError(f"feed {feed_id} must use an HTTPS URL")
        if not _is_valid_host(parsed_url.host):
            raise ConfigError(f"feed {feed_id} has invalid feed_url: URL must include a valid host")
        canonical_url = str(parsed_url)
        if canonical_url in seen_urls:
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
        seen_urls.add(canonical_url)

    return feeds


def load_topics(path: Path) -> list[TopicConfig]:
    return list(load_topic_catalog(path).topics)


def load_topic_catalog(path: Path) -> TopicCatalog:
    document = _load_document(path)
    _reject_unknown_fields(document, _TOPIC_CATALOG_FIELDS, "topic catalog")
    group_rows = _document_rows(document, "topic_groups")
    topic_rows = _document_rows(document, "topics")

    groups: list[TopicGroupConfig] = []
    seen_group_ids: set[str] = set()
    seen_group_labels: set[str] = set()
    seen_orders: set[int] = set()
    for index, row in enumerate(group_rows, start=1):
        if not isinstance(row, Mapping):
            raise ConfigError(f"topic group row {index} must be a mapping")

        group_id = _required_text(row, "id", f"topic group row {index}")
        _reject_unknown_fields(row, _TOPIC_GROUP_FIELDS, f"topic group {group_id}")
        if group_id in seen_group_ids:
            raise ConfigError(f"duplicate topic group id: {group_id}")

        label = _required_text(row, "label", f"topic group {group_id}")
        if label in seen_group_labels:
            raise ConfigError(f"duplicate topic group label: {label}")
        order = row.get("order")
        if isinstance(order, bool) or not isinstance(order, int) or order <= 0:
            raise ConfigError(f"topic group {group_id} order must be a positive integer")
        if order in seen_orders:
            raise ConfigError(f"duplicate topic group order: {order}")

        groups.append(TopicGroupConfig(id=group_id, label=label, order=order))
        seen_group_ids.add(group_id)
        seen_group_labels.add(label)
        seen_orders.add(order)

    expected_orders = list(range(1, len(groups) + 1))
    if sorted(seen_orders) != expected_orders:
        raise ConfigError(f"topic group order must be continuous from 1 to {len(groups)}")

    topics: list[TopicConfig] = []
    seen_topic_ids: set[str] = set()
    seen_topic_labels: set[str] = set()
    populated_groups: set[str] = set()

    for index, row in enumerate(topic_rows, start=1):
        if not isinstance(row, Mapping):
            raise ConfigError(f"topic row {index} must be a mapping")

        topic_id = _required_text(row, "id", f"topic row {index}")
        _reject_unknown_fields(row, _TOPIC_FIELDS, f"topic {topic_id}")
        if topic_id in seen_topic_ids:
            raise ConfigError(f"duplicate topic id: {topic_id}")

        label = _required_text(row, "label", f"topic {topic_id}")
        if label in seen_topic_labels:
            raise ConfigError(f"duplicate topic label: {label}")
        group = _required_text(row, "group", f"topic {topic_id}")
        if group not in seen_group_ids:
            raise ConfigError(f"topic {topic_id} references unknown group: {group}")
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

        normalized_keywords: set[str] = set()
        for keyword in keywords:
            normalized_keyword = normalize_match_separators(keyword)
            if normalized_keyword in normalized_keywords:
                raise ConfigError(
                    f"topic {topic_id} has duplicate normalized keyword: {normalized_keyword}"
                )
            normalized_keywords.add(normalized_keyword)

        requires_any_group: tuple[str, ...] = ()
        if "requires_any_group" in row:
            requires_any_group = _text_tuple(
                row["requires_any_group"], f"topic {topic_id} requires_any_group"
            )
            if not requires_any_group:
                raise ConfigError(
                    f"topic {topic_id} requires_any_group must be a non-empty list when provided"
                )
            if len(set(requires_any_group)) != len(requires_any_group):
                raise ConfigError(f"topic {topic_id} requires_any_group contains duplicates")
            unknown_groups = set(requires_any_group) - seen_group_ids
            if unknown_groups:
                unknown_group = next(
                    candidate for candidate in requires_any_group if candidate in unknown_groups
                )
                raise ConfigError(
                    f"topic {topic_id} requires_any_group references unknown group: {unknown_group}"
                )
            if group in requires_any_group:
                raise ConfigError(
                    f"topic {topic_id} requires_any_group cannot contain its own group"
                )

        topics.append(
            TopicConfig(
                id=topic_id,
                label=label,
                keywords=keywords,
                group=group,
                requires_any_group=requires_any_group,
            )
        )
        seen_topic_ids.add(topic_id)
        seen_topic_labels.add(label)
        populated_groups.add(group)

    empty_groups = seen_group_ids - populated_groups
    if empty_groups:
        empty_group = next(group.id for group in groups if group.id in empty_groups)
        raise ConfigError(f"topic group has no topics: {empty_group}")

    ordered_groups = tuple(sorted(groups, key=lambda group: group.order))
    return TopicCatalog(groups=ordered_groups, topics=tuple(topics))


def _load_rows(path: Path, key: str) -> list[Any]:
    document = _load_document(path)
    return _document_rows(document, key)


def _load_document(path: Path) -> Mapping[str, Any]:
    try:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"could not read YAML configuration {path}: {exc}") from exc

    if not isinstance(document, Mapping):
        raise ConfigError("configuration root must be a mapping")
    return document


def _document_rows(document: Mapping[str, Any], key: str) -> list[Any]:
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


def _reject_unknown_fields(
    row: Mapping[str, Any], allowed_fields: frozenset[str], context: str
) -> None:
    unknown_fields = set(row) - allowed_fields
    if unknown_fields:
        fields = ", ".join(sorted(str(field) for field in unknown_fields))
        raise ConfigError(f"{context} has unknown fields: {fields}")


def _is_valid_host(host: str) -> bool:
    if not host or len(host) > 253:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if all(character.isdigit() or character == "." for character in host):
            return False
        return all(_HOST_LABEL.fullmatch(label) for label in host.rstrip(".").split("."))
    return True
