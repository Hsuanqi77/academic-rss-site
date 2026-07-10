import zlib
from collections.abc import Mapping
from html.parser import HTMLParser
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic
from typing import Any
from urllib.parse import urljoin

import feedparser
import httpx

from paper_radar.config import FeedConfig
from paper_radar.identifiers import normalize_doi
from paper_radar.models import FeedFetchResult, RawFeedItem


USER_AGENT = "paper-radar/0.1 (+personal academic RSS reader)"
MAX_FEED_BYTES = 8 * 1024 * 1024
FETCH_DEADLINE_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 25.0
READ_TIMEOUT_SLICE_SECONDS = 1.0
DECODE_CHUNK_BYTES = 64 * 1024
MAX_REDIRECTS = 5
_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
_SUPPORTED_CONTENT_ENCODINGS = {"", "identity", "gzip", "deflate"}
_SUPPORTED_FEED_VERSIONS = {"atom10", "rss10", "rss20"}


class FeedParseError(ValueError):
    pass


class FeedFetchError(RuntimeError):
    pass


class _PlainTextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


class _DeflateDecoder:
    def __init__(self) -> None:
        self._decoder = None
        self._prefix = bytearray()

    def decompress(self, data: bytes, max_output: int) -> bytes:
        if self._decoder is None:
            needed = 2 - len(self._prefix)
            self._prefix.extend(data[:needed])
            data = data[needed:]
            if len(self._prefix) < 2:
                return b""
            cmf, flg = self._prefix
            has_zlib_header = (
                cmf & 0x0F == zlib.DEFLATED and cmf >> 4 <= 7 and (cmf << 8 | flg) % 31 == 0
            )
            wbits = zlib.MAX_WBITS if has_zlib_header else -zlib.MAX_WBITS
            self._decoder = zlib.decompressobj(wbits)
            data = bytes(self._prefix) + data
            self._prefix.clear()
        return self._decoder.decompress(data, max_output)

    @property
    def unconsumed_tail(self) -> bytes:
        return b"" if self._decoder is None else self._decoder.unconsumed_tail

    @property
    def eof(self) -> bool:
        return False if self._decoder is None else self._decoder.eof


def parse_feed(
    content: bytes,
    feed_id: str,
    feed_url: str,
    *,
    effective_url: str | None = None,
) -> list[RawFeedItem]:
    return parse_feed_bytes(
        content,
        feed_id,
        feed_url,
        effective_url=effective_url,
    )


def parse_feed_bytes(
    content: bytes,
    feed_id: str,
    feed_url: str,
    *,
    effective_url: str | None = None,
) -> list[RawFeedItem]:
    base_url = effective_url or feed_url
    try:
        parsed = feedparser.parse(
            content,
            response_headers={
                "content-location": base_url,
                "content-type": "application/xml",
            },
        )
    except Exception as exc:
        raise FeedParseError(f"could not parse feed {feed_id} ({feed_url}): {exc}") from exc

    items: list[RawFeedItem] = []
    for entry in parsed.entries:
        title = _text(entry.get("title"))
        link = _text(entry.get("link"))
        if title is None or link is None:
            continue
        link = urljoin(base_url, link)

        summary = _first_text(entry, "summary", "description")
        items.append(
            RawFeedItem(
                feed_id=feed_id,
                feed_url=feed_url,
                title=title,
                link=link,
                published=_first_text(entry, "published", "updated", "dc_date"),
                doi=_extract_doi(entry, summary, link),
                authors=_extract_authors(entry),
                summary=summary,
                raw_type=_first_text(entry, "prism_section", "type"),
            )
        )

    if parsed.bozo and not items:
        error = getattr(parsed, "bozo_exception", "unknown parser error")
        raise FeedParseError(f"could not parse feed {feed_id} ({feed_url}): {error}")
    if getattr(parsed, "version", "") not in _SUPPORTED_FEED_VERSIONS:
        raise FeedParseError(
            f"could not parse feed {feed_id} ({feed_url}): not a recognized RSS or Atom feed"
        )
    if not parsed.entries and not _has_required_feed_metadata(parsed):
        raise FeedParseError(
            f"could not parse feed {feed_id} ({feed_url}): missing required feed metadata"
        )

    return items


def _has_required_feed_metadata(parsed: Mapping[str, Any]) -> bool:
    feed = parsed.get("feed")
    if not isinstance(feed, Mapping):
        return False
    version = parsed.get("version")
    if version == "atom10":
        return all(_text(feed.get(field)) is not None for field in ("title", "id", "updated"))
    return all(_text(feed.get(field)) is not None for field in ("title", "link", "description"))


