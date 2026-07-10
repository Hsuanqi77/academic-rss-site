import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from paper_radar.enrich import (
    MAX_METADATA_BYTES,
    USER_AGENT,
    EnrichmentResponseError,
    enrich_article,
)
from paper_radar.models import ArticleRecord


def _article(**changes: Any) -> ArticleRecord:
    article = ArticleRecord(
        uid="doi:10.1000/example",
        doi="10.1000/example",
        journal_id="example",
        title="Meaningful RSS title",
        abstract=None,
        authors=(),
        published_at="2026-01-01T00:00:00+00:00",
        article_type="other",
        article_url="https://example.test/article",
        normalized_url="https://example.test/article",
        oa_status="unknown",
        source_feed_url="https://example.test/feed",
        metadata_status="rss_only",
    )
    return replace(article, **changes)


def _json_response(request: httpx.Request, payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=request)


def test_enrich_fills_missing_crossref_fields_and_open_access() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.crossref.org":
            return _json_response(
                request,
                {
                    "message": {
                        "title": ["Crossref title must not replace RSS"],
                        "author": [
                            {"given": " Alice ", "family": "Müller"},
                            {"name": " Example Consortium "},
                            {"given": "ALICE", "family": "MÜLLER"},
                            {"given": 42, "family": None},
                        ],
                        "abstract": "<jats:p>An &lt;b&gt;AlScN&lt;/b&gt; abstract.</jats:p>",
                        "type": "journal-article",
                    }
                },
            )
        return _json_response(request, {"is_oa": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, _article(), unpaywall_email="reader@example.org")

    assert result.title == "Meaningful RSS title"
    assert result.authors == ("Alice Müller", "Example Consortium")
    assert result.abstract == "An AlScN abstract."
    assert result.article_type == "research"
    assert result.oa_status == "open"
    assert result.metadata_status == "enriched"
    assert result.enriched_fields == ("authors", "abstract", "article_type", "oa_status")
    assert len(requests) == 2


def test_enrich_crossref_http_failure_preserves_record_and_marks_partial() -> None:
    article = _article()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, article)

    assert result == replace(article, metadata_status="partial")


def test_enrich_crossref_failure_does_not_block_unpaywall_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.crossref.org":
            return httpx.Response(503, request=request)
        return _json_response(request, {"is_oa": False})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, _article(), unpaywall_email="reader@example.org")

    assert result.oa_status == "closed"
    assert result.metadata_status == "partial"
    assert result.enriched_fields == ("oa_status",)


def test_enrich_unpaywall_failure_keeps_successful_crossref_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.crossref.org":
            return _json_response(
                request,
                {"message": {"author": [{"given": "Ada", "family": "Lovelace"}]}},
            )
        return httpx.Response(500, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, _article(), unpaywall_email="reader@example.org")

    assert result.authors == ("Ada Lovelace",)
    assert result.oa_status == "unknown"
    assert result.metadata_status == "partial"
    assert result.enriched_fields == ("authors",)


def test_enrich_without_doi_returns_identical_object_without_requests() -> None:
    article = _article(doi=None, uid="url:abc")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _json_response(request, {})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, article, unpaywall_email="reader@example.org")

    assert result is article
    assert calls == 0


