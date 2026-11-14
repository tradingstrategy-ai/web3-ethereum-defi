"""Paced reads of the public Morpho API.

Worker-local HTTP connections share a two-requests-per-second quota and a
persisted provider cooldown. See `Morpho's rate limits
<https://docs.morpho.org/api/#rate-limits-and-best-practices>`__.
"""

import datetime
import json
import logging
import math
import sqlite3
import threading
import time
from _thread import RLock  # noqa: PLC2701 -- concrete lock type; threading.RLock is a factory.
from collections.abc import Callable
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from pathlib import Path

from atomicwrites import atomic_write
from filelock import FileLock, Timeout
from pyrate_limiter import BucketFullException, FileLockSQLiteBucket, Limiter, RequestRate
from requests import Response, Session

logger = logging.getLogger(__name__)

#: Public read endpoint, shared by V1 and V2 warning queries.
MORPHO_GRAPHQL_URL = "https://api.morpho.org/graphql"
#: Persistent quota and cooldown state, shared by production containers.
MORPHO_STATE_DIRECTORY = Path("~/.tradingstrategy/morpho").expanduser()
#: Provider identity independent of chain or vault version.
MORPHO_BUCKET = "api.morpho.org"


class _SharedSQLiteBucket(FileLockSQLiteBucket):
    """Use a lock in the mounted state directory instead of library-global /tmp."""

    def __init__(self, maxsize: int, identity: str, path: str) -> None:
        """Bind quota accounting to a shared filesystem lock.

        The parent refreshes its count on every acquisition, unlike SQLiteBucket.

        :param maxsize: Bucket capacity supplied by PyrateLimiter.
        :param identity: Host identity supplied by PyrateLimiter.
        :param path: SQLite database shared by workers and processes.
        """
        super().__init__(maxsize=maxsize, identity=identity, path=path, timeout=1)
        self._lock = FileLock(path + ".lock", timeout=1)


def parse_retry_after(value: str | None, now: float) -> float:
    """Convert a provider delay into an absolute UTC epoch timestamp.

    Both HTTP date and delta-seconds forms are accepted. Missing or unusable
    headers use Morpho's documented ten-minute cooldown; seven-day bans remain
    intact. This does not sleep or shorten valid provider deadlines.

    :param value: Optional Retry-After response header.
    :param now: Current UTC epoch seconds.
    :return: Finite, representable future UTC epoch seconds.
    """
    try:
        try:
            retry_at = now + float(value)
        except ValueError:
            date = parsedate_to_datetime(value)
            retry_at = date.replace(tzinfo=datetime.UTC).timestamp() if date.tzinfo is None else date.timestamp()
        if not math.isfinite(retry_at) or retry_at <= now:
            message = "invalid provider delay"
            raise ValueError(message)
        datetime.datetime.fromtimestamp(retry_at, datetime.UTC)
        return retry_at
    except (TypeError, ValueError, OverflowError, OSError):
        return now + 600


