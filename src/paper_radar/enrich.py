import json
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Any
from urllib.parse import quote

import httpx

from paper_radar.models import ArticleRecord
from paper_radar.normalize import clean_text, normalize_article_type


USER_AGENT = "paper-radar/0.1 (+personal academic RSS reader)"
MAX_METADATA_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 20.0

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@(?:[^@\s.]+\.)+[^@\s.]+$")
_LOW_QUALITY_AUTHORS = frozenset(
    {
        "anonymous",
        "et al",
        "n/a",
        "na",
        "not available",
        "unknown",
        "unknown author",
        "unknown author(s)",
    }
)
_LOW_QUALITY_ABSTRACTS = frozenset(
    {"abstract unavailable", "n/a", "no abstract", "no abstract available", "not available"}
)
_METADATA_STATUS_RANK = {"rss_only": 0, "partial": 1, "enriched": 2}


def enrich_article(
    client: httpx.Client,
    article: ArticleRecord,
    *,
    unpaywall_email: str | None = None,
) -> ArticleRecord:
    if not isinstance(article.doi, str) or not article.doi.strip():
        return article

    try:
        encoded_doi = quote(article.doi, safe="")
    except (UnicodeError, ValueError):
        metadata_status = _higher_metadata_status(article.metadata_status, "partial")
        return replace(article, metadata_status=metadata_status)
    enriched = article
    source_results: list[bool] = []

    try:
        crossref_payload = _fetch_json(
            client,
            f"https://api.crossref.org/works/{encoded_doi}",
        )
        crossref_message = _crossref_message(crossref_payload)
        enriched = _apply_crossref(enriched, crossref_message)
    except Exception:
        source_results.append(False)
    else:
        source_results.append(True)

    email = _reasonable_email(unpaywall_email)
    if email is not None:
        try:
            unpaywall_payload = _fetch_json(
                client,
                f"https://api.unpaywall.org/v2/{encoded_doi}",
                params={"email": email},
            )
            is_oa = _unpaywall_is_oa(unpaywall_payload)
            enriched = replace(enriched, oa_status="open" if is_oa else "closed")
        except Exception:
            source_results.append(False)
        else:
            source_results.append(True)

    requested_status = "enriched" if all(source_results) else "partial"
    metadata_status = _higher_metadata_status(enriched.metadata_status, requested_status)
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
    ) as response:
        response.raise_for_status()
        _reject_declared_oversize(response)
        content = bytearray()
        for chunk in response.iter_bytes():
            if len(content) + len(chunk) > MAX_METADATA_BYTES:
                raise ValueError("metadata response exceeds maximum size")
            content.extend(chunk)
    return json.loads(content)


def _reject_declared_oversize(response: httpx.Response) -> None:
    content_length = response.headers.get("Content-Length")
    if content_length is None:
        return
    try:
        declared_size = int(content_length)
    except ValueError:
        return
    if declared_size > MAX_METADATA_BYTES:
        raise ValueError("declared metadata response exceeds maximum size")


def _crossref_message(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Crossref response root must be an object")
    message = payload.get("message")
    if not isinstance(message, Mapping):
        raise ValueError("Crossref response message must be an object")
    return message


def _apply_crossref(article: ArticleRecord, message: Mapping[str, Any]) -> ArticleRecord:
    title = article.title
    crossref_title = _crossref_title(message.get("title"))
    if _is_placeholder_title(title) and crossref_title is not None:
        title = crossref_title

    authors = article.authors
    crossref_authors = _crossref_authors(message.get("author"))
    if _authors_are_low_quality(authors) and crossref_authors:
        authors = crossref_authors

    abstract = article.abstract
    crossref_abstract = _clean_string(message.get("abstract"))
    if _abstract_is_low_quality(abstract) and crossref_abstract is not None:
        abstract = crossref_abstract

    article_type = article.article_type
    crossref_type = message.get("type")
    if article_type.casefold() == "other" and isinstance(crossref_type, str):
        article_type = normalize_article_type(crossref_type)

    return replace(
        article,
        title=title,
        authors=authors,
        abstract=abstract,
        article_type=article_type,
    )


def _crossref_title(value: Any) -> str | None:
    candidates = value if isinstance(value, (list, tuple)) else (value,)
    for candidate in candidates:
        title = _clean_string(candidate)
        if title is not None:
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
        given = _clean_string(row.get("given"))
        family = _clean_string(row.get("family"))
        if given is not None or family is not None:
            author = " ".join(part for part in (given, family) if part is not None)
        else:
            author = _clean_string(row.get("name"))
        if author is None:
            continue
        identity = author.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        authors.append(author)
    return tuple(authors)


def _clean_string(value: Any) -> str | None:
    return clean_text(value) if isinstance(value, str) else None


def _is_placeholder_title(value: str) -> bool:
    title = clean_text(value)
    return title is None or title.casefold() == "untitled"


def _authors_are_low_quality(authors: tuple[str, ...]) -> bool:
    cleaned = tuple(clean_text(author) for author in authors)
    return not cleaned or all(
        author is None or _placeholder_key(author) in _LOW_QUALITY_AUTHORS for author in cleaned
    )


def _abstract_is_low_quality(value: str | None) -> bool:
    abstract = clean_text(value)
    return abstract is None or _placeholder_key(abstract) in _LOW_QUALITY_ABSTRACTS


def _placeholder_key(value: str) -> str:
    return value.casefold().strip(" \t\r\n.,:;!?")


def _reasonable_email(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    email = value.strip()
    if len(email) > 254 or _EMAIL_PATTERN.fullmatch(email) is None:
        return None
    return email


def _unpaywall_is_oa(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        raise ValueError("Unpaywall response root must be an object")
    is_oa = payload.get("is_oa")
    if type(is_oa) is not bool:
        raise ValueError("Unpaywall is_oa must be a boolean")
    return is_oa


def _higher_metadata_status(existing: str, requested: str) -> str:
    if _METADATA_STATUS_RANK.get(existing, 0) >= _METADATA_STATUS_RANK[requested]:
        return existing
    return requested
