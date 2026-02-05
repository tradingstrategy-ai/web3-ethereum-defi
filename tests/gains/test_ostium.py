"""Ostium vault tests."""

import datetime
import os
from decimal import Decimal

import pytest

from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect, detect_vault_features
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import GainsDepositManager, GainsRedemptionRequest
from eth_defi.erc_4626.vault_protocol.gains.testing import force_next_gains_epoch
from eth_defi.erc_4626.vault_protocol.gains.vault import GainsHistoricalReader, GainsVault, OstiumVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.base import DEPOSIT_CLOSED_CAP_REACHED

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
CI = os.environ.get("CI") == "true"
pytestmark = pytest.mark.skipif(not JSON_RPC_ARBITRUM, reason="Set JSON_RPC_ARBITRUM to run this test")


@pytest.fixture(scope="module")
def anvil_arbitrum_fork(request) -> AnvilLaunch:
    launch = fork_network_anvil(JSON_RPC_ARBITRUM, fork_block_number=375_216_652)
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_arbitrum_fork):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)
    return web3


@pytest.fixture(scope="module")
def vault(web3) -> GainsVault:
    """ostiumLP vault on Arbitrum"""
    vault_address = "0x20d419a8e12c45f88fda7c5760bb6923cee27f98"
    vault = create_vault_instance_autodetect(web3, vault_address)
    assert isinstance(vault, GainsVault)
    assert isinstance(vault, OstiumVault)
    return vault


def test_ostium_features(web3):
    vault_address = "0x20d419a8e12c45f88fda7c5760bb6923cee27f98"
    features = detect_vault_features(web3, vault_address, verbose=True)
    assert ERC4626Feature.ostium_like in features, f"Got features: {features}"


@pytest.mark.skipif(CI, reason="This just does not work on Github due to some RPC errors even after changing provider")
def test_ostium_read_data(web3, vault: GainsVault):
    assert vault.name == f"Ostium Liquidity Pool Vault"
    # https://arbiscan.io/address/0x20d419a8e12c45f88fda7c5760bb6923cee27f98#readContract
    assert vault.gains_open_trades_pnl_feed is None
    assert vault.open_pnl_contract.address == "0xE607aC9FF58697c5978AfA1Fc1C5C437a6D1858c"
    assert vault.fetch_epoch_duration() == datetime.timedelta(seconds=10800)
    assert vault.fetch_current_epoch_start() == datetime.datetime(2025, 9, 2, 12, 44, 20)
    assert vault.fetch_withdraw_epochs_time_lock() == 3
    assert vault.estimate_redemption_ready() is None

    # Ostium inherits Gains historical reader
    reader = vault.get_historical_reader(stateful=False)
    assert isinstance(reader, GainsHistoricalReader)

    # Read vault state at the fork block using the historical reader
    block_number = web3.eth.block_number
    block = web3.eth.get_block(block_number)
    timestamp = datetime.datetime.fromtimestamp(block["timestamp"], tz=datetime.timezone.utc).replace(tzinfo=None)

    calls = list(reader.construct_multicalls())
    call_results = [c.call_as_result(web3=web3, block_identifier=block_number) for c in calls]
    vault_read = reader.process_result(block_number, timestamp, call_results)

    assert vault_read.block_number == block_number
    assert vault_read.share_price == Decimal("1.098157")
    assert vault_read.total_assets == Decimal("31668258.181211")
    assert vault_read.total_supply == Decimal("28772893.664136")
    assert vault_read.max_deposit == Decimal("1555220.50855")
    assert vault_read.max_redeem is None

    # Ostium: deposits are always open
    assert vault_read.deposits_open is True
    # At this fork block, nextEpochValuesRequestCount == 0, so redemptions are open
    assert vault_read.redemption_open is True
    # Ostium does not track trading state
    assert vault_read.trading is None

    # Verify export round-trip
    exported = vault_read.export()
    assert exported["deposits_open"] == "true"
    assert exported["redemption_open"] == "true"
    assert exported["trading"] == ""

    # Test deposit/redemption status methods
    deposit_reason = vault.fetch_deposit_closed_reason()
    redemption_reason = vault.fetch_redemption_closed_reason()
    deposit_next = vault.fetch_deposit_next_open()
    redemption_next = vault.fetch_redemption_next_open()

    # Check deposits - could be open or closed depending on vault supply vs cap
    # At fork block 375_216_652, maxDeposit > 0 so deposits should be open
    assert deposit_reason is None or deposit_reason == DEPOSIT_CLOSED_CAP_REACHED
    # Deposit timing unpredictable (depends on supply vs cap)
    assert deposit_next is None

    # At fork block, redemptions are open (nextEpochValuesRequestCount == 0)
    assert redemption_reason is None
    assert redemption_next is None


@pytest.mark.skipif(CI, reason="This just does not work on Github due to some RPC errors even after changing provider")
def test_ostium_deposit_withdraw(
    web3_write: Web3,
    test_user,
    usdc: TokenDetails,
):
    """Do deposit/redeem cycle on Ostium vault."""
    web3 = web3_write
    vault: GainsVault = create_vault_instance_autodetect(web3, "0x20d419a8e12c45f88fda7c5760bb6923cee27f98")

    deposit_manager = vault.get_deposit_manager()
    assert isinstance(deposit_manager, GainsDepositManager)

    amount = Decimal(100)

    tx_hash = usdc.approve(
        vault.address,
        amount,
    ).transact({"from": test_user})
    assert_transaction_success_with_explanation(web3, tx_hash)

    deposit_request = deposit_manager.create_deposit_request(
        test_user,
        amount=amount,
    )
    deposit_request.broadcast()

    share_token = vault.share_token
    shares = share_token.fetch_balance_of(test_user)
    assert shares == pytest.approx(Decimal("91.061642"))

    # Withdrawals can be only executed on the first two days of an epoch.
    # We start in a state that is outside of this window, so we need to move to the next epoch first.
    assert vault.open_pnl_contract.functions.nextEpochValuesRequestCount().call() == 0
    assert deposit_manager.can_create_redemption_request(test_user) is True

    # 1. Create a redemption request
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
    assert vault.open_pnl_contract.functions.nextEpochValuesRequestCount().call() == 0
    tx_hashes = []
    funcs = redemption_request.funcs
    tx_hash = funcs[0].transact({"from": test_user, "gas": 1_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hashes.append(tx_hash)

    # 2.b) Parse result
    redemption_ticket = redemption_request.parse_redeem_transaction(tx_hashes)
    assert redemption_ticket.raw_shares == pytest.approx(91.061642 * 10**6)
    assert redemption_ticket.owner == test_user
    assert redemption_ticket.to == test_user
    assert redemption_ticket.current_epoch == 122
    assert redemption_ticket.unlock_epoch == 125

    # Cannot redeem yet, need to wait for the next epoch
    assert deposit_manager.can_finish_redeem(redemption_ticket) is False

    # 3. Move forward few epochs where our request unlocks
    for i in range(0, 3):
        force_next_gains_epoch(
            vault,
            test_user,
        )

    assert vault.fetch_current_epoch() >= 125

    # Cannot redeem yet, need to wait for the next epoch
    assert deposit_manager.can_finish_redeem(redemption_ticket) is True

    # 4. Settle our redemption
    func = deposit_manager.finish_redemption(redemption_ticket)
    tx_hash = func.transact({"from": test_user})
    assert_transaction_success_with_explanation(web3, tx_hash)

    shares = share_token.fetch_balance_of(test_user)
    assert shares == 0
