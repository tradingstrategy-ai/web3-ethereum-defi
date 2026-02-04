"""Test Altura vault metadata"""

import os
from pathlib import Path

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.altura.vault import AlturaVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_HYPERLIQUID = os.environ.get("JSON_RPC_HYPERLIQUID")

pytestmark = pytest.mark.skipif(
    JSON_RPC_HYPERLIQUID is None,
    reason="JSON_RPC_HYPERLIQUID needed to run these tests",
)


@pytest.fixture(scope="module")
def anvil_hyperliquid_fork(request) -> AnvilLaunch:
    """Fork HyperEVM at a specific block for reproducibility"""
    launch = fork_network_anvil(JSON_RPC_HYPERLIQUID, fork_block_number=23_755_870)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_hyperliquid_fork):
    web3 = create_multi_provider_web3(anvil_hyperliquid_fork.json_rpc_url)
    return web3


@flaky.flaky
def test_altura(
    web3: Web3,
    tmp_path: Path,
):
    """Read Altura vault metadata.

    https://hyperevmscan.io/address/0xd0ee0cf300dfb598270cd7f4d0c6e0d8f6e13f29
    """

    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xd0ee0cf300dfb598270cd7f4d0c6e0d8f6e13f29",
    )

    assert isinstance(vault, AlturaVault)
    assert vault.get_protocol_name() == "Altura"
    assert vault.features == {ERC4626Feature.altura_like}

    # Check vault name and symbol
    assert vault.name == "Altura Vault Tokens"
    assert vault.symbol == "AVLT"

    # Check fee data
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") is None

    # Check exit fee (should be 1 bps = 0.0001)
    exit_fee = vault.get_exit_fee("latest")
    assert exit_fee == pytest.approx(0.0001, rel=0.01)

    # Check link
    assert vault.get_link() == "https://app.altura.trade"

    # Check maxDeposit and maxRedeem with address(0)
    # maxRedeem returns 0 because address(0) has no shares, not because redemptions are closed
    # This vault cannot use address(0) checks for redemption availability
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit > 0  # Deposits are open
    assert max_redeem == 0  # address(0) has no shares
    assert vault.can_check_redeem() is False
