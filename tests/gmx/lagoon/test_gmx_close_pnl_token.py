"""Regression tests for the GMX PnL payout-token leak (Defect A).

GMX v2 always pays a profitable long's PnL in the market's *long* token
(WETH for ETH/USD), never in the collateral token, unless the close order
explicitly asks GMX to swap the PnL leg back into the collateral token via
``decreasePositionSwapType``. This repository's Lagoon vaults open longs with
**USDC** collateral (see :func:`eth_defi.gmx.trading.GMXTrading.open_position`
and ``_classify_collateral_support()`` in
:mod:`eth_defi.gmx.order.order_argument_parser`), so collateral and PnL token
diverge on every profitable long close.

Before the fix, ``decreasePositionSwapType`` was hardcoded to ``NoSwap`` and
``shouldUnwrapNativeToken`` to ``True`` (`eth_defi/gmx/order/base_order.py`
lines 707/709, `eth_defi/gmx/order/sltp_order.py` lines 475/477). GMX then
paid the WETH profit unmodified and unwrapped it into **native ETH**, an
asset the Lagoon NAV calculation cannot see
(:func:`eth_defi.gmx.valuation.fetch_gmx_total_equity`). Every profitable
long close therefore leaked profit out of reported NAV.

These tests fork Arbitrum mainnet at the fixed GMX test block, open a real
leveraged position through a deployed Lagoon Safe, move the mock oracle price
to force the position into profit, close it through the same keeper-execution
path production uses, and inspect the Safe's raw balances before and after.

See ``docs/claude-plans/2026-08-20-gmx-pnl-token-nav-leak.md`` (Defect A,
Phase 1/2) for the full root-cause analysis.
"""

import logging
import os

import pytest

from eth_defi.gmx.constants import OrderType
from eth_defi.gmx.order.pending_orders import fetch_pending_orders
from eth_defi.provider.anvil import AnvilLaunch
from eth_defi.token import fetch_erc20_details
from tests.gmx.fork_helpers import execute_order_as_keeper, extract_order_key_from_receipt, fetch_on_chain_oracle_prices, setup_mock_oracle
from tests.gmx.lagoon.test_gmx_lagoon_integration import (
    USDC_ARBITRUM,
    WETH_ARBITRUM,
    LagoonGMXForkEnv,
    _create_lagoon_gmx_fork_env,
)

logger = logging.getLogger(__name__)

# Skip entire module if JSON_RPC_ARBITRUM not set
pytestmark = pytest.mark.skipif(
    not os.environ.get("JSON_RPC_ARBITRUM"),
    reason="JSON_RPC_ARBITRUM environment variable not set",
)

#: Position size used by both tests below. Large enough that the forced PnL
#: swamps execution-fee refund dust (a few dollars at most, paid in native
#: ETH regardless of the fix — GMX always refunds unused keeper gas that
#: way), but small enough that the swap leg introduced by the fix (WETH ->
#: USDC on close) never comes close to exhausting the real forked ETH/USD
#: pool's liquidity.
_SIZE_DELTA_USD = 1_000.0

#: Leverage used to size collateral: collateral == size / leverage == $200.
_LEVERAGE = 5.0

#: Fractional mock-oracle price move used to force the position into
#: profit. Applied upward for the long test, downward for the short test.
_PRICE_MOVE_FRACTION = 0.20

#: Ceiling, in USD-equivalent, below which a native ETH increase on the
#: Safe is explained by an ordinary execution-fee refund rather than a
#: leaked PnL payout. GMX refunds unused keeper gas in native ETH on every
#: order regardless of this fix, so this is not zero.
_GAS_REFUND_CEILING_USD = 20.0


