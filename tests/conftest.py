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

import os
import pathlib
import shutil
from typing import Iterator

import pytest

from eth_defi.testing.anvil_fork_pool import AnvilForkPool


# ---------------------------------------------------------------------------
# TEMPORARY DIAGNOSTIC (2026-07-24): why is the Anvil fork RPC cache not warm
# on CI? Locally ~/.foundry/cache/rpc grows to ~8.7 MB; on CI the saved cache is
# only ~40 KB. This hook prints, at session start and finish, exactly where
# Foundry's RPC cache lives and how big it is, so the CI log tells us:
#   - whether FOUNDRY_DIR relocates the cache away from the path actions/cache
#     saves (~/.foundry/cache/rpc),
#   - whether Anvil writes to it at all during the run (start -> finish delta),
#   - which networks/blocks get cached.
# Remove this block once the cause is confirmed.
# ---------------------------------------------------------------------------
def _anvil_cache_dirs() -> list[pathlib.Path]:
    """Candidate Foundry RPC cache directories to inspect."""
    bases = []
    if os.environ.get("FOUNDRY_DIR"):
        bases.append(pathlib.Path(os.environ["FOUNDRY_DIR"]))
    bases.append(pathlib.Path.home() / ".foundry")
    # Anvil binary location may hint at a bundled foundry dir.
    anvil = shutil.which("anvil")
    return [b / "cache" / "rpc" for b in bases] + ([pathlib.Path(anvil)] if anvil else [])


def _log_anvil_cache_state(label: str) -> None:
    worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    out = [f"ANVIL_CACHE_DIAG [{label}/{worker}] FOUNDRY_DIR={os.environ.get('FOUNDRY_DIR')!r} HOME={os.environ.get('HOME')!r} CI={os.environ.get('CI')!r}"]
    seen = set()
    for d in _anvil_cache_dirs():
        if d in seen:
            continue
        seen.add(d)
        if d.name != "rpc":
            out.append(f"ANVIL_CACHE_DIAG   anvil-bin={d}")
            continue
        if d.exists():
            files = list(d.rglob("*.json"))
            size = sum(f.stat().st_size for f in files if f.exists())
            nets = sorted({f.relative_to(d).parts[0] for f in files if f.relative_to(d).parts})
            out.append(f"ANVIL_CACHE_DIAG   {d}: {len(files)} files, {size / 1e6:.2f} MB, networks={nets}")
        else:
            out.append(f"ANVIL_CACHE_DIAG   {d}: MISSING")
    print("\n".join(out), flush=True)


def pytest_configure(config: pytest.Config) -> None:
    """Log the Anvil RPC cache state at session start (diagnostic)."""
    _log_anvil_cache_state("start")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Log the Anvil RPC cache state at session finish (diagnostic)."""
    _log_anvil_cache_state("finish")


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