def test_crossref_success_without_contributed_fields_marks_source_enriched_only() -> None:
    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return _json_response(request, {"message": {}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, _article())

    assert requested_hosts == ["api.crossref.org"]
    assert result.metadata_status == "enriched"
    assert result.enriched_fields == ()


def test_crossref_placeholder_title_is_not_recorded_as_enriched() -> None:
    article = _article(title="Untitled")

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(request, {"message": {"title": [" Untitled "]}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, article)

    assert result.title == "Untitled"
    assert result.metadata_status == "enriched"
    assert result.enriched_fields == ()


@pytest.mark.parametrize("placeholder", ["Untitled.", "Unknown", "N/A.", "Not available!"])
def test_crossref_explicit_title_placeholders_never_replace_or_gain_provenance(
    placeholder: str,
) -> None:
    article = _article(title="Untitled")

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(request, {"message": {"title": [placeholder]}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, article)

    assert result.title == "Untitled"
    assert result.metadata_status == "enriched"
    assert result.enriched_fields == ()


def test_crossref_title_skips_placeholders_before_using_meaningful_candidate() -> None:
    article = _article(title="Unknown.")

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            request,
            {"message": {"title": ["Untitled.", "N/A", "Meaningful title"]}},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, article)

    assert result.title == "Meaningful title"
    assert result.enriched_fields == ("title",)


def test_crossref_low_quality_placeholders_are_not_recorded_as_enriched() -> None:
    article = _article()

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            request,
            {
                "message": {
                    "author": [{"name": "Unknown"}],
                    "abstract": "No abstract available.",
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, article)

    assert result.authors == ()
    assert result.abstract is None
    assert result.metadata_status == "enriched"
    assert result.enriched_fields == ()


def test_crossref_unknown_abstract_is_not_recorded_as_enriched() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(request, {"message": {"abstract": "Unknown."}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, _article())

    assert result.abstract is None
    assert result.metadata_status == "enriched"
    assert result.enriched_fields == ()


def test_crossref_filters_placeholder_authors_individually_before_assignment() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            request,
            {
                "message": {
                    "author": [
                        {"name": "Unknown."},
                        {"given": "Ada", "family": "Lovelace"},
                        {"name": "N/A"},
                    ]
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, _article())

    assert result.authors == ("Ada Lovelace",)
    assert result.enriched_fields == ("authors",)


@pytest.mark.parametrize(
    ("payload", "headers"),
    [
        (b"not json", {}),
        (json.dumps([]).encode(), {}),
        (json.dumps({"message": []}).encode(), {}),
        (b"{}", {"Content-Length": str(MAX_METADATA_BYTES + 1)}),
        (b"x" * (MAX_METADATA_BYTES + 1), {}),
    ],
    ids=["invalid-json", "root-list", "message-list", "declared-oversize", "body-oversize"],
)
def test_enrich_malformed_or_oversize_crossref_response_is_partial(
    payload: bytes, headers: dict[str, str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers=headers, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, _article())

    assert result == replace(_article(), metadata_status="partial")


def test_enrich_deep_json_recursion_is_an_expected_partial_failure() -> None:
    depth = 5_000
    content = b'{"message":' + b"[" * depth + b"0" + b"]" * depth + b"}"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, _article())

    assert result == replace(_article(), metadata_status="partial")


def test_enrich_request_error_is_graceful() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, _article())

    assert result.metadata_status == "partial"


def test_crossref_redirect_is_not_followed_even_when_client_default_follows() -> None:
    target_called = False
    article = _article()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal target_called
        if request.url.host == "api.crossref.org":
            return httpx.Response(
                302,
                headers={"Location": "http://169.254.169.254/latest/meta-data"},
                request=request,
            )
        target_called = True
        return _json_response(request, {"message": {"title": ["stolen"]}})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result = enrich_article(client, article)

    assert target_called is False
    assert result == replace(article, metadata_status="partial")


def test_unpaywall_redirect_is_not_followed_even_when_client_default_follows() -> None:
    target_called = False
    article = _article()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal target_called
        if request.url.host == "api.crossref.org":
            return _json_response(request, {"message": {}})
        if request.url.host == "api.unpaywall.org":
            return httpx.Response(
                302,
                headers={"Location": "http://169.254.169.254/latest/meta-data"},
                request=request,
            )
        target_called = True
        return _json_response(request, {"is_oa": True})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        result = enrich_article(client, article, unpaywall_email="reader@example.org")

    assert target_called is False
    assert result == replace(article, metadata_status="partial")


def test_malformed_crossref_unicode_text_is_an_expected_partial_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"message":{"abstract":"\\ud800"}}',
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, _article())

    assert result == replace(_article(), metadata_status="partial")


def test_malformed_crossref_type_text_is_an_expected_partial_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"message":{"type":"\\ud800"}}',
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, _article())

    assert result == replace(_article(), metadata_status="partial")


@pytest.mark.parametrize(
    "unexpected_error",
    [
        AssertionError("assertion bug"),
        AttributeError("attribute bug"),
        RuntimeError("runtime bug"),
        MemoryError("memory pressure"),
    ],
    ids=["assertion", "attribute", "runtime", "memory"],
)
def test_enrich_propagates_unexpected_programming_and_resource_errors(
    unexpected_error: Exception,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise unexpected_error

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(type(unexpected_error), match=str(unexpected_error)):
            enrich_article(client, _article())


def test_enrichment_response_error_is_a_dedicated_expected_failure() -> None:
    assert issubclass(EnrichmentResponseError, Exception)
    assert not issubclass(EnrichmentResponseError, httpx.HTTPError)


def test_enrich_invalid_doi_text_is_graceful_without_requests() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _json_response(request, {})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, _article(doi="10.1000/\ud800"))

    assert calls == 0
    assert result == replace(_article(doi="10.1000/\ud800"), metadata_status="partial")


