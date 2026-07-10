from dataclasses import FrozenInstanceError
import gzip
from pathlib import Path
import socket
from threading import Event, Thread
from time import perf_counter
from types import SimpleNamespace
import zlib

import httpx
import pytest
import respx

import paper_radar.feeds as feeds_module
from paper_radar.config import FeedConfig
from paper_radar.feeds import USER_AGENT, FeedParseError, fetch_feed, parse_feed
from paper_radar.models import FeedFetchResult, RawFeedItem


FIXTURES = Path(__file__).parent / "fixtures"


class TrackingStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.iterated = False
        self.closed = False

    def __iter__(self):
        self.iterated = True
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


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
    assert result.effective_url is None
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


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (
            "10.1002/(SICI)1097-0312(199707)50:7<601::AID-CPA5>3.0.CO;2-L",
            "10.1002/(SICI)1097-0312(199707)50:7<601::AID-CPA5>3.0.CO;2-L",
        ),
        ("10.1234/example(a)", "10.1234/example(a)"),
        ("10.1234/a+b=c@d", "10.1234/a+b=c@d"),
        (
            "https://doi.org/10.1234/example%28encoded%29",
            "10.1234/example(encoded)",
        ),
    ],
)
def test_parse_feed_preserves_complete_legal_doi_suffixes(
    candidate: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = SimpleNamespace(
        bozo=False,
        version="rss20",
        entries=[
            {
                "title": "DOI fixture",
                "link": "https://example.org/doi-fixture",
                "id": candidate,
            }
        ],
    )
    monkeypatch.setattr(feeds_module.feedparser, "parse", lambda *args, **kwargs: parsed)

    item = parse_feed(b"ignored", "fixture", "https://example.org/feed.xml")[0]

    assert item.doi == expected


def test_parse_feed_removes_only_prose_punctuation_after_balanced_doi() -> None:
    content = b"""\
        <rss version="2.0"><channel><title>Fixture</title>
          <item>
            <title>Prose DOI</title>
            <link>https://example.org/prose-doi</link>
            <description>See DOI 10.1234/example(a)., for details.</description>
          </item>
        </channel></rss>
    """

    item = parse_feed(content, "fixture", "https://example.org/feed.xml")[0]

    assert item.doi == "10.1234/example(a)"


def test_parse_feed_ignores_invalid_higher_priority_doi_without_truncating_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = SimpleNamespace(
        bozo=False,
        version="rss20",
        entries=[
            {
                "title": "DOI precedence",
                "link": "https://doi.org/10.1234/from-link",
                "prism_doi": "10.1234/unbalanced(",
                "dc_identifier": "doi:10.1234/from-dc",
                "id": "doi:10.1234/from-id",
                "guid": "doi:10.1234/from-guid",
                "summary": "10.1234/from-summary",
            }
        ],
    )
    monkeypatch.setattr(feeds_module.feedparser, "parse", lambda *args, **kwargs: parsed)

    item = parse_feed(b"ignored", "fixture", "https://example.org/feed.xml")[0]

    assert item.doi == "10.1234/from-dc"


def test_parse_feed_does_not_extract_partial_doi_from_invalid_explicit_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = SimpleNamespace(
        bozo=False,
        version="rss20",
        entries=[
            {
                "title": "Invalid explicit DOI",
                "link": "https://example.org/no-doi",
                "prism_doi": "reference 10.1234/partial followed by prose",
            }
        ],
    )
    monkeypatch.setattr(feeds_module.feedparser, "parse", lambda *args, **kwargs: parsed)

    item = parse_feed(b"ignored", "fixture", "https://example.org/feed.xml")[0]

    assert item.doi is None


def test_parse_feed_distinguishes_explicit_terminal_period_from_prose_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = SimpleNamespace(
        bozo=False,
        version="rss20",
        entries=[
            {
                "title": "Explicit DOI punctuation",
                "link": "https://example.org/explicit-doi",
                "prism_doi": "doi:10.1234/explicit.",
                "summary": "A different DOI 10.1234/prose.",
            }
        ],
    )
    monkeypatch.setattr(feeds_module.feedparser, "parse", lambda *args, **kwargs: parsed)

    item = parse_feed(b"ignored", "fixture", "https://example.org/feed.xml")[0]

    assert item.doi == "10.1234/explicit."


@pytest.mark.parametrize(
    "summary",
    [
        "<p>See doi:10.1234/foo.</p>",
        "<strong>doi:10.1234/foo</strong>",
    ],
)
def test_parse_feed_extracts_doi_from_html_summary_text_only(
    summary: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = SimpleNamespace(
        bozo=False,
        version="rss20",
        entries=[
            {
                "title": "HTML summary DOI",
                "link": "https://example.org/html-summary",
                "summary": summary,
            }
        ],
    )
    monkeypatch.setattr(feeds_module.feedparser, "parse", lambda *args, **kwargs: parsed)

    item = parse_feed(b"ignored", "fixture", "https://example.org/feed.xml")[0]

    assert item.doi == "10.1234/foo"


@pytest.mark.parametrize(
    "link",
    [
        "https://publisher.example/articles/10.1234/foo?utm_source=rss",
        "https://publisher.example/articles/10.1234/foo#fragment",
    ],
)
def test_parse_feed_extracts_doi_from_url_path_without_query_or_fragment(
    link: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = SimpleNamespace(
        bozo=False,
        version="rss20",
        entries=[{"title": "URL DOI", "link": link, "id": "publisher-item"}],
    )
    monkeypatch.setattr(feeds_module.feedparser, "parse", lambda *args, **kwargs: parsed)

    item = parse_feed(b"ignored", "fixture", "https://example.org/feed.xml")[0]

    assert item.doi == "10.1234/foo"


def test_parse_feed_uses_trimmed_singular_author_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed = SimpleNamespace(
        bozo=False,
        version="rss20",
        entries=[
            {
                "title": "Fallback author",
                "link": "https://example.org/fallback-author",
                "author": "  Ada Example  ",
            }
        ],
    )
    monkeypatch.setattr(feeds_module.feedparser, "parse", lambda *args, **kwargs: parsed)

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


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"<html><head><title>Not a feed</title></head><body>Hello</body></html>",
    ],
)
def test_parse_feed_rejects_success_bodies_that_are_not_feeds(content: bytes) -> None:
    with pytest.raises(FeedParseError, match="not a recognized RSS or Atom feed"):
        parse_feed(content, "fixture", "https://example.org/feed.xml")


@pytest.mark.parametrize(
    "content",
    [
        b"""<rss version="2.0"><channel><title>Empty RSS</title>
               <link>https://example.org/</link><description>No entries</description>
             </channel></rss>""",
        b"""<feed xmlns="http://www.w3.org/2005/Atom">
               <title>Empty Atom</title><id>empty</id><updated>2026-07-01T00:00:00Z</updated>
             </feed>""",
    ],
)
def test_parse_feed_accepts_recognized_empty_feeds(content: bytes) -> None:
    assert parse_feed(content, "fixture", "https://example.org/feed.xml") == []


@pytest.mark.parametrize(
    "content",
    [
        b"<rss version='2.0'/>",
        b"<rss><channel/></rss>",
        b"<feed xmlns='http://www.w3.org/2005/Atom'/>",
    ],
)
def test_parse_feed_rejects_empty_containers_without_feed_metadata(content: bytes) -> None:
    with pytest.raises(FeedParseError, match="could not parse feed"):
        parse_feed(content, "fixture", "https://example.org/feed.xml")


def test_parse_feed_bytes_resolves_relative_atom_links_against_effective_url() -> None:
    content = b"""\
        <feed xmlns="http://www.w3.org/2005/Atom">
          <title>Relative links</title><id>relative</id>
          <updated>2026-07-01T00:00:00Z</updated>
          <entry>
            <title>Relative paper</title><id>relative-paper</id>
            <link rel="alternate" href="../articles/paper" />
            <updated>2026-07-01T00:00:00Z</updated>
          </entry>
        </feed>
    """

    items = feeds_module.parse_feed_bytes(
        content,
        "fixture",
        "https://example.org/original/feed.xml",
        effective_url="https://cdn.example.org/publications/feeds/current.xml",
    )

    assert items[0].link == "https://cdn.example.org/publications/articles/paper"


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
    assert request.headers["Accept-Encoding"] == "gzip, deflate, identity"
    assert request.headers["If-None-Match"] == '"prior"'
    assert request.headers["If-Modified-Since"] == "Thu, 02 Jul 2026 08:00:00 GMT"
    request_timeouts = request.extensions["timeout"]
    assert set(request_timeouts) == {"connect", "read", "write", "pool"}
    assert all(0 < timeout <= 25.0 for timeout in request_timeouts.values())
    assert request_timeouts["read"] <= feeds_module.READ_TIMEOUT_SLICE_SECONDS
    assert result == FeedFetchResult(
        content=None,
        etag=expected_etag,
        last_modified=expected_last_modified,
        not_modified=True,
        effective_url="https://example.org/feed.xml",
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
        effective_url="https://example.org/feed.xml",
    )


def test_fetch_feed_accepts_materialized_mock_transport_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<rss />",
            headers={"ETag": '"materialized"'},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch_feed(client, make_feed())

    assert result.content == b"<rss />"
    assert result.etag == '"materialized"'


def test_fetch_feed_accepts_content_consumed_by_response_hook() -> None:
    stream = TrackingStream([b"<rss />"])

    def consume(response: httpx.Response) -> None:
        response.read()

    with respx.mock:
        respx.get("https://example.org/feed.xml").mock(
            return_value=httpx.Response(200, stream=stream)
        )
        with httpx.Client(event_hooks={"response": [consume]}) as client:
            result = fetch_feed(client, make_feed())

    assert result.content == b"<rss />"
    assert stream.closed is True


def test_fetch_feed_rejects_oversized_materialized_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feeds_module, "MAX_FEED_BYTES", 5)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 6)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            feeds_module.FeedFetchError,
            match="consumed feed size.*maximum",
        ):
            fetch_feed(client, make_feed())


