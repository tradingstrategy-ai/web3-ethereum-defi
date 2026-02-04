"""Test Hyperlend vault metadata"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.hyperlend.vault import WrappedHLPVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_HYPERLIQUID = os.environ.get("JSON_RPC_HYPERLIQUID")

pytestmark = pytest.mark.skipif(JSON_RPC_HYPERLIQUID is None, reason="JSON_RPC_HYPERLIQUID needed to run these tests")


@pytest.fixture(scope="module")
def anvil_hyperliquid_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_HYPERLIQUID, fork_block_number=24_882_542)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_hyperliquid_fork):
    web3 = create_multi_provider_web3(anvil_hyperliquid_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_hyperlend(
    web3: Web3,
    tmp_path: Path,
):
    """Read Hyperlend Wrapped HLP vault metadata.

    https://hyperevmscan.io/address/0x06fd9d03b3d0f18e4919919b72d30c582f0a97e5
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x06fd9d03b3d0f18e4919919b72d30c582f0a97e5",
    )

    assert isinstance(vault, WrappedHLPVault)
    assert vault.get_protocol_name() == "Hyperlend"
    assert vault.features == {ERC4626Feature.hyperlend_like}
    assert vault.name == "Wrapped HLP"

    # Fee data
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.10

    # Link to the vault
    assert vault.get_link() == "https://app.hyperlend.finance/hlp"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit == 0
    assert max_redeem == 0

    # Hyperlend doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_max_deposit_and_redeem() is False