@dataclass(slots=True)
class _RequestState:
    """One process's shared quota handles and persisted cooldown view."""

    #: Shared state directory.
    directory: Path
    #: Aggregate host request budget.
    requests_per_second: int
    #: UTC epoch clock for persisted state.
    clock: Callable[[], float] = time.time
    #: Monotonic clock for the local queue budget.
    monotonic: Callable[[], float] = time.monotonic
    #: Injectable queue sleeper.
    sleeper: Callable[[float], None] = time.sleep
    #: Lazily initialised, process-shared quota handle.
    limiter: Limiter | None = None
    #: Provider ban retained even if its file cannot be written.
    retry_at: float = 0
    #: Suppress repeated local-state warnings until recovery.
    warned: bool = False
    #: Last provider deadline announced by this process.
    observed_retry_at: float = 0
    #: Announce quota exhaustion once, without a warning for every vault.
    queue_warned: bool = False
    #: Serialise limiter initialisation and local state updates.
    lock: RLock = field(default_factory=RLock)

    @property
    def cooldown_path(self) -> Path:
        """Return the provider state file.

        Its JSON timestamp is separate from resettable quota tables.

        :return: Shared provider cooldown path.
        """
        return self.directory / "provider-cooldown.json"

    def warn(self, operation: str, error: BaseException) -> None:
        """Log one deferred local-state failure until storage recovers.

        Repeated vault lookups must not produce a warning storm.

        :param operation: Deferred operation.
        :param error: Local storage or locking failure, without HTTP credentials.
        :return: None.
        """
        if not self.warned:
            path = self.cooldown_path if operation.startswith("cooldown") else self.directory / "rate-limit.sqlite"
            logger.warning("Morpho %s deferred at %s: %s; state access will be retried; repair persistent state or permission errors", operation, path, error)
            self.warned = True

    def read_retry_at(self) -> float:
        """Read provider cooldown without changing an expired timestamp.

        Atomic replacement makes unlocked readers safe. Malformed existing
        files raise rather than silently bypassing a possible provider ban.

        :return: UTC epoch deadline, or zero for missing state.
        :raises ValueError: Invalid state schema or timestamp.
        """
        try:
            data = json.loads(self.cooldown_path.read_text())
        except FileNotFoundError:
            return 0
        if not isinstance(data, dict) or data.get("version") != 1:
            message = "invalid cooldown schema"
            raise ValueError(message)
        date = datetime.datetime.fromisoformat(data["retry_at"])
        if date.tzinfo is not None:
            message = "cooldown must use naive UTC"
            raise ValueError(message)
        retry_at = date.replace(tzinfo=datetime.UTC).timestamp()
        datetime.datetime.fromtimestamp(retry_at, datetime.UTC)
        return retry_at

    def cooling_down(self) -> bool:
        """Check persisted and unsaved provider bans before a network read.

        Invalid state is retried on later calls but never permits HTTP traffic.

        :return: Whether this read must be deferred.
        """
        with self.lock:
            try:
                retry_at = self.read_retry_at()
            except (OSError, ValueError, KeyError, TypeError, OverflowError, Timeout) as error:
                self.warn("cooldown read", error)
                return True
            deadline = max(retry_at, self.retry_at)
            if deadline > self.clock():
                if deadline > self.observed_retry_at:
                    date = datetime.datetime.fromtimestamp(deadline, datetime.UTC).replace(tzinfo=None)
                    logger.warning("Morpho metadata reads deferred until %s UTC: persisted provider cooldown", date)
                    self.observed_retry_at = deadline
                return True
            return False

    def warn_queue_timeout(self, timeout: float) -> None:
        """Announce exhausted queueing once without flooding the scan log.

        A later scan can retry these lookups under the same provider budget.

        :param timeout: Exhausted queue budget in seconds.
        :return: None.
        """
        with self.lock:
            if not self.queue_warned:
                logger.warning("Morpho quota queue budget of %s seconds exhausted; warning metadata deferred to a later scan", timeout)
                self.queue_warned = True

    def acquire(self, remaining: float) -> float:
        """Try one quota acquisition, without sleeping under any lock.

        The caller rechecks the remaining queue budget after contention.

        :param remaining: Seconds remaining in the caller's queue budget.
        :return: Zero on acquisition, otherwise the bucket's requested wait.
        """
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            if self.limiter is None:
                self.limiter = Limiter(
                    RequestRate(self.requests_per_second, 1),
                    bucket_class=_SharedSQLiteBucket,
                    bucket_kwargs={"path": str(self.directory / "rate-limit.sqlite")},
                    time_function=self.clock,
                )
            try:
                self.limiter.try_acquire(MORPHO_BUCKET)
            except BucketFullException as error:
                return max(0.001, min(float(error.meta_info["remaining_time"]), remaining))
            except (OSError, sqlite3.Error, Timeout):
                # The library releases quota locks for a full bucket, but not
                # arbitrary SQLite failures. Do not strand other processes.
                bucket = self.limiter.bucket_group.get(MORPHO_BUCKET)
                if bucket is not None:
                    bucket._lock.release(force=True)
                    bucket.close()
                self.limiter = None
                raise
            self.warned = False
            return 0

    def record_cooldown(self, value: str | None) -> None:
        """Persist a 429 without shortening an existing provider deadline.

        The in-memory deadline is set first so a failed write still stops all
        worker-local sessions in this process. Successful HTTP responses never
        remove cooldown state.

        :param value: Retry-After response header.
        :return: None.
        """
        with self.lock:
            proposed = parse_retry_after(value, self.clock())
            self.retry_at = max(self.retry_at, proposed)
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                with FileLock(str(self.cooldown_path) + ".lock", timeout=1):
                    previous = self.read_retry_at()
                    self.retry_at = max(self.retry_at, previous)
                    if self.retry_at > previous:
                        date = datetime.datetime.fromtimestamp(self.retry_at, datetime.UTC).replace(tzinfo=None)
                        with atomic_write(str(self.cooldown_path), overwrite=True) as output:
                            json.dump({"version": 1, "retry_at": date.isoformat()}, output)
                        logger.warning("Morpho HTTP 429: metadata refresh deferred until %s UTC", date)
                        self.observed_retry_at = self.retry_at
            except (OSError, ValueError, KeyError, TypeError, OverflowError, Timeout) as error:
                self.warn("cooldown write (other processes may not see this ban)", error)


