"""Gains (Gtrade) vault tests."""

import datetime
import os
from decimal import Decimal

import pytest

from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect, detect_vault_features
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.gains.deposit_redeem import GainsDepositManager, GainsRedemptionRequest
from eth_defi.gains.testing import force_next_gains_epoch
from eth_defi.gains.vault import GainsVault
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
pytestmark = pytest.mark.skipif(not JSON_RPC_ARBITRUM, reason="Set JSON_RPC_ARBITRUM to run this test")


@pytest.fixture(scope="module")
def vault(web3) -> GainsVault:
    """gTrade USDC vault on Arbitrum"""
    vault_address = "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0"
    vault = create_vault_instance_autodetect(web3, vault_address)
    assert isinstance(vault, GainsVault)
    return vault


def test_gains_features(web3):
    vault_address = "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0"
    features = detect_vault_features(web3, vault_address, verbose=True)
    assert ERC4626Feature.gains_like in features, f"Got features: {features}"


def test_gains_read_data(web3, vault: GainsVault):
    assert vault.name == "Gains Network USDC"
    # https://arbiscan.io/address/0xBF55C78132ab06a2B217040b7A7F20B5cBD47982#readContract
    assert vault.gains_open_trades_pnl_feed.address == "0xBF55C78132ab06a2B217040b7A7F20B5cBD47982"
    assert vault.fetch_epoch_duration() == datetime.timedelta(seconds=21600)
    assert vault.fetch_current_epoch_start() == datetime.datetime(2025, 8, 31, 21, 53, 55)
    assert vault.fetch_withdraw_epochs_time_lock() == 3
    assert vault.estimate_redemption_ready() == datetime.datetime(2025, 9, 1, 15, 53, 55)
    assert vault.get_max_discount_percent() == 0.05


def test_gains_deposit_withdraw(
    web3_write: Web3,
    test_user,
    usdc: TokenDetails,
):
    """Do deposit/redeem cycle on Gains vault."""
    web3 = web3_write
    vault: GainsVault = create_vault_instance_autodetect(web3, "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0")

    amount = Decimal(100)

    tx_hash = usdc.approve(
        vault.address,
        amount,
    ).transact({"from": test_user})
    assert_transaction_success_with_explanation(web3, tx_hash)

    deposit_manager = vault.get_deposit_manager()
    assert isinstance(deposit_manager, GainsDepositManager)

    estimated = deposit_manager.estimate_deposit(test_user, amount)
    assert estimated == pytest.approx(Decimal('81.54203'))

    deposit_request = deposit_manager.create_deposit_request(
        test_user,
        amount=amount,
    )
    deposit_request.broadcast()

    share_token = vault.share_token
    shares = share_token.fetch_balance_of(test_user)
    assert shares == pytest.approx(Decimal("81.54203"))

    # Withdrawals can be only executed on the first two days of an epoch.
    # We start in a state that is outside of this window, so we need to move to the next epoch first.
    assert vault.open_pnl_contract.functions.nextEpochValuesRequestCount().call() == 2
    assert deposit_manager.can_create_redemption_request(test_user) is False
    assert not deposit_manager.has_synchronous_redemption()

    # 0. Clear epoch
    force_next_gains_epoch(
        vault,
        test_user,
    )

    estimated = deposit_manager.estimate_redeem(test_user, shares)
    assert estimated == pytest.approx(Decimal('100'))

    # 1. Create a redemption request
    assert deposit_manager.estimate_redemption_delay() == datetime.timedelta(days=3)
    assert vault.open_pnl_contract.functions.nextEpochValuesRequestCount().call() == 0
    assert deposit_manager.can_create_redemption_request(test_user) is True, f"We have {vault.open_pnl_contract.functions.nextEpochValuesRequestCount().call()}"
    redemption_request = deposit_manager.create_redemption_request(
        owner=test_user,
        shares=shares,
    )
    assert isinstance(redemption_request, GainsRedemptionRequest)
    assert redemption_request.owner == test_user
    assert redemption_request.to == test_user
    assert redemption_request.shares == shares

    # 2.a) Broadcast and parse redemption request tx
    assert deposit_manager.is_redemption_in_progress(test_user) is False
    assert vault.open_pnl_contract.functions.nextEpochValuesRequestCount().call() == 0
    tx_hashes = []
    funcs = redemption_request.funcs
    tx_hash = funcs[0].transact({"from": test_user, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hashes.append(tx_hash)

    # 2.b) Parse result
    redemption_ticket = redemption_request.parse_redeem_transaction(tx_hashes)
    assert redemption_ticket.raw_shares == pytest.approx(81.54203 * 10**6)
    assert redemption_ticket.owner == test_user
    assert redemption_ticket.to == test_user
    assert redemption_ticket.current_epoch == 197
    assert redemption_ticket.unlock_epoch == 200
    assert vault.vault_contract.functions.totalSharesBeingWithdrawn(test_user).call() == redemption_ticket.raw_shares
    assert deposit_manager.is_redemption_in_progress(test_user) is True

    # Cannot redeem yet, need to wait for the next epoch
    assert deposit_manager.can_finish_redeem(redemption_ticket) is False

    # 3. Move forward few epochs where our request unlocks
    for i in range(0, 3):
        force_next_gains_epoch(
            vault,
            test_user,
        )

    assert vault.fetch_current_epoch() >= 200

    # Cannot redeem yet, need to wait for the next epoch
    assert deposit_manager.can_finish_redeem(redemption_ticket) is True

    # 4. Settle our redemption
    func = deposit_manager.finish_redemption(redemption_ticket)
    tx_hash = func.transact({"from": test_user})
    assert_transaction_success_with_explanation(web3, tx_hash)

    shares = share_token.fetch_balance_of(test_user)
    assert shares == 0