def fetch_feed(
    client: httpx.Client,
    feed: FeedConfig,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FeedFetchResult:
    """Fetch a bounded feed, following HTTPS redirects without allowing downgrades."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip, deflate, identity",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    started_at = monotonic()
    current_url = httpx.URL(feed.feed_url)
    if current_url.scheme != "https":
        raise FeedFetchError(f"initial feed URL must use HTTPS: {current_url}")
    redirect_count = 0
    redirect_request: httpx.Request | None = None
    while True:
        timeout_extensions = _request_timeout_extensions(started_at, current_url)
        owned_client = _owned_direct_client(client, current_url)
        request_client = owned_client or client
        if redirect_request is None:
            request = request_client.build_request(
                "GET",
                current_url,
                headers=headers,
                extensions={"timeout": timeout_extensions},
            )
            apply_default_auth = True
        else:
            request = redirect_request
            request.extensions["timeout"] = timeout_extensions
            apply_default_auth = False
        try:
            response = _send_request_with_deadline(
                request_client,
                request,
                started_at,
                abort_transport=owned_client is not None,
                apply_default_auth=apply_default_auth,
            )
        except BaseException:
            _release_owned_client(owned_client)
            raise
        try:
            if response.status_code in _REDIRECT_STATUS_CODES:
                location = response.headers.get("Location")
                if location is None:
                    raise FeedFetchError(
                        f"redirect response missing Location header: {response.url}"
                    )
                if redirect_count >= MAX_REDIRECTS:
                    raise FeedFetchError(
                        f"redirect limit exceeded after {MAX_REDIRECTS} redirects: {response.url}"
                    )
                redirect_request = request_client._build_redirect_request(  # type: ignore[attr-defined]
                    response.request,
                    response,
                )
                _require_https_redirect_target(redirect_request.url)
                current_url = redirect_request.url
                redirect_count += 1
                continue

            response_etag = response.headers.get("ETag")
            response_last_modified = response.headers.get("Last-Modified")

            if response.status_code == httpx.codes.NOT_MODIFIED:
                _enforce_fetch_deadline(started_at, response.url)
                return FeedFetchResult(
                    content=None,
                    etag=response_etag or etag,
                    last_modified=response_last_modified or last_modified,
                    not_modified=True,
                    effective_url=str(response.url),
                )

            response.raise_for_status()
            _enforce_fetch_deadline(started_at, response.url)
            if response.is_stream_consumed:
                content = _read_consumed_content(response)
            else:
                _reject_declared_oversize(response)
                content = _read_bounded_content(response, started_at)

            return FeedFetchResult(
                content=content,
                etag=response_etag,
                last_modified=response_last_modified,
                not_modified=False,
                effective_url=str(response.url),
            )
        finally:
            client.cookies.extract_cookies(response)
            response.close()
            _release_owned_client(owned_client)


def _send_request_with_deadline(
    client: httpx.Client,
    request: httpx.Request,
    started_at: float,
    *,
    abort_transport: bool = True,
    apply_default_auth: bool = True,
) -> httpx.Response:
    results: Queue[httpx.Response | BaseException] = Queue(maxsize=1)
    remaining = _remaining_fetch_seconds(started_at, request.url)
    cancelled = Event()

    def send() -> None:
        try:
            send_auth = httpx.USE_CLIENT_DEFAULT if apply_default_auth else None
            response = client.send(
                request,
                stream=True,
                auth=send_auth,
                follow_redirects=False,
            )
        except BaseException as exc:
            results.put(exc)
        else:
            if cancelled.is_set():
                response.close()
                return
            results.put(response)
            if cancelled.is_set():
                _close_queued_response(results)

    Thread(target=send, daemon=True).start()
    try:
        result = results.get(timeout=remaining)
    except Empty as exc:
        cancelled.set()
        if abort_transport:
            _abort_client_transport(client, request.url)
        _close_queued_response(results)
        raise FeedFetchError(
            f"total fetch deadline of {FETCH_DEADLINE_SECONDS:g} seconds exceeded while "
            f"waiting for response headers: {request.url}"
        ) from exc

    if isinstance(result, BaseException):
        raise result
    try:
        _enforce_fetch_deadline(started_at, result.url)
    except FeedFetchError:
        result.close()
        if abort_transport:
            _abort_client_transport(client, request.url)
        raise
    return result


def _owned_direct_client(client: httpx.Client, url: httpx.URL) -> httpx.Client | None:
    selected_transport = client._transport_for_url(url)  # type: ignore[attr-defined]
    if selected_transport is not client._transport:  # type: ignore[attr-defined]
        return None
    if type(selected_transport) is not httpx.HTTPTransport:
        return None

    pool = selected_transport._pool  # type: ignore[attr-defined]
    pool_type = type(pool)
    backend_type = type(getattr(pool, "_network_backend", None))
    if not (
        pool_type.__module__ in {"httpcore", "httpcore._sync.connection_pool"}
        and pool_type.__name__ == "ConnectionPool"
        and backend_type.__module__ in {"httpcore", "httpcore._backends.sync"}
        and backend_type.__name__ == "SyncBackend"
    ):
        return None

    transport = httpx.HTTPTransport(
        verify=pool._ssl_context,
        http1=pool._http1,
        http2=pool._http2,
        limits=httpx.Limits(
            max_connections=pool._max_connections,
            max_keepalive_connections=pool._max_keepalive_connections,
            keepalive_expiry=pool._keepalive_expiry,
        ),
        uds=pool._uds,
        local_address=pool._local_address,
        retries=pool._retries,
        socket_options=pool._socket_options,
    )
    return httpx.Client(
        transport=transport,
        auth=client._auth,  # type: ignore[attr-defined]
        headers=client.headers,
        params=client.params,
        cookies=client.cookies,
        event_hooks=client._event_hooks,  # type: ignore[attr-defined]
        default_encoding=client._default_encoding,  # type: ignore[attr-defined]
    )


def _release_owned_client(owned: httpx.Client | None) -> None:
    if owned is None:
        return
    owned.close()


def _close_queued_response(results: Queue[httpx.Response | BaseException]) -> None:
    try:
        result = results.get_nowait()
    except Empty:
        return
    if isinstance(result, httpx.Response):
        result.close()


def _abort_client_transport(client: httpx.Client, url: httpx.URL) -> None:
    try:
        transport = client._transport_for_url(url)  # type: ignore[attr-defined]
        transport.close()
    except Exception:
        pass


def _reject_declared_oversize(response: httpx.Response) -> None:
    content_length = response.headers.get("Content-Length")
    if content_length is None:
        return
    try:
        declared_size = int(content_length)
    except ValueError:
        return
    if declared_size > MAX_FEED_BYTES:
        raise FeedFetchError(
            f"declared feed size {declared_size} exceeds maximum of {MAX_FEED_BYTES} bytes: "
            f"{response.url}"
        )


def _read_consumed_content(response: httpx.Response) -> bytes:
    content = response.content
    if len(content) > MAX_FEED_BYTES:
        raise FeedFetchError(
            f"consumed feed size {len(content)} exceeds maximum of {MAX_FEED_BYTES} bytes: "
            f"{response.url}"
        )
    return content


def _read_bounded_content(response: httpx.Response, started_at: float) -> bytes:
    content_encoding = response.headers.get("Content-Encoding", "").strip().lower()
    if content_encoding not in _SUPPORTED_CONTENT_ENCODINGS:
        raise FeedFetchError(f"unsupported Content-Encoding {content_encoding!r}: {response.url}")

    decoder = _content_decoder(content_encoding)
    content = bytearray()
    raw_size = 0
    raw_iterator = iter(response.iter_raw())
    captured_read_timeout = response.request.extensions["timeout"]["read"]
    while True:
        try:
            raw_chunk = _next_raw_chunk(
                raw_iterator,
                response,
                started_at,
                captured_read_timeout,
            )
        except StopIteration:
            break

        raw_size += len(raw_chunk)
        if raw_size > MAX_FEED_BYTES:
            raise FeedFetchError(
                f"raw feed size exceeds maximum of {MAX_FEED_BYTES} bytes: {response.url}"
            )

        if decoder is None:
            _append_decoded(content, raw_chunk, response.url)
        else:
            try:
                for decoded_chunk in _decode_bounded(decoder, raw_chunk, len(content)):
                    _append_decoded(content, decoded_chunk, response.url)
            except zlib.error as exc:
                raise FeedFetchError(
                    f"invalid {content_encoding} feed body: {response.url}: {exc}"
                ) from exc

    if decoder is not None and not decoder.eof:
        raise FeedFetchError(f"truncated {content_encoding} feed body: {response.url}")
    _enforce_fetch_deadline(started_at, response.url)
    return bytes(content)


def _content_decoder(content_encoding: str):
    if content_encoding == "gzip":
        return zlib.decompressobj(zlib.MAX_WBITS | 16)
    if content_encoding == "deflate":
        return _DeflateDecoder()
    return None


def _decode_bounded(decoder, raw_chunk: bytes, decoded_size: int):
    pending = raw_chunk
    while True:
        remaining = MAX_FEED_BYTES - decoded_size
        max_output = min(DECODE_CHUNK_BYTES, remaining + 1)
        decoded_chunk = _bounded_decompress(decoder, pending, max_output)
        pending = decoder.unconsumed_tail
        if decoded_chunk:
            yield decoded_chunk
            decoded_size += len(decoded_chunk)
        if pending:
            continue
        if decoded_chunk and len(decoded_chunk) == max_output:
            pending = b""
            continue
        break


def _bounded_decompress(decoder, data: bytes, max_output: int) -> bytes:
    return decoder.decompress(data, max_output)


def _append_decoded(content: bytearray, chunk: bytes, url: httpx.URL) -> None:
    if len(content) + len(chunk) > MAX_FEED_BYTES:
        raise FeedFetchError(f"decoded feed size exceeds maximum of {MAX_FEED_BYTES} bytes: {url}")
    content.extend(chunk)


def _next_raw_chunk(
    raw_iterator,
    response: httpx.Response,
    started_at: float,
    captured_read_timeout: float,
) -> bytes:
    remaining = _remaining_fetch_seconds(started_at, response.url)
    # httpcore 1.0 captures the HTTP/1.1 body-read timeout on the first next().
    # Keep that captured slice below the hard deadline; HTTP/2 and custom
    # transports also observe the refreshed extension on every subsequent read.
    if remaining <= captured_read_timeout:
        raise FeedFetchError(
            f"total fetch deadline of {FETCH_DEADLINE_SECONDS:g} seconds exceeded before read: "
            f"{response.url}"
        )
    response.request.extensions["timeout"]["read"] = min(
        captured_read_timeout,
        remaining / 2,
    )
    chunk = next(raw_iterator)
    _enforce_fetch_deadline(started_at, response.url)
    return chunk


def _require_https_redirect_target(url: httpx.URL) -> None:
    if url.scheme != "https":
        raise FeedFetchError(
            f"redirect target must use HTTPS; final feed URL must use HTTPS: {url}"
        )


def _remaining_fetch_seconds(started_at: float, url: httpx.URL) -> float:
    remaining = FETCH_DEADLINE_SECONDS - (monotonic() - started_at)
    if remaining <= 0:
        raise FeedFetchError(
            f"total fetch deadline of {FETCH_DEADLINE_SECONDS:g} seconds exceeded: {url}"
        )
    return remaining


def _request_timeout_extensions(started_at: float, url: httpx.URL) -> dict[str, float]:
    remaining = _remaining_fetch_seconds(started_at, url)
    phase_timeout = min(REQUEST_TIMEOUT_SECONDS, remaining / 4)
    return {
        "connect": phase_timeout,
        "read": min(READ_TIMEOUT_SLICE_SECONDS, phase_timeout),
        "write": phase_timeout,
        "pool": phase_timeout,
    }


def _enforce_fetch_deadline(started_at: float, url: httpx.URL) -> None:
    if monotonic() - started_at > FETCH_DEADLINE_SECONDS:
        raise FeedFetchError(
            f"total fetch deadline of {FETCH_DEADLINE_SECONDS:g} seconds exceeded: {url}"
        )


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _first_text(entry: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _text(entry.get(key))
        if value is not None:
            return value
    return None


def _extract_authors(entry: Mapping[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    authors = entry.get("authors")
    if isinstance(authors, (list, tuple)):
        for author in authors:
            if isinstance(author, Mapping):
                name = _text(author.get("name"))
            else:
                name = _text(author)
            if name is not None:
                names.append(name)

    if not names:
        author = _text(entry.get("author"))
        if author is not None:
            names.append(author)
    return tuple(names)


def _extract_doi(entry: Mapping[str, Any], summary: str | None, link: str) -> str | None:
    for candidate in (
        entry.get("prism_doi"),
        entry.get("dc_identifier"),
        entry.get("id"),
        entry.get("guid"),
    ):
        doi = normalize_doi(candidate, source="explicit", lowercase=False)
        if doi is not None:
            return doi

    summary_text = _html_to_plain_text(summary)
    doi = normalize_doi(summary_text, source="free_text", lowercase=False)
    return doi if doi is not None else _extract_doi_from_link(link)


def _html_to_plain_text(value: str | None) -> str | None:
    if value is None:
        return None
    parser = _PlainTextHTMLParser()
    parser.feed(value)
    parser.close()
    return " ".join(parser.parts)


def _extract_doi_from_link(link: str) -> str | None:
    return normalize_doi(link, source="url_path", lowercase=False)
