import sqlite3
from collections.abc import Sequence

from paper_radar.classify import classify_article
from paper_radar.config import TopicConfig
from paper_radar.database import list_articles, replace_all_article_tags
from paper_radar.models import ClassificationSummary


def reclassify_all_articles(
    connection: sqlite3.Connection,
    topics: Sequence[TopicConfig],
) -> ClassificationSummary:
    """Classify all stored articles, then atomically replace their tag links."""

    articles = list_articles(connection)
    assignments = {
        article.uid: tuple(classify_article(article, topics)) for article in articles
    }
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
