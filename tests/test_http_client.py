from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
from time import monotonic

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
    ticks = iter((0.0, 0.1, 0.5))
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


def test_polite_client_rebases_next_slot_after_sleep_overshoot() -> None:
    now = {"value": 0.0}
    sleep_calls = 0
    arrivals: list[float] = []

    def sleeper(delay: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            now["value"] = 1.5
        else:
            now["value"] += delay

    def handler(request: httpx.Request) -> httpx.Response:
        arrivals.append(now["value"])
        return httpx.Response(200, request=request)

    with PoliteClient(
        transport=httpx.MockTransport(handler),
        min_interval=0.5,
        clock=lambda: now["value"],
        sleeper=sleeper,
    ) as client:
        client.get("https://same.test/first")
        now["value"] = 0.1
        client.get("https://same.test/second")
        now["value"] = 1.6
        client.get("https://same.test/third")

    assert arrivals == [0.0, 1.5, 2.0]
    assert [later - earlier for earlier, later in zip(arrivals, arrivals[1:])] == [1.5, 0.5]


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
    ticks = iter((0.0, 0.1, 0.5))
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
    ticks = iter((0.0, 0.1, 0.5))
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
        assert client._event_hooks["request"] == hooks["request"]  # type: ignore[attr-defined]
        client.get("https://example.test/feed")

    assert events == ["request:example.test", "response:200"]
    assert len(hooks["request"]) == 1


def test_transport_boundary_pacing_prevents_preempted_request_zero_gap() -> None:
    a_at_boundary = Event()
    release_a = Event()
    b_arrived = Event()
    arrivals: list[tuple[str, float]] = []

    class BoundaryBlockingClient(PoliteClient):
        def _send_single_request(self, request: httpx.Request) -> httpx.Response:
            if request.url.path == "/a":
                a_at_boundary.set()
                if not release_a.wait(2):
                    raise TimeoutError("test did not release /a at transport boundary")
            return super()._send_single_request(request)

    def handler(request: httpx.Request) -> httpx.Response:
        arrivals.append((request.url.path, monotonic()))
        if request.url.path == "/b":
            b_arrived.set()
        return httpx.Response(200, request=request)

    with BoundaryBlockingClient(
        transport=httpx.MockTransport(handler),
        min_interval=0.05,
    ) as client:
        with ThreadPoolExecutor(max_workers=2) as executor:
            a_future = executor.submit(client.get, "https://same.test/a")
            assert a_at_boundary.wait(1)
            b_future = executor.submit(client.get, "https://same.test/b")
            try:
                assert b_arrived.wait(1)
            finally:
                release_a.set()
            b_future.result(timeout=1)
            a_future.result(timeout=1)

    assert [path for path, _ in arrivals] == ["/b", "/a"]
    assert arrivals[1][1] - arrivals[0][1] >= 0.04


def test_same_origin_gate_is_held_until_response_headers_return() -> None:
    a_arrived = Event()
    release_a = Event()
    b_arrived = Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/a":
            a_arrived.set()
            if not release_a.wait(2):
                raise TimeoutError("test did not release /a transport")
        else:
            b_arrived.set()
        return httpx.Response(200, request=request)

    with PoliteClient(
        transport=httpx.MockTransport(handler),
        min_interval=0,
    ) as client:
        with ThreadPoolExecutor(max_workers=2) as executor:
            a_future = executor.submit(client.get, "https://same.test/a")
            assert a_arrived.wait(1)
            b_future = executor.submit(client.get, "https://same.test/b")
            try:
                assert not b_arrived.wait(0.05)
            finally:
                release_a.set()
            a_future.result(timeout=1)
            b_future.result(timeout=1)

    assert b_arrived.is_set()


def test_different_origin_transport_dispatches_remain_concurrent() -> None:
    a_arrived = Event()
    release_a = Event()
    b_arrived = Event()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "one.test":
            a_arrived.set()
            if not release_a.wait(2):
                raise TimeoutError("test did not release first origin")
        else:
            b_arrived.set()
        return httpx.Response(200, request=request)

    with PoliteClient(
        transport=httpx.MockTransport(handler),
        min_interval=1,
    ) as client:
        with ThreadPoolExecutor(max_workers=2) as executor:
            a_future = executor.submit(client.get, "https://one.test/a")
            assert a_arrived.wait(1)
            b_future = executor.submit(client.get, "https://two.test/b")
            try:
                assert b_arrived.wait(1)
            finally:
                release_a.set()
            a_future.result(timeout=1)
            b_future.result(timeout=1)


def test_polite_client_paces_origin_after_caller_hook_mutates_url() -> None:
    ticks = iter((0.0, 0.1, 0.5))
    sleeps: list[float] = []

    def share_origin(request: httpx.Request) -> None:
        request.url = request.url.copy_with(host="shared.test")

    with PoliteClient(
        transport=_transport(),
        min_interval=0.5,
        clock=lambda: next(ticks),
        sleeper=sleeps.append,
        event_hooks={"request": [share_origin]},
    ) as client:
        client.get("https://one.test/first")
        client.get("https://two.test/second")

    assert sleeps == [pytest.approx(0.4)]


def test_blocking_caller_hook_cannot_reverse_actual_same_origin_pacing() -> None:
    a_hook_entered = Event()
    release_a = Event()
    b_arrived = Event()
    arrivals: list[tuple[str, float]] = []

    def blocking_hook(request: httpx.Request) -> None:
        if request.url.path == "/a":
            a_hook_entered.set()
            if not release_a.wait(2):
                raise TimeoutError("test did not release /a hook")

    def handler(request: httpx.Request) -> httpx.Response:
        arrivals.append((request.url.path, monotonic()))
        if request.url.path == "/b":
            b_arrived.set()
        return httpx.Response(200, request=request)

    with PoliteClient(
        transport=httpx.MockTransport(handler),
        min_interval=0.05,
        event_hooks={"request": [blocking_hook]},
    ) as client:
        with ThreadPoolExecutor(max_workers=2) as executor:
            a_future = executor.submit(client.get, "https://same.test/a")
            assert a_hook_entered.wait(1)
            b_future = executor.submit(client.get, "https://same.test/b")
            try:
                assert b_arrived.wait(1)
            finally:
                release_a.set()
            b_future.result(timeout=1)
            a_future.result(timeout=1)

    assert [path for path, _ in arrivals] == ["/b", "/a"]
    assert arrivals[1][1] - arrivals[0][1] >= 0.04


def test_feed_owned_direct_client_clone_retains_shared_pacing_hook() -> None:
    ticks = iter((0.0, 0.1, 0.5))
    sleeps: list[float] = []

    def share_origin(request: httpx.Request) -> None:
        request.url = request.url.copy_with(host="shared.test")

    with PoliteClient(
        min_interval=0.5,
        clock=lambda: next(ticks),
        sleeper=sleeps.append,
        event_hooks={"request": [share_origin]},
    ) as client:
        owned = _owned_direct_client(client, httpx.URL("https://example.test/feed"))
        assert owned is not None
        assert isinstance(owned, PoliteClient)
        client._transport.close()  # type: ignore[attr-defined]
        owned._transport.close()  # type: ignore[attr-defined]
        client._transport = _transport()  # type: ignore[attr-defined]
        owned._transport = _transport()  # type: ignore[attr-defined]
        try:
            client.get("https://one.test/first")
            owned.get("https://two.test/second")
        finally:
            owned.close()

    assert sleeps == [pytest.approx(0.4)]


def test_separate_feed_owned_clones_share_pacing_across_retry_attempts() -> None:
    ticks = iter((0.0, 0.1, 0.5, 0.6, 1.0))
    sleeps: list[float] = []

    with PoliteClient(
        min_interval=0.5,
        clock=lambda: next(ticks),
        sleeper=sleeps.append,
    ) as client:
        first = _owned_direct_client(client, httpx.URL("https://same.test/feed"))
        second = _owned_direct_client(client, httpx.URL("https://same.test/feed"))
        assert isinstance(first, PoliteClient)
        assert isinstance(second, PoliteClient)
        first._transport.close()  # type: ignore[attr-defined]
        second._transport.close()  # type: ignore[attr-defined]
        first._transport = _transport()  # type: ignore[attr-defined]
        second._transport = _transport()  # type: ignore[attr-defined]
        try:
            client._transport.close()  # type: ignore[attr-defined]
            client._transport = _transport()  # type: ignore[attr-defined]
            client.get("https://same.test/original")
            first.get("https://same.test/retry-one")
            second.get("https://same.test/retry-two")
        finally:
            first.close()
            second.close()

    assert sleeps == [pytest.approx(0.4), pytest.approx(0.4)]


def test_transport_error_releases_same_origin_gate() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, request=request)

    with PoliteClient(
        transport=httpx.MockTransport(handler),
        min_interval=0,
    ) as client:
        with pytest.raises(httpx.ConnectError):
            client.get("https://same.test/first")
        response = client.get("https://same.test/second")

    assert response.status_code == 200
    assert attempts == 2


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


