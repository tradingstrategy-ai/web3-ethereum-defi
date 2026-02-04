"""Test Avant Protocol vault metadata."""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.avant.vault import AvantVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_AVALANCHE = os.environ.get("JSON_RPC_AVALANCHE")

pytestmark = pytest.mark.skipif(
    JSON_RPC_AVALANCHE is None,
    reason="JSON_RPC_AVALANCHE needed to run these tests",
)


@pytest.fixture(scope="module")
def anvil_avalanche_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_AVALANCHE, fork_block_number=76086124)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_avalanche_fork):
    web3 = create_multi_provider_web3(anvil_avalanche_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_avant(
    web3: Web3,
    tmp_path: Path,
):
    """Read Avant Protocol vault metadata.

    https://snowtrace.io/address/0x06d47f3fb376649c3a9dafe069b3d6e35572219e
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x06d47f3fb376649c3a9dafe069b3d6e35572219e",
    )

    assert isinstance(vault, AvantVault)
    assert vault.get_protocol_name() == "Avant"
    assert vault.features == {ERC4626Feature.avant_like}

    # Verify fee structure - Avant has no fees
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0

    # Verify vault name and symbol
    assert vault.name == "Staked avUSD"
    assert vault.symbol == "savUSD"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # Avant doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
