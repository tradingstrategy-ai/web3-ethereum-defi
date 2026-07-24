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

from typing import Iterator

import pytest

from eth_defi.testing.anvil_fork_pool import AnvilForkPool


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