@pytest.fixture()
def lagoon_gmx_fork_env(anvil_chain_fork: AnvilLaunch) -> LagoonGMXForkEnv:
    """Initialise Lagoon GMX state on an isolated fixed-block fork.

    Reuses :func:`tests.gmx.lagoon.test_gmx_lagoon_integration._create_lagoon_gmx_fork_env`
    rather than duplicating its ~150 lines of Safe/vault/GMX deployment setup.
    See that module for the full deployment sequence (mock oracle first,
    then Lagoon vault + Safe, then Safe funding, then ``LagoonGMXTradingWallet``
    and ``GMXConfig``).

    :param anvil_chain_fork: Fixed-block Arbitrum Anvil fork fixture from ``tests/gmx/conftest.py``.
    :return: Fully wired :class:`LagoonGMXForkEnv`.
    """
    return _create_lagoon_gmx_fork_env(anvil_chain_fork)


def _open_long_and_get_position(env: LagoonGMXForkEnv) -> dict:
    """Open a $1,000 5x long ETH position with USDC collateral through the Safe.

    Mirrors the open flow in ``test_gmx_lagoon_integration.py``: build the
    order via :class:`~eth_defi.gmx.trading.GMXTrading`, sign it through
    :class:`~eth_defi.gmx.lagoon.wallet.LagoonGMXTradingWallet` (wraps it in
    ``performCall`` so it passes the on-chain Guard), submit, then execute as
    keeper.

    :param env: Fully wired Lagoon GMX fork environment.
    :return: The freshly opened position dict from :meth:`GetOpenPositions.get_data`.
    """
    safe_address = env.vault.safe_address
    env.lagoon_wallet.sync_nonce(env.web3)

    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=True,
        size_delta_usd=_SIZE_DELTA_USD,
        leverage=_LEVERAGE,
        slippage_percent=0.005,
        execution_buffer=30,
    )

    transaction = order_result.transaction.copy()
    transaction.pop("nonce", None)
    signed_tx = env.lagoon_wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1, "Open order transaction should succeed"

    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None, "Should extract order key from receipt"
    exec_receipt, _keeper = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1, "Open order execution should succeed"

    positions = env.positions.get_data(safe_address)
    assert len(positions) == 1, f"Expected exactly 1 open position, got {len(positions)}"
    _key, position = next(iter(positions.items()))
    assert position["market_symbol"] == "ETH"
    assert position["is_long"] is True
    return position


def _close_position(env: LagoonGMXForkEnv, position: dict, is_long: bool) -> None:
    """Close ``position`` fully through the Safe using the current oracle price.

    Uses the exact raw on-chain size (``position_size_usd_raw``) and the
    position's own collateral for the withdrawal amount, matching the
    proven full-close pattern in ``test_trading.py::test_open_and_close_position``.

    :param env: Fully wired Lagoon GMX fork environment.
    :param position: Position dict returned by :meth:`GetOpenPositions.get_data`.
    :param is_long: Whether the position being closed is long (matches ``position["is_long"]``).
    """
    env.lagoon_wallet.sync_nonce(env.web3)

    close_result = env.trading.close_position(
        market_symbol="ETH",
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=is_long,
        size_delta_usd=position["position_size_usd_raw"],
        initial_collateral_delta=position["initial_collateral_amount_usd"],
        slippage_percent=0.005,
        execution_buffer=30,
    )

    transaction = close_result.transaction.copy()
    transaction.pop("nonce", None)
    signed_tx = env.lagoon_wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = env.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = env.web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1, "Close order transaction should succeed"

    order_key = extract_order_key_from_receipt(receipt)
    assert order_key is not None, "Should extract order key from receipt"
    exec_receipt, _keeper = execute_order_as_keeper(env.web3, order_key)
    assert exec_receipt["status"] == 1, "Close order execution should succeed"


