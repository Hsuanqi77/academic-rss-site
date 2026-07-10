import re
from html import unescape
from typing import Any, Literal
from urllib.parse import unquote, urlsplit


DoiSource = Literal["auto", "explicit", "free_text", "url_path"]

_DOI_PREFIX_PATTERN = re.compile(r"10\.\d{4,9}/", re.IGNORECASE)
_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_DOI_LABEL_PATTERN = re.compile(r"^doi\s*:\s*", re.IGNORECASE)
_DOI_RESOLVER_HOSTS = {"doi.org", "dx.doi.org"}
_DOI_DELIMITERS = {"(": ")", "<": ">", "[": "]", "{": "}"}
_DOI_PROSE_PUNCTUATION = ".,!?\"'"
_ASCII_CASE_FOLD = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
)


def normalize_doi(
    value: Any,
    *,
    source: DoiSource = "auto",
    lowercase: bool = True,
) -> str | None:
    """Normalize a DOI using punctuation rules appropriate to its source.

    Explicit identifier fields may legally end in punctuation, while resolver URLs,
    publisher URL paths, and prose treat unmatched terminal punctuation as decoration.
    """
    if not isinstance(value, str):
        return None
    candidate = unescape(value).strip()
    if not candidate:
        return None

    if source == "url_path":
        candidate = _url_path(candidate)
        if candidate is None:
            return None
        source = "free_text"

    parsed = _parsed_url(candidate)
    if parsed is None:
        return None
    hostname = parsed.hostname
    is_resolver = (
        parsed.scheme.lower() in {"http", "https"}
        and hostname is not None
        and hostname.lower() in _DOI_RESOLVER_HOSTS
    )
    if is_resolver:
        candidate = unquote(parsed.path.lstrip("/"))
        source = "free_text"
    else:
        candidate = _DOI_LABEL_PATTERN.sub("", candidate, count=1)
        match = _DOI_PREFIX_PATTERN.search(candidate)
        if match is None:
            return None
        if source == "free_text":
            candidate = candidate[match.start() :].split(maxsplit=1)[0]
        elif match.start() > 0:
            return None

    if source == "free_text":
        candidate = _strip_free_text_suffix(candidate)
    else:
        candidate = _strip_explicit_suffix(candidate)
    if not _is_complete_doi(candidate):
        return None
    return candidate.translate(_ASCII_CASE_FOLD) if lowercase else candidate


def _parsed_url(value: str):
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return None
    return parsed


def _url_path(value: str) -> str | None:
    parsed = _parsed_url(value)
    if parsed is None:
        return None
    return unquote(parsed.path)


def _strip_free_text_suffix(candidate: str) -> str:
    candidate = candidate.strip()
    closing_to_opening = {closing: opening for opening, closing in _DOI_DELIMITERS.items()}
    while candidate:
        if candidate[-1] in _DOI_PROSE_PUNCTUATION:
            candidate = candidate[:-1]
            continue
        opening = closing_to_opening.get(candidate[-1])
        if opening is not None and candidate.count(candidate[-1]) > candidate.count(opening):
            candidate = candidate[:-1]
            continue
        break
    return candidate


def _strip_explicit_suffix(candidate: str) -> str:
    candidate = candidate.strip()
    if _is_complete_doi(candidate):
        return candidate

    unmatched_index = _first_unmatched_closing_index(candidate)
    if unmatched_index is not None:
        removable = set(_DOI_PROSE_PUNCTUATION) | set(_DOI_DELIMITERS.values())
        if set(candidate[unmatched_index:]) <= removable:
            return candidate[:unmatched_index].rstrip(_DOI_PROSE_PUNCTUATION)

    return candidate


def _first_unmatched_closing_index(candidate: str) -> int | None:
    stack: list[str] = []
    closing_to_opening = {closing: opening for opening, closing in _DOI_DELIMITERS.items()}
    for index, character in enumerate(candidate):
        if character in _DOI_DELIMITERS:
            stack.append(character)
        elif character in closing_to_opening:
            if not stack or stack[-1] != closing_to_opening[character]:
                return index
            stack.pop()
    return None


def _is_complete_doi(candidate: str) -> bool:
    if _DOI_PATTERN.fullmatch(candidate) is None:
        return False
    suffix = candidate[candidate.find("/") + 1 :]
    if not suffix or any(
        character.isspace() or not character.isprintable() for character in suffix
    ):
        return False

    stack: list[str] = []
    closing_to_opening = {closing: opening for opening, closing in _DOI_DELIMITERS.items()}
    for character in suffix:
        if character in _DOI_DELIMITERS:
            stack.append(character)
        elif character in closing_to_opening:
            if not stack or stack.pop() != closing_to_opening[character]:
                return False
    return not stack
