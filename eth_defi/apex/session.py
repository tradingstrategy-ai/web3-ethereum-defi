"""Bounded HTTP session pool for the ApeX public API.

Every worker receives a private :py:class:`requests.Session`, while all workers
share one budget-aware process limiter. Monotonic budgets clamp queueing,
timeouts and retries, but the requests socket timeout remains inactivity-based.
"""

# ruff: noqa: EM101

import json
import logging
import math
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Callable, TypeVar

import requests
from requests.adapters import HTTPAdapter

from eth_defi.apex.constants import (
    APEX_API_BASE_URL,
    APEX_DEFAULT_CONNECT_TIMEOUT,
    APEX_DEFAULT_MAX_RESPONSE_BYTES,
    APEX_DEFAULT_MAX_RETRY_DELAY,
    APEX_DEFAULT_READ_TIMEOUT,
    APEX_DEFAULT_REQUEST_DEADLINE,
    APEX_DEFAULT_REQUESTS_PER_SECOND,
    APEX_DEFAULT_RETRIES,
)

logger = logging.getLogger(__name__)

ParsedResponse = TypeVar("ParsedResponse")

_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class ApexAPIError(RuntimeError):
    """Base exception for an invalid or failed ApeX API operation."""


class ApexDeadlineExceededError(ApexAPIError):
    """Raised when an HTTP operation detects exhausted monotonic budget."""


class ApexResponseTooLargeError(ApexAPIError):
    """Raised when a JSON response exceeds its configured byte limit."""


class _ApexRetryableHTTPError(ApexAPIError):
    """Internal retryable HTTP status with an optional server delay."""

    def __init__(self, status_code: int, retry_after: str | None) -> None:
        """Create an internal retryable status exception.

        The optional raw server delay is retained for bounded ``Retry-After``
        parsing by the request loop.

        :param status_code:
            Retryable HTTP response status.
        :param retry_after:
            Optional raw ``Retry-After`` header.
        """
        super().__init__(f"Retryable ApeX HTTP status {status_code}")
        self.retry_after = retry_after


@dataclass(slots=True, frozen=True)
class ApexTimeoutPolicy:
    """Finite timeout and response-bound configuration."""

    #: TCP connection timeout in seconds.
    connect_timeout: float = APEX_DEFAULT_CONNECT_TIMEOUT

    #: Socket inactivity timeout in seconds.
    read_timeout: float = APEX_DEFAULT_READ_TIMEOUT

    #: Monotonic budget for one request attempt in seconds.
    request_deadline: float = APEX_DEFAULT_REQUEST_DEADLINE

    #: Longest retry delay in seconds.
    max_retry_delay: float = APEX_DEFAULT_MAX_RETRY_DELAY

    #: Largest accepted JSON response.
    max_response_bytes: int = APEX_DEFAULT_MAX_RESPONSE_BYTES

    def __post_init__(self) -> None:
        """Validate all timeout policy fields.

        Invalid values fail configuration before a request or worker session
        can be created.

        :return:
            None.
        """
        finite_values = (
            self.connect_timeout,
            self.read_timeout,
            self.request_deadline,
            self.max_retry_delay,
        )
        if not all(math.isfinite(value) and value > 0 for value in finite_values):
            raise ValueError("All ApeX timeout values must be finite and positive")
        if self.max_response_bytes <= 0:
            raise ValueError("ApeX maximum response size must be positive")


class _DeadlineRateLimiter:
    """Simple thread-safe limiter whose queueing honours operation budgets."""

    def __init__(
        self,
        requests_per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        """Initialise a budget-aware shared request limiter.

        The limiter spaces reservations across all worker-local sessions while
        clock and sleeper injection keep its behaviour deterministic in tests.

        :param requests_per_second:
            Shared finite positive request rate.
        :param clock:
            Monotonic clock callable.
        :param sleeper:
            Delay callable.
        """
        if not math.isfinite(requests_per_second) or requests_per_second <= 0:
            raise ValueError("requests_per_second must be finite and positive")
        self._interval = 1.0 / requests_per_second
        self._clock = clock
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def acquire(self, deadline: float) -> None:
        """Reserve one rate-limited request slot.

        Queueing consumes the supplied monotonic operation budget and fails
        before sleeping when no request slot remains available.

        :param deadline:
            Monotonic operation-budget boundary.
        :return:
            None.
        """
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_slot)
            if slot >= deadline:
                raise ApexDeadlineExceededError("ApeX request expired while queued for the rate limiter")
            self._next_slot = slot + self._interval
        delay = slot - now
        if delay > 0:
            self._sleeper(delay)
        if self._clock() >= deadline:
            raise ApexDeadlineExceededError("ApeX request expired while queued for the rate limiter")


