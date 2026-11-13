"""Bounded pytest retries for transient failures in live Hypersync tests.

Load this pytest plugin and mark live integration tests with
``pytest.mark.hypersync_retry``. The existing ``flaky`` plugin reruns the test
with fresh function fixtures; ``pytest-timeout`` bounds the complete protocol,
including setup, attempts, backoff and teardown, to five minutes.
"""

import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from types import TracebackType

import pytest

logger = logging.getLogger(__name__)

#: Total per-test budget, including all attempts and quota waits.
HYPERSYNC_TEST_TIMEOUT = 300

#: Leave room for pytest bookkeeping before starting another attempt.
_RETRY_MARGIN = 5


@dataclass(slots=True)
class _RetryState:
    """Retry bookkeeping owned by one parametrised pytest item."""

    #: Monotonic deadline shared by all attempts.
    deadline: float
    #: Number of accepted retries.
    retries: int = 0
    #: Flaky checks the same failure more than once.
    last_error: BaseException | None = None
    #: Cached decision prevents duplicate sleeps for the same failure.
    retry: bool = False


_STATE = pytest.StashKey[_RetryState]()


def pytest_configure(config: pytest.Config) -> None:
    """Register the opt-in live Hypersync retry marker.

    :param config: Pytest session configuration.
    :return: None.
    """
    config.addinivalue_line("markers", "hypersync_retry: retry transient Hypersync failures within a five-minute total test budget")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Serialise marked live reads while preserving existing shared-fork groups.

    :param items: Tests before xdist assigns its loadgroup node identifiers.
    :return: None; standalone live reads share one worker.
    """
    for item in items:
        if item.get_closest_marker("hypersync_retry") is not None and item.get_closest_marker("xdist_group") is None:
            item.add_marker(pytest.mark.xdist_group("hypersync-live"))


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item) -> Iterator[bool | None]:
    """Start the retry deadline before pytest-timeout and flaky run the test.

    A single outer timeout covers every attempt. Signal timeouts fail this test
    without terminating the entire CI worker when the budget is exhausted.

    :param item: Collected test, including its parametrisation.
    :return: The underlying pytest protocol result.
    """
    if item.get_closest_marker("hypersync_retry") is not None:
        item.stash[_STATE] = _RetryState(time.monotonic() + HYPERSYNC_TEST_TIMEOUT)
        item.add_marker(pytest.mark.flaky(max_runs=20, min_passes=1, rerun_filter=retry_hypersync_failure), append=False)
        item.add_marker(pytest.mark.timeout(HYPERSYNC_TEST_TIMEOUT, method="signal", func_only=False), append=False)
    return (yield)


def retry_hypersync_failure(err: tuple[type[BaseException], BaseException, TracebackType | None], name: str, test: pytest.Item, plugin: object) -> bool:  # noqa: ARG001 - flaky callback contract.
    """Back off only for recognised transient transport and indexer failures.

    Assertions, metadata errors and unrelated runtime errors remain failures.
    Envio's reset hint takes precedence over exponential waits of 15-60 seconds.
    Flaky may call its filter twice per failure, so cache the decision per error.

    :param err: Exception information supplied by flaky.
    :param name: Test name used in progress logging.
    :param test: Pytest item owning the shared retry deadline.
    :param plugin: Flaky plugin supplied by its callback contract.
    :return: Whether another attempt is allowed after the quota wait.
    """
    # Unrelated test suites must load the plugin without the optional SDK.
    from eth_defi.hypersync.hypersync_timestamp import HypersyncFlaky, is_hypersync_retryable_runtime_error  # noqa: PLC0415

    error = err[1]
    transient = isinstance(error, (HypersyncFlaky, TimeoutError)) or (isinstance(error, RuntimeError) and (is_hypersync_retryable_runtime_error(error) or any(message in str(error).lower() for message in ("timed out", "dns error", "connection reset", "502 bad gateway", "503 service unavailable"))))
    if not transient:
        return False
    state = test.stash[_STATE]
    if error is state.last_error:
        return state.retry
    state.last_error = error
    delay = min(15 * 2**state.retries, 60)
    reset = re.search(r"resets_in=(\d+(?:\.\d+)?)s", str(error))
    if reset is not None:
        delay = max(delay, float(reset[1]) + 1)
    state.retry = time.monotonic() + delay + _RETRY_MARGIN < state.deadline
    if state.retry:
        state.retries += 1
        logger.warning("Hypersync test %s deferred for %.1fs after a transient failure (retry %s, %.1fs budget left)", name, delay, state.retries, state.deadline - time.monotonic())
        time.sleep(delay)
    return state.retry
