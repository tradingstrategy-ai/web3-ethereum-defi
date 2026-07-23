"""Bounded ApeX HTTP session policy tests."""

# ruff: noqa: PLR2004

import datetime
import json
import threading
from collections.abc import Iterator
from email.utils import format_datetime

import pytest
import requests

from eth_defi.apex.session import (
    ApexAPIError,
    ApexDeadlineExceededError,
    ApexResponseTooLargeError,
    ApexSessionPool,
    ApexTimeoutPolicy,
)


class _Response:
    def __init__(
        self,
        payload: object,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.raw = json.dumps(payload).encode()
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size: int) -> Iterator[bytes]:
        del chunk_size
        yield self.raw

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def close(self) -> None:
        self.closed = True


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = responses
        self.closed = False
        self.timeouts: list[tuple[float, float]] = []
        self.calls = 0

    def get(
        self,
        url: str,
        *,
        params: dict[str, str | int] | None,
        timeout: tuple[float, float],
        stream: bool,
    ) -> _Response:
        del url, params, stream
        self.calls += 1
        self.timeouts.append(timeout)
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def _pool(session: _Session, *, max_bytes: int = 1024, retries: int = 0) -> ApexSessionPool:
    pool = ApexSessionPool(
        api_url="https://example.invalid",
        requests_per_second=1000,
        pool_maxsize=1,
        timeout_policy=ApexTimeoutPolicy(
            connect_timeout=10,
            read_timeout=30,
            request_deadline=60,
            max_retry_delay=1,
            max_response_bytes=max_bytes,
        ),
        retries=retries,
    )
    pool._create_session = lambda: session
    return pool


def test_fetch_json_clamps_timeout_and_closes_response() -> None:
    """Clamp socket timeouts to the remaining request budget."""
    response = _Response({"data": {"ok": True}})
    session = _Session([response])
    pool = _pool(session)
    try:
        result = pool.fetch_json(
            "test",
            params=None,
            operation_deadline=pool._clock() + 0.1,
            validator=lambda payload: payload,
        )
        assert result["data"]["ok"] is True
        assert session.timeouts[0][0] <= 0.1
        assert session.timeouts[0][1] <= 0.1
        assert response.closed
    finally:
        pool.close()
    assert session.closed


def test_fetch_json_rejects_oversized_response_and_closes() -> None:
    """Reject oversized streamed JSON and close its response."""
    response = _Response({"data": {"large": "x" * 200}})
    session = _Session([response])
    pool = _pool(session, max_bytes=20)
    try:
        with pytest.raises(ApexResponseTooLargeError):
            pool.fetch_json(
                "test",
                params=None,
                operation_deadline=pool._clock() + 1,
                validator=lambda payload: payload,
            )
        assert response.closed
    finally:
        pool.close()


def test_rate_limiter_queue_honours_deadline() -> None:
    """Fail before network access when limiter queueing exhausts a deadline."""
    response = _Response({"data": {}})
    session = _Session([response])
    pool = ApexSessionPool(
        api_url="https://example.invalid",
        requests_per_second=0.01,
        pool_maxsize=1,
        timeout_policy=ApexTimeoutPolicy(),
        retries=0,
    )
    pool._create_session = lambda: session
    try:
        pool._limiter._next_slot = pool._clock() + 10
        with pytest.raises(ApexDeadlineExceededError):
            pool.fetch_json(
                "test",
                params=None,
                operation_deadline=pool._clock() + 0.01,
                validator=lambda payload: payload,
            )
    finally:
        pool.close()


def test_permanent_http_error_is_not_retried() -> None:
    """Return permanent HTTP client failures without retrying them."""
    response = _Response({"error": "bad request"}, status_code=400)
    session = _Session([response])
    pool = _pool(session, retries=3)
    try:
        with pytest.raises(ApexAPIError, match="ApeX request failed"):
            pool.fetch_json(
                "test",
                params=None,
                operation_deadline=pool._clock() + 1,
                validator=lambda payload: payload,
            )
        assert session.calls == 1
        assert response.closed
    finally:
        pool.close()


def test_malformed_retry_after_uses_bounded_backoff() -> None:
    """Ignore malformed Retry-After values and complete a bounded retry."""
    retryable = _Response(
        {"code": 2},
        status_code=429,
        headers={"Retry-After": "not-a-date"},
    )
    successful = _Response({"data": {"ok": True}})
    session = _Session([retryable, successful])
    pool = _pool(session, retries=1)
    pool._sleeper = lambda _delay: None
    try:
        result = pool.fetch_json(
            "test",
            params=None,
            operation_deadline=pool._clock() + 2,
            validator=lambda payload: payload,
        )
        assert result["data"]["ok"] is True
        assert session.calls == 2
        assert retryable.closed
        assert successful.closed
    finally:
        pool.close()


@pytest.mark.parametrize(
    "retry_after",
    (
        "9999",
        format_datetime(datetime.datetime.fromtimestamp(1100, datetime.UTC)),
    ),
)
def test_retry_after_is_capped_with_injected_clocks(retry_after: str) -> None:
    """Cap numeric and HTTP-date server delays using deterministic clocks."""
    pool = ApexSessionPool(
        api_url="https://example.invalid",
        requests_per_second=1000,
        pool_maxsize=1,
        timeout_policy=ApexTimeoutPolicy(max_retry_delay=2),
        retries=0,
        clock=lambda: 100,
        wall_clock=lambda: 1000,
    )
    try:
        assert pool._retry_delay(0, retry_after, 110) == 2
    finally:
        pool.close()


def test_close_cannot_race_with_session_registration() -> None:
    """Close a session created concurrently without leaking it."""
    session = _Session([])
    creating = threading.Event()
    release = threading.Event()
    pool = _pool(session)

    def create_session() -> _Session:
        creating.set()
        assert release.wait(timeout=1)
        return session

    pool._create_session = create_session
    getter = threading.Thread(target=pool.get_session)
    closer = threading.Thread(target=pool.close)
    getter.start()
    assert creating.wait(timeout=1)
    closer.start()
    closer.join(timeout=0.05)
    release.set()
    getter.join(timeout=1)
    closer.join(timeout=1)
    assert not getter.is_alive()
    assert not closer.is_alive()
    assert session.closed
    assert pool._sessions == []
