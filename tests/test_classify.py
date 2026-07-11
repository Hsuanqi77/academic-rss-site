from dataclasses import replace
from pathlib import Path

from paper_radar.classify import classify_article
from paper_radar.config import TopicConfig, load_topics
from paper_radar.models import ArticleRecord


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _article(*, title: str, abstract: str | None = None) -> ArticleRecord:
    return ArticleRecord(
        uid="doi:10.1000/example",
        doi="10.1000/example",
        journal_id="example",
        title=title,
        abstract=abstract,
        authors=(),
        published_at=None,
        article_type="other",
        article_url="https://example.test/article",
        normalized_url="https://example.test/article",
        oa_status="unknown",
        source_feed_url="https://example.test/feed",
        metadata_status="rss_only",
    )


def _topic(topic_id: str, *keywords: str) -> TopicConfig:
    return TopicConfig(
        id=topic_id,
        label=topic_id.upper(),
        keywords=keywords,
        group="acoustic-rf",
    )


def test_classify_matches_phrase_without_matching_unrelated_acronym() -> None:
    article = _article(title="An AlScN surface acoustic wave resonator")
    saw = _topic("saw", "surface acoustic wave", "SAW")
    rf = _topic("rf", "RF")

    assert classify_article(article, [saw, rf]) == [saw]


def test_classify_acronyms_require_token_boundaries_but_allow_hyphens() -> None:
    rf = _topic("rf", "RF")

    assert classify_article(_article(title="High-performance device"), [rf]) == []
    assert classify_article(_article(title="An RF-MEMS filter"), [rf]) == [rf]
    assert classify_article(_article(title="The RF response"), [rf]) == [rf]


def test_classify_real_rf_microwave_topic_uses_approved_catalog_entry() -> None:
    rf = next(
        topic for topic in load_topics(PROJECT_ROOT / "topics.yml") if topic.id == "rf-microwave"
    )

    assert rf.keywords == (
        "radio frequency",
        "RF front-end",
        "RF filter",
        "microwave",
        "millimeter wave",
        "millimetre wave",
        "mmWave",
    )
    assert classify_article(_article(title="radio frequency response"), [rf]) == [rf]


def test_classify_unicode_dash_keyword_matches_dash_compound_continuation() -> None:
    rf = _topic("rf-dash", "radio–frequency")

    assert classify_article(_article(title="radio–frequency–response"), [rf]) == [rf]


def test_classify_phrase_tolerates_collapsed_whitespace_only() -> None:
    saw = _topic("saw", "surface acoustic wave")

    assert classify_article(_article(title="Surface\n acoustic   wave resonator"), [saw]) == [saw]
    assert classify_article(_article(title="A surface-acoustic-wave resonator"), [saw]) == []
    assert classify_article(_article(title="A subsurface acoustic waveform"), [saw]) == []


def test_classify_escapes_keyword_metacharacters_and_respects_boundaries() -> None:
    c_plus_plus = _topic("cpp", "C++")
    dotted = _topic("dot", "A.B")

    assert classify_article(
        _article(title="C++ methods for A.B testing"), [c_plus_plus, dotted]
    ) == [
        c_plus_plus,
        dotted,
    ]
    assert (
        classify_article(_article(title="C methods for AxB testing"), [c_plus_plus, dotted]) == []
    )


def test_classify_does_not_match_inside_longer_punctuation_identifiers() -> None:
    c_plus_plus = _topic("cpp", "C++")
    dotted = _topic("dot", "A.B")

    assert classify_article(_article(title="A.B.C notation"), [dotted]) == []
    assert classify_article(_article(title="C++++ extensions"), [c_plus_plus]) == []


def test_classify_boundaries_include_unicode_marks_numbers_and_connectors() -> None:
    devanagari = _topic("devanagari", "क")
    rf = _topic("rf", "RF")

    assert classify_article(_article(title="कि resonance"), [devanagari]) == []
    assert classify_article(_article(title="क resonance"), [devanagari]) == [devanagari]
    assert classify_article(_article(title="RF2 and RF_filter"), [rf]) == []
    assert classify_article(_article(title="RF-MEMS"), [rf]) == [rf]


def test_classify_is_unicode_case_insensitive_and_normalizes_text() -> None:
    material = _topic("material", "älscn", "café")

    assert classify_article(_article(title="ÄlScN and CAFE\u0301 devices"), [material]) == [
        material
    ]


def test_classify_searches_title_and_abstract_preserving_order_without_duplicates() -> None:
    article = _article(title="AlScN resonator", abstract="RF response in a SAW device")
    rf = _topic("rf", "RF")
    saw = _topic("saw", "SAW", "resonator")
    material = _topic("material", "AlScN")
    original = replace(article)
    topics = [saw, material, rf]

    result = classify_article(article, topics)

    assert result == [saw, material, rf]
    assert result.count(saw) == 1
    assert article == original
    assert topics == [saw, material, rf]


def test_classify_handles_empty_abstract_and_does_not_match_substrings() -> None:
    saw = _topic("saw", "SAW")
    material = _topic("material", "AlN")

    assert classify_article(_article(title="Seesaw performance", abstract=None), [saw]) == []
    assert classify_article(_article(title="AlScN film", abstract=""), [material]) == []
