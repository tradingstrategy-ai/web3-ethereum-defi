"""Test Yuzu Money vault metadata."""

import os

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.yuzu_money.vault import YuzuMoneyVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_PLASMA = os.environ.get("JSON_RPC_PLASMA")

pytestmark = pytest.mark.skipif(
    JSON_RPC_PLASMA is None,
    reason="JSON_RPC_PLASMA needed to run these tests",
)


@pytest.fixture(scope="module")
def anvil_plasma_fork(request) -> AnvilLaunch:
    """Fork at a specific block for reproducibility."""
    launch = fork_network_anvil(JSON_RPC_PLASMA, fork_block_number=10687319)
    try:
        yield launch
    finally:
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_plasma_fork):
    web3 = create_multi_provider_web3(anvil_plasma_fork.json_rpc_url)
    return web3


# Anvil is broken
@flaky.flaky
def test_yuzu_money(web3: Web3):
    """Read Yuzu Money vault metadata."""

    # yzPP (Yuzu Protection Pool) vault on Plasma
    vault = create_vault_instance_autodetect(
        web3,
        vault_address="0xebfc8c2fe73c431ef2a371aea9132110aab50dca",
    )

    assert isinstance(vault, YuzuMoneyVault)
    assert vault.get_protocol_name() == "Yuzu Money"
    assert vault.features == {ERC4626Feature.yuzu_money_like}

    # Yuzu Money has no fees (uses yield-smoothing mechanism)
    # https://yuzu-money.gitbook.io/yuzu-money/faq-1/performance-fee
    assert vault.get_management_fee("latest") == 0.0
    assert vault.get_performance_fee("latest") == 0.0

    # Check the vault link
    assert vault.get_link() == "https://app.yuzu.money/"

    # Check maxDeposit/maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem == 0

    # Yuzu Money doesn't support address(0) checks for maxDeposit/maxRedeem
    assert vault.can_check_max_deposit_and_redeem() is False
