"""Test Lagoon vault offchain metadata fetching.

- Lagoon stores vault descriptions in their web app API, not on-chain
- We reverse-engineered the API endpoints from the Lagoon Next.js JavaScript bundles
"""

import datetime
import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect, detect_vault_features
from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.erc_4626.scan import create_vault_scan_record
from eth_defi.erc_4626.vault_protocol.lagoon.offchain_metadata import (
    LagoonVaultMetadata,
    fetch_lagoon_vaults_for_chain,
)
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDiskCache

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

#: RockSolid rETH Vault on Ethereum - known to have descriptions in Lagoon's API
ROCKSOLID_VAULT_ADDRESS = "0x936facdf10c8c36294e7b9d28345255539d81bc7"

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_ETHEREUM)
    return web3


@flaky.flaky
def test_lagoon_metadata(web3: Web3):
    """Read Lagoon vault metadata from offchain web app API."""

    vault = create_vault_instance_autodetect(
        web3,
        vault_address=ROCKSOLID_VAULT_ADDRESS,
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


@flaky.flaky
def test_lagoon_scan_record_has_descriptions(web3: Web3, tmp_path: Path):
    """Verify that descriptions flow through create_vault_scan_record() for Lagoon vaults."""

    vault_address = Web3.to_checksum_address(ROCKSOLID_VAULT_ADDRESS)
    features = detect_vault_features(web3, vault_address, verbose=False)

    detection = ERC4262VaultDetection(
        chain=1,
        address=vault_address,
        first_seen_at_block=0,
        first_seen_at=datetime.datetime(2024, 1, 1),
        features=features,
        updated_at=datetime.datetime(2024, 1, 1),
        deposit_count=0,
        redeem_count=0,
    )

    token_cache = TokenDiskCache()
    block_number = web3.eth.block_number

    record = create_vault_scan_record(
        web3,
        detection,
        block_number,
        token_cache=token_cache,
    )

    assert record["Protocol"] == "Lagoon Finance"
    assert record["_description"] is not None, f"Expected _description to be set, got record keys: {list(record.keys())}"
    assert len(record["_description"]) > 10, f"Expected non-trivial description, got: {record['_description']}"
    assert record["_short_description"] is not None, f"Expected _short_description to be set"