def test_fetch_feed_preserves_redirected_effective_url_for_relative_links() -> None:
    content = b"""\
        <feed xmlns="http://www.w3.org/2005/Atom">
          <title>Redirected feed</title><id>redirected</id>
          <updated>2026-07-01T00:00:00Z</updated>
          <entry><title>Redirected paper</title><id>paper</id>
            <link href="../articles/paper" />
            <updated>2026-07-01T00:00:00Z</updated>
          </entry>
        </feed>
    """
    final_url = "https://cdn.example.org/publications/feeds/current.xml"
    with respx.mock:
        respx.get("https://example.org/feed.xml").mock(
            return_value=httpx.Response(302, headers={"Location": final_url})
        )
        respx.get(final_url).mock(return_value=httpx.Response(200, content=content))
        with httpx.Client() as client:
            result = fetch_feed(client, make_feed())

    assert result.effective_url == final_url
    assert result.content is not None
    items = feeds_module.parse_feed_bytes(
        result.content,
        "fixture",
        make_feed().feed_url,
        effective_url=result.effective_url,
    )
    assert items[0].link == "https://cdn.example.org/publications/articles/paper"


def test_fetch_feed_rejects_final_http_url_after_redirect() -> None:
    insecure_url = "http://insecure.example.org/feed.xml"
    insecure_stream = TrackingStream([b"<rss />"])
    with respx.mock:
        respx.get("https://example.org/feed.xml").mock(
            return_value=httpx.Response(302, headers={"Location": insecure_url})
        )
        insecure_route = respx.get(insecure_url).mock(
            return_value=httpx.Response(200, stream=insecure_stream)
        )
        with httpx.Client() as client:
            with pytest.raises(
                feeds_module.FeedFetchError,
                match="final feed URL must use HTTPS",
            ):
                fetch_feed(client, make_feed())

    assert insecure_stream.iterated is False
    assert insecure_stream.closed is False
    assert insecure_route.called is False


