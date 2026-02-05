"""Gains (Gtrade) vault tests."""

import datetime
import os
from decimal import Decimal

import flaky
import pytest

from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect, detect_vault_features
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import GainsDepositManager, GainsRedemptionRequest
from eth_defi.erc_4626.vault_protocol.gains.testing import force_next_gains_epoch
from eth_defi.erc_4626.vault_protocol.gains.vault import GainsHistoricalReader, GainsVault
from eth_defi.event_reader.multicall_batcher import read_multicall_historical_stateful
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details, USDC_NATIVE_TOKEN
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import VaultHistoricalReadMulticaller

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
    assert vault.name == "gTrade (Gains Network USDC)"
    # https://arbiscan.io/address/0xBF55C78132ab06a2B217040b7A7F20B5cBD47982#readContract
    assert vault.gains_open_trades_pnl_feed.address == "0xBF55C78132ab06a2B217040b7A7F20B5cBD47982"
    assert vault.fetch_epoch_duration() == datetime.timedelta(seconds=21600)
    assert vault.fetch_current_epoch_start() == datetime.datetime(2025, 8, 31, 21, 53, 55)
    assert vault.fetch_withdraw_epochs_time_lock() == 3
    now_ = datetime.datetime(2025, 9, 1)
    assert vault.estimate_redemption_ready(now_) == datetime.datetime(2025, 9, 1, 15, 53, 55)
    assert vault.get_max_discount_percent() == 0.05

    # Verify Gains-specific historical reader is returned
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
    assert vault_read.share_price == Decimal("1.226361")
    assert vault_read.total_assets == Decimal("13521484.785442")
    assert vault_read.total_supply == Decimal("10218226.071715")
    # Gains maxDeposit returns a very large number (uint256 max-like)
    assert vault_read.max_deposit > Decimal("1e70")
    assert vault_read.max_redeem is None

    # Gains: deposits are always open
    assert vault_read.deposits_open is True
    # At this fork block, nextEpochValuesRequestCount == 2, so redemptions are closed
    assert vault_read.redemption_open is False
    # Gains does not track trading state
    assert vault_read.trading is None

    # Verify export round-trip
    exported = vault_read.export()
    assert exported["deposits_open"] == "true"
    assert exported["redemption_open"] == "false"
    assert exported["trading"] == ""


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
    assert estimated == pytest.approx(Decimal("81.54203"))

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
    assert estimated == pytest.approx(Decimal("100"))

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


@flaky.flaky
def test_gains_historical_stateful(tmp_path):
    """Read historical data of gTrade USDC vault using the stateful multicall reader.

    - Exercises the full historical scanning pipeline with a Gains vault
    - Uses live Arbitrum RPC (not forked)
    - Reads 1 week of data with hourly steps
    - Verifies Gains-specific vault state fields (deposits_open, redemption_open)
    """

    web3 = create_multi_provider_web3(JSON_RPC_ARBITRUM)
    chain_id = web3.eth.chain_id
    assert chain_id == 42161

    latest_block = web3.eth.block_number

    # Arbitrum block time is ~0.25s
    # 1 week = 7 * 24 * 3600 / 0.25 = 2_419_200 blocks
    one_week_blocks = 2_419_200
    start_block = latest_block - one_week_blocks
    end_block = latest_block

    # 1 hour step = 3600 / 0.25 = 14_400 blocks
    step = 14_400

    vault = GainsVault(web3, VaultSpec(chain_id, "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0"))
    vault.first_seen_at_block = start_block

    usdc = fetch_erc20_details(web3, USDC_NATIVE_TOKEN[chain_id])

    timestamp_cache_path = tmp_path / "timestamp_cache"

    reader = VaultHistoricalReadMulticaller(
        web3factory=MultiProviderWeb3Factory(JSON_RPC_ARBITRUM),
        supported_quote_tokens={usdc},
        timestamp_cache_file=timestamp_cache_path,
    )

    records = reader.read_historical(
        vaults=[vault],
        start_block=start_block,
        end_block=end_block,
        step=step,
        reader_func=read_multicall_historical_stateful,
    )

    records = list(records)
    assert len(records) >= 1, f"Expected at least 1 record, got {len(records)}"

    # Verify reader state is populated
    vault_readers = reader.readers
    assert len(vault_readers) == 1
    state = list(vault_readers.values())[0].reader_state
    assert state.last_call_at is not None
    assert state.entry_count >= 1

    # Sort records by block number
    records.sort(key=lambda r: r.block_number)

    # Check last record has valid data
    r = records[-1]
    assert r.share_price is not None and r.share_price > 0
    assert r.total_assets is not None and r.total_assets > 0
    assert r.total_supply is not None and r.total_supply > 0
    assert r.max_deposit is not None
    assert r.max_redeem is None

    # Gains-specific fields
    assert r.deposits_open is True
    assert r.redemption_open is not None
    assert isinstance(r.redemption_open, bool)
    assert r.trading is None

    # Verify export round-trip includes all state fields
    exported = r.export()
    assert "deposits_open" in exported
    assert "redemption_open" in exported
    assert "trading" in exported
    assert exported["deposits_open"] == "true"
    assert exported["trading"] == ""
