from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
import pytest

from paper_radar.feeds import _owned_direct_client
from paper_radar.http_client import PoliteClient, retry_operation


def test_task7_http_client_surface_is_importable() -> None:
    assert PoliteClient is not None
    assert retry_operation is not None


def _transport() -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, request=request))


def test_polite_client_waits_for_reserved_same_origin_slot() -> None:
    ticks = iter((0.0, 0.1))
    sleeps: list[float] = []
    with PoliteClient(
        transport=_transport(),
        min_interval=0.5,
        clock=lambda: next(ticks),
        sleeper=sleeps.append,
    ) as client:
        client.get("https://example.test/first")
        client.get("https://example.test/second")

    assert sleeps == [pytest.approx(0.4)]


def test_polite_client_does_not_delay_different_origins() -> None:
    ticks = iter((0.0, 0.1))
    sleeps: list[float] = []
    with PoliteClient(
        transport=_transport(),
        min_interval=0.5,
        clock=lambda: next(ticks),
        sleeper=sleeps.append,
    ) as client:
        client.get("https://one.test/feed")
        client.get("https://two.test/feed")

    assert sleeps == []


def test_polite_client_normalizes_host_case_and_effective_default_port() -> None:
    ticks = iter((0.0, 0.1))
    sleeps: list[float] = []
    with PoliteClient(
        transport=_transport(),
        min_interval=0.5,
        clock=lambda: next(ticks),
        sleeper=sleeps.append,
    ) as client:
        client.get("https://EXAMPLE.test/first")
        client.get("https://example.test:443/second")

    assert sleeps == [pytest.approx(0.4)]


def test_polite_client_paces_stream_and_send_requests() -> None:
    ticks = iter((0.0, 0.1))
    sleeps: list[float] = []
    with PoliteClient(
        transport=_transport(),
        min_interval=0.5,
        clock=lambda: next(ticks),
        sleeper=sleeps.append,
    ) as client:
        with client.stream("GET", "https://example.test/stream") as response:
            response.read()
        response = client.send(client.build_request("GET", "https://example.test/send"))
        response.close()

    assert sleeps == [pytest.approx(0.4)]


def test_polite_client_reserves_concurrent_same_origin_slots_atomically() -> None:
    sleeps: list[float] = []
    with PoliteClient(
        transport=_transport(),
        min_interval=0.5,
        clock=lambda: 0.0,
        sleeper=sleeps.append,
    ) as client:
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(client.get, ("https://same.test/a", "https://same.test/b"))
            )
        for response in responses:
            response.close()

    assert sorted(sleeps) == [pytest.approx(0.5)]


@pytest.mark.parametrize("interval", [-0.1, float("nan"), float("inf"), True, "0.5"])
def test_polite_client_rejects_invalid_intervals(interval: object) -> None:
    with pytest.raises((TypeError, ValueError), match="interval"):
        PoliteClient(min_interval=interval)  # type: ignore[arg-type]


@pytest.mark.parametrize("argument", ["clock", "sleeper"])
def test_polite_client_rejects_noncallable_clock_or_sleeper(argument: str) -> None:
    kwargs = {argument: None}
    with pytest.raises(TypeError, match=argument):
        PoliteClient(**kwargs)  # type: ignore[arg-type]


def test_polite_client_preserves_caller_request_and_response_hooks() -> None:
    events: list[str] = []
    hooks = {
        "request": [lambda request: events.append(f"request:{request.url.host}")],
        "response": [lambda response: events.append(f"response:{response.status_code}")],
    }
    with PoliteClient(transport=_transport(), min_interval=0, event_hooks=hooks) as client:
        client.get("https://example.test/feed")

    assert events == ["request:example.test", "response:200"]
    assert len(hooks["request"]) == 1


def test_feed_owned_direct_client_clone_retains_shared_pacing_hook() -> None:
    ticks = iter((0.0, 0.1))
    sleeps: list[float] = []
    with PoliteClient(
        min_interval=0.5,
        clock=lambda: next(ticks),
        sleeper=sleeps.append,
    ) as client:
        owned = _owned_direct_client(client, httpx.URL("https://example.test/feed"))
        assert owned is not None
        owned._transport.close()  # type: ignore[attr-defined]
        owned._transport = _transport()  # type: ignore[attr-defined]
        try:
            owned.get("https://example.test/first")
            owned.get("https://example.test/second")
        finally:
            owned.close()

    assert sleeps == [pytest.approx(0.4)]


def test_retry_operation_uses_deterministic_exponential_backoff() -> None:
    attempts = 0
    sleeps: list[float] = []

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ReadTimeout("temporary")
        return "done"

    assert retry_operation(operation, sleeper=sleeps.append) == "done"
    assert attempts == 3
    assert sleeps == [0.5, 1.0]


def test_retry_operation_does_not_retry_nontransient_404() -> None:
    attempts = 0
    request = httpx.Request("GET", "https://example.test/feed")
    response = httpx.Response(404, request=request)

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise httpx.HTTPStatusError("not found", request=request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        retry_operation(operation, sleeper=lambda _: pytest.fail("unexpected retry"))
    assert attempts == 1


def test_retry_operation_respects_retry_after_for_429() -> None:
    attempts = 0
    sleeps: list[float] = []
    request = httpx.Request("GET", "https://example.test/feed")
    response = httpx.Response(429, headers={"Retry-After": "2"}, request=request)

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.HTTPStatusError("rate limited", request=request, response=response)
        return "done"

    assert retry_operation(operation, sleeper=sleeps.append) == "done"
    assert sleeps == [2.0]


def test_retry_operation_respects_http_date_retry_after_with_cap() -> None:
    attempts = 0
    sleeps: list[float] = []
    request = httpx.Request("GET", "https://example.test/feed")
    response = httpx.Response(
        503,
        headers={"Retry-After": "Wed, 21 Oct 2015 07:29:00 GMT"},
        request=request,
    )

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.HTTPStatusError("busy", request=request, response=response)
        return "done"

    result = retry_operation(
        operation,
        sleeper=sleeps.append,
        wall_clock=lambda: datetime(2015, 10, 21, 7, 28, tzinfo=timezone.utc),
        retry_after_cap=30,
    )

    assert result == "done"
    assert sleeps == [30.0]


def test_retry_operation_reraises_identical_last_error_after_three_attempts() -> None:
    errors = [httpx.ConnectError(f"failure {index}") for index in range(3)]
    sleeps: list[float] = []

    def operation() -> None:
        raise errors.pop(0)

    with pytest.raises(httpx.ConnectError) as captured:
        retry_operation(operation, sleeper=sleeps.append)

    assert captured.value.args == ("failure 2",)
    assert sleeps == [0.5, 1.0]


@pytest.mark.parametrize("error_type", [httpx.WriteError, httpx.CloseError])
def test_retry_operation_retries_transient_network_write_and_close_errors(
    error_type: type[httpx.NetworkError],
) -> None:
    attempts = 0
    sleeps: list[float] = []

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise error_type("temporary network failure")
        return "done"

    assert retry_operation(operation, sleeper=sleeps.append) == "done"
    assert attempts == 2
    assert sleeps == [0.5]


def test_retry_operation_does_not_retry_local_protocol_programming_error() -> None:
    attempts = 0

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise httpx.LocalProtocolError("invalid request state")

    with pytest.raises(httpx.LocalProtocolError):
        retry_operation(operation, sleeper=lambda _: pytest.fail("unexpected retry"))

    assert attempts == 1
