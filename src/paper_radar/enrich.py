import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any
from urllib.parse import quote

import httpx

from paper_radar.models import (
    ArticleRecord,
    enriched_field_has_meaningful_value,
    higher_metadata_status,
    normalize_enriched_fields,
)
from paper_radar.normalize import clean_text, normalize_article_type


USER_AGENT = "paper-radar/0.1 (+personal academic RSS reader)"
MAX_METADATA_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 20.0
# httpx applies this timeout to individual network phases; it is not a total wall-clock budget.

_EMAIL_LOCAL_PATTERN = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$"
)
_DNS_LABEL_PATTERN = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


class EnrichmentResponseError(ValueError):
    """Raised when a metadata service returns an unusable response."""


def enrich_article(
    client: httpx.Client,
    article: ArticleRecord,
    *,
    unpaywall_email: str | None = None,
) -> ArticleRecord:
    if not isinstance(article.doi, str) or not article.doi.strip():
        return article

    email = _optional_unpaywall_email(unpaywall_email)
    request_doi = article.doi.strip()
    try:
        encoded_doi = quote(request_doi, safe="")
    except (UnicodeError, ValueError):
        metadata_status = higher_metadata_status(article.metadata_status, "partial")
        return replace(article, metadata_status=metadata_status)
    enriched = article
    source_results: list[bool] = []
    contributed_fields: set[str] = set()

    try:
        crossref_payload = _fetch_json(
            client,
            f"https://api.crossref.org/works/{encoded_doi}",
        )
        crossref_message = _crossref_message(crossref_payload)
        enriched, crossref_fields = _apply_crossref(enriched, crossref_message)
        contributed_fields.update(crossref_fields)
    except (httpx.HTTPError, EnrichmentResponseError):
        source_results.append(False)
    else:
        source_results.append(True)

    if email is not None:
        try:
            unpaywall_payload = _fetch_json(
                client,
                f"https://api.unpaywall.org/v2/{encoded_doi}",
                params={"email": email},
            )
            is_oa = _unpaywall_is_oa(unpaywall_payload)
            contributed_fields.add("oa_status")
            enriched = replace(
                enriched,
                oa_status="open" if is_oa else "closed",
                enriched_fields=_with_enriched_fields(enriched, contributed_fields),
            )
        except (httpx.HTTPError, EnrichmentResponseError):
            source_results.append(False)
        else:
            source_results.append(True)

    if not all(source_results):
        requested_status = "partial"
    else:
        requested_status = "enriched"
    metadata_status = higher_metadata_status(enriched.metadata_status, requested_status)
    return replace(enriched, metadata_status=metadata_status)


def _fetch_json(
    client: httpx.Client,
    url: str,
    *,
    params: Mapping[str, str] | None = None,
) -> Any:
    with client.stream(
        "GET",
        url,
        params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=False,
    ) as response:
        response.raise_for_status()
        _reject_declared_oversize(response)
        content = bytearray()
        for chunk in response.iter_bytes():
            if len(content) + len(chunk) > MAX_METADATA_BYTES:
                raise EnrichmentResponseError("metadata response exceeds maximum size")
            content.extend(chunk)
    try:
        return json.loads(content)
    except (json.JSONDecodeError, RecursionError, UnicodeError) as exc:
        raise EnrichmentResponseError("metadata response contains invalid JSON") from exc


def _reject_declared_oversize(response: httpx.Response) -> None:
    content_length = response.headers.get("Content-Length")
    if content_length is None:
        return
    try:
        declared_size = int(content_length)
    except ValueError:
        return
    if declared_size > MAX_METADATA_BYTES:
        raise EnrichmentResponseError("declared metadata response exceeds maximum size")