def test_fetch_feed_rejects_initial_http_url_before_request() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"<rss />")

    insecure_feed = FeedConfig(
        id="insecure",
        name="Insecure Feed",
        publisher="nature",
        feed_url="http://insecure.example.org/feed.xml",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(
            feeds_module.FeedFetchError,
            match="initial feed URL must use HTTPS",
        ):
            fetch_feed(client, insecure_feed)

    assert calls == 0


def test_fetch_feed_rejects_intermediate_http_redirect_before_requesting_it() -> None:
    insecure_url = "http://insecure.example.org/intermediate.xml"
    final_url = "https://cdn.example.org/final.xml"
    with respx.mock(assert_all_called=False) as router:
        router.get("https://example.org/feed.xml").mock(
            return_value=httpx.Response(302, headers={"Location": insecure_url})
        )
        insecure_route = router.get(insecure_url).mock(
            return_value=httpx.Response(302, headers={"Location": final_url})
        )
        final_route = router.get(final_url).mock(
            return_value=httpx.Response(200, content=b"<rss />")
        )
        with httpx.Client() as client:
            with pytest.raises(
                feeds_module.FeedFetchError,
                match="redirect target must use HTTPS",
            ):
                fetch_feed(client, make_feed())

    assert insecure_route.called is False
    assert final_route.called is False


def test_fetch_feed_rejects_redirect_loop_at_configured_limit() -> None:
    with respx.mock:
        route = respx.get("https://example.org/feed.xml").mock(
            return_value=httpx.Response(302, headers={"Location": "/feed.xml"})
        )
        with httpx.Client() as client:
            with pytest.raises(
                feeds_module.FeedFetchError,
                match="redirect limit exceeded",
            ):
                fetch_feed(client, make_feed())

    assert route.call_count == feeds_module.MAX_REDIRECTS + 1


def test_fetch_feed_closes_oversized_redirect_response_without_consuming_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feeds_module, "MAX_FEED_BYTES", 5)
    redirect_stream = TrackingStream([b"x" * 6])
    final_url = "https://cdn.example.org/final.xml"
    with respx.mock:
        respx.get("https://example.org/feed.xml").mock(
            return_value=httpx.Response(
                302,
                headers={"Location": final_url},
                stream=redirect_stream,
            )
        )
        respx.get(final_url).mock(return_value=httpx.Response(200, content=b"<x/>"))
        with httpx.Client() as client:
            result = fetch_feed(client, make_feed())

    assert result.effective_url == final_url
    assert redirect_stream.iterated is False
    assert redirect_stream.closed is True


