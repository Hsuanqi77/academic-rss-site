import re
from dataclasses import FrozenInstanceError

import pytest

from paper_radar.config import FeedConfig
from paper_radar.models import ArticleRecord, RawFeedItem
from paper_radar.normalize import (
    clean_text,
    make_uid,
    normalize_article_type,
    normalize_date,
    normalize_doi,
    normalize_item,
    normalize_url,
)


def test_clean_text_unescapes_entities_and_collapses_whitespace() -> None:
    assert clean_text("  A&nbsp;paper\n\t title  ") == "A paper title"


def test_clean_text_turns_markup_into_safe_plain_text() -> None:
    value = "<p>Hello <strong>world</strong></p><script>alert('x')</script><p>Again</p>"

    assert clean_text(value) == "Hello world Again"


def test_clean_text_sanitizes_entity_escaped_markup_before_returning_text() -> None:
    value = "&lt;p&gt;Visible&lt;/p&gt;&lt;script&gt;ignore()&lt;/script&gt;"

    assert clean_text(value) == "Visible"


def test_clean_text_returns_none_for_empty_input() -> None:
    assert clean_text(None) is None
    assert clean_text(" <br> \t") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://doi.org/10.1000/ABC.", "10.1000/abc"),
        (" DOI: 10.1000/ABC ", "10.1000/abc"),
        ("https://DX.DOI.ORG/10.1234/Foo%28A%29?utm_source=rss#fragment", "10.1234/foo(a)"),
        (
            "10.1002/(SICI)1097-0312(199707)50:7<601::AID-CPA5>3.0.CO;2-L",
            "10.1002/(sici)1097-0312(199707)50:7<601::aid-cpa5>3.0.co;2-l",
        ),
        ("10.1234/example(a).,", "10.1234/example(a)"),
        ("10.1234/a+b=c@d", "10.1234/a+b=c@d"),
    ],
)
def test_normalize_doi_canonicalizes_complete_dois(value: str, expected: str) -> None:
    assert normalize_doi(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "not a DOI",
        "reference 10.1234/partial followed by prose",
        "https://example.org/10.1234/not-a-resolver",
        "https://doi.org/not-a-doi",
        "https://doi.org:bad/10.1234/malformed-resolver-port",
        "10.1234/",
        "10.1234/unbalanced(",
        "10.123/too-short-registrant",
    ],
)
def test_normalize_doi_rejects_incomplete_or_non_doi_values(value: str | None) -> None:
    assert normalize_doi(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "https://example.org/paper/?utm_source=rss&x=1",
            "https://example.org/paper?x=1",
        ),
        ("HTTPS://Example.ORG:443/paper/#fragment", "https://example.org/paper"),
        ("http://Example.ORG:80/", "http://example.org/"),
        ("https://Example.ORG:8443/path/", "https://example.org:8443/path"),
        (
            "https://example.org/p?x=1&x=2&empty=&flag&utm_MEDIUM=email",
            "https://example.org/p?x=1&x=2&empty=&flag",
        ),
        (
            "https://User:Secret@Example.Org:443/path#private",
            "https://example.org/path",
        ),
        ("https://例子.测试/论文/", "https://xn--fsqu00a.xn--0zwm56d/论文"),
        ("https://[2001:DB8::1]:443/paper/", "https://[2001:db8::1]/paper"),
    ],
)
def test_normalize_url_canonicalizes_http_urls(value: str, expected: str) -> None:
    assert normalize_url(value) == expected


def test_normalize_url_removes_tracking_parameters_case_insensitively() -> None:
    value = "https://example.org/p?SPM=x&FbClId=y&GCLID=z&keep=yes"

    assert normalize_url(value) == "https://example.org/p?keep=yes"


def test_normalize_url_canonicalizes_empty_root_path_to_slash() -> None:
    assert normalize_url("https://example.org") == "https://example.org/"
    assert normalize_url("https://example.org") == normalize_url("https://example.org/")


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "relative/path",
        "ftp://example.org/paper",
        "https:///hostless",
        "https://example.org:bad/path",
        "https://[2001:db8::1/path",
        "https://exa mple.org/path",
        "https://example.org/\x7fpaper",
    ],
)
def test_normalize_url_rejects_blank_malformed_or_non_http_urls(value: str | None) -> None:
    assert normalize_url(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-01", "2026-07-01T00:00:00+00:00"),
        ("2026-07-01T12:30:45Z", "2026-07-01T12:30:45+00:00"),
        ("2026-07-01T08:00:00+08:00", "2026-07-01T00:00:00+00:00"),
        ("Wed, 01 Jul 2026 12:30:00 GMT", "2026-07-01T12:30:00+00:00"),
        ("Wed, 01 Jul 2026 12:30:00", "2026-07-01T12:30:00+00:00"),
    ],
)
def test_normalize_date_converts_supported_dates_to_utc(value: str, expected: str) -> None:
    assert normalize_date(value) == expected


@pytest.mark.parametrize(
    "value",
    [None, "", "not a date", "2026-02-31", "0001-01-01T00:00:00+14:00"],
)
def test_normalize_date_returns_none_for_invalid_values(value: str | None) -> None:
    assert normalize_date(value) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Review Article", "review"),
        ("Correction to a review article", "review"),
        ("Erratum to research article", "correction"),
        ("Corrigendum", "correction"),
        ("Editorial: research directions", "editorial"),
        ("Commentary", "editorial"),
        ("Perspective", "editorial"),
        ("Research Article", "research"),
        ("Letter", "research"),
        ("Original article", "research"),
        ("Preview", "other"),
        (None, "other"),
        ("", "other"),
    ],
)
def test_normalize_article_type_classifies_with_priority(value: str | None, expected: str) -> None:
    assert normalize_article_type(value) == expected


