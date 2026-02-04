"""Test USDX Money vault metadata"""

import os
from pathlib import Path

import pytest
from web3 import Web3
import flaky

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import get_vault_protocol_name, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.usdx_money.vault import USDXMoneyVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

pytestmark = pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests")


@pytest.fixture(scope="module")
def anvil_ethereum_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_ETHEREUM, fork_block_number=24_249_000)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_ethereum_fork):
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url, retries=2)
    return web3


@flaky.flaky
def test_usdx_money(
    web3: Web3,
    tmp_path: Path,
):
    """Read USDX Money sUSDX vault metadata.

    https://etherscan.io/address/0x7788a3538c5fc7f9c7c8a74eac4c898fc8d87d92
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0x7788a3538c5fc7f9c7c8a74eac4c898fc8d87d92",
    )

    assert isinstance(vault, USDXMoneyVault)
    assert vault.get_protocol_name() == "USDX Money"
    assert vault.features == {ERC4626Feature.usdx_money_like}

    # Check vault metadata
    assert "sUSDX" in vault.name or "Staked USDX" in vault.name

    # Check fee data
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0

    # Check that we can get link
    link = vault.get_link()
    assert "usdx.money" in link

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit == 0
    assert max_redeem == 0

    # USDX Money doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_max_deposit_and_redeem() is False
