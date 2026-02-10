"""Test Lagoon vault offchain metadata fetching.

- Lagoon stores vault descriptions in their web app API, not on-chain
- We reverse-engineered the API endpoints from the Lagoon Next.js JavaScript bundles
"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.lagoon.offchain_metadata import (
    LagoonVaultMetadata,
    fetch_lagoon_vaults_for_chain,
)
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    return web3


@flaky.flaky
def test_lagoon_metadata(web3: Web3, tmp_path: Path):
    """Read Lagoon vault metadata from offchain web app API."""

    # RockSolid rETH Vault on Ethereum
    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x936facdf10c8c36294e7b9d28345255539d81bc7",
    )

    assert isinstance(vault, LagoonVault)
    assert vault.lagoon_metadata is not None
    assert vault.description is not None
    assert "rETH" in vault.description
    assert vault.short_description is not None


@flaky.flaky
def test_lagoon_metadata_cache(tmp_path: Path):
    """Verify disk caching works for Lagoon metadata."""
    chain_id = 1  # Ethereum
    vaults = fetch_lagoon_vaults_for_chain(chain_id, cache_path=tmp_path)
    assert isinstance(vaults, dict)
    assert len(vaults) > 0

    # Should have cached the file
    cache_file = tmp_path / f"lagoon_vaults_chain_{chain_id}.json"
    assert cache_file.exists()
    assert cache_file.stat().st_size > 0

    # Second call should use cache (no API calls)
    vaults2 = fetch_lagoon_vaults_for_chain(chain_id, cache_path=tmp_path)
    assert vaults2 == vaults

    # Check that at least one vault has a description
    has_description = any(v.get("description") for v in vaults.values())
    assert has_description, "Expected at least one Lagoon vault with a description"