def test_enrich_strips_doi_for_request_path_without_mutating_record() -> None:
    requests: list[httpx.Request] = []
    article = _article(doi="  10.1000/example  ")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _json_response(request, {"message": {}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, article)

    assert requests[0].url.raw_path.endswith(b"10.1000%2Fexample")
    assert result.doi == article.doi


@pytest.mark.parametrize(
    "invalid_email",
    [
        "not-an-email",
        "reader@example",
        "reader@@example.org",
        "reader@example..org",
        "x@y.z\nBcc:q",
        ".reader@example.org",
        "reader.@example.org",
        "read..er@example.org",
        "réader@example.org",
        "reader@exam_ple.org",
        "reader@-example.org",
        "reader@example-.org",
        f"{'a' * 65}@example.org",
        f"reader@{'a' * 64}.org",
        f"{'a' * 64}@{'b' * 63}.{'c' * 63}.{'d' * 63}.org",
    ],
)
def test_enrich_rejects_nonblank_invalid_unpaywall_email_before_requests(
    invalid_email: str,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _json_response(request, {"message": {}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="Unpaywall email"):
            enrich_article(client, _article(), unpaywall_email=invalid_email)

    assert calls == 0


@pytest.mark.parametrize(
    "valid_email",
    ["me@example.org", "me+tag@example.org", "m.e@example-domain.org"],
)
def test_enrich_accepts_ascii_dot_atom_email_with_dns_domain(valid_email: str) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.crossref.org":
            return _json_response(request, {"message": {}})
        return _json_response(request, {"is_oa": False})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        enrich_article(client, _article(), unpaywall_email=valid_email)

    assert len(requests) == 2
    assert requests[1].url.params["email"] == valid_email


@pytest.mark.parametrize("email", [None, "", "  "])
def test_blank_unpaywall_email_is_an_explicit_opt_out(email: str | None) -> None:
    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return _json_response(request, {"message": {}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        enrich_article(client, _article(), unpaywall_email=email)

    assert requested_hosts == ["api.crossref.org"]


@pytest.mark.parametrize("payload", [{}, {"is_oa": 1}, {"is_oa": "true"}])
def test_enrich_malformed_unpaywall_boolean_is_a_partial_failure(payload: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.crossref.org":
            return _json_response(request, {"message": {}})
        return _json_response(request, payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, _article(), unpaywall_email="reader@example.org")

    assert result.oa_status == "unknown"
    assert result.metadata_status == "partial"


def test_enrich_preserves_existing_values_but_can_replace_placeholder_title() -> None:
    article = _article(
        title="Untitled",
        abstract="RSS abstract",
        authors=("RSS Author",),
        article_type="review",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            request,
            {
                "message": {
                    "title": ["Crossref title"],
                    "abstract": "Crossref abstract",
                    "author": [{"given": "Crossref", "family": "Author"}],
                    "type": "journal-article",
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, article)

    assert result.title == "Crossref title"
    assert result.abstract == "RSS abstract"
    assert result.authors == ("RSS Author",)
    assert result.article_type == "review"
    assert result.enriched_fields == ("title",)


def test_enrich_replaces_low_quality_authors_and_deduplicates_unicode_names() -> None:
    article = _article(authors=("Unknown",))

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            request,
            {
                "message": {
                    "author": [
                        {"given": "Jose\u0301", "family": "García"},
                        {"given": "JOSÉ", "family": "GARCÍA"},
                        {"name": "Research Group"},
                        None,
                    ]
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, article)

    assert result.authors == ("José García", "Research Group")
    assert result.enriched_fields == ("authors",)


def test_enrich_replaces_common_punctuated_metadata_placeholders() -> None:
    article = _article(authors=("Unknown author(s)",), abstract="No abstract available.")

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(
            request,
            {
                "message": {
                    "author": [{"given": "Grace", "family": "Hopper"}],
                    "abstract": "<p>Useful abstract.</p>",
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, article)

    assert result.authors == ("Grace Hopper",)
    assert result.abstract == "Useful abstract."
    assert result.enriched_fields == ("authors", "abstract")


def test_enrich_does_not_downgrade_existing_status_or_known_oa_on_failure() -> None:
    article = _article(
        metadata_status="enriched",
        oa_status="open",
        authors=("Known",),
        enriched_fields=("oa_status",),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, article, unpaywall_email="reader@example.org")

    assert result == article


def test_enrich_encodes_entire_doi_and_sends_deterministic_requests() -> None:
    article = _article(doi="10.1000/slash/?# café")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.crossref.org":
            return _json_response(request, {"message": {}})
        return _json_response(request, {"is_oa": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = enrich_article(client, article, unpaywall_email="reader@example.org")

    encoded = b"10.1000%2Fslash%2F%3F%23%20caf%C3%A9"
    assert requests[0].url.raw_path.endswith(encoded)
    assert requests[1].url.raw_path.endswith(encoded + b"?email=reader%40example.org")
    assert all(request.headers["User-Agent"] == USER_AGENT for request in requests)
    assert all(
        request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 20.0)
        for request in requests
    )
    assert result.uid == article.uid
    assert result.doi == article.doi
    assert result.journal_id == article.journal_id
    assert result.article_url == article.article_url
    assert result.normalized_url == article.normalized_url
    assert result.source_feed_url == article.source_feed_url


@pytest.mark.parametrize("control_exception", [KeyboardInterrupt(), SystemExit()])
def test_enrich_allows_process_control_exceptions_to_propagate(
    control_exception: BaseException,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise control_exception

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(type(control_exception)):
            enrich_article(client, _article())
