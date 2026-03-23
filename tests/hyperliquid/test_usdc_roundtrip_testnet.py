"""Test Hyperliquid testnet USDC round-trip through spot and perp balances.

1. Connect to HyperEVM testnet with a funded hot wallet.
2. Move the configured test USDC amount from HyperEVM to HyperCore spot and assert the spot balance increases.
3. Move the same USDC from spot to perp and back to spot with direct CoreWriter actions.
4. Bridge the USDC back from HyperCore spot to HyperEVM with ``sendAsset`` and assert the EVM balance returns.
5. Verify the account ends with the same EVM and perp balances, with spot returning to baseline within bridge-fee tolerance.
"""

import os
import time
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.hotwallet import HotWallet
from eth_defi.hyperliquid.api import HyperliquidSession, PerpClearinghouseState, SpotClearinghouseState, fetch_perp_clearinghouse_state, fetch_spot_clearinghouse_state, fetch_user_abstraction_mode
from eth_defi.hyperliquid.core_writer import CORE_DEPOSIT_WALLET, SPOT_DEX, USDC_TOKEN_INDEX, encode_send_asset_to_evm, encode_transfer_usd_class, get_core_deposit_wallet_contract, get_core_writer_contract
from eth_defi.hyperliquid.evm_escrow import wait_for_evm_escrow_clear
from eth_defi.hyperliquid.session import HYPERLIQUID_TESTNET_API_URL, create_hyperliquid_session
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, TokenDetails, fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation

HYPERCORE_WRITER_TEST_PRIVATE_KEY = os.environ.get("HYPERCORE_WRITER_TEST_PRIVATE_KEY")
HYPERLIQUID_TESTNET_RPC = "https://rpc.hyperliquid-testnet.xyz/evm"
ROUNDTRIP_AMOUNT = Decimal("1")
MIN_HYPE_BALANCE = Decimal("0.01")
BALANCE_TOLERANCE = 0.02
POLL_TIMEOUT = 60.0
POLL_INTERVAL = 2.0

pytestmark = [
    pytest.mark.skipif(
        not HYPERCORE_WRITER_TEST_PRIVATE_KEY,
        reason="HYPERCORE_WRITER_TEST_PRIVATE_KEY environment variable required",
    ),
    pytest.mark.timeout(180),
]


def _get_spot_usdc_balances(spot_state: SpotClearinghouseState) -> tuple[Decimal, Decimal]:
    """Extract total and free USDC balances from HyperCore spot state."""
    for balance in spot_state.balances:
        if balance.coin == "USDC":
            return balance.total, balance.total - balance.hold
    return Decimal(0), Decimal(0)


def _fetch_evm_usdc_balance(usdc: TokenDetails, address: str) -> Decimal:
    """Read the hot wallet's HyperEVM USDC balance."""
    return usdc.fetch_balance_of(address)


def _wait_for_spot_free_delta(
    session: HyperliquidSession,
    user: str,
    baseline_free_spot: Decimal,
    expected_delta: Decimal,
    timeout: float = POLL_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
) -> SpotClearinghouseState:
    """Wait until the HyperCore free spot USDC balance changes by the expected amount."""
    deadline = time.time() + timeout
    last_state = fetch_spot_clearinghouse_state(session, user=user)
    while True:
        last_state = fetch_spot_clearinghouse_state(session, user=user)
        _, free_spot = _get_spot_usdc_balances(last_state)
        delta = free_spot - baseline_free_spot
        if float(delta) == pytest.approx(float(expected_delta), abs=BALANCE_TOLERANCE):
            return last_state

        if time.time() >= deadline:
            raise AssertionError(f"Timed out waiting for free spot USDC delta {expected_delta} for {user}. Last free spot delta was {delta}.")

        time.sleep(poll_interval)


