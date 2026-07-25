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
from eth_defi.testing.rpc_cache import seed_default_foundry_rpc_cache
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
        if cache is not None and is_token_cache_rebuild_requested():
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
