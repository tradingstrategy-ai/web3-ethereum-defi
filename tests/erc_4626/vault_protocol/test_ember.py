"""Test Ember vault metadata"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.ember.offchain_metadata import fetch_ember_vaults
from eth_defi.erc_4626.vault_protocol.ember.vault import EmberVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultTechnicalRisk

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_496_689)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_ember(
    web3: Web3,
    tmp_path: Path,
):
    """Read Ember vault metadata with offchain data.

    https://etherscan.io/address/0xf3190a3ecc109f88e7947b849b281918c798a0c4
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xf3190a3ecc109f88e7947b849b281918c798a0c4",
    )

    assert isinstance(vault, EmberVault)
    assert vault.get_protocol_name() == "Ember"
    assert vault.features == {ERC4626Feature.ember_like}

    # Check risk level
    assert vault.get_risk() == VaultTechnicalRisk.severe

    # Offchain metadata from Ember's Bluefin API
    assert vault.ember_metadata is not None
    assert vault.ember_metadata["name"] == "Crosschain USD Vault"
    assert vault.description is not None
    assert len(vault.description) > 10
    assert vault.short_description is not None

    # Fees from offchain API (management fee is 0% for this vault)
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") is not None
    assert vault.get_performance_fee("latest") >= 0

    # Manager info
    assert vault.ember_metadata["manager_name"] == "Third Eye"

    # Withdrawal period from offchain API
    assert vault.get_estimated_lock_up().days == 4

    # Check link
    assert vault.get_link() == "https://ember.so/earn"


def test_ember_offchain_fetch(tmp_path: Path):
    """Test Ember offchain metadata fetch and caching."""

    vaults = fetch_ember_vaults(cache_path=tmp_path)

    # Should find Ethereum vaults
    assert len(vaults) >= 5

    # Check the Crosschain USD Vault is present
    crosschain_vault = vaults.get("0xf3190A3ECC109F88e7947b849b281918c798A0C4")
    assert crosschain_vault is not None
    assert crosschain_vault["name"] == "Crosschain USD Vault"
    assert crosschain_vault["description"] is not None
    assert crosschain_vault["management_fee"] is not None
    assert crosschain_vault["weekly_performance_fee"] is not None
    assert crosschain_vault["withdrawal_period_days"] is not None
    assert crosschain_vault["reported_apy"] is not None
    assert crosschain_vault["manager_name"] is not None

    # Verify cache file was written
    cache_file = tmp_path / "ember_vaults.json"
    assert cache_file.exists()
    assert cache_file.stat().st_size > 0