def _wait_for_perp_withdrawable_delta(
    session: HyperliquidSession,
    user: str,
    baseline_withdrawable: Decimal,
    expected_delta: Decimal,
    timeout: float = POLL_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
) -> PerpClearinghouseState:
    """Wait until the HyperCore perp withdrawable balance changes by the expected amount."""
    deadline = time.time() + timeout
    last_state = fetch_perp_clearinghouse_state(session, user=user)
    while True:
        last_state = fetch_perp_clearinghouse_state(session, user=user)
        delta = last_state.withdrawable - baseline_withdrawable
        if float(delta) == pytest.approx(float(expected_delta), abs=BALANCE_TOLERANCE):
            return last_state

        if time.time() >= deadline:
            raise AssertionError(f"Timed out waiting for perp withdrawable delta {expected_delta} for {user}. Last perp withdrawable delta was {delta}, account value was {last_state.margin_summary.account_value}, and raw USD was {last_state.margin_summary.total_raw_usd}.")

        time.sleep(poll_interval)


def _wait_for_evm_usdc_balance(
    usdc: TokenDetails,
    address: str,
    expected_balance: Decimal,
    timeout: float = POLL_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
) -> Decimal:
    """Wait until the HyperEVM USDC balance reaches the expected level."""
    deadline = time.time() + timeout
    last_balance = _fetch_evm_usdc_balance(usdc, address)
    while True:
        last_balance = _fetch_evm_usdc_balance(usdc, address)
        if float(last_balance) == pytest.approx(float(expected_balance), abs=BALANCE_TOLERANCE):
            return last_balance

        if time.time() >= deadline:
            raise AssertionError(f"Timed out waiting for EVM USDC balance {expected_balance} for {address}. Last EVM USDC balance was {last_balance}.")

        time.sleep(poll_interval)


@pytest.fixture()
def web3() -> Web3:
    """Connect to HyperEVM testnet."""
    return create_multi_provider_web3(HYPERLIQUID_TESTNET_RPC, default_http_timeout=(3, 30.0))


@pytest.fixture()
def session() -> HyperliquidSession:
    """Create a Hyperliquid testnet API session."""
    return create_hyperliquid_session(api_url=HYPERLIQUID_TESTNET_API_URL)


@pytest.fixture()
def hot_wallet(web3: Web3) -> HotWallet:
    """Create a hot wallet for the funded HyperEVM testnet account."""
    wallet = HotWallet.from_private_key(HYPERCORE_WRITER_TEST_PRIVATE_KEY)
    wallet.sync_nonce(web3)
    return wallet


@pytest.fixture()
def usdc(web3: Web3) -> TokenDetails:
    """Load the HyperEVM testnet USDC token details."""
    return fetch_erc20_details(web3, USDC_NATIVE_TOKEN[998])