@pytest.mark.parametrize(
    ("auth", "authorization"),
    [
        (httpx.BasicAuth("reader", "secret"), None),
        (None, "Bearer static-token"),
    ],
)
def test_cross_origin_redirect_sanitizes_credentials_host_cookies_and_params(
    auth,
    authorization: str | None,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                302,
                headers={"Location": "https://destination.example/final?from=location"},
            )
        return httpx.Response(200, content=b"<rss />")

    headers = {
        "Cookie": "static-cookie=must-not-leak",
        "Host": "origin.example",
    }
    if authorization is not None:
        headers["Authorization"] = authorization
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        auth=auth,
        headers=headers,
        params={"client_param": "initial-only"},
    )
    client.cookies.set("origin-cookie", "origin", domain="origin.example", path="/")
    client.cookies.set(
        "destination-cookie",
        "destination",
        domain="destination.example",
        path="/",
    )
    feed = FeedConfig(
        id="origin",
        name="Origin Feed",
        publisher="nature",
        feed_url="https://origin.example/feed.xml",
    )
    try:
        result = fetch_feed(client, feed)
    finally:
        client.close()

    assert result.content == b"<rss />"
    assert requests[0].url.query == b"client_param=initial-only"
    assert requests[0].headers["Cookie"] == "static-cookie=must-not-leak"
    assert requests[0].headers["Host"] == "origin.example"
    assert "Authorization" in requests[0].headers
    assert requests[1].url == httpx.URL(
        "https://destination.example/final?from=location"
    )
    assert "Authorization" not in requests[1].headers
    assert requests[1].headers["Host"] == "destination.example"
    assert "static-cookie" not in requests[1].headers.get("Cookie", "")
    assert "origin-cookie" not in requests[1].headers.get("Cookie", "")
    assert "destination-cookie=destination" in requests[1].headers["Cookie"]


def test_same_origin_redirect_preserves_auth_and_jar_cookie_without_reapplying_params() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={"Location": "/final?from=location"})
        return httpx.Response(200, content=b"<rss />")

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        auth=httpx.BasicAuth("reader", "secret"),
        headers={"Cookie": "static-cookie=initial-only"},
        params={"client_param": "initial-only"},
    )
    client.cookies.set("session", "same-origin", domain="example.org", path="/")
    try:
        result = fetch_feed(
            client,
            make_feed(),
            etag='"prior"',
            last_modified="Thu, 02 Jul 2026 08:00:00 GMT",
        )
    finally:
        client.close()

    assert result.content == b"<rss />"
    assert requests[0].url.query == b"client_param=initial-only"
    assert requests[0].headers["Authorization"].startswith("Basic ")
    assert requests[1].url == httpx.URL("https://example.org/final?from=location")
    assert requests[1].headers["Authorization"] == requests[0].headers["Authorization"]
    assert requests[1].headers["Host"] == "example.org"
    assert "session=same-origin" in requests[1].headers["Cookie"]
    assert "static-cookie" not in requests[1].headers["Cookie"]
    assert requests[1].headers["If-None-Match"] == '"prior"'
    assert requests[1].headers["If-Modified-Since"] == "Thu, 02 Jul 2026 08:00:00 GMT"


