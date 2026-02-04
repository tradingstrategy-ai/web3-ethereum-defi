"""Test HypurrFi vault metadata"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.hypurrfi.vault import HypurrFiVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_HYPERLIQUID = os.environ.get("JSON_RPC_HYPERLIQUID")

pytestmark = pytest.mark.skipif(JSON_RPC_HYPERLIQUID is None, reason="JSON_RPC_HYPERLIQUID needed to run these tests")


@pytest.fixture(scope="module")
def anvil_hyperevm_fork(request) -> AnvilLaunch:
    """Fork HyperEVM at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_HYPERLIQUID, fork_block_number=24_743_853)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_hyperevm_fork):
    web3 = create_multi_provider_web3(anvil_hyperevm_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_hypurrfi(
    web3: Web3,
    tmp_path: Path,
):
    """Read HypurrFi vault metadata.

    https://hyperevmscan.io/address/0x8001e1e7b05990d22dd8cdb9737f9fe6589827ce
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x8001e1e7b05990d22dd8cdb9737f9fe6589827ce",
    )

    assert isinstance(vault, HypurrFiVault)
    assert vault.get_protocol_name() == "HypurrFi"
    assert vault.features == {ERC4626Feature.hypurrfi_like}
    assert vault.name == "hyUSDXL (Purr) - 2"
    assert vault.symbol == "hyUSDXL(PURR)-2"

    # Fees are internalised/unknown
    assert vault.get_management_fee("latest") is None
    assert vault.get_performance_fee("latest") is None
    assert vault.has_custom_fees() is False

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0

    # HypurrFi doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_redeem() is False