def test_hyperliquid_testnet_usdc_roundtrip_hot_wallet(
    web3: Web3,
    session: HyperliquidSession,
    hot_wallet: HotWallet,
    usdc: TokenDetails,
) -> None:
    """Test a direct hot-wallet USDC round-trip across HyperEVM, spot, and perp.

    1. Read baseline HyperEVM, HyperCore spot, and HyperCore perp balances for the funded test wallet.
    2. Deposit the configured test USDC amount from HyperEVM to HyperCore spot and verify the spot balance increases.
    3. Move the configured test USDC amount from HyperCore spot to perp and back to spot and verify each balance change.
    4. Bridge the configured test USDC amount from HyperCore spot back to HyperEVM with ``sendAsset`` and verify the EVM balance returns to baseline.
    5. Assert the final EVM and perp balances match the initial baseline values and the spot balance stays within bridge-fee tolerance.
    """
    core_deposit_wallet = get_core_deposit_wallet_contract(
        web3,
        CORE_DEPOSIT_WALLET[web3.eth.chain_id],
    )
    core_writer = get_core_writer_contract(web3)
    amount_raw = usdc.convert_to_raw(ROUNDTRIP_AMOUNT)

    # 1. Read baseline HyperEVM, HyperCore spot, and HyperCore perp balances for the funded test wallet.
    assert web3.eth.chain_id == 998, f"Expected HyperEVM testnet chain id 998, got {web3.eth.chain_id}"
    evm_hype_balance = Decimal(web3.eth.get_balance(hot_wallet.address)) / Decimal(10**18)
    baseline_evm_usdc = _fetch_evm_usdc_balance(usdc, hot_wallet.address)
    baseline_spot_state = fetch_spot_clearinghouse_state(session, user=hot_wallet.address)
    baseline_perp_state = fetch_perp_clearinghouse_state(session, user=hot_wallet.address)
    baseline_spot_total, baseline_free_spot = _get_spot_usdc_balances(baseline_spot_state)

    assert evm_hype_balance >= MIN_HYPE_BALANCE, f"Hot wallet {hot_wallet.address} needs at least {MIN_HYPE_BALANCE} HYPE on HyperEVM testnet, has {evm_hype_balance}"
    assert baseline_evm_usdc >= ROUNDTRIP_AMOUNT, f"Hot wallet {hot_wallet.address} needs at least {ROUNDTRIP_AMOUNT} USDC on HyperEVM testnet, has {baseline_evm_usdc}"
    abstraction_mode = fetch_user_abstraction_mode(session, hot_wallet.address)
    if abstraction_mode != "standard":
        pytest.skip(f"Hot wallet {hot_wallet.address} is in Hyperliquid mode {abstraction_mode}. This legacy hot-wallet round-trip test only covers standard accounts; unified-account behaviour is covered by the manual Lagoon Safe flow.")
    assert not baseline_perp_state.asset_positions, f"Hot wallet {hot_wallet.address} has open HyperCore perp positions; the round-trip test requires an idle perp account."
    assert not baseline_spot_state.evm_escrows, f"Hot wallet {hot_wallet.address} has pending HyperCore EVM escrow entries; the round-trip test requires a clean spot account."

    # 2. Deposit the configured test USDC amount from HyperEVM to HyperCore spot and verify the spot balance increases.
    approve_tx_hash = hot_wallet.transact_and_broadcast_with_contract(
        usdc.contract.functions.approve(
            core_deposit_wallet.address,
            amount_raw,
        ),
        gas_limit=200_000,
    )
    assert_transaction_success_with_explanation(web3, approve_tx_hash)

    deposit_tx_hash = hot_wallet.transact_and_broadcast_with_contract(
        core_deposit_wallet.functions.deposit(
            amount_raw,
            SPOT_DEX,
        ),
        gas_limit=200_000,
    )
    assert_transaction_success_with_explanation(web3, deposit_tx_hash)
    wait_for_evm_escrow_clear(
        session,
        user=hot_wallet.address,
        timeout=POLL_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    )
    spot_after_deposit = _wait_for_spot_free_delta(
        session,
        user=hot_wallet.address,
        baseline_free_spot=baseline_free_spot,
        expected_delta=ROUNDTRIP_AMOUNT,
    )
    spot_total_after_deposit, free_spot_after_deposit = _get_spot_usdc_balances(spot_after_deposit)
    assert float(spot_total_after_deposit - baseline_spot_total) == pytest.approx(float(ROUNDTRIP_AMOUNT), abs=BALANCE_TOLERANCE)
    assert float(free_spot_after_deposit - baseline_free_spot) == pytest.approx(float(ROUNDTRIP_AMOUNT), abs=BALANCE_TOLERANCE)

    # 3. Move the configured test USDC amount from HyperCore spot to perp and back to spot and verify each balance change.
    spot_to_perp_tx_hash = hot_wallet.transact_and_broadcast_with_contract(
        core_writer.functions.sendRawAction(
            encode_transfer_usd_class(amount_raw, True),
        ),
        gas_limit=200_000,
    )
    assert_transaction_success_with_explanation(web3, spot_to_perp_tx_hash)
    perp_after_spot_to_perp = _wait_for_perp_withdrawable_delta(
        session,
        user=hot_wallet.address,
        baseline_withdrawable=baseline_perp_state.withdrawable,
        expected_delta=ROUNDTRIP_AMOUNT,
    )
    spot_after_spot_to_perp = _wait_for_spot_free_delta(
        session,
        user=hot_wallet.address,
        baseline_free_spot=baseline_free_spot,
        expected_delta=Decimal(0),
    )
    _, free_spot_after_spot_to_perp = _get_spot_usdc_balances(spot_after_spot_to_perp)
    assert float(perp_after_spot_to_perp.withdrawable - baseline_perp_state.withdrawable) == pytest.approx(float(ROUNDTRIP_AMOUNT), abs=BALANCE_TOLERANCE)
    assert float(free_spot_after_spot_to_perp - baseline_free_spot) == pytest.approx(0.0, abs=BALANCE_TOLERANCE)

    perp_to_spot_tx_hash = hot_wallet.transact_and_broadcast_with_contract(
        core_writer.functions.sendRawAction(
            encode_transfer_usd_class(amount_raw, False),
        ),
        gas_limit=200_000,
    )
    assert_transaction_success_with_explanation(web3, perp_to_spot_tx_hash)
    perp_after_perp_to_spot = _wait_for_perp_withdrawable_delta(
        session,
        user=hot_wallet.address,
        baseline_withdrawable=baseline_perp_state.withdrawable,
        expected_delta=Decimal(0),
    )
    spot_after_perp_to_spot = _wait_for_spot_free_delta(
        session,
        user=hot_wallet.address,
        baseline_free_spot=baseline_free_spot,
        expected_delta=ROUNDTRIP_AMOUNT,
    )
    _, free_spot_after_perp_to_spot = _get_spot_usdc_balances(spot_after_perp_to_spot)
    assert float(perp_after_perp_to_spot.withdrawable - baseline_perp_state.withdrawable) == pytest.approx(0.0, abs=BALANCE_TOLERANCE)
    assert float(free_spot_after_perp_to_spot - baseline_free_spot) == pytest.approx(float(ROUNDTRIP_AMOUNT), abs=BALANCE_TOLERANCE)

    # 4. Bridge the configured test USDC amount from HyperCore spot back to HyperEVM and verify the EVM balance returns to baseline.
    send_asset_tx_hash = hot_wallet.transact_and_broadcast_with_contract(
        core_writer.functions.sendRawAction(
            encode_send_asset_to_evm(USDC_TOKEN_INDEX, amount_raw),
        ),
        gas_limit=200_000,
    )
    assert_transaction_success_with_explanation(web3, send_asset_tx_hash)
    final_evm_usdc = _wait_for_evm_usdc_balance(
        usdc,
        hot_wallet.address,
        baseline_evm_usdc,
    )

    # 5. Assert the final EVM, spot, and perp balances match the initial baseline values.
    final_spot_state = fetch_spot_clearinghouse_state(session, user=hot_wallet.address)
    final_perp_state = fetch_perp_clearinghouse_state(session, user=hot_wallet.address)
    final_spot_total, final_free_spot = _get_spot_usdc_balances(final_spot_state)

    assert float(final_evm_usdc) == pytest.approx(float(baseline_evm_usdc), abs=BALANCE_TOLERANCE)
    assert float(final_spot_total) == pytest.approx(float(baseline_spot_total), abs=BALANCE_TOLERANCE)
    assert float(final_free_spot) == pytest.approx(float(baseline_free_spot), abs=BALANCE_TOLERANCE)
    assert float(final_perp_state.withdrawable) == pytest.approx(float(baseline_perp_state.withdrawable), abs=BALANCE_TOLERANCE)
    assert float(final_perp_state.margin_summary.account_value) == pytest.approx(float(baseline_perp_state.margin_summary.account_value), abs=BALANCE_TOLERANCE)
