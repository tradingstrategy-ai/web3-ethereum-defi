"""Integration tests for the close-flow authoritative-pool override.

Covers the production bug where SOL/USDC:USDC (and BTC/USDC:USDC) closes
failed with "Not a valid collateral for selected market!" because
``_execute_close_with_position`` used ``self.markets[symbol]["info"]["market_token"]``
— which can hold the wrong pool when multiple GMX pools share one index token —
instead of the on-chain pool address recorded in ``position_to_close["market"]``.

All tests use live Arbitrum mainnet data via the ``ccxt_gmx_arbitrum`` fixture.
Requires ``JSON_RPC_ARBITRUM`` environment variable.
"""

import logging
from collections import defaultdict
from unittest.mock import MagicMock

import pytest

from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.core.markets import Markets


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_markets(gmx: GMX) -> dict:
    """Load markets and return the raw Markets.get_available_markets() dict.

    :param gmx: Live GMX CCXT exchange instance.
    :return: All available markets keyed by checksum pool address.
    """
    gmx.load_markets()
    return Markets(gmx.config).get_available_markets()


def _find_multi_pool_symbol(gmx: GMX, all_markets: dict) -> tuple[str, str, str]:
    """Find a CCXT symbol whose index token appears in multiple pools.

    Returns ``(unified_symbol, correct_pool_address, wrong_pool_address)``
    where *correct_pool* accepts USDC as collateral and *wrong_pool* does
    not (e.g. a synthetic pool where long==short).

    :param gmx: Live GMX CCXT exchange instance (markets already loaded).
    :param all_markets: Full available markets dict from Markets.get_available_markets().
    :return: Tuple of ``(symbol, correct_pool, wrong_pool)``.
    :raises pytest.skip.Exception: If no suitable pair is found on-chain.
    """
    by_index: dict[str, list[str]] = defaultdict(list)
    for key, m in all_markets.items():
        idx = m.get("index_token_address", "").lower()
        if idx:
            by_index[idx].append(key)

    for keys in by_index.values():
        if len(keys) < 2:
            continue
        # Split into USDC-accepting pools and synthetic (long==short) pools
        usdc_pools = [k for k in keys if all_markets[k].get("short_token_address", "").lower() != all_markets[k].get("long_token_address", "").lower()]
        synthetic_pools = [k for k in keys if all_markets[k].get("short_token_address", "").lower() == all_markets[k].get("long_token_address", "").lower()]
        if not usdc_pools or not synthetic_pools:
            continue

        correct_pool = usdc_pools[0]
        wrong_pool = synthetic_pools[0]
        market_symbol = all_markets[correct_pool].get("market_symbol", "")
        unified_symbol = f"{market_symbol}/USDC:USDC"

        if unified_symbol not in gmx.markets:
            continue

        logger.info(
            "Found multi-pool symbol %s: correct_pool=%s wrong_pool=%s",
            unified_symbol,
            correct_pool,
            wrong_pool,
        )
        return unified_symbol, correct_pool, wrong_pool

    pytest.skip("No symbol with both a USDC-accepting pool and a synthetic pool found on Arbitrum")


def _make_gmx_params(gmx: GMX, symbol: str, market_key: str) -> dict:
    """Build a ``gmx_params`` dict as ``_convert_ccxt_to_gmx_params`` would produce.

    Uses real market data from the loaded ``gmx.markets`` dict.

    :param gmx: Live GMX CCXT exchange instance (markets already loaded).
    :param symbol: CCXT unified symbol (e.g. ``SOL/USDC:USDC``).
    :param market_key: Pool address to pre-set as market_key (may be wrong).
    :return: Parameters dict compatible with ``_execute_close_with_position``.
    """
    market = gmx.markets[symbol]
    base = market["base"]
    index_token = market.get("info", {}).get("index_token", "")
    return {
        "market_symbol": base,
        "collateral_symbol": "USDC",
        "start_token_symbol": "USDC",
        "is_long": True,
        "size_delta_usd": 100.0,
        "leverage": 2.0,
        "slippage_percent": 0.003,
        "execution_buffer": 1.3,
        "auto_cancel": False,
        "market_key": market_key,
        "index_token_address": index_token,
        "_gmx_position": None,
        "_resolved_market_info": {},
        "_collateral_explicitly_set": False,
    }


