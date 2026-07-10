from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx

import paper_radar.feeds as feeds_module
from paper_radar.config import FeedConfig
from paper_radar.feeds import USER_AGENT, FeedParseError, fetch_feed, parse_feed
from paper_radar.models import FeedFetchResult, RawFeedItem


FIXTURES = Path(__file__).parent / "fixtures"


def test_feed_models_are_immutable_and_slotted() -> None:
    item = RawFeedItem(
        feed_id="fixture",
        feed_url="https://example.org/feed.xml",
        title="Fixture paper",
        link="https://example.org/paper",
        published=None,
        doi=None,
        authors=(),
        summary=None,
        raw_type=None,
    )
    result = FeedFetchResult(
        content=b"<rss />",
        etag=None,
        last_modified=None,
        not_modified=False,
    )

    assert not hasattr(item, "__dict__")
    assert not hasattr(result, "__dict__")
    assert item.authors == ()
    with pytest.raises(FrozenInstanceError):
        item.title = "Changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("fixture", "feed_id", "expected"),
    [
        (
            "rss1.xml",
            "nature",
            RawFeedItem(
                feed_id="nature",
                feed_url="https://example.org/rss1.xml",
                title="Nature fixture paper",
                link="https://example.org/nature-paper",
                published="2026-07-01",
                doi="10.1000/nature",
                authors=("Marie Example",),
                summary=None,
                raw_type="Research",
            ),
        ),
        (
            "rss2.xml",
            "ieee",
            RawFeedItem(
                feed_id="ieee",
                feed_url="https://example.org/rss2.xml",
                title="IEEE fixture paper",
                link="https://example.org/ieee-paper",
                published="Thu, 02 Jul 2026 08:00:00 GMT",
                doi="10.1000/ieee",
                authors=("Ada Example",),
                summary="Ultrasonic fixture abstract.",
                raw_type=None,
            ),
        ),
        (
            "atom.xml",
            "wiley",
            RawFeedItem(
                feed_id="wiley",
                feed_url="https://example.org/atom.xml",
                title="Wiley fixture paper",
                link="https://example.org/wiley-paper",
                published="2026-07-03T09:00:00Z",
                doi="10.1000/wiley",
                authors=("Grace Example",),
                summary="Materials fixture abstract.",
                raw_type=None,
            ),
        ),
    ],
)
def test_parse_feed_normalizes_supported_formats(
    fixture: str, feed_id: str, expected: RawFeedItem
) -> None:
    feed_url = f"https://example.org/{fixture.removesuffix('.xml')}.xml"

    items = parse_feed((FIXTURES / fixture).read_bytes(), feed_id, feed_url)

    assert items == [expected]


def test_parse_feed_skips_incomplete_entries_and_preserves_order() -> None:
    content = b"""\
        <rss version="2.0"><channel><title>Fixture</title>
          <item><title>First valid</title><link>https://example.org/first</link></item>
          <item><title>   </title><link>https://example.org/blank-title</link></item>
          <item><title>Missing link</title></item>
          <item><title>Second valid</title><link>https://example.org/second</link></item>
        </channel></rss>
    """

    items = parse_feed(content, "fixture", "https://example.org/feed.xml")

    assert [item.title for item in items] == ["First valid", "Second valid"]
    assert [item.link for item in items] == [
        "https://example.org/first",
        "https://example.org/second",
    ]


def test_parse_feed_extracts_preferred_doi_and_strips_trailing_punctuation() -> None:
    content = b"""\
        <rss version="2.0"
             xmlns:dc="http://purl.org/dc/elements/1.1/"
             xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">
          <channel><title>Fixture</title>
            <item>
              <title>DOI precedence</title>
              <link>https://doi.org/10.1000/from-link</link>
              <guid isPermaLink="false">doi:10.1000/from-guid</guid>
              <dc:identifier>doi:10.1000/from-dc</dc:identifier>
              <prism:doi>DOI:10.1000/preferred.),</prism:doi>
              <description>Also 10.1000/from-summary.</description>
            </item>
          </channel>
        </rss>
    """

    item = parse_feed(content, "fixture", "https://example.org/feed.xml")[0]

    assert item.doi == "10.1000/preferred"


def test_parse_feed_extracts_doi_from_summary_when_identifiers_do_not_contain_one() -> None:
    content = b"""\
        <rss version="2.0"><channel><title>Fixture</title>
          <item>
            <title>Summary DOI</title>
            <link>https://example.org/summary-doi</link>
            <guid isPermaLink="false">fixture-entry</guid>
            <description>Abstract ending in doi: 10.1000/from-summary.</description>
          </item>
        </channel></rss>
    """

    item = parse_feed(content, "fixture", "https://example.org/feed.xml")[0]

    assert item.doi == "10.1000/from-summary"


def test_parse_feed_uses_trimmed_singular_author_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = SimpleNamespace(
        bozo=False,
        entries=[
            {
                "title": "Fallback author",
                "link": "https://example.org/fallback-author",
                "author": "  Ada Example  ",
            }
        ],
    )
    monkeypatch.setattr(feeds_module.feedparser, "parse", lambda _: parsed)

    item = parse_feed(b"ignored", "fixture", "https://example.org/feed.xml")[0]

    assert item.authors == ("Ada Example",)


