import math
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from numbers import Real
from threading import Lock
from typing import Any, TypeVar

import httpx


_T = TypeVar("_T")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_RETRYABLE_STATUS_CODES = frozenset({408, 425, 429})
_TRANSIENT_ERRORS = (
    httpx.NetworkError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
    httpx.ProxyError,
)


class PoliteClient(httpx.Client):
    """A synchronous HTTP client that reserves per-origin request slots."""

    def __init__(
        self,
        *,
        min_interval: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        event_hooks: Mapping[str, list[Callable[..., Any]]] | None = None,
        **kwargs: Any,
    ) -> None:
        self._min_interval = _nonnegative_finite(min_interval, "minimum interval")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        self._pacing_clock = clock
        self._pacing_sleeper = sleeper
        self._pacing_lock = Lock()
        self._next_slot_by_origin: dict[tuple[str, str, int | None], float] = {}

        copied_hooks = {name: list(callbacks) for name, callbacks in (event_hooks or {}).items()}
        copied_hooks["request"] = [*copied_hooks.get("request", []), self._pace_request]
        super().__init__(event_hooks=copied_hooks, **kwargs)

    def _pace_request(self, request: httpx.Request) -> None:
        origin = _origin(request.url)
        with self._pacing_lock:
            now = _finite_clock_value(self._pacing_clock())
            reserved_at = max(now, self._next_slot_by_origin.get(origin, now))
            self._next_slot_by_origin[origin] = reserved_at + self._min_interval
            delay = reserved_at - now
        if delay > 0:
            self._pacing_sleeper(delay)


def retry_operation(
    operation: Callable[[], _T],
    *,
    sleeper: Callable[[float], None] = time.sleep,
    wall_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    retry_after_cap: float = 60.0,
) -> _T:
    """Run an operation up to three times when a failure is transient."""

    if not callable(operation):
        raise TypeError("operation must be callable")
    if not callable(sleeper):
        raise TypeError("sleeper must be callable")
    if not callable(wall_clock):
        raise TypeError("wall_clock must be callable")
    cap = _nonnegative_finite(retry_after_cap, "Retry-After cap")

    for attempt in range(3):
        try:
            return operation()
        except httpx.HTTPStatusError as exc:
            if not _retryable_status(exc.response.status_code):
                raise
            if attempt == 2:
                raise
            retry_after = _retry_after_seconds(exc.response, wall_clock, cap)
        except _TRANSIENT_ERRORS:
            if attempt == 2:
                raise
            retry_after = None

        backoff = 0.5 * (2**attempt)
        sleeper(max(backoff, retry_after or 0.0))

    raise RuntimeError("unreachable")


def _origin(url: httpx.URL) -> tuple[str, str, int | None]:
    scheme = url.scheme.casefold()
    port = url.port if url.port is not None else _DEFAULT_PORTS.get(scheme)
    return scheme, url.host.casefold(), port


def _nonnegative_finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a nonnegative finite number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"{name} must be a nonnegative finite number")
    return normalized


def _finite_clock_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError("clock must return a finite number")
    return float(value)


def _retryable_status(status_code: int) -> bool:
    return status_code in _RETRYABLE_STATUS_CODES or (
        500 <= status_code <= 599 and status_code not in {501, 505}
    )


def _retry_after_seconds(
    response: httpx.Response,
    wall_clock: Callable[[], datetime],
    cap: float,
) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit():
        seconds_decimal = Decimal(value)
        if seconds_decimal >= Decimal(str(cap)):
            return cap
        return float(seconds_decimal)
    try:
        retry_at = parsedate_to_datetime(value)
        now = wall_clock()
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        seconds = (retry_at - now).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(seconds, cap)
