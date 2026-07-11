import sqlite3
from collections.abc import Sequence

from paper_radar.classify import classify_article
from paper_radar.config import TopicConfig
from paper_radar.database import (
    RepositoryConflictError,
    list_articles,
    replace_all_article_tags,
)
from paper_radar.models import ClassificationSummary


class ReclassificationError(RuntimeError):
    """Raised when a stored article cannot be classified."""


def reclassify_all_articles(
    connection: sqlite3.Connection,
    topics: Sequence[TopicConfig],
) -> ClassificationSummary:
    """Classify all stored articles, then atomically replace their tag links."""

    articles = list_articles(connection)
    assignments: dict[str, tuple[TopicConfig, ...]] = {}
    for article in articles:
        try:
            classified = classify_article(article, topics)
        except Exception as error:
            raise ReclassificationError(f"failed to classify article {article.uid}") from error
        assignments[article.uid] = _normalize_classified_topics(classified)
    replace_all_article_tags(connection, assignments)
    used_topic_ids = {
        topic.id for article_topics in assignments.values() for topic in article_topics
    }
    return ClassificationSummary(
        articles_scanned=len(articles),
        articles_tagged=sum(bool(article_topics) for article_topics in assignments.values()),
        tag_assignments=sum(len(article_topics) for article_topics in assignments.values()),
        active_tags=len(used_topic_ids),
    )


def _normalize_classified_topics(topics: Sequence[TopicConfig]) -> tuple[TopicConfig, ...]:
    topics_by_id: dict[str, TopicConfig] = {}
    for topic in topics:
        previous = topics_by_id.get(topic.id)
        if previous is not None and previous.label != topic.label:
            raise RepositoryConflictError(f"conflicting labels supplied for tag {topic.id}")
        topics_by_id[topic.id] = topic
    return tuple(topics_by_id.values())
