"""Test Ember vault metadata"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
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
    """Read Ember vault metadata.

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

    # Fees are internalised in the share price and not readable on-chain
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None

    # Check lock-up
    assert vault.get_estimated_lock_up().days == 4

    # Check link
    assert vault.get_link() == "https://ember.so/earn"
