"""Top-level shared pytest fixtures.

Currently exposes an **opt-in** session-scoped Anvil fork pool so that many tests
sharing the same ``(chain, fork_block_number, launch config)`` reuse a single
Anvil process instead of each launching (and archive-replaying) its own. This is
Lever 1 of the test-suite performance plan
(:file:`docs/README-test-suite-performance.md`).

The reusable pool lives in :mod:`eth_defi.testing.anvil_fork_pool`; this module
only wires it into a session-scoped fixture. See that module for the usage
contract (the required ``xdist_group`` marker and the CI-gating caveat).
"""

import json
import logging
import os
import resource
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.rpc_cache import seed_default_foundry_rpc_cache
from eth_defi.testing.slow_tests import discover_slow_test_files
from eth_defi.testing.token_cache import (
    install_token_cache,
    is_token_cache_rebuild_requested,
    is_token_cache_seeding_disabled,
    merge_into_token_cache_seed,
)


@pytest.fixture(scope="session", autouse=True)
def _seed_token_cache(worker_id: str) -> Iterator[None]:
    """Install the committed token cache as the default vault token cache.

    Vault token **address** resolution only caches when ``token_cache`` is a
    :py:class:`~eth_defi.token.TokenDiskCache`; the library default is an
    in-memory LRU, which disables that cache and starts empty in every xdist
    worker. Installing the shipped disk cache means vaults resolve their
    denomination / share tokens from disk instead of re-reading them over RPC on
    each cold fork.

    With ``$ETH_DEFI_TOKEN_CACHE_REBUILD`` set, everything resolved during the
    session is merged back into the committed seed at teardown. See
    :mod:`eth_defi.testing.token_cache`.

    :param worker_id:
        ``pytest-xdist`` worker id, so each worker gets its own SQLite file.
    """
    if is_token_cache_seeding_disabled():
        yield
        return

    cache = install_token_cache(worker_id)
    try:
        yield
    finally:
        if is_token_cache_rebuild_requested():
            merge_into_token_cache_seed(cache)


@pytest.fixture(scope="session", autouse=True)
def _seed_foundry_rpc_cache() -> None:
    """Warm the Foundry fork RPC cache from repo-supplied defaults, once per worker.

    Copies any committed / env-supplied default cache files into
    ``~/.foundry/cache/rpc`` before forks launch, so a cold CI cache still starts
    warm for the canonical midnight blocks. Non-destructive (never overwrites a
    warmer live file) and a no-op when no seed files exist. See
    :mod:`eth_defi.testing.rpc_cache`.
    """
    seed_default_foundry_rpc_cache()


@pytest.fixture(scope="session")
def anvil_fork_pool() -> Iterator[AnvilForkPool]:
    """Session-scoped shared Anvil fork pool.

    Opt-in: a test module's own ``web3`` fixture calls
    :meth:`~eth_defi.testing.anvil_fork_pool.AnvilForkPool.get_web3` with its
    ``(rpc_url, fork_block_number)`` to obtain a Web3 backed by a shared fork.

    :return:
        Iterator yielding the pool; all forks are closed on teardown.
    """
    pool = AnvilForkPool()
    try:
        yield pool
    finally:
        pool.close_all()


@pytest.fixture(autouse=True)
def _restore_logging_configuration() -> Iterator[None]:
    """Restore logging configuration after tests which run script entrypoints.

    Console setup replaces pytest's root handlers and adjusts noisy named
    loggers. Close newly installed handlers before their captured streams expire,
    then restore the original handlers and levels for the next test.

    :return:
        Yield control to the test before restoring the worker's logging state.
    """
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    names = ("web3.providers.HTTPProvider", "web3.RequestManager", "urllib3.connectionpool", "eth_defi.token")
    named_levels = {name: logging.getLogger(name).level for name in names}
    try:
        yield
    finally:
        for handler in list(root.handlers):
            if handler not in handlers:
                root.removeHandler(handler)
                handler.close()
        root.handlers[:] = handlers
        root.setLevel(level)
        for name, old_level in named_levels.items():
            logging.getLogger(name).setLevel(old_level)