def test_close_profitable_long_pays_pnl_in_usdc_not_native_eth(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """A profitable long ETH/USDC close must return USDC, not leak profit as native ETH.

    Regression test for Defect A in
    ``docs/claude-plans/2026-08-20-gmx-pnl-token-nav-leak.md``. Opens a long
    ETH position with USDC collateral, forces it into profit by moving the
    mock oracle price up 20%, then closes it fully.

    Before the fix (``decreasePositionSwapType = NoSwap``,
    ``shouldUnwrapNativeToken = True`` hardcoded), GMX pays the WETH profit
    unmodified and unwraps it, so the Safe's native ETH balance jumps by
    roughly the profit while its USDC balance only recovers the collateral.

    After the fix (``decreasePositionSwapType =
    swap_pnl_token_to_collateral_token``, ``shouldUnwrapNativeToken =
    False``), GMX swaps the WETH profit into USDC before payout, so the
    Safe's USDC balance increases by roughly collateral + profit and its
    native ETH balance only moves by an ordinary execution-fee refund.
    """
    env = lagoon_gmx_fork_env
    web3 = env.web3
    safe_address = env.vault.safe_address
    usdc = fetch_erc20_details(web3, USDC_ARBITRUM)

    # === Step 1: open a long ETH position with USDC collateral ===
    position = _open_long_and_get_position(env)
    collateral_usd = position["initial_collateral_amount_usd"]
    logger.info("Opened long ETH position: size_usd_raw=%s, collateral_usd=%.2f", position["position_size_usd_raw"], collateral_usd)

    # === Step 2: force the position into profit ===
    current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(web3)
    new_eth_price = int(current_eth_price * (1 + _PRICE_MOVE_FRACTION))
    setup_mock_oracle(web3, eth_price_usd=new_eth_price, usdc_price_usd=current_usdc_price)
    expected_profit_usd = _SIZE_DELTA_USD * _PRICE_MOVE_FRACTION
    logger.info("Moved mock ETH price %d -> %d (expected PnL ~$%.2f)", current_eth_price, new_eth_price, expected_profit_usd)

    # === Step 3: record balances immediately before close ===
    safe_eth_before = web3.eth.get_balance(safe_address)
    safe_usdc_before = usdc.contract.functions.balanceOf(safe_address).call()

    # === Step 4: close the position fully ===
    _close_position(env, position, is_long=True)

    # === Step 5: record balances after close and compute deltas ===
    safe_eth_after = web3.eth.get_balance(safe_address)
    safe_usdc_after = usdc.contract.functions.balanceOf(safe_address).call()

    eth_delta_wei = safe_eth_after - safe_eth_before
    usdc_delta = (safe_usdc_after - safe_usdc_before) / 10**usdc.decimals
    eth_delta_usd = (eth_delta_wei / 10**18) * new_eth_price

    logger.info(
        "Close deltas: native ETH %+.6f ETH (~$%.2f), USDC %+.2f (expected collateral+profit ~$%.2f)",
        eth_delta_wei / 10**18,
        eth_delta_usd,
        usdc_delta,
        collateral_usd + expected_profit_usd,
    )

    # The fix: profit must come back as USDC, not leak out as native ETH.
    assert eth_delta_usd < _GAS_REFUND_CEILING_USD, f"Native ETH increased by ~${eth_delta_usd:.2f} on close — PnL is leaking out as native ETH instead of being swapped to USDC"
    assert usdc_delta > collateral_usd + 0.5 * expected_profit_usd, f"USDC only increased by {usdc_delta:.2f}, expected collateral (~${collateral_usd:.2f}) plus most of the ~${expected_profit_usd:.2f} profit"


def test_close_profitable_short_is_unaffected_by_pnl_swap_fix(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """A profitable short's PnL token already equals its collateral token (USDC).

    Highest regression risk identified in
    ``docs/claude-plans/2026-08-20-gmx-pnl-token-nav-leak.md``:
    ``decreasePositionSwapType = SwapPnlTokenToCollateralToken`` *should* be
    a no-op for a short, since a short's PnL is denominated in the market's
    **short** token (USDC for ETH/USD), which already equals its collateral.
    That must be proven on a fork, not assumed — a broken swap-type change
    could revert closes or misroute funds even where the original bug never
    applied.

    Opens a short ETH position with USDC collateral, forces it into profit
    by moving the mock oracle price *down* 20% (shorts profit on falling
    price), closes it fully, and asserts the close succeeds and pays out in
    USDC only — no unexpected native ETH movement beyond an ordinary
    execution-fee refund.
    """
    env = lagoon_gmx_fork_env
    web3 = env.web3
    safe_address = env.vault.safe_address
    usdc = fetch_erc20_details(web3, USDC_ARBITRUM)

    env.lagoon_wallet.sync_nonce(web3)

    # === Step 1: open a short ETH position with USDC collateral ===
    order_result = env.trading.open_position(
        market_symbol="ETH",
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=False,
        size_delta_usd=_SIZE_DELTA_USD,
        leverage=_LEVERAGE,
        slippage_percent=0.005,
        execution_buffer=30,
    )
    transaction = order_result.transaction.copy()
    transaction.pop("nonce", None)
    signed_tx = env.lagoon_wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1

    order_key = extract_order_key_from_receipt(receipt)
    exec_receipt, _keeper = execute_order_as_keeper(web3, order_key)
    assert exec_receipt["status"] == 1

    positions = env.positions.get_data(safe_address)
    assert len(positions) == 1
    _key, position = next(iter(positions.items()))
    assert position["market_symbol"] == "ETH"
    assert position["is_long"] is False
    collateral_usd = position["initial_collateral_amount_usd"]

    # === Step 2: force the short into profit (price falls) ===
    current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(web3)
    new_eth_price = int(current_eth_price * (1 - _PRICE_MOVE_FRACTION))
    setup_mock_oracle(web3, eth_price_usd=new_eth_price, usdc_price_usd=current_usdc_price)
    expected_profit_usd = _SIZE_DELTA_USD * _PRICE_MOVE_FRACTION
    logger.info("Moved mock ETH price %d -> %d (expected short PnL ~$%.2f)", current_eth_price, new_eth_price, expected_profit_usd)

    # === Step 3: record balances before close ===
    safe_eth_before = web3.eth.get_balance(safe_address)
    safe_usdc_before = usdc.contract.functions.balanceOf(safe_address).call()

    # === Step 4: close the short fully ===
    _close_position(env, position, is_long=False)

    # === Step 5: record balances after close and compute deltas ===
    safe_eth_after = web3.eth.get_balance(safe_address)
    safe_usdc_after = usdc.contract.functions.balanceOf(safe_address).call()

    eth_delta_wei = safe_eth_after - safe_eth_before
    usdc_delta = (safe_usdc_after - safe_usdc_before) / 10**usdc.decimals
    eth_delta_usd = (eth_delta_wei / 10**18) * new_eth_price

    logger.info(
        "Short close deltas: native ETH %+.6f ETH (~$%.2f), USDC %+.2f (expected collateral+profit ~$%.2f)",
        eth_delta_wei / 10**18,
        eth_delta_usd,
        usdc_delta,
        collateral_usd + expected_profit_usd,
    )

    # SwapPnlTokenToCollateralToken must be a no-op here: short PnL token
    # (USDC) already equals collateral token (USDC), so behaviour must be
    # unchanged from before the fix — no native ETH leak, profit in USDC.
    assert eth_delta_usd < _GAS_REFUND_CEILING_USD, f"Native ETH increased by ~${eth_delta_usd:.2f} on a short close — unexpected for a position whose PnL token already equals its collateral token"
    assert usdc_delta > collateral_usd + 0.5 * expected_profit_usd, f"USDC only increased by {usdc_delta:.2f}, expected collateral (~${collateral_usd:.2f}) plus most of the ~${expected_profit_usd:.2f} short profit"


def test_take_profit_order_execution_pays_pnl_in_usdc(lagoon_gmx_fork_env: LagoonGMXForkEnv):
    """A triggered take-profit order must pay PnL in USDC, not native ETH.

    Every other test in this file drives a close through
    ``GMXTrading.close_position()`` — the manual/standalone close path. GMX's
    bundled take-profit order is built by a *separate* code path,
    ``SLTPOrder._build_decrease_order_arguments()``
    (``eth_defi/gmx/order/sltp_order.py``), which carries its own
    ``decrease_position_swap_type``/``should_unwrap_native_token`` fields set
    from the ``SLTPOrder`` instance rather than an ``OrderParams`` object.

    Neither this file's other tests nor ``tests/gmx/test_sltp_order.py``'s
    ``test_full_lifecycle_open_and_close_with_sl_tp`` ever let a bundled
    take-profit order actually *trigger* — that test opens with a bundled
    TP, then closes manually via ``close_position()``, leaving the TP order
    dangling and unexecuted. That left the SL/TP-specific fix path completely
    unexercised, even though the design plan
    (``docs/claude-plans/2026-08-20-gmx-pnl-token-nav-leak.md``) explicitly
    names take-profit as the highest-exposure case — it only ever fires in
    profit, by construction.

    This test opens a long with a bundled take-profit, executes the open,
    finds the resulting pending take-profit (``LIMIT_DECREASE``) order via
    the on-chain Reader, moves the mock oracle price past its trigger, and
    executes *that specific order* via the keeper harness — the same call
    GMX's real keeper infrastructure makes when a take-profit fires in
    production.
    """
    env = lagoon_gmx_fork_env
    web3 = env.web3
    safe_address = env.vault.safe_address
    usdc = fetch_erc20_details(web3, USDC_ARBITRUM)
    weth = fetch_erc20_details(web3, WETH_ARBITRUM)

    env.lagoon_wallet.sync_nonce(web3)

    # === Step 1: open a long with a bundled 15% take-profit ===
    take_profit_percent = 0.15
    order_result = env.trading.open_position_with_sltp(
        market_symbol="ETH",
        collateral_symbol="USDC",
        start_token_symbol="USDC",
        is_long=True,
        size_delta_usd=_SIZE_DELTA_USD,
        leverage=_LEVERAGE,
        take_profit_percent=take_profit_percent,
        slippage_percent=0.005,
        execution_buffer=30,
    )
    assert order_result.take_profit_trigger_price is not None, "Take-profit trigger should be set"

    transaction = order_result.transaction.copy()
    transaction.pop("nonce", None)
    signed_tx = env.lagoon_wallet.sign_transaction_with_new_nonce(transaction)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1, "Bundled open+take-profit transaction should succeed"

    # === Step 2: execute the main open order as keeper ===
    open_order_key = extract_order_key_from_receipt(receipt)
    exec_receipt, _keeper = execute_order_as_keeper(web3, open_order_key)
    assert exec_receipt["status"] == 1, "Open order execution should succeed"

    positions = env.positions.get_data(safe_address)
    assert len(positions) == 1, f"Expected exactly 1 open position after open, got {len(positions)}"
    _key, position = next(iter(positions.items()))
    assert position["market_symbol"] == "ETH"
    assert position["is_long"] is True
    collateral_usd = position["initial_collateral_amount_usd"]

    # === Step 3: locate the pending take-profit order on-chain ===
    pending_tp_orders = list(
        fetch_pending_orders(
            web3,
            "arbitrum",
            safe_address,
            order_type_filter=OrderType.LIMIT_DECREASE,
        )
    )
    assert len(pending_tp_orders) == 1, f"Expected exactly 1 pending take-profit order, found {len(pending_tp_orders)}"
    tp_order = pending_tp_orders[0]
    logger.info("Found pending take-profit order: key=%s trigger=$%.2f", tp_order.order_key.hex(), tp_order.trigger_price_usd)

    # === Step 4: move the mock oracle price past the take-profit trigger ===
    current_eth_price, current_usdc_price = fetch_on_chain_oracle_prices(web3)
    # Cross the trigger with margin, not just touch it.
    new_eth_price = int(order_result.take_profit_trigger_price * 1.02)
    setup_mock_oracle(web3, eth_price_usd=new_eth_price, usdc_price_usd=current_usdc_price)
    expected_profit_usd = _SIZE_DELTA_USD * take_profit_percent
    logger.info(
        "Moved mock ETH price %d -> %d to cross TP trigger $%.2f (expected PnL ~$%.2f)",
        current_eth_price,
        new_eth_price,
        order_result.take_profit_trigger_price,
        expected_profit_usd,
    )

    # === Step 5: record balances immediately before the take-profit fires ===
    safe_eth_before = web3.eth.get_balance(safe_address)
    safe_usdc_before = usdc.contract.functions.balanceOf(safe_address).call()
    safe_weth_before = weth.contract.functions.balanceOf(safe_address).call()

    # === Step 6: execute the take-profit order as keeper — this is the
    # actual code path under test, SLTPOrder._build_decrease_order_arguments() ===
    tp_exec_receipt, _tp_keeper = execute_order_as_keeper(web3, tp_order.order_key)
    assert tp_exec_receipt["status"] == 1, "Take-profit order execution should succeed"

    # === Step 7: verify the position closed and PnL landed in USDC ===
    positions_after = env.positions.get_data(safe_address)
    assert len(positions_after) == 0, "Take-profit should have fully closed the position"

    safe_eth_after = web3.eth.get_balance(safe_address)
    safe_usdc_after = usdc.contract.functions.balanceOf(safe_address).call()
    safe_weth_after = weth.contract.functions.balanceOf(safe_address).call()

    eth_delta_wei = safe_eth_after - safe_eth_before
    usdc_delta = (safe_usdc_after - safe_usdc_before) / 10**usdc.decimals
    weth_delta = (safe_weth_after - safe_weth_before) / 10**weth.decimals
    eth_delta_usd = (eth_delta_wei / 10**18) * new_eth_price

    logger.info(
        "Take-profit fire deltas: native ETH %+.6f ETH (~$%.2f), WETH %+.8f, USDC %+.2f (expected collateral+profit ~$%.2f)",
        eth_delta_wei / 10**18,
        eth_delta_usd,
        weth_delta,
        usdc_delta,
        collateral_usd + expected_profit_usd,
    )

    # The fix-under-test: WETH (the market's long token, i.e. the PnL token
    # for a long) must not accumulate in the Safe -- that is the actual leak
    # this test exists to catch, per SLTPOrder._build_decrease_order_arguments().
    #
    # Native ETH is deliberately NOT asserted against a tight ceiling here,
    # unlike the manual-close tests above. shouldUnwrapNativeToken only
    # controls the collateral/PnL settlement token; it does not touch GMX's
    # unused-execution-fee refund, which is always paid in native ETH
    # regardless of that flag. The bundled take-profit order's execution fee
    # was pre-funded *at open time* using the same execution_buffer=30 as the
    # main order, and actual fork gas usage is negligible, so almost that
    # entire escrow legitimately refunds as native ETH on execution -- a
    # fixed-dollar ceiling calibrated for a plain close_position() call (a
    # few dollars) does not hold for this pre-funded-at-open path and would
    # make this assertion fight the fee mechanism instead of testing the fix.
    assert weth_delta == pytest.approx(0, abs=1e-6), f"WETH increased by {weth_delta:.8f} when the take-profit fired — PnL is leaking out as the market's long token instead of being swapped to USDC"
    assert usdc_delta > collateral_usd + 0.5 * expected_profit_usd, f"USDC only increased by {usdc_delta:.2f}, expected collateral (~${collateral_usd:.2f}) plus most of the ~${expected_profit_usd:.2f} take-profit"
