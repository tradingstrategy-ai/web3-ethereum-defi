"""Test Yuzu Money vault metadata."""

import os

import flaky
import pytest
from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.yuzu_money.vault import YuzuMoneyVault

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import PLASMA_MIDNIGHT_BLOCK

JSON_RPC_PLASMA = os.environ.get("JSON_RPC_PLASMA")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_PLASMA is None, reason="JSON_RPC_PLASMA needed to run these tests"),
    # Shared with the other Plasma midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:plasma:midnight"),
]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Plasma fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_PLASMA, PLASMA_MIDNIGHT_BLOCK)


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
    assert vault.can_check_redeem() is False