class ApexSessionPool:
    """Worker-local HTTP sessions with shared bounded request policy."""

    def __init__(
        self,
        *,
        api_url: str,
        requests_per_second: float,
        pool_maxsize: int,
        timeout_policy: ApexTimeoutPolicy,
        retries: int,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        """Initialise the worker-local session registry and shared limiter.

        Use :func:`create_apex_session_pool` for normal construction. Clock and
        sleeper injection keep deadline behaviour deterministic in tests.

        :param api_url:
            Public ApeX API base URL.
        :param requests_per_second:
            Shared finite positive request rate.
        :param pool_maxsize:
            Per-worker connection pool size.
        :param timeout_policy:
            Request deadline and response-bound policy.
        :param retries:
            Retry count after the initial attempt.
        :param clock:
            Monotonic clock callable.
        :param sleeper:
            Delay callable.
        :param wall_clock:
            Unix timestamp clock used only for HTTP-date ``Retry-After``.
        """
        if pool_maxsize <= 0:
            raise ValueError("pool_maxsize must be positive")
        if retries < 0:
            raise ValueError("retries cannot be negative")
        self.api_url = api_url.rstrip("/")
        self.timeout_policy = timeout_policy
        self.retries = retries
        self._clock = clock
        self._sleeper = sleeper
        self._wall_clock = wall_clock
        self._limiter = _DeadlineRateLimiter(requests_per_second, clock=clock, sleeper=sleeper)
        self._pool_maxsize = pool_maxsize
        self._local = threading.local()
        self._sessions: list[tuple[int, requests.Session]] = []
        self._sessions_lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._worker_condition = threading.Condition()
        self._active_history_workers = 0
        self._closed = False

    def _create_session(self) -> requests.Session:
        """Create one requests session with adapter retries disabled.

        Retry timing is implemented by this integration so it can honour the
        shared monotonic operation budget.

        :return:
            Configured private requests session.
        """
        session = requests.Session()
        adapter = HTTPAdapter(max_retries=0, pool_connections=self._pool_maxsize, pool_maxsize=self._pool_maxsize)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def get_session(self) -> requests.Session:
        """Return the calling worker's private HTTP session.

        Sessions are created once per live thread and registered for bounded
        cleanup by the owning scan or command.

        :return:
            Worker-local configured requests session.
        """
        with self._sessions_lock:
            if self._closed:
                raise RuntimeError("ApeX session pool is closed")
            session = getattr(self._local, "session", None)
            if session is None:
                session = self._create_session()
                self._local.session = session
                self._sessions.append((threading.get_ident(), session))
        return session

    def close_worker_sessions(self) -> None:
        """Close sessions created outside the calling thread.

        A new joblib thread pool is created for each scan cycle. Closing its
        worker-local sessions after the fetch phase prevents dead worker
        threads and their connection pools from accumulating during loop mode.
        The calling thread's ranking session remains available for reuse.

        :return:
            None.
        """
        with self._worker_condition:
            self._worker_condition.wait_for(lambda: self._active_history_workers == 0)
        current_thread_id = threading.get_ident()
        with self._sessions_lock:
            worker_sessions = tuple(session for thread_id, session in self._sessions if thread_id != current_thread_id)
            self._sessions = [(thread_id, session) for thread_id, session in self._sessions if thread_id == current_thread_id]
        for session in worker_sessions:
            session.close()

    @contextmanager
    def history_worker_scope(self) -> Iterator[None]:
        """Track one active history worker through cleanup.

        Joblib may surface one worker exception before sibling threads have
        returned. The owning scan waits for all scopes to exit before closing
        any worker-local session.

        :return:
            Context manager yielding while one history worker is active.
        """
        with self._worker_condition:
            self._active_history_workers += 1
        try:
            yield
        finally:
            with self._worker_condition:
                self._active_history_workers -= 1
                self._worker_condition.notify_all()

    @contextmanager
    def scan_scope(self) -> Iterator[None]:
        """Reserve this pool for one complete scan.

        A pool may serve many sequential loop cycles, but concurrent scans
        would make one caller unable to distinguish another caller's sessions
        during worker cleanup. Fail fast instead of closing active sessions
        owned by a different scan.

        :return:
            Context manager yielding while this pool has exclusive scan
            ownership.
        """
        if not self._scan_lock.acquire(blocking=False):
            raise RuntimeError("ApeX session pool already has an active scan")
        try:
            yield
        finally:
            self._scan_lock.release()

    def _retry_delay(self, attempt: int, retry_after: str | None, deadline: float) -> float:
        """Calculate one capped budget-aware retry delay.

        Invalid HTTP-date or numeric ``Retry-After`` values are ignored in
        favour of exponential backoff.

        :param attempt:
            Zero-based retry number.
        :param retry_after:
            Optional server-supplied ``Retry-After`` header.
        :param deadline:
            Absolute enclosing operation deadline.
        :return:
            Delay in seconds, capped to the remaining operation budget.
        """
        delay = min(0.5 * (2**attempt), self.timeout_policy.max_retry_delay)
        if retry_after:
            try:
                parsed_delay = float(retry_after)
                if not math.isfinite(parsed_delay) or parsed_delay < 0:
                    raise ValueError
                delay = parsed_delay
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(retry_after)
                    delay = max(0.0, parsed.timestamp() - self._wall_clock())
                except (TypeError, ValueError, OverflowError):
                    logger.warning("Ignoring malformed ApeX Retry-After header: %s", retry_after)
            delay = min(delay, self.timeout_policy.max_retry_delay)
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise ApexDeadlineExceededError("ApeX operation deadline exhausted before retry")
        return min(delay, remaining)

    def _read_json_response(self, response: requests.Response, request_deadline: float) -> object:
        """Read one streamed response within its size and time limits.

        The monotonic budget is checked whenever ``iter_content`` yields.
        Because requests exposes an inactivity timeout rather than a hard
        wall-clock timeout, a slow-drip read can be detected later than the
        nominal budget.

        :param response:
            Successful HTTP response.
        :param request_deadline:
            Monotonic request-attempt budget boundary.
        :return:
            Decoded JSON value.
        """
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                advertised_size = int(content_length)
            except ValueError as exc:
                raise ApexAPIError(f"ApeX returned malformed Content-Length: {content_length!r}") from exc
            if advertised_size > self.timeout_policy.max_response_bytes:
                raise ApexResponseTooLargeError(f"ApeX response advertised more than {self.timeout_policy.max_response_bytes} bytes")

        raw = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if self._clock() >= request_deadline:
                raise ApexDeadlineExceededError("ApeX response exceeded its total request deadline")
            if chunk:
                raw.extend(chunk)
            if len(raw) > self.timeout_policy.max_response_bytes:
                raise ApexResponseTooLargeError(f"ApeX response exceeded {self.timeout_policy.max_response_bytes} bytes")
        if self._clock() >= request_deadline:
            raise ApexDeadlineExceededError("ApeX response exceeded its total request deadline")
        try:
            return json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApexAPIError("ApeX returned malformed JSON") from exc

    def _fetch_json_attempt(
        self,
        url: str,
        *,
        params: dict[str, str | int] | None,
        request_deadline: float,
        validator: Callable[[object], ParsedResponse],
    ) -> ParsedResponse:
        """Perform one request attempt and always close its response.

        :param url:
            Absolute endpoint URL.
        :param params:
            Query parameters.
        :param request_deadline:
            Monotonic attempt-budget boundary including limiter queueing.
        :param validator:
            Endpoint-specific typed parser.
        :return:
            Parsed response.
        """
        self._limiter.acquire(request_deadline)
        remaining = request_deadline - self._clock()
        if remaining <= 0:
            raise ApexDeadlineExceededError("ApeX request deadline exhausted before connection")
        timeout = (
            min(self.timeout_policy.connect_timeout, remaining),
            min(self.timeout_policy.read_timeout, remaining),
        )
        response = self.get_session().get(url, params=params, timeout=timeout, stream=True)
        try:
            if response.status_code in _RETRYABLE_STATUS_CODES:
                raise _ApexRetryableHTTPError(response.status_code, response.headers.get("Retry-After"))
            response.raise_for_status()
            return validator(self._read_json_response(response, request_deadline))
        finally:
            response.close()

    def fetch_json(
        self,
        path: str,
        *,
        params: dict[str, str | int] | None,
        operation_deadline: float,
        validator: Callable[[object], ParsedResponse],
    ) -> ParsedResponse:
        """Fetch and validate one bounded JSON response.

        Retry sleeps, limiter queueing and timeout arguments consume the
        supplied operation budget. Endpoint-specific validation happens inside
        the retry loop so malformed HTTP-200 envelopes are retried consistently.

        :param path:
            API path relative to :py:attr:`api_url`.
        :param params:
            Query parameters.
        :param operation_deadline:
            :func:`time.monotonic` budget boundary shared by the enclosing
            ranking or vault-history operation.
        :param validator:
            Endpoint parser returning the typed response.
        :return:
            Parsed endpoint response.
        :raise ApexAPIError:
            All bounded attempts failed.
        """
        url = f"{self.api_url}/{path.lstrip('/')}"
        last_error: BaseException | None = None
        for attempt in range(self.retries + 1):
            request_deadline = min(operation_deadline, self._clock() + self.timeout_policy.request_deadline)
            try:
                return self._fetch_json_attempt(
                    url,
                    params=params,
                    request_deadline=request_deadline,
                    validator=validator,
                )
            except requests.HTTPError as exc:
                last_error = exc
                break
            except (requests.RequestException, ApexAPIError, ValueError, TypeError, KeyError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                retry_after = exc.retry_after if isinstance(exc, _ApexRetryableHTTPError) else None
                delay = self._retry_delay(attempt, retry_after, operation_deadline)
                if delay > 0:
                    self._sleeper(delay)
        if isinstance(last_error, ApexAPIError):
            raise last_error
        raise ApexAPIError(f"ApeX request failed: {last_error}") from last_error

    def close(self) -> None:
        """Close every worker-local session created by this pool.

        Closing an active scan would race with in-flight network work, so the
        operation fails fast until the scan scope has ended.

        :return:
            None.
        """
        if not self._scan_lock.acquire(blocking=False):
            raise RuntimeError("Cannot close ApeX session pool during an active scan")
        try:
            with self._sessions_lock:
                sessions = tuple(session for _, session in self._sessions)
                self._sessions.clear()
                self._closed = True
            for session in sessions:
                session.close()
        finally:
            self._scan_lock.release()

    def __enter__(self) -> "ApexSessionPool":
        """Return this open pool for context-manager use.

        The corresponding exit method closes every registered session.

        :return:
            This configured session pool.
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close all sessions when leaving a context manager.

        Exception information is accepted for the context-manager protocol but
        is not suppressed.

        :param exc_type:
            Optional exception type raised inside the context.
        :param exc_value:
            Optional exception instance raised inside the context.
        :param traceback:
            Optional exception traceback.
        :return:
            None.
        """
        self.close()


def create_apex_session_pool(
    requests_per_second: float = APEX_DEFAULT_REQUESTS_PER_SECOND,
    pool_maxsize: int = 8,
    timeout_policy: ApexTimeoutPolicy | None = None,
    *,
    api_url: str = APEX_API_BASE_URL,
    retries: int = APEX_DEFAULT_RETRIES,
) -> ApexSessionPool:
    """Create a bounded worker-local ApeX HTTP session pool.

    :param requests_per_second:
        Shared maximum request rate.
    :param pool_maxsize:
        Connection pool size for each worker-local session.
    :param timeout_policy:
        Finite network, deadline and response-size policy.
    :param api_url:
        Public API base URL override, primarily for tests.
    :param retries:
        Retry count after the initial request.
    :return:
        Configured session pool.
    """
    return ApexSessionPool(
        api_url=api_url,
        requests_per_second=requests_per_second,
        pool_maxsize=pool_maxsize,
        timeout_policy=timeout_policy or ApexTimeoutPolicy(),
        retries=retries,
    )