def _crossref_message(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise EnrichmentResponseError("Crossref response root must be an object")
    message = payload.get("message")
    if not isinstance(message, Mapping):
        raise EnrichmentResponseError("Crossref response message must be an object")
    return message


def _apply_crossref(
    article: ArticleRecord, message: Mapping[str, Any]
) -> tuple[ArticleRecord, tuple[str, ...]]:
    contributed_fields: set[str] = set()
    title = article.title
    crossref_title = _crossref_title(message.get("title"))
    if _is_placeholder_title(title) and crossref_title is not None:
        title = crossref_title
        contributed_fields.add("title")

    authors = article.authors
    crossref_authors = _crossref_authors(message.get("author"))
    if (
        _authors_are_low_quality(authors)
        and crossref_authors
        and not _authors_are_low_quality(crossref_authors)
    ):
        authors = crossref_authors
        contributed_fields.add("authors")

    abstract = article.abstract
    crossref_abstract = _clean_string(message.get("abstract"))
    if (
        _abstract_is_low_quality(abstract)
        and crossref_abstract is not None
        and not _abstract_is_low_quality(crossref_abstract)
    ):
        abstract = crossref_abstract
        contributed_fields.add("abstract")

    article_type = article.article_type
    crossref_type = _clean_string(message.get("type"))
    if article_type.casefold() == "other" and crossref_type is not None:
        normalized_type = normalize_article_type(crossref_type)
        if normalized_type != "other":
            article_type = normalized_type
            contributed_fields.add("article_type")

    contributed = normalize_enriched_fields(contributed_fields)
    return (
        replace(
            article,
            title=title,
            authors=authors,
            abstract=abstract,
            article_type=article_type,
            enriched_fields=_with_enriched_fields(article, contributed),
        ),
        contributed,
    )


def _with_enriched_fields(article: ArticleRecord, fields: Iterable[str]) -> tuple[str, ...]:
    return normalize_enriched_fields((*article.enriched_fields, *fields))


def _crossref_title(value: Any) -> str | None:
    candidates = value if isinstance(value, (list, tuple)) else (value,)
    for candidate in candidates:
        title = _clean_string(candidate)
        if title is not None and not _is_placeholder_title(title):
            return title
    return None


def _crossref_authors(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    authors: list[str] = []
    seen: set[str] = set()
    for row in value:
        if not isinstance(row, Mapping):
            continue
        try:
            given = _clean_string(row.get("given"))
            family = _clean_string(row.get("family"))
            organization = _clean_string(row.get("name"))
        except EnrichmentResponseError:
            continue
        if given is not None or family is not None:
            author = " ".join(part for part in (given, family) if part is not None)
        else:
            author = organization
        if author is None or not enriched_field_has_meaningful_value("authors", (author,)):
            continue
        identity = author.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        authors.append(author)
    return tuple(authors)


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        value.encode("utf-8")
        return clean_text(value)
    except (UnicodeError, ValueError) as exc:
        raise EnrichmentResponseError("metadata response contains malformed text") from exc


def _is_placeholder_title(value: str) -> bool:
    title = clean_text(value)
    return not enriched_field_has_meaningful_value("title", title)


def _authors_are_low_quality(authors: tuple[str, ...]) -> bool:
    cleaned = tuple(clean_text(author) for author in authors)
    return not enriched_field_has_meaningful_value("authors", cleaned)


def _abstract_is_low_quality(value: str | None) -> bool:
    abstract = clean_text(value)
    return not enriched_field_has_meaningful_value("abstract", abstract)


def _optional_unpaywall_email(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Unpaywall email must be a string or None")
    if not value.strip():
        return None
    if value != value.strip() or len(value) > 254 or value.count("@") != 1:
        raise ValueError("Unpaywall email must be a syntactically valid nonblank address")
    local, domain = value.split("@")
    labels = domain.split(".")
    if (
        len(local) > 64
        or len(domain) > 253
        or _EMAIL_LOCAL_PATTERN.fullmatch(local) is None
        or len(labels) < 2
        or any(_DNS_LABEL_PATTERN.fullmatch(label) is None for label in labels)
    ):
        raise ValueError("Unpaywall email must be a syntactically valid nonblank address")
    return value


def _unpaywall_is_oa(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        raise EnrichmentResponseError("Unpaywall response root must be an object")
    is_oa = payload.get("is_oa")
    if type(is_oa) is not bool:
        raise EnrichmentResponseError("Unpaywall is_oa must be a boolean")
    return is_oa
