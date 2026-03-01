"""Shared pytest fixtures for Lighter tests.

Lighter pool data is fetched from public endpoints — no API key required.
"""

import pytest

from eth_defi.lighter.session import create_lighter_session
from eth_defi.lighter.vault import fetch_all_pools


@pytest.fixture(scope="module")
def lighter_session():
    """Create a shared HTTP session for all tests in this module."""
    return create_lighter_session()


@pytest.fixture(scope="module")
def lighter_pool_listing(lighter_session):
    """Fetch all Lighter public pools (cached per module)."""
    return fetch_all_pools(lighter_session)


@pytest.fixture(scope="module")
def lighter_llp_pool(lighter_pool_listing):
    """The LLP (Lighter Liquidity Pool) from the pool listing."""
    llp_pools = [p for p in lighter_pool_listing if p.is_llp]
    assert len(llp_pools) == 1, f"Expected 1 LLP pool, found {len(llp_pools)}"
    return llp_pools[0]