def test_parse_feed_trims_structured_authors_and_drops_blank_names() -> None:
    content = b"""\
        <feed xmlns="http://www.w3.org/2005/Atom">
          <title>Fixture</title><id>fixture</id><updated>2026-07-01T00:00:00Z</updated>
          <entry>
            <title>Author whitespace</title><id>author-whitespace</id>
            <link href="https://example.org/author-whitespace" />
            <updated>2026-07-01T00:00:00Z</updated>
            <author><name>   </name></author>
            <author><name>  Grace Example  </name></author>
          </entry>
        </feed>
    """

    item = parse_feed(content, "fixture", "https://example.org/feed.xml")[0]

    assert item.authors == ("Grace Example",)


def test_parse_feed_raises_actionable_error_for_malformed_feed_without_entries() -> None:
    with pytest.raises(
        FeedParseError, match="could not parse feed.*https://example.org/broken.xml"
    ):
        parse_feed(
            b"<rss><channel><title>Broken",
            "broken",
            "https://example.org/broken.xml",
        )


def test_parse_feed_tolerates_recoverable_bozo_feed_with_usable_entry() -> None:
    content = b"""\
        <rss version="2.0"><channel><title>Recoverable</title>
          <item><title>Recovered paper</title><link>https://example.org/recovered</link></item>
    """

    items = parse_feed(content, "fixture", "https://example.org/feed.xml")

    assert [item.title for item in items] == ["Recovered paper"]


def make_feed() -> FeedConfig:
    return FeedConfig(
        id="fixture",
        name="Fixture Feed",
        publisher="nature",
        feed_url="https://example.org/feed.xml",
    )


@pytest.mark.parametrize(
    ("response_headers", "expected_etag", "expected_last_modified"),
    [
        ({}, '"prior"', "Thu, 02 Jul 2026 08:00:00 GMT"),
        (
            {"ETag": '"refreshed"', "Last-Modified": "Fri, 03 Jul 2026 09:00:00 GMT"},
            '"refreshed"',
            "Fri, 03 Jul 2026 09:00:00 GMT",
        ),
    ],
)
def test_fetch_feed_sends_conditional_headers_and_handles_not_modified(
    response_headers: dict[str, str],
    expected_etag: str,
    expected_last_modified: str,
) -> None:
    with respx.mock:
        route = respx.get("https://example.org/feed.xml").mock(
            return_value=httpx.Response(304, headers=response_headers)
        )
        with httpx.Client() as client:
            result = fetch_feed(
                client,
                make_feed(),
                etag='"prior"',
                last_modified="Thu, 02 Jul 2026 08:00:00 GMT",
            )

    request = route.calls.last.request
    assert request.headers["User-Agent"] == USER_AGENT
    assert request.headers["If-None-Match"] == '"prior"'
    assert request.headers["If-Modified-Since"] == "Thu, 02 Jul 2026 08:00:00 GMT"
    assert request.extensions["timeout"] == {
        "connect": 25.0,
        "read": 25.0,
        "write": 25.0,
        "pool": 25.0,
    }
    assert result == FeedFetchResult(
        content=None,
        etag=expected_etag,
        last_modified=expected_last_modified,
        not_modified=True,
    )


def test_fetch_feed_returns_content_and_response_validators() -> None:
    with respx.mock:
        route = respx.get("https://example.org/feed.xml").mock(
            return_value=httpx.Response(
                200,
                content=b"<rss />",
                headers={
                    "ETag": '"current"',
                    "Last-Modified": "Fri, 03 Jul 2026 09:00:00 GMT",
                },
            )
        )
        with httpx.Client() as client:
            result = fetch_feed(client, make_feed())

    assert "If-None-Match" not in route.calls.last.request.headers
    assert "If-Modified-Since" not in route.calls.last.request.headers
    assert result == FeedFetchResult(
        content=b"<rss />",
        etag='"current"',
        last_modified="Fri, 03 Jul 2026 09:00:00 GMT",
        not_modified=False,
    )


def test_fetch_feed_propagates_http_status_errors_with_request_context() -> None:
    with respx.mock:
        respx.get("https://example.org/feed.xml").mock(return_value=httpx.Response(503))
        with httpx.Client() as client:
            with pytest.raises(httpx.HTTPStatusError) as raised:
                fetch_feed(client, make_feed())

    assert raised.value.request.url == httpx.URL("https://example.org/feed.xml")
    assert raised.value.response.status_code == 503


def test_fetch_feed_propagates_request_errors_with_request_context() -> None:
    with respx.mock:
        respx.get("https://example.org/feed.xml").mock(
            side_effect=httpx.ConnectError("fixture connection failed")
        )
        with httpx.Client() as client:
            with pytest.raises(httpx.ConnectError) as raised:
                fetch_feed(client, make_feed())

    assert raised.value.request.url == httpx.URL("https://example.org/feed.xml")