def test_owned_client_applies_default_params_only_to_initial_request() -> None:
    with respx.mock:
        initial_route = respx.get(
            "https://example.org/feed.xml",
            params={"client_param": "initial-only"},
        ).mock(
            return_value=httpx.Response(
                302,
                headers={"Location": "/final?from=location"},
            )
        )
        final_route = respx.get("https://example.org/final?from=location").mock(
            return_value=httpx.Response(200, content=b"<rss />")
        )
        with httpx.Client(
            trust_env=False,
            params={"client_param": "initial-only"},
        ) as client:
            result = fetch_feed(client, make_feed())

    assert result.content == b"<rss />"
    assert initial_route.called is True
    assert final_route.called is True


def test_owned_response_cookie_deletion_and_addition_sync_to_caller() -> None:
    with respx.mock:
        respx.get("https://example.org/feed.xml").mock(
            return_value=httpx.Response(
                200,
                content=b"<rss />",
                headers=[
                    ("Set-Cookie", "session=; Max-Age=0; Path=/"),
                    ("Set-Cookie", "added=new-value; Path=/"),
                ],
            )
        )
        with httpx.Client(trust_env=False) as client:
            client.cookies.set("session", "old-value", domain="example.org", path="/")
            result = fetch_feed(client, make_feed())

            assert result.content == b"<rss />"
            assert client.cookies.get("session", domain="example.org", path="/") is None
            assert client.cookies.get("added", domain="example.org", path="/") == "new-value"


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


def test_fetch_feed_rejects_declared_oversized_body_before_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feeds_module, "MAX_FEED_BYTES", 5, raising=False)
    stream = TrackingStream([b"should not be consumed"])
    with respx.mock:
        respx.get("https://example.org/feed.xml").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Length": "6"},
                stream=stream,
            )
        )
        with httpx.Client() as client:
            with pytest.raises(RuntimeError, match="declared feed size.*maximum") as raised:
                fetch_feed(client, make_feed())

    assert isinstance(raised.value, feeds_module.FeedFetchError)
    assert stream.iterated is False
    assert stream.closed is True


def test_fetch_feed_rejects_oversized_decoded_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feeds_module, "MAX_FEED_BYTES", 100, raising=False)
    stream = TrackingStream([gzip.compress(b"x" * 101)])
    with respx.mock:
        respx.get("https://example.org/feed.xml").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                stream=stream,
            )
        )
        with httpx.Client() as client:
            with pytest.raises(RuntimeError, match="decoded feed size.*maximum") as raised:
                fetch_feed(client, make_feed())

    assert isinstance(raised.value, feeds_module.FeedFetchError)
    assert stream.iterated is True
    assert stream.closed is True


def test_fetch_feed_enforces_total_elapsed_deadline_without_sleeping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter([100.0, 100.2, 100.3, 102.0])
    monkeypatch.setattr(feeds_module, "monotonic", lambda: next(times, 102.0), raising=False)
    monkeypatch.setattr(feeds_module, "FETCH_DEADLINE_SECONDS", 1.0, raising=False)
    stream = TrackingStream([b"<rss />"])
    with respx.mock:
        respx.get("https://example.org/feed.xml").mock(
            return_value=httpx.Response(200, stream=stream)
        )
        with httpx.Client() as client:
            with pytest.raises(RuntimeError, match="total fetch deadline") as raised:
                fetch_feed(client, make_feed())

    assert isinstance(raised.value, feeds_module.FeedFetchError)
    assert stream.closed is True


def test_fetch_feed_rejects_unsupported_content_encoding_before_streaming() -> None:
    stream = TrackingStream([b"unsupported"])
    with respx.mock:
        respx.get("https://example.org/feed.xml").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Encoding": "br"},
                stream=stream,
            )
        )
        with httpx.Client() as client:
            with pytest.raises(
                feeds_module.FeedFetchError,
                match="unsupported Content-Encoding",
            ):
                fetch_feed(client, make_feed())

    assert stream.iterated is False
    assert stream.closed is True