#: One file per worker keeps suppressed flaky attempts without write contention.
_timing_path: Path | None = None
_timing_worker = "master"
_timing_attempts: dict[str, int] = {}


def pytest_configure(config: pytest.Config) -> None:
    """Start an optional timing report owned by this pytest process.

    Flaky suppresses intermediate log reports, so record where phase reports are
    created. Workers use separate files and the controller never duplicates them.

    :param config:
        Pytest configuration, including xdist worker identity when applicable.
    """
    global _timing_path, _timing_worker  # noqa: PLW0603 - Per-process pytest plugin state.
    _timing_attempts.clear()
    _timing_worker = getattr(config, "workerinput", {}).get("workerid", "master")
    filename = os.environ.get("TEST_TIMINGS_FILE")
    _timing_path = Path(filename) if filename else None
    if _timing_path is not None:
        if _timing_worker != "master":
            _timing_path = _timing_path.with_name(f"{_timing_path.stem}-{_timing_worker}{_timing_path.suffix}")
        _timing_path.parent.mkdir(parents=True, exist_ok=True)
        _timing_path.write_text("")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> Iterator[pytest.TestReport]:
    """Persist every phase before flaky can suppress a failed retry attempt.

    Durations are elapsed time, not CPU consumption. Node IDs retain loadgroup
    suffixes, and worker and attempt fields expose repeated setup costs.

    :param item:
        Test whose phase completed.
    :param call:
        Setup, call or teardown outcome passed to pytest's report builder.
    :return:
        The unchanged pytest phase report.
    """
    report = yield
    if _timing_path is not None:
        if call.when == "setup":
            _timing_attempts[item.nodeid] = _timing_attempts.get(item.nodeid, 0) + 1
        record = {
            "nodeid": item.nodeid,
            "phase": report.when,
            "outcome": report.outcome,
            "duration": report.duration,
            "worker": _timing_worker,
            "attempt": _timing_attempts.get(item.nodeid, 1),
        }
        with _timing_path.open("a") as output:
            output.write(json.dumps(record) + "\n")
    return report


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Record worker CPU and memory accounting after fixture teardown.

    Self and reaped-child CPU are separate so Anvil work is visible. RSS is the
    process-lifetime high-water mark, not a sum of concurrent memory or a per-test
    peak. Live children are excluded from reaped-child accounting. Per-worker
    files avoid concurrent writes; the controller has its own report.

    :param session:
        Finished pytest session.
    :param exitstatus:
        Pytest's result code.
    """
    directory = os.environ.get("TEST_RESOURCES_DIR")
    if directory is None:
        return
    worker = getattr(session.config, "workerinput", {}).get("workerid", "master")
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    output = {
        "worker": worker,
        "exitstatus": int(exitstatus),
        "self_user_cpu_seconds": own.ru_utime,
        "self_system_cpu_seconds": own.ru_stime,
        "reaped_children_user_cpu_seconds": children.ru_utime,
        "reaped_children_system_cpu_seconds": children.ru_stime,
        "self_max_rss": own.ru_maxrss,
        "reaped_children_max_rss": children.ru_maxrss,
        "rss_unit": "bytes" if sys.platform == "darwin" else "KiB",
    }
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{worker}.json").write_text(json.dumps(output, indent=2) + "\n")


@pytest.hookimpl(wrapper=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> Iterator[None]:
    """Prevent narrowed slow-workflow collection from silently losing coverage.

    Main CI still collects the broader suite. Any slow-marked item absent from
    discovery fails with an actionable error, including indirectly added marks.

    :param config:
        Pytest configuration with the repository root.
    :param items:
        Collected items before marker deselection.
    """
    collected = list(items)
    yield
    candidates = set(discover_slow_test_files(config.rootpath))
    missing = {str(item.path.relative_to(config.rootpath)) for item in collected if item.path.is_relative_to(config.rootpath / "tests") and item.get_closest_marker("slow") is not None and "gmx" not in item.path.relative_to(config.rootpath / "tests").parts and item.path.resolve() not in candidates}
    if missing:
        message = "Slow workflow cannot discover these modules; add an explicit pytest.mark.slow: " + ", ".join(sorted(missing))
        raise pytest.UsageError(message)