class MorphoSession:
    """Reusable HTTP connections for paced, read-only Morpho queries."""

    def __init__(self, state: _RequestState, timeout: float, queue_timeout: float) -> None:
        """Attach a worker's connections to process-shared request state.

        Use :func:`create_morpho_session` rather than constructing this directly.

        :param state: Shared quota and cooldown state.
        :param timeout: HTTP inactivity timeout in seconds.
        :param queue_timeout: Maximum quota queueing time in seconds.
        """
        self._http = Session()
        self._state = state
        self.timeout = timeout
        self.queue_timeout = queue_timeout

    def fetch_response(self, payload: dict) -> Response | None:
        """Send one existing read query, or defer without network traffic.

        There are no inline retries or redirects. Every actual attempt consumes
        quota; queued requests observe cooldowns recorded by another worker.

        :param payload: Existing read-only GraphQL query and variables.
        :return: HTTP response, or None for cooldown/local-state deferral.
        """
        state = self._state
        deadline = state.monotonic() + self.queue_timeout
        while not state.cooling_down():
            remaining = deadline - state.monotonic()
            if remaining <= 0:
                state.warn_queue_timeout(self.queue_timeout)
                return None
            try:
                delay = state.acquire(remaining)
            except (OSError, sqlite3.Error, Timeout) as error:
                state.warn("quota acquisition", error)
                return None
            remaining = deadline - state.monotonic()
            if remaining <= 0:
                state.warn_queue_timeout(self.queue_timeout)
                return None
            if delay:
                state.sleeper(min(delay, remaining))
                continue
            if state.cooling_down():
                return None
            response = self._http.post(MORPHO_GRAPHQL_URL, json=payload, timeout=self.timeout, allow_redirects=False)
            if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
                state.record_cooldown(response.headers.get("Retry-After"))
                response.close()
                return None
            return response
        return None


_states: dict[Path, _RequestState] = {}
_states_lock = threading.Lock()
_worker = threading.local()


def create_morpho_session(
    state_directory: Path = MORPHO_STATE_DIRECTORY,
    requests_per_second: int = 2,
    timeout: float = 30,
    queue_timeout: float = 60,
) -> MorphoSession:
    """Reuse a worker-local session with a shared provider request budget.

    Sessions and separate processes using the same directory coordinate through
    SQLite and a lock in that directory, including across container mounts.

    :param state_directory: Persistent quota/cooldown directory.
    :param requests_per_second: Positive integer aggregate request rate.
    :param timeout: Finite positive HTTP inactivity timeout in seconds.
    :param queue_timeout: Finite positive quota queue budget in seconds.
    :return: Worker-local session; callers must not close the shared instance.
    """
    assert isinstance(requests_per_second, int) and requests_per_second > 0
    assert all(math.isfinite(value) and value > 0 for value in (timeout, queue_timeout))
    directory = state_directory.expanduser().resolve()
    with _states_lock:
        if directory not in _states:
            _states[directory] = _RequestState(directory, requests_per_second)
        state = _states[directory]
        assert state.requests_per_second == requests_per_second, "Sessions sharing a Morpho state directory must use the same request rate"
    sessions = getattr(_worker, "sessions", None)
    if sessions is None:
        sessions = _worker.sessions = {}
    session_key = (directory, timeout, queue_timeout)
    if session_key not in sessions:
        sessions[session_key] = MorphoSession(state, timeout, queue_timeout)
    return sessions[session_key]