def test_make_uid_prefers_canonical_doi_over_url() -> None:
    uid = make_uid(
        "DOI:10.1000/ABC",
        "https://example.org/paper?x=1",
        "journal",
        "A title",
        "2026-07-01",
    )

    assert uid == "doi:10.1000/abc"


def test_make_uid_uses_stable_normalized_url_hash() -> None:
    first = make_uid(
        None,
        "https://EXAMPLE.org:443/paper/?utm_source=rss&x=1#top",
        "journal",
        "First title",
        "2026-07-01",
    )
    second = make_uid(
        None,
        "https://example.org/paper?x=1&utm_medium=email",
        "other-journal",
        "Other title",
        "2025-01-01",
    )

    assert first == second
    assert re.fullmatch(r"url:[0-9a-f]{24}", first)


def test_make_uid_hash_fallback_is_stable_for_clean_title_and_published_date() -> None:
    first = make_uid(None, None, "journal", "  A&nbsp;TITLE ", "2026-07-01")
    second = make_uid(
        None,
        None,
        "journal",
        "a title",
        "2026-07-01T21:10:00+00:00",
    )

    assert first == second
    assert re.fullmatch(r"hash:[0-9a-f]{24}", first)
    assert first != make_uid(None, None, "journal", "different", "2026-07-01")


def make_feed() -> FeedConfig:
    return FeedConfig(
        id="example-journal",
        name="Example Journal",
        publisher="nature",
        feed_url="https://feeds.example.org/journal.xml",
    )


def test_normalize_item_builds_canonical_article_record() -> None:
    feed = make_feed()
    item = RawFeedItem(
        feed_id=feed.id,
        feed_url=feed.feed_url,
        title="  A&nbsp;paper  ",
        link=" https://EXAMPLE.org:443/paper/?utm_source=rss&x=1#abstract ",
        published="2026-07-01",
        doi="doi:10.1000/ABC",
        authors=(" Alice Example ", "", "alice example", "<b>Bob Example</b>"),
        summary="<p>A&nbsp;summary.</p><script>ignore()</script>",
        raw_type="Research Article",
    )

    record = normalize_item(item, feed)

    assert record == ArticleRecord(
        uid="doi:10.1000/abc",
        doi="10.1000/abc",
        journal_id="example-journal",
        title="A paper",
        abstract="A summary.",
        authors=("Alice Example", "Bob Example"),
        published_at="2026-07-01T00:00:00+00:00",
        article_type="research",
        article_url="https://EXAMPLE.org:443/paper/?utm_source=rss&x=1#abstract",
        normalized_url="https://example.org/paper?x=1",
        oa_status="unknown",
        source_feed_url=feed.feed_url,
        metadata_status="rss_only",
    )


def test_normalize_item_uses_untitled_fallback_for_direct_malformed_raw_item() -> None:
    feed = make_feed()
    item = RawFeedItem(
        feed_id=feed.id,
        feed_url=feed.feed_url,
        title=" <br> ",
        link="https://example.org/untitled",
        published=None,
        doi=None,
        authors=(" ",),
        summary=" ",
        raw_type=None,
    )

    record = normalize_item(item, feed)

    assert record.title == "Untitled"
    assert record.abstract is None
    assert record.authors == ()
    assert record.uid.startswith("url:")


@pytest.mark.parametrize("link", [None, "", "relative/path", "ftp://example.org/paper"])
def test_normalize_item_rejects_unusable_article_urls(link: str | None) -> None:
    feed = make_feed()
    item = RawFeedItem(
        feed_id=feed.id,
        feed_url=feed.feed_url,
        title="Bad URL",
        link=link,  # type: ignore[arg-type]
        published=None,
        doi=None,
        authors=(),
        summary=None,
        raw_type=None,
    )

    with pytest.raises(ValueError, match="article URL"):
        normalize_item(item, feed)


@pytest.mark.parametrize(
    ("feed_id", "feed_url"),
    [
        ("different-journal", "https://feeds.example.org/journal.xml"),
        ("example-journal", "https://feeds.example.org/different.xml"),
    ],
)
def test_normalize_item_rejects_feed_identity_mismatches(feed_id: str, feed_url: str) -> None:
    feed = make_feed()
    item = RawFeedItem(
        feed_id=feed_id,
        feed_url=feed_url,
        title="Cross-feed record",
        link="https://example.org/paper",
        published=None,
        doi=None,
        authors=(),
        summary=None,
        raw_type=None,
    )

    with pytest.raises(ValueError, match="feed"):
        normalize_item(item, feed)


def test_article_record_is_frozen_and_slotted() -> None:
    record = ArticleRecord(
        uid="url:0123456789abcdef01234567",
        doi=None,
        journal_id="journal",
        title="Title",
        abstract=None,
        authors=(),
        published_at=None,
        article_type="other",
        article_url="https://example.org/paper",
        normalized_url="https://example.org/paper",
        oa_status="unknown",
        source_feed_url="https://example.org/feed.xml",
        metadata_status="rss_only",
    )

    with pytest.raises(FrozenInstanceError):
        record.title = "Changed"  # type: ignore[misc]
    assert not hasattr(record, "__dict__")
