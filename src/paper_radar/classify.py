import re
from collections.abc import Sequence

from paper_radar.config import TopicConfig
from paper_radar.models import ArticleRecord
from paper_radar.normalize import clean_text


def classify_article(
    article: ArticleRecord,
    topics: Sequence[TopicConfig],
) -> list[TopicConfig]:
    searchable_fields = tuple(
        cleaned.casefold()
        for value in (article.title, article.abstract)
        if (cleaned := clean_text(value)) is not None
    )
    matches: list[TopicConfig] = []
    for topic in topics:
        if any(_keyword_matches(keyword, searchable_fields) for keyword in topic.keywords):
            matches.append(topic)
    return matches


def _keyword_matches(keyword: str, searchable_fields: tuple[str, ...]) -> bool:
    cleaned = clean_text(keyword)
    if cleaned is None:
        return False
    escaped_words = (re.escape(word) for word in cleaned.casefold().split())
    pattern = re.compile(r"(?<!\w)" + r"\s+".join(escaped_words) + r"(?!\w)")
    return any(pattern.search(field) is not None for field in searchable_fields)
