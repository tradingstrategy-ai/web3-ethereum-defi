"Autopool vault tests"

import os
from decimal import Decimal
from pathlib import Path

import pytest

from web3 import Web3

from eth_defi.abi import ZERO_ADDRESS_STR
from eth_defi.erc_4626.vault_protocol.autopool.vault import AutoPoolVault, AutoPoolDepositManager
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.vault.fee import VaultFeeMode

from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    # Shared with the other Arbitrum midnight-block characterisation tests.
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]

#: Tokemak arbUSD vault on Arbitrum
AUTOPOOL_VAULT_ADDRESS = "0xf63b7f49b4f5dc5d0e7e583cfd79dc64e646320c"

#: A Safe that holds shares in the Autopool vault at the pinned block
DEPOSITOR_SAFE = "0x62e6a0111f6DaeDf94d24197C32e469EA8eF1A8E"


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Web3 backed by a shared Arbitrum fork from the session-scoped pool.

    Reuses one Anvil process across every module carrying the matching
    ``xdist_group`` marker. Read-only test, so no snapshot/revert reset is
    needed between tests.
    """
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(scope="module")
def vault(web3) -> AutoPoolVault:
    vault = create_vault_instance_autodetect(
        web3,
        vault_address=AUTOPOOL_VAULT_ADDRESS,
    )
    assert isinstance(vault, AutoPoolVault)
    return vault


def test_autopool(
    web3: Web3,
    vault: AutoPoolVault,
):
    """Read Autopool metadata."""

    assert vault.features == {ERC4626Feature.autopool_like}
    assert isinstance(vault, AutoPoolVault)
    assert vault.get_protocol_name() == "AUTO Finance"
    assert vault.get_management_fee("latest") == 0.00
    assert vault.get_performance_fee("latest") == 0.00
    assert vault.get_fee_mode() == VaultFeeMode.internalised_minting

    # Check maxDeposit and maxRedeem with address(0)
    max_deposit = vault.vault_contract.functions.maxDeposit(ZERO_ADDRESS_STR).call()
    max_redeem = vault.vault_contract.functions.maxRedeem(ZERO_ADDRESS_STR).call()
    assert max_deposit >= 0
    assert max_redeem >= 0
    assert vault.can_check_redeem() is False


def test_autopool_redeem_estimate(
    web3: Web3,
    vault: AutoPoolVault,
):
    """Autopool previewRedeem() reverts — deposit manager falls back to share price.

    - Tokemak arbUSD vault uses flash-accounting (like Uniswap v4)
    - previewRedeem() always reverts with BalanceNotSettled() outside callback context
    - AutoPoolDepositManager.estimate_redeem() bypasses previewRedeem() and uses
      totalAssets/totalSupply share price instead
    - We verify this produces a sane, non-zero value for a real depositor
    """
    deposit_manager = vault.deposit_manager
    assert isinstance(deposit_manager, AutoPoolDepositManager)

    # The Safe holds real shares in this vault at the pinned block
    shares = vault.share_token.fetch_balance_of(DEPOSITOR_SAFE)
    assert shares > 0, f"Safe {DEPOSITOR_SAFE} has no shares at the pinned block"

    # This should NOT revert — it bypasses previewRedeem() and uses share price
    estimated_value = deposit_manager.estimate_redeem(DEPOSITOR_SAFE, shares)

    # Sanity: estimated value must be a positive number roughly in the same
    # ballpark as the share count (arbUSD denomination token is a stablecoin)
    assert estimated_value > 0
    assert isinstance(estimated_value, Decimal)

    # Cross-check: the estimate should be close to shares * (totalAssets / totalSupply)
    share_price = vault.fetch_share_price(block_identifier="latest")
    assert share_price > 0
    expected = shares * share_price
    assert estimated_value == pytest.approx(expected)