@pytest.mark.parametrize("boundary", [1, 2, 3, 5])
def test_fetch_feed_decodes_raw_deflate_across_arbitrary_chunk_boundaries(
    boundary: int,
) -> None:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    encoded = compressor.compress(b"<rss />") + compressor.flush()
    stream = TrackingStream([encoded[:boundary], encoded[boundary:]])
    with respx.mock:
        respx.get("https://example.org/feed.xml").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Encoding": "deflate"},
                stream=stream,
            )
        )
        with httpx.Client() as client:
            result = fetch_feed(client, make_feed())

    assert result.content == b"<rss />"


@pytest.mark.parametrize(
    ("content_encoding", "encoded"),
    [
        ("deflate", zlib.compress(b"<rss />")),
        ("gzip", gzip.compress(b"<rss />")),
    ],
)
def test_fetch_feed_decodes_wrapped_compression_regressions(
    content_encoding: str,
    encoded: bytes,
) -> None:
    stream = TrackingStream([encoded[:1], encoded[1:]])
    with respx.mock:
        respx.get("https://example.org/feed.xml").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Encoding": content_encoding},
                stream=stream,
            )
        )
        with httpx.Client() as client:
            result = fetch_feed(client, make_feed())

    assert result.content == b"<rss />"


def test_fetch_feed_caps_each_gzip_decoder_output_during_compression_bomb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(feeds_module, "MAX_FEED_BYTES", 2_000)
    monkeypatch.setattr(feeds_module, "DECODE_CHUNK_BYTES", 16, raising=False)
    observed: list[tuple[int, int]] = []

    def observing_decompress(decoder, data: bytes, max_output: int) -> bytes:
        output = decoder.decompress(data, max_output)
        observed.append((max_output, len(output)))
        return output

    monkeypatch.setattr(
        feeds_module,
        "_bounded_decompress",
        observing_decompress,
        raising=False,
    )
    stream = TrackingStream([gzip.compress(b"x" * 1_000_000)])
    with respx.mock:
        respx.get("https://example.org/feed.xml").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                stream=stream,
            )
        )
        with httpx.Client() as client:
            with pytest.raises(feeds_module.FeedFetchError, match="decoded feed size"):
                fetch_feed(client, make_feed())

    assert observed
    assert max(requested for requested, _ in observed) <= 16
    assert max(produced for _, produced in observed) <= 16


def test_fetch_feed_propagates_decreasing_remaining_timeouts_without_sleeping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr(feeds_module, "monotonic", lambda: now[0])
    monkeypatch.setattr(feeds_module, "FETCH_DEADLINE_SECONDS", 12.0)
    monkeypatch.setattr(feeds_module, "READ_TIMEOUT_SLICE_SECONDS", 1.0, raising=False)
    hop_timeouts: list[dict[str, float]] = []
    read_timeouts: list[float] = []

    class TimeoutAwareStream(httpx.SyncByteStream):
        def __init__(self, request: httpx.Request) -> None:
            self.request = request

        def __iter__(self):
            for chunk in (b"<rss", b" />"):
                read_timeouts.append(self.request.extensions["timeout"]["read"])
                now[0] += 0.25
                yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        hop_timeouts.append(dict(request.extensions["timeout"]))
        if len(hop_timeouts) == 1:
            now[0] += 4.0
            return httpx.Response(302, headers={"Location": "/final.xml"})
        return httpx.Response(200, stream=TimeoutAwareStream(request))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch_feed(client, make_feed())

    assert result.content == b"<rss />"
    assert len(hop_timeouts) == 2
    assert hop_timeouts[0]["connect"] <= 3.0
    assert hop_timeouts[1]["connect"] < hop_timeouts[0]["connect"]
    assert read_timeouts
    assert all(timeout <= 1.0 for timeout in read_timeouts)
    assert read_timeouts == sorted(read_timeouts, reverse=True)


