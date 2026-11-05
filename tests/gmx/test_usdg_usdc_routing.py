"""GMX USDG pool migration: zero-cap guard and USDC routing (offline).

GMX moved ZEC, HYPE, XRP and NEAR to single-token USDG pools (2026-11). Their
old ``[X-USDC]`` pools are retired by setting both open-interest caps to 0, so
every open is keeper-cancelled with ``MaxOpenInterestExceeded(maxAllowed: 0)``.
GMX's guidance for USDC holders: open and close with
``swapPath = [USDC/USDG pool 0x408E…]``.

Covered here, with mocked data sources (no RPC, no transactions):

- Part A: markets whose ``maxOpenInterestLong``/``Short`` are both 0 are dropped
  (GraphQL fields and the on-chain DataStore pass).
- Part B: ``ZEC/USDC:USDC`` resolves to the USDG pool with a USDC route; opens send
  USDC with the swap path and a slippage floor; closes keep USDG as
  ``initialCollateralToken`` and swap the payout back; SL/TP legs carry the swap path;
  positions from every tier map back to the plain symbol.

Run with ``--noconftest`` (the shared conftest needs an Anvil fork).
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from eth_utils import to_checksum_address

from eth_defi.gmx.ccxt.async_support.exchange import GMX as AsyncGMX
from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.collateral_routing import (
    USDC_USDG_SWAP_POOL_ARBITRUM,
    USDG_ARBITRUM,
    assign_usdc_routed_symbols,
    calculate_swap_min_output,
    get_usdc_route,
    is_zero_open_interest_cap,
)
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.gmx.market_guards import filter_zero_open_interest_cap_markets
from eth_defi.gmx.order.base_order import BaseOrder, OrderParams
from eth_defi.gmx.order.sltp_order import SLTPOrder
from eth_defi.gmx.trading import GMXTrading, resolve_increase_funding

USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
USDG = USDG_ARBITRUM
SWAP_POOL = USDC_USDG_SWAP_POOL_ARBITRUM
WBTC = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"
ZEC_INDEX = "0x7ca0B5Ca80291B1fEB2d45702FFE56a7A53E7a97"
BTC_INDEX = "0x47904963fc8b2340414262125aF798B9655E58Cd"
FOO_INDEX = "0x1111111111111111111111111111111111111111"
ZEC_OLD = "0x587759c237acCa739bCE3911647BacF56C876E60"
ZEC_NEW = "0x2CeEafcA180B07C9d1d2f1086f8f59AAFd282832"
BTC_USDC = "0x47c031236e19d024b42f8AE6780E44A573170703"
BTC_USDG = "0xe57B97F2f83F39E74Ec83f3e039FD370884784fF"
FOO_RETIRED = "0x2222222222222222222222222222222222222222"
CAP = "5909000000000000000000000000000000000000"


def _market_infos() -> list[dict]:
    """Subsquid marketInfos as of 2026-11-04, plus a synthetic zero-cap market."""
    return [
        # Old ZEC pool: in DEPRECATED_MARKET_TOKENS.
        {"marketTokenAddress": ZEC_OLD, "indexTokenAddress": ZEC_INDEX, "longTokenAddress": WBTC, "shortTokenAddress": USDC, "maxOpenInterestLong": "300000" + "0" * 30, "maxOpenInterestShort": "300000" + "0" * 30, "minCollateralFactor": "5000000000000000000000000000"},
        # New ZEC pool, USDG single-token.
        {"marketTokenAddress": ZEC_NEW, "indexTokenAddress": ZEC_INDEX, "longTokenAddress": USDG, "shortTokenAddress": USDG, "maxOpenInterestLong": CAP, "maxOpenInterestShort": CAP, "minCollateralFactor": "5000000000000000000000000000"},
        # BTC: live USDC pool plus a USDG pool (likely next batch).
        {"marketTokenAddress": BTC_USDC, "indexTokenAddress": BTC_INDEX, "longTokenAddress": WBTC, "shortTokenAddress": USDC, "maxOpenInterestLong": CAP, "maxOpenInterestShort": CAP, "minCollateralFactor": "5000000000000000000000000000"},
        {"marketTokenAddress": BTC_USDG, "indexTokenAddress": BTC_INDEX, "longTokenAddress": USDG, "shortTokenAddress": USDG, "maxOpenInterestLong": CAP, "maxOpenInterestShort": CAP, "minCollateralFactor": "5000000000000000000000000000"},
        # A retired USDC pool not in DEPRECATED_MARKET_TOKENS: caps 0 on both sides.
        {"marketTokenAddress": FOO_RETIRED, "indexTokenAddress": FOO_INDEX, "longTokenAddress": WBTC, "shortTokenAddress": USDC, "maxOpenInterestLong": "0", "maxOpenInterestShort": "0", "minCollateralFactor": "5000000000000000000000000000"},
    ]


def _tokens() -> list[dict]:
    return [
        {"address": ZEC_INDEX, "symbol": "ZEC", "decimals": 8},
        {"address": BTC_INDEX, "symbol": "BTC", "decimals": 8},
        {"address": FOO_INDEX, "symbol": "FOO", "decimals": 18},
        {"address": USDC, "symbol": "USDC", "decimals": 6},
        {"address": USDG, "symbol": "USDG", "decimals": 6},
        {"address": WBTC, "symbol": "WBTC.b", "decimals": 8},
    ]


def _sync_gmx() -> GMX:
    gmx = GMX.__new__(GMX)
    gmx.subsquid = MagicMock()
    gmx.subsquid.get_market_infos.return_value = _market_infos()
    gmx.api = MagicMock()
    gmx.api.get_tokens.return_value = _tokens()
    gmx.config = MagicMock()
    gmx.config.get_chain.return_value = "arbitrum"
    gmx._filter_datastore_disabled_markets = lambda markets: markets
    gmx.markets = {}
    return gmx


def _routed_market(symbol: str = "ZEC/USDC:USDC") -> dict:
    markets = assign_usdc_routed_symbols(
        {
            "ZEC2/USDC:USDC": {
                "id": "ZEC2",
                "symbol": "ZEC2/USDC:USDC",
                "base": "ZEC2",
                "quote": "USDC",
                "info": {"market_token": ZEC_NEW, "index_token": ZEC_INDEX, "long_token": USDG, "short_token": USDG},
            }
        },
        "arbitrum",
    )
    return markets[symbol]


# ---------------------------------------------------------------------------
# Part A: zero-cap guard
# ---------------------------------------------------------------------------


def test_is_zero_open_interest_cap():
    assert is_zero_open_interest_cap("0", "0")
    assert is_zero_open_interest_cap(0, 0)
    assert not is_zero_open_interest_cap("0", CAP)
    assert not is_zero_open_interest_cap(None, "0")
    assert not is_zero_open_interest_cap("nan", "0")


def test_sync_graphql_loader_drops_zero_cap_market():
    """A retired pool (both caps 0) must not load, even when not hand-listed."""
    markets = _sync_gmx()._load_markets_from_graphql()
    assert all(m["info"]["market_token"] != FOO_RETIRED for m in markets.values()), markets.keys()
    assert "FOO/USDC:USDC" not in markets


def test_async_graphql_loader_drops_zero_cap_market():
    markets = asyncio.run(_run_async_graphql())
    assert "FOO/USDC:USDC" not in markets


def test_onchain_zero_cap_filter_drops_only_both_zero():
    """DataStore pass: drop when both caps read 0; keep on failed reads or a non-zero side."""
    markets = {
        "FOO/USDC:USDC": {"info": {"market_token": FOO_RETIRED}},
        "BTC/USDC:USDC": {"info": {"market_token": BTC_USDC}},
        "ZEC/USDC:USDC": {"info": {"market_token": ZEC_NEW}},
    }
    zero = (0).to_bytes(32, "big")
    cap = (5).to_bytes(32, "big")
    results = [
        (True, zero),
        (True, zero),  # FOO: both 0 -> drop
        (True, cap),
        (True, zero),  # BTC: long non-zero -> keep
        (False, b""),
        (True, zero),  # ZEC: failed read -> keep
    ]
    datastore = MagicMock()
    datastore.address = "0x0000000000000000000000000000000000000001"
    datastore.encode_abi.return_value = "0x00"
    multicall = MagicMock()
    multicall.functions.aggregate3.return_value.call.return_value = results
    with patch("eth_defi.gmx.market_guards.get_datastore_contract", return_value=datastore), patch("eth_defi.gmx.market_guards.get_multicall_contract", return_value=multicall):
        kept = filter_zero_open_interest_cap_markets(MagicMock(), "arbitrum", markets)
    assert set(kept) == {"BTC/USDC:USDC", "ZEC/USDC:USDC"}


def test_onchain_zero_cap_filter_fails_open():
    markets = {"BTC/USDC:USDC": {"info": {"market_token": BTC_USDC}}}
    with patch("eth_defi.gmx.market_guards.get_datastore_contract", side_effect=RuntimeError("rpc down")):
        assert filter_zero_open_interest_cap_markets(MagicMock(), "arbitrum", markets) == markets


# ---------------------------------------------------------------------------
# Part B: symbol mapping
# ---------------------------------------------------------------------------


async def _run_async_graphql() -> dict:
    gmx = AsyncGMX({})
    gmx.chain = "arbitrum"
    gmx.subsquid = AsyncMock()
    gmx.subsquid.get_market_infos = AsyncMock(return_value=_market_infos())
    gmx._fetch_tokens_async = AsyncMock(return_value=_tokens())
    gmx._filter_datastore_disabled_markets = lambda markets: markets
    return await gmx._load_markets_from_graphql()


@pytest.mark.parametrize("loader", ["sync", "async"])
def test_graphql_loaders_map_zec_to_usdg_pool_with_usdc_route(loader):
    markets = _sync_gmx()._load_markets_from_graphql() if loader == "sync" else asyncio.run(_run_async_graphql())

    zec = markets["ZEC/USDC:USDC"]
    assert zec["info"]["market_token"].lower() == ZEC_NEW.lower()
    assert zec["base"] == "ZEC"
    assert zec["info"]["usdc_route"]["collateral_token"] == USDG
    assert zec["info"]["usdc_route"]["swap_path"] == [SWAP_POOL]
    assert "ZEC2/USDC:USDC" not in markets
    # Old pool never loads (deprecated).
    assert all(m["info"]["market_token"].lower() != ZEC_OLD.lower() for m in markets.values())
    # BTC keeps the live USDC pool; the BTC USDG pool stays out (BTC2 excluded).
    assert markets["BTC/USDC:USDC"]["info"]["market_token"].lower() == BTC_USDC.lower()
    assert "usdc_route" not in markets["BTC/USDC:USDC"]["info"]
    assert all(m["info"]["market_token"].lower() != BTC_USDG.lower() for m in markets.values())


def test_assign_keeps_x2_name_when_plain_name_is_owned():
    markets = {
        "ZEC/USDC:USDC": {"base": "ZEC", "info": {"market_token": ZEC_OLD, "long_token": WBTC, "short_token": USDC}},
        "ZEC2/USDC:USDC": {"base": "ZEC2", "info": {"market_token": ZEC_NEW, "long_token": USDG, "short_token": USDG}},
    }
    result = assign_usdc_routed_symbols(markets, "arbitrum", {"BTC2"})
    assert result["ZEC/USDC:USDC"]["info"]["market_token"] == ZEC_OLD
    assert result["ZEC2/USDC:USDC"]["info"]["usdc_route"]["swap_path"] == [SWAP_POOL]


def test_assign_ignores_other_chains():
    markets = {"ZEC2/USDC:USDC": {"base": "ZEC2", "info": {"market_token": ZEC_NEW, "long_token": USDG, "short_token": USDG}}}
    assert assign_usdc_routed_symbols(markets, "avalanche") == markets


# ---------------------------------------------------------------------------
# Part B: opens
# ---------------------------------------------------------------------------


def test_calculate_swap_min_output():
    assert calculate_swap_min_output(1_000_000, 6, 6, 0.005) == 995_000
    assert calculate_swap_min_output(1_000_000, 6, 18, 0.0) == 10**18
    assert calculate_swap_min_output(0, 6, 6, 0.005) == 0
    with pytest.raises(ValueError):
        calculate_swap_min_output(1, 6, 6, 1.0)


def test_resolve_market_info_keeps_routed_pool_for_usdc():
    """The collateral-aware pool scan must not reroute a USDC open to the retired pool."""
    gmx = GMX.__new__(GMX)
    gmx.markets = {"ZEC/USDC:USDC": _routed_market()}
    # A scan that offers the retired [WBTC-USDC] pool, which accepts USDC directly.
    gmx.fetch_pools_for_symbol = MagicMock(
        return_value=[
            {"market_address": ZEC_OLD, "index_token": ZEC_INDEX, "long_token": WBTC, "long_token_symbol": "WBTC.b", "short_token": USDC, "short_token_symbol": "USDC"},
            {"market_address": ZEC_NEW, "index_token": ZEC_INDEX, "long_token": USDG, "long_token_symbol": "USDG", "short_token": USDG, "short_token_symbol": "USDG"},
        ]
    )
    info = gmx._resolve_market_info("ZEC/USDC:USDC", {})
    assert info["market_token"] == ZEC_NEW
    gmx.fetch_pools_for_symbol.assert_not_called()


def test_fetch_pools_for_symbol_skips_deprecated_pools():
    gmx = GMX.__new__(GMX)
    gmx.config = MagicMock()
    gmx.markets = {"ZEC/USDC:USDC": _routed_market()}
    available = {
        ZEC_OLD: {"index_token_address": ZEC_INDEX, "long_token_address": WBTC, "short_token_address": USDC, "long_token_metadata": {"symbol": "WBTC.b"}, "short_token_metadata": {"symbol": "USDC"}, "market_symbol": "ZEC"},
        ZEC_NEW: {"index_token_address": ZEC_INDEX, "long_token_address": USDG, "short_token_address": USDG, "long_token_metadata": {"symbol": "USDG"}, "short_token_metadata": {"symbol": "USDG"}, "market_symbol": "ZEC2"},
    }
    with patch("eth_defi.gmx.ccxt.exchange.Markets") as markets_cls:
        markets_cls.return_value.get_available_markets.return_value = available
        pools = gmx.fetch_pools_for_symbol("ZEC/USDC:USDC")
    assert [p["market_address"] for p in pools] == [ZEC_NEW]


def _convert_open(params: dict) -> dict:
    gmx = GMX.__new__(GMX)
    gmx.markets = {"ZEC/USDC:USDC": _routed_market()}
    gmx.markets_loaded = True
    gmx.leverage = {}
    gmx.default_slippage = 0.005
    gmx.config = MagicMock()
    gmx.config.get_chain.return_value = "arbitrum"
    return gmx._convert_ccxt_to_gmx_params("ZEC/USDC:USDC", "limit", "buy", 0.1, 400.0, params)


def test_convert_open_on_routed_market_funds_usdg_with_usdc():
    gmx_params = _convert_open({"leverage": 1.0})
    assert gmx_params["market_key"] == ZEC_NEW
    assert gmx_params["collateral_symbol"] == "USDG"
    assert gmx_params["start_token_symbol"] == "USDC"
    assert gmx_params["swap_path"] == [SWAP_POOL]


def test_convert_open_with_explicit_usdg_sends_usdg():
    gmx_params = _convert_open({"leverage": 1.0, "collateral_symbol": "USDG"})
    assert gmx_params["collateral_symbol"] == "USDG"
    assert gmx_params["start_token_symbol"] == "USDG"
    assert "swap_path" not in gmx_params


def _parsed_increase(**overrides) -> dict:
    parsed = {
        "market_key": ZEC_NEW,
        "index_token_address": ZEC_INDEX,
        "collateral_address": USDG,
        "start_token_address": USDC,
        "swap_path": [SWAP_POOL],
        "is_long": True,
        "size_delta": 40 * 10**30,
        "initial_collateral_delta": 40_000_000,
        "slippage_percent": 0.005,
    }
    parsed.update(overrides)
    return parsed


def test_resolve_increase_funding_usdc_route():
    initial, path, floor = resolve_increase_funding(_parsed_increase(), "arbitrum", {USDC: 6, USDG: 6})
    assert initial == USDC
    assert path == [SWAP_POOL]
    assert floor == 39_800_000


def test_resolve_increase_funding_no_swap_and_other_routes():
    initial, path, floor = resolve_increase_funding(_parsed_increase(start_token_address=USDG, swap_path=[]), "arbitrum")
    assert (initial, path, floor) == (USDG, [], 0)
    weth = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    other = _parsed_increase(collateral_address=USDC, start_token_address=weth, swap_path=["0x70d95587d40A2caf56bd97485aB3Eec10Bee6336"])
    initial, path, floor = resolve_increase_funding(other, "arbitrum")
    assert initial == weth and floor == 0
    assert resolve_increase_funding(_parsed_increase(), "arbitrum", min_output_amount=7)[2] == 7


@pytest.mark.parametrize("method", ["open_position", "open_limit_position"])
def test_trading_open_sends_usdc_with_swap_path(method):
    """IncreaseOrder must send USDC (initialCollateralToken) with the swap leg, not USDG."""
    trading = GMXTrading.__new__(GMXTrading)
    trading.config = MagicMock()
    trading.config.get_chain.return_value = "arbitrum"
    trading._gas_monitor_config = SimpleNamespace(enabled=False)
    trading._check_gas_and_log_estimate = MagicMock()
    parser = MagicMock()
    parser.return_value.process_parameters_dictionary.side_effect = lambda params: {**params, **_parsed_increase()}
    with patch("eth_defi.gmx.trading.OrderArgumentParser", parser), patch("eth_defi.gmx.trading.IncreaseOrder") as order_cls, patch("eth_defi.gmx.trading._get_token_metadata_dict", return_value={USDC: {"decimals": 6}, USDG: {"decimals": 6}}):
        kwargs = dict(market_symbol="ZEC", collateral_symbol="USDG", start_token_symbol="USDC", is_long=True, size_delta_usd=40.0, leverage=1.0, slippage_percent=0.005, market_key=ZEC_NEW, index_token_address=ZEC_INDEX, swap_path=[SWAP_POOL])
        if method == "open_limit_position":
            kwargs["trigger_price"] = 400.0
        getattr(trading, method)(**kwargs)

    passed = parser.return_value.process_parameters_dictionary.call_args.args[0]
    assert passed["swap_path"] == [SWAP_POOL]
    assert order_cls.call_args.kwargs["collateral_address"] == USDC
    create = order_cls.return_value.create_limit_increase_order if method == "open_limit_position" else order_cls.return_value.create_increase_order
    assert create.call_args.kwargs["swap_path"] == [SWAP_POOL]
    assert create.call_args.kwargs["min_output_amount"] == 39_800_000


def _base_order() -> BaseOrder:
    order = BaseOrder.__new__(BaseOrder)
    order._gas_limits = {"increase_order": 3_000_000, "decrease_order": 3_000_000, "single_swap": 1_000_000, "multicall_base": 200_000}
    order.config = MagicMock()
    order.config.get_wallet_address.return_value = "0x000000000000000000000000000000000000dEaD"
    order.config.referral_code = None
    return order


def test_gas_limits_include_swap_hops():
    order = _base_order()
    assert order._determine_gas_limits(True, False, False)["execution"] == 3_000_000
    assert order._determine_gas_limits(True, False, False, swap_count=1)["execution"] == 4_000_000
    assert order._determine_gas_limits(False, True, False, swap_count=1)["total"] == 4_200_000


def test_increase_order_arguments_carry_usdc_and_swap_path():
    params = OrderParams(market_key=ZEC_NEW, collateral_address=USDC, index_token_address=ZEC_INDEX, is_long=True, size_delta=40 * 10**30, initial_collateral_delta_amount="40000000", swap_path=[SWAP_POOL], min_output_amount=39_800_000)
    addresses, numbers, *_ = _base_order()._build_order_arguments(params, 1, 2, 3, 4)
    assert addresses[4] == ZEC_NEW
    assert addresses[5] == USDC
    assert addresses[6] == [SWAP_POOL]
    assert numbers[1] == 40_000_000
    assert numbers[6] == 39_800_000


# ---------------------------------------------------------------------------
# Part B: closes and positions
# ---------------------------------------------------------------------------

#: Trimmed GMX REST v2 /positions entry for a public account's ZEC USDG short (2026-11-04).
REST_USDG_POSITION = {
    "account": "0x39958845c0Fd534b934589cddf5627512DB8a543",
    "marketAddress": ZEC_NEW,
    "collateralTokenAddress": USDG,
    "sizeInUsd": "1009307285953030687520625000000",
    "sizeInTokens": "88068",
    "collateralAmount": "2499637",
    "isLong": False,
    "indexName": "ZEC/USD",
    "poolName": "USDG-USDG",
    "markPrice": "1305611944955227500000000000000000",
    "entryPrice": "1146054510097913757006659626652132",
    "collateralUsd": "2500126881791566292295450000000",
    "leverage": "4284",
    "pnlPercentage": "-562",
}


def test_rest_position_uses_real_collateral_token():
    """The REST tier hard-coded collateral "USDC"; a USDG position must say USDG."""
    tiers = GetOpenPositions.__new__(GetOpenPositions)
    processed = tiers._process_rest_api_position(REST_USDG_POSITION, {USDG.lower(): "USDG", USDC.lower(): "USDC"})
    assert processed["collateral_token"] == "USDG"
    assert processed["collateral_token_address"] == USDG
    assert processed["market"] == ZEC_NEW
    assert processed["market_symbol"] == "ZEC"


def _close_gmx() -> GMX:
    gmx = GMX.__new__(GMX)
    gmx.markets = {"ZEC/USDC:USDC": _routed_market()}
    gmx.config = MagicMock()
    gmx.config.get_chain.return_value = "arbitrum"
    gmx.default_slippage = 0.005
    gmx.execution_buffer = 3.0
    gmx.trader = MagicMock()
    return gmx


def test_find_position_matches_usdg_pool_by_address_across_tier_names():
    gmx = _close_gmx()
    positions = {"ZEC2_short": {"market": ZEC_NEW, "market_symbol": "ZEC2", "collateral_token": "USDG", "is_long": False}}
    found = gmx._find_position_for_close(positions, gmx.markets["ZEC/USDC:USDC"], "USDC", is_closing_long=False)
    assert found is positions["ZEC2_short"]
    assert gmx._find_position_for_close(positions, gmx.markets["ZEC/USDC:USDC"], "USDC", is_closing_long=True) is None


def test_find_position_still_finds_leftover_old_pool_position():
    gmx = _close_gmx()
    positions = {"ZEC_long": {"market": ZEC_OLD, "market_symbol": "ZEC", "collateral_token": "USDC", "collateral_token_address": USDC, "is_long": True}}
    found = gmx._find_position_for_close(positions, gmx.markets["ZEC/USDC:USDC"], "USDC", is_closing_long=True)
    assert found is positions["ZEC_long"]


def _close_kwargs(position: dict) -> dict:
    gmx = _close_gmx()
    gmx_params = {"market_symbol": "ZEC", "collateral_symbol": "USDC", "market_key": ZEC_NEW, "index_token_address": ZEC_INDEX}
    gmx._execute_close_with_position("ZEC/USDC:USDC", "market", "buy", gmx_params, position, 10 * 10**30, 0)
    return gmx.trader.close_position.call_args.kwargs


def test_close_usdg_position_swaps_payout_to_usdc():
    position = {"market": ZEC_NEW, "collateral_token": "USDC", "collateral_token_address": USDG, "is_long": False}
    kwargs = _close_kwargs(position)
    assert kwargs["market_key"] == ZEC_NEW
    assert kwargs["collateral_symbol"] == "USDG"  # position key collateral, label corrected from address
    assert kwargs["start_token_symbol"] == "USDG"
    assert kwargs["swap_path"] == [SWAP_POOL]


def test_close_leftover_old_pool_position_has_no_swap():
    position = {"market": ZEC_OLD, "collateral_token": "USDC", "collateral_token_address": USDC, "is_long": True}
    kwargs = _close_kwargs(position)
    assert kwargs["market_key"] == to_checksum_address(ZEC_OLD)
    assert kwargs["collateral_symbol"] == "USDC"
    assert "swap_path" not in kwargs


def test_trading_close_passes_swap_path_to_decrease_order():
    trading = GMXTrading.__new__(GMXTrading)
    trading.config = MagicMock()
    trading.config.get_chain.return_value = "arbitrum"
    trading._gas_monitor_config = SimpleNamespace(enabled=False)
    trading._check_gas_and_log_estimate = MagicMock()
    parser = MagicMock()
    parser.return_value.process_parameters_dictionary.side_effect = lambda params: {**params, "market_key": ZEC_NEW, "collateral_address": USDG, "index_token_address": ZEC_INDEX, "size_delta": 10 * 10**30, "initial_collateral_delta": 0}
    with patch("eth_defi.gmx.trading.OrderArgumentParser", parser), patch("eth_defi.gmx.trading.DecreaseOrder") as order_cls:
        trading.close_position(market_symbol="ZEC", collateral_symbol="USDG", start_token_symbol="USDG", is_long=False, size_delta_usd=10.0, initial_collateral_delta=0, market_key=ZEC_NEW, index_token_address=ZEC_INDEX, swap_path=[SWAP_POOL])
    assert order_cls.call_args.kwargs["collateral_address"] == USDG
    assert order_cls.return_value.create_decrease_order.call_args.kwargs["swap_path"] == [SWAP_POOL]


def test_positions_map_back_to_plain_symbol():
    gmx = _close_gmx()
    assert gmx._unified_symbol_for_position({"market": ZEC_NEW, "market_symbol": "ZEC2"}) == "ZEC/USDC:USDC"
    assert gmx._unified_symbol_for_position({"market": BTC_USDC, "market_symbol": "BTC"}) == "BTC/USDC:USDC"


# ---------------------------------------------------------------------------
# Part B: SL/TP
# ---------------------------------------------------------------------------


def _sltp(**kwargs) -> SLTPOrder:
    order = SLTPOrder.__new__(SLTPOrder)
    order.market_key = ZEC_NEW
    order.collateral_address = USDG
    order.index_token_address = ZEC_INDEX
    order.is_long = True
    order.decrease_position_swap_type = 1
    order.should_unwrap_native_token = False
    order.decrease_swap_path = kwargs.get("decrease_swap_path", [])
    order.initial_collateral_address = USDG
    order.config = MagicMock()
    order.config.get_wallet_address.return_value = "0x000000000000000000000000000000000000dEaD"
    order.config.referral_code = None
    order._gas_limits = {"decrease_order": 3_000_000, "single_swap": 1_000_000, "estimated_fee_base_gas_limit_v2_1": 130_000, "estimated_fee_per_oracle_price": 330_000, "estimated_fee_multiplier_factor": 10**30}
    order.web3 = MagicMock()
    order.web3.eth.gas_price = 1
    return order


def test_sltp_decrease_legs_carry_swap_path():
    args = _sltp(decrease_swap_path=[SWAP_POOL])._build_decrease_order_arguments(10 * 10**30, 1, 1, 5, 1)
    assert args[0][5] == USDG
    assert args[0][6] == [SWAP_POOL]
    assert _sltp()._build_decrease_order_arguments(10 * 10**30, 1, 1, 5, 1)[0][6] == []


def test_sltp_fee_includes_swap_hop():
    plain = _sltp()._calculate_execution_fee("decrease_order")
    routed = _sltp(decrease_swap_path=[SWAP_POOL])._calculate_execution_fee("decrease_order")
    assert routed - plain == 1_000_000 + 330_000


def test_sltp_constructor_defaults():
    with patch("eth_defi.gmx.order.base_order.BaseOrder.__init__", return_value=None):
        order = SLTPOrder(MagicMock(), ZEC_NEW, USDG, ZEC_INDEX, True, decrease_swap_path=[SWAP_POOL.lower()], initial_collateral_address=USDC)
    assert order.decrease_swap_path == [SWAP_POOL]
    assert order.initial_collateral_address == USDC
    with patch("eth_defi.gmx.order.base_order.BaseOrder.__init__", return_value=None):
        order = SLTPOrder(MagicMock(), ZEC_NEW, USDG, ZEC_INDEX, True)
    assert order.decrease_swap_path == [] and order.initial_collateral_address == USDG


def test_route_table():
    route = get_usdc_route("arbitrum", USDG.lower())
    assert route.pay_token == USDC
    assert route.swap_path == (SWAP_POOL,)
    assert get_usdc_route("arbitrum", USDC) is None
