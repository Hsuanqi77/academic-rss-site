from collections.abc import Sequence
from unicodedata import category

from paper_radar.config import TopicConfig
from paper_radar.matching import normalize_match_separators
from paper_radar.models import ArticleRecord
from paper_radar.normalize import clean_text


def classify_article(
    article: ArticleRecord,
    topics: Sequence[TopicConfig],
) -> list[TopicConfig]:
    searchable_fields = tuple(
        normalize_match_separators(cleaned)
        for value in (article.title, article.abstract)
        if (cleaned := clean_text(value)) is not None
    )
    base_hits = tuple(
        any(_keyword_matches(keyword, searchable_fields) for keyword in topic.keywords)
        for topic in topics
    )
    base_groups = {
        topic.group
        for topic, hit in zip(topics, base_hits, strict=True)
        if hit and not topic.requires_any_group
    }
    return [
        topic
        for topic, hit in zip(topics, base_hits, strict=True)
        if hit
        and (
            not topic.requires_any_group
            or any(group in base_groups for group in topic.requires_any_group)
        )
    ]


def _keyword_matches(keyword: str, searchable_fields: tuple[str, ...]) -> bool:
    cleaned = clean_text(keyword)
    if cleaned is None:
        return False
    normalized_keyword = normalize_match_separators(cleaned)
    return any(_field_contains_keyword(field, normalized_keyword) for field in searchable_fields)


def _field_contains_keyword(field: str, keyword: str) -> bool:
    punctuation = {
        character
        for character in keyword
        if (
            not character.isspace()
            and not _is_token_component(character)
            and category(character) != "Pd"
        )
    }
    start = field.find(keyword)
    while start >= 0:
        end = start + len(keyword)
        before = field[start - 1] if start else None
        after = field[end] if end < len(field) else None
        if (
            (before is None or not _is_token_component(before))
            and (after is None or not _is_token_component(after))
            and before not in punctuation
            and after not in punctuation
        ):
            return True
        start = field.find(keyword, start + 1)
    return False


def _is_token_component(character: str) -> bool:
    unicode_category = category(character)
    return unicode_category[0] in {"L", "N", "M"} or unicode_category == "Pc"