def test_send_request_absolute_deadline_aborts_slow_drip_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = getattr(feeds_module, "_send_request_with_deadline", None)
    assert sender is not None, "absolute-deadline request wrapper is required"

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(1.0)
    stop = Event()

    def serve_slow_headers() -> None:
        try:
            connection, _ = listener.accept()
        except (OSError, TimeoutError):
            return
        with connection:
            connection.settimeout(1.0)
            request = bytearray()
            try:
                while b"\r\n\r\n" not in request:
                    request.extend(connection.recv(4096))
                connection.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                while not stop.wait(0.02):
                    connection.sendall(b"a")
            except OSError:
                pass

    server_thread = Thread(target=serve_slow_headers, daemon=True)
    server_thread.start()
    monkeypatch.setattr(feeds_module, "FETCH_DEADLINE_SECONDS", 0.15)
    url = httpx.URL(f"http://127.0.0.1:{listener.getsockname()[1]}/feed.xml")
    started_at = feeds_module.monotonic()
    wall_started = perf_counter()
    try:
        with httpx.Client(trust_env=False) as client:
            owned_client = feeds_module._owned_direct_client(client, url)
            assert owned_client is not None
            try:
                request = owned_client.build_request(
                    "GET",
                    url,
                    extensions={
                        "timeout": {
                            "connect": 1.0,
                            "read": 1.0,
                            "write": 1.0,
                            "pool": 1.0,
                        }
                    },
                )
                with pytest.raises(feeds_module.FeedFetchError, match="total fetch deadline"):
                    sender(owned_client, request, started_at)
            finally:
                owned_client.close()
    finally:
        stop.set()
        listener.close()
        server_thread.join(timeout=1.0)

    assert perf_counter() - wall_started < 0.75


def test_header_deadline_closes_response_returned_late_by_custom_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = Event()
    release = Event()
    closed = Event()

    class LateStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"<rss />"

        def close(self) -> None:
            closed.set()

    def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        release.wait(timeout=1.0)
        return httpx.Response(200, stream=LateStream())

    monkeypatch.setattr(feeds_module, "FETCH_DEADLINE_SECONDS", 0.05)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(feeds_module.FeedFetchError, match="total fetch deadline"):
            fetch_feed(client, make_feed())
        assert entered.is_set()
        release.set()
        assert closed.wait(timeout=0.5)
    finally:
        release.set()
        client.close()


@pytest.mark.parametrize("mounted", [False, True])
def test_custom_transport_remains_usable_after_timeout_and_late_response_closes(
    mounted: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = Event()
    release = Event()
    late_closed = Event()
    seen_headers: list[httpx.Headers] = []

    class LateStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"late"

        def close(self) -> None:
            late_closed.set()

    class StatefulTransport(httpx.BaseTransport):
        def __init__(self) -> None:
            self.calls = 0
            self.close_calls = 0
            self.closed = False

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            if self.closed:
                raise RuntimeError("transport was closed")
            self.calls += 1
            seen_headers.append(request.headers)
            if self.calls == 1:
                entered.set()
                release.wait(timeout=1.0)
                return httpx.Response(200, stream=LateStream())
            return httpx.Response(200, content=b"<rss />")

        def close(self) -> None:
            self.close_calls += 1
            self.closed = True

    class UnrelatedTransport(httpx.BaseTransport):
        def __init__(self) -> None:
            self.close_calls = 0

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"unrelated-ok")

        def close(self) -> None:
            self.close_calls += 1

    transport = StatefulTransport()
    unrelated = UnrelatedTransport()
    client_kwargs = {
        "headers": {"X-Client": "preserved"},
        "cookies": {"session": "cookie-value"},
        "mounts": {"https://unrelated.example": unrelated},
    }
    if mounted:
        client_kwargs["mounts"]["https://example.org"] = transport
        client = httpx.Client(
            **client_kwargs,
        )
    else:
        client = httpx.Client(transport=transport, **client_kwargs)
    try:
        monkeypatch.setattr(feeds_module, "FETCH_DEADLINE_SECONDS", 0.05)
        with pytest.raises(feeds_module.FeedFetchError, match="total fetch deadline"):
            fetch_feed(client, make_feed())
        assert entered.is_set()
        assert transport.close_calls == 0

        unrelated_response = client.get("https://unrelated.example/ping")
        assert unrelated_response.content == b"unrelated-ok"
        assert unrelated.close_calls == 0

        release.set()
        assert late_closed.wait(timeout=0.5)

        monkeypatch.setattr(feeds_module, "FETCH_DEADLINE_SECONDS", 1.0)
        result = fetch_feed(client, make_feed())
        assert result.content == b"<rss />"
        assert transport.close_calls == 0
        assert seen_headers[-1]["X-Client"] == "preserved"
        assert "session=cookie-value" in seen_headers[-1]["Cookie"]
    finally:
        release.set()
        client.close()
