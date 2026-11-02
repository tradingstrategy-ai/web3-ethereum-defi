"""Time-budgeted retries of :py:class:`eth_defi.logging_retry.LoggingRetry`."""

import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3 import HTTPResponse
from urllib3.exceptions import MaxRetryError

import eth_defi.logging_retry
from eth_defi.logging_retry import LoggingRetry


class _AlwaysUnavailableHandler(BaseHTTPRequestHandler):
    """Answer every request with HTTP 503, like an API during an outage."""

    def do_GET(self) -> None:  # noqa: N802
        self.server.hits += 1
        self.send_response(503)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format, *args) -> None:  # noqa: A002
        pass


@pytest.fixture()
def unavailable_server() -> Iterator[ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AlwaysUnavailableHandler)
    server.hits = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def test_logging_retry_backs_off_exponentially_for_ten_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry an outage with exponential backoff until a 10 minute budget is used.

    1. Replace the monotonic clock so sleeps are simulated, not waited.
    2. Fail every attempt with HTTP 503, advancing the clock by each backoff sleep.
    3. Verify the exponential schedule, the 120 second cap and the shortened final sleep.
    4. Verify the mounted policy, shared by later requests, never started a clock.
    """
    # 1. Replace the monotonic clock so sleeps are simulated, not waited.
    clock = [1000.0]
    monkeypatch.setattr(eth_defi.logging_retry.time, "monotonic", lambda: clock[0])
    policy = LoggingRetry(
        total=None,
        backoff_factor=0.5,
        status_forcelist=[503],
        max_retry_duration=600,
    )

    # 2. Fail every attempt with HTTP 503, advancing the clock by each backoff sleep.
    retry = policy
    sleeps = []
    with pytest.raises(MaxRetryError):
        while True:
            retry = retry.increment("GET", "/api/v1/account", response=HTTPResponse(status=503))
            sleeps.append(retry.get_backoff_time())
            clock[0] += sleeps[-1]

    # 3. Verify the exponential schedule, the 120 second cap and the shortened final sleep.
    assert sleeps == [0, 1, 2, 4, 8, 16, 32, 64, 120, 120, 120, 113]
    assert sum(sleeps) == 600

    # 4. Verify the mounted policy, shared by later requests, never started a clock.
    assert policy.retry_started_at is None


def test_logging_retry_time_budget_over_http(unavailable_server: ThreadingHTTPServer) -> None:
    """Stop retrying a real HTTP 503 outage when the time budget is used.

    1. Mount a time-budgeted policy without a retry count limit.
    2. Request an endpoint that always answers HTTP 503.
    3. Verify the request kept retrying until the budget ran out, then failed.
    4. Verify a second request receives a fresh budget from the shared policy.
    """
    # 1. Mount a time-budgeted policy without a retry count limit.
    policy = LoggingRetry(
        total=None,
        backoff_factor=0.05,
        backoff_max=0.1,
        status_forcelist=[503],
        max_retry_duration=1.0,
    )
    session = requests.Session()
    session.mount("http://", HTTPAdapter(max_retries=policy))
    url = f"http://127.0.0.1:{unavailable_server.server_port}/api"

    # 2. Request an endpoint that always answers HTTP 503.
    started_at = time.monotonic()
    with pytest.raises(requests.exceptions.RetryError):
        session.get(url, timeout=5)
    elapsed = time.monotonic() - started_at

    # 3. Verify the request kept retrying until the budget ran out, then failed.
    first_hits = unavailable_server.hits
    assert first_hits > 6
    assert 1.0 <= elapsed < 2.0

    # 4. Verify a second request receives a fresh budget from the shared policy.
    started_at = time.monotonic()
    with pytest.raises(requests.exceptions.RetryError):
        session.get(url, timeout=5)
    assert time.monotonic() - started_at >= 1.0
    assert unavailable_server.hits - first_hits > 6
    assert policy.retry_started_at is None


def test_logging_retry_count_limit_applies_with_time_budget(unavailable_server: ThreadingHTTPServer) -> None:
    """Keep the ``total`` count limit when a time budget is also given.

    1. Mount a policy allowing two retries within a long time budget.
    2. Verify the request fails after three attempts, long before the budget.
    """
    # 1. Mount a policy allowing two retries within a long time budget.
    policy = LoggingRetry(
        total=2,
        backoff_factor=0.01,
        status_forcelist=[503],
        max_retry_duration=600,
    )
    session = requests.Session()
    session.mount("http://", HTTPAdapter(max_retries=policy))

    # 2. Verify the request fails after three attempts, long before the budget.
    with pytest.raises(requests.exceptions.RetryError):
        session.get(f"http://127.0.0.1:{unavailable_server.server_port}/api", timeout=5)
    assert unavailable_server.hits == 3