@pytest.mark.parametrize(
    ("retry_after", "expected_sleep"),
    [
        ("0", 0.5),
        ("9" * 400, 60.0),
        ("not-a-delay", 0.5),
        ("-5", 0.5),
    ],
)
def test_retry_operation_bounds_numeric_retry_after_before_float_conversion(
    retry_after: str, expected_sleep: float
) -> None:
    attempts = 0
    sleeps: list[float] = []
    request = httpx.Request("GET", "https://example.test/feed")
    response = httpx.Response(
        429,
        headers={"Retry-After": retry_after},
        request=request,
    )

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.HTTPStatusError("retry later", request=request, response=response)
        return "done"

    assert retry_operation(operation, sleeper=sleeps.append) == "done"
    assert sleeps == [expected_sleep]


@pytest.mark.parametrize("status_code", [501, 505])
def test_retry_operation_does_not_retry_permanent_server_status(status_code: int) -> None:
    attempts = 0
    request = httpx.Request("GET", "https://example.test/feed")
    response = httpx.Response(status_code, request=request)

    def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise httpx.HTTPStatusError("permanent server error", request=request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        retry_operation(operation, sleeper=lambda _: pytest.fail("unexpected retry"))

    assert attempts == 1


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_retry_operation_retries_transient_server_status(status_code: int) -> None:
    attempts = 0
    request = httpx.Request("GET", "https://example.test/feed")
    response = httpx.Response(status_code, request=request)

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.HTTPStatusError(
                "temporary server error", request=request, response=response
            )
        return "done"

    assert retry_operation(operation, sleeper=lambda _: None) == "done"
    assert attempts == 2


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
