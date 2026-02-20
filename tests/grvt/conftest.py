"""Shared pytest fixtures for GRVT tests.

This module provides common fixtures used across all GRVT test modules.
GRVT vault data is fetched from public endpoints — no API key required.

Vault discovery uses the public GraphQL API at
``https://edge.grvt.io/query`` (includes per-vault fee data).
"""

import pytest
import requests

from eth_defi.grvt.vault import fetch_vault_listing_graphql


@pytest.fixture(scope="module")
def grvt_session():
    """Create a shared HTTP session for all tests in this module."""
    return requests.Session()


@pytest.fixture(scope="module")
def grvt_vault_listing(grvt_session):
    """Fetch the vault listing once for all tests in this module."""
    return fetch_vault_listing_graphql(grvt_session, only_discoverable=True)


@pytest.fixture(scope="module")
def grvt_sample_vault(grvt_vault_listing):
    """First discoverable vault from the listing."""
    assert len(grvt_vault_listing) > 0, "No discoverable GRVT vaults found"
    return grvt_vault_listing[0]