def _make_position(market: str, collateral_token: str = "USDC", is_long: bool = True) -> dict:
    """Build a minimal on-chain position dict as ``GetOpenPositions`` returns.

    :param market: Authoritative on-chain pool address.
    :param collateral_token: Collateral token symbol.
    :param is_long: Position direction.
    :return: Position dict compatible with ``_execute_close_with_position``.
    """
    return {
        "market": market,
        "collateral_token": collateral_token,
        "is_long": is_long,
        "position_size": 200.0,
        "position_size_usd_raw": 200 * 10**30,
        "initial_collateral_amount_usd": 100.0,
        "leverage": 2.0,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_close_uses_on_chain_pool_when_self_markets_is_wrong(ccxt_gmx_arbitrum: GMX) -> None:
    """Regression: close must target on-chain position's pool, not self.markets.

    Loads live Arbitrum market data to find a real symbol (SOL or BTC) that has
    both a USDC-accepting pool and a synthetic pool sharing the same index token.
    Patches ``self.markets`` for that symbol to point at the wrong synthetic pool,
    then supplies a ``position_to_close["market"]`` with the correct USDC pool.

    Asserts that ``_execute_close_with_position`` passes the correct (authoritative)
    pool address to ``trader.close_position``, overriding the stale self.markets value.

    Requires ``JSON_RPC_ARBITRUM``.
    """
    gmx = ccxt_gmx_arbitrum
    all_markets = _load_markets(gmx)

    symbol, correct_pool, wrong_pool = _find_multi_pool_symbol(gmx, all_markets)

    # Simulate the broken state: self.markets holds the wrong (synthetic) pool
    gmx.markets[symbol]["info"]["market_token"] = wrong_pool

    gmx_params = _make_gmx_params(gmx, symbol, market_key=wrong_pool)
    position_to_close = _make_position(market=correct_pool, collateral_token="USDC")

    captured_kwargs: dict = {}

    def fake_close_position(**kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        return MagicMock()

    gmx.trader = MagicMock()
    gmx.trader.close_position.side_effect = fake_close_position

    GMX._execute_close_with_position(
        gmx,
        symbol=symbol,
        type="market",
        side="sell",
        gmx_params=gmx_params,
        position_to_close=position_to_close,
        size_delta_usd=200 * 10**30,
        initial_collateral_delta=100.0,
    )

    assert captured_kwargs.get("market_key", "").lower() == correct_pool.lower(), f"Expected market_key={correct_pool} (correct USDC pool), got {captured_kwargs.get('market_key')} for symbol {symbol}"
    assert captured_kwargs.get("collateral_symbol") == "USDC"
    assert captured_kwargs.get("start_token_symbol") == "USDC"


def test_close_no_override_when_pool_already_correct(ccxt_gmx_arbitrum: GMX) -> None:
    """No spurious override when self.markets already has the correct pool.

    When ``position_to_close["market"]`` matches what ``gmx_params["market_key"]``
    already holds, ``_execute_close_with_position`` must pass through the same
    address unchanged — no silent modification.

    Requires ``JSON_RPC_ARBITRUM``.
    """
    gmx = ccxt_gmx_arbitrum
    all_markets = _load_markets(gmx)

    symbol, correct_pool, _ = _find_multi_pool_symbol(gmx, all_markets)

    # Healthy state: self.markets and position both agree on the correct pool
    gmx.markets[symbol]["info"]["market_token"] = correct_pool

    gmx_params = _make_gmx_params(gmx, symbol, market_key=correct_pool)
    position_to_close = _make_position(market=correct_pool, collateral_token="USDC")

    captured_kwargs: dict = {}

    def fake_close_position(**kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        return MagicMock()

    gmx.trader = MagicMock()
    gmx.trader.close_position.side_effect = fake_close_position

    GMX._execute_close_with_position(
        gmx,
        symbol=symbol,
        type="market",
        side="sell",
        gmx_params=gmx_params,
        position_to_close=position_to_close,
        size_delta_usd=200 * 10**30,
        initial_collateral_delta=100.0,
    )

    assert captured_kwargs.get("market_key", "").lower() == correct_pool.lower(), f"market_key should remain {correct_pool} — no override expected"


def test_close_logs_warning_on_pool_override(
    ccxt_gmx_arbitrum: GMX,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Override must emit a WARNING so operators can see the pool-drift in logs.

    Uses live Arbitrum market data to find a real multi-pool symbol, then
    verifies that ``_execute_close_with_position`` emits a WARNING when it
    overrides the stale market_key from on-chain position data.

    Requires ``JSON_RPC_ARBITRUM``.
    """
    gmx = ccxt_gmx_arbitrum
    all_markets = _load_markets(gmx)

    symbol, correct_pool, wrong_pool = _find_multi_pool_symbol(gmx, all_markets)

    gmx.markets[symbol]["info"]["market_token"] = wrong_pool
    gmx_params = _make_gmx_params(gmx, symbol, market_key=wrong_pool)
    position_to_close = _make_position(market=correct_pool, collateral_token="USDC")

    gmx.trader = MagicMock()
    gmx.trader.close_position.return_value = MagicMock()

    with caplog.at_level(logging.WARNING, logger="eth_defi.gmx.ccxt.exchange"):
        GMX._execute_close_with_position(
            gmx,
            symbol=symbol,
            type="market",
            side="sell",
            gmx_params=gmx_params,
            position_to_close=position_to_close,
            size_delta_usd=200 * 10**30,
            initial_collateral_delta=100.0,
        )

    assert any("overriding market_key" in rec.message for rec in caplog.records), "Expected pool-override WARNING when self.markets and position_to_close disagree"


def test_btc_close_uses_on_chain_pool(ccxt_gmx_arbitrum: GMX) -> None:
    """BTC/USDC:USDC close must use the WBTC-USDC pool, not the BTC-BTC synthetic.

    Explicit BTC regression: directly patches self.markets to hold the BTC-BTC
    synthetic pool, supplies a position on the WBTC-USDC pool, and asserts the
    override fires correctly.  Uses real BTC pool addresses fetched from Arbitrum.

    Requires ``JSON_RPC_ARBITRUM``.
    """
    gmx = ccxt_gmx_arbitrum
    all_markets = _load_markets(gmx)

    symbol = "BTC/USDC:USDC"
    if symbol not in gmx.markets:
        pytest.skip(f"{symbol} not in gmx.markets on this RPC endpoint")

    # Fetch real BTC pool addresses from Arbitrum
    btc_pools = gmx.fetch_pools_for_symbol("BTC/USD")
    assert len(btc_pools) >= 2, f"Expected at least 2 BTC pools, got {btc_pools}"

    usdc_pool_entry = next(
        (p for p in btc_pools if p["short_token_symbol"].upper() == "USDC"),
        None,
    )
    synthetic_pool_entry = next(
        (p for p in btc_pools if p["long_token"].lower() == p["short_token"].lower()),
        None,
    )
    if not usdc_pool_entry or not synthetic_pool_entry:
        pytest.skip("Could not find both USDC and synthetic BTC pools on Arbitrum")

    correct_pool = usdc_pool_entry["market_address"]
    wrong_pool = synthetic_pool_entry["market_address"]

    # Patch self.markets to hold the wrong synthetic pool
    gmx.markets[symbol]["info"]["market_token"] = wrong_pool
    gmx_params = _make_gmx_params(gmx, symbol, market_key=wrong_pool)
    position_to_close = _make_position(market=correct_pool, collateral_token="USDC")

    captured_kwargs: dict = {}

    def fake_close_position(**kwargs: object) -> MagicMock:
        captured_kwargs.update(kwargs)
        return MagicMock()

    gmx.trader = MagicMock()
    gmx.trader.close_position.side_effect = fake_close_position

    GMX._execute_close_with_position(
        gmx,
        symbol=symbol,
        type="market",
        side="sell",
        gmx_params=gmx_params,
        position_to_close=position_to_close,
        size_delta_usd=200 * 10**30,
        initial_collateral_delta=100.0,
    )

    assert captured_kwargs.get("market_key", "").lower() == correct_pool.lower(), f"BTC close: expected WBTC-USDC pool {correct_pool}, got {captured_kwargs.get('market_key')}"
    assert captured_kwargs.get("collateral_symbol") == "USDC"
