"""Live regression tests for _resolve_order_from_sources (sync + async).

These tests hit real Arbitrum mainnet. Set env vars before running:
    export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC
    export GMX_BOT_WALLET_ADDRESS=0x...   # wallet with historical GMX fills

Run:
    cd <web3-ethereum-defi repo root>
    poetry run pytest tests/gmx/ccxt/test_resolve_order_from_sources.py -v -m live
"""

from __future__ import annotations

import os
import pytest
from web3 import Web3, AsyncWeb3
from eth_defi.gmx.graphql.client import GMXSubsquidClient
from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.ccxt.async_support.exchange import GMX as AsyncGMX
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.utils import convert_raw_price_to_usd

BOT_WALLET = os.environ.get("GMX_BOT_WALLET_ADDRESS", "")


@pytest.fixture(scope="module")
def web3_arb():
    rpc = os.environ["JSON_RPC_ARBITRUM"]
    return Web3(Web3.HTTPProvider(rpc))


@pytest.fixture(scope="module")
def known_executed_order_key(web3_arb):
    """Return an order_key (0x-prefixed bytes32) for a known executed order.

    Looks up the most recent APE/USDC:USDC execute-limit fill for BOT_WALLET
    via Subsquid positionChanges. The test expects executionPrice ~ 0.16033.
    """
    client = GMXSubsquidClient(chain="arbitrum")
    changes = client.get_position_changes(account=BOT_WALLET, limit=20)
    for ch in changes:
        price = convert_raw_price_to_usd(ch.get("executionPrice", 0), 18)  # APE has 18 decimals
        if 0.15 < price < 0.17 and ch.get("orderKey"):
            key = ch["orderKey"]
            return key if key.startswith("0x") else "0x" + key
    pytest.skip("No APE execute-limit fill found for BOT_WALLET — check wallet address")


@pytest.mark.live
@pytest.mark.skipif(not os.getenv("JSON_RPC_ARBITRUM"), reason="requires JSON_RPC_ARBITRUM")
@pytest.mark.skipif(not os.getenv("GMX_BOT_WALLET_ADDRESS"), reason="requires GMX_BOT_WALLET_ADDRESS")
def test_resolver_tier_a_returns_fill_from_subsquid(web3_arb, known_executed_order_key):
    """Tier A: Subsquid tradeActions returns real fill data for an executed order."""
    config = GMXConfig(web3_arb, user_wallet_address=BOT_WALLET)
    gmx = GMX(config)
    gmx.load_markets()

    result = gmx._resolve_order_from_sources(
        order_key_hex=known_executed_order_key,
        symbol="APE/USDC:USDC",
        receipt=None,
        tx=None,
    )

    assert result is not None, "Tier A must return a result for a known executed order"
    assert result["status"] in ("closed", "filled")
    avg = result.get("average") or result.get("price")
    assert avg is not None, "Fill price must be set"
    assert 0.15 < avg < 0.17, f"Expected APE price ~0.16033, got {avg}"
    ts = result.get("timestamp")
    assert ts is not None and ts > 1_700_000_000_000, f"Timestamp must be real epoch-ms, got {ts}"


DOGE_INDEX = "0x" + "aa" * 20
FIL_INDEX = "0x" + "bb" * 20
TAO_INDEX = "0x" + "cc" * 20


def _raw_price(price: float, token_decimals: int) -> str:
    """Encode USD price the way GMX stores index-token prices."""
    return str(int(price * 10 ** (30 - token_decimals)))


def _unit_gmx(markets: dict[str, dict], metadata: dict[str, dict]) -> GMX:
    """Minimal sync GMX instance for pure builder unit tests."""
    gmx = GMX.__new__(GMX)
    gmx.markets = markets
    gmx.markets_loaded = True
    gmx._token_metadata = metadata
    gmx.web3 = type(
        "FakeWeb3",
        (),
        {
            "eth": type(
                "FakeEth",
                (),
                {
                    "block_number": 123,
                    "get_block": staticmethod(lambda block: {"timestamp": 1_780_000_000}),
                },
            )()
        },
    )()
    return gmx


def _unit_async_gmx(markets: dict[str, dict], metadata: dict[str, dict]) -> AsyncGMX:
    """Minimal async GMX instance for pure builder unit tests."""
    gmx = AsyncGMX.__new__(AsyncGMX)
    gmx.markets = markets
    gmx._token_metadata = metadata
    gmx.milliseconds = lambda: 1_780_000_000_000
    gmx.iso8601 = lambda ts: f"{ts}"
    return gmx


def _market(index_token: str) -> dict:
    return {"symbol": "X/USDC:USDC", "info": {"index_token": index_token}}


@pytest.mark.parametrize(
    ("symbol", "index_token", "decimals", "price"),
    [
        ("DOGE/USDC:USDC", DOGE_INDEX, 8, 0.10086039),
        ("FIL/USDC:USDC", FIL_INDEX, 18, 0.91901046),
        ("TAO/USDC:USDC", TAO_INDEX, 9, 266.63831204),
    ],
)
def test_sync_order_builders_decode_token_decimal_aware_prices(symbol, index_token, decimals, price):
    markets = {symbol: _market(index_token)}
    metadata = {index_token: {"decimals": decimals}}
    gmx = _unit_gmx(markets, metadata)

    trade_action_order = gmx._build_order_from_trade_action(
        {
            "eventName": "OrderExecuted",
            "executionPrice": _raw_price(price, decimals),
            "sizeDeltaUsd": str(int(5 * 1e30)),
            "timestamp": "1780000000",
            "orderKey": "0xorderkey",
            "isLong": True,
        },
        symbol,
    )
    rest_order = gmx._build_order_from_rest_order(
        {
            "key": "0xrestorder",
            "isLong": True,
            "triggerPrice": _raw_price(price, decimals),
            "sizeDeltaUsd": str(int(5 * 1e30)),
        },
        symbol,
    )

    assert trade_action_order["average"] == pytest.approx(price)
    assert rest_order["price"] == pytest.approx(price)


@pytest.mark.parametrize(
    ("symbol", "index_token", "decimals", "price"),
    [
        ("DOGE/USDC:USDC", DOGE_INDEX, 8, 0.10086039),
        ("FIL/USDC:USDC", FIL_INDEX, 18, 0.91901046),
        ("TAO/USDC:USDC", TAO_INDEX, 9, 266.63831204),
    ],
)
def test_async_order_builders_decode_token_decimal_aware_prices(symbol, index_token, decimals, price):
    markets = {symbol: _market(index_token)}
    metadata = {index_token: {"decimals": decimals}}
    gmx = _unit_async_gmx(markets, metadata)

    trade_action_order = gmx._build_order_from_trade_action(
        {
            "eventName": "OrderExecuted",
            "executionPrice": _raw_price(price, decimals),
            "sizeDeltaUsd": str(int(5 * 1e30)),
            "timestamp": "1780000000",
            "orderKey": "0xorderkey",
            "isLong": True,
        },
        symbol,
    )
    rest_order = gmx._build_order_from_rest_order(
        {
            "key": "0xrestorder",
            "isLong": True,
            "triggerPrice": _raw_price(price, decimals),
            "sizeDeltaUsd": str(int(5 * 1e30)),
        },
        symbol,
    )

    assert trade_action_order["average"] == pytest.approx(price)
    assert rest_order["price"] == pytest.approx(price)


@pytest.mark.parametrize(
    ("symbol", "index_token", "decimals", "price"),
    [
        ("DOGE/USDC:USDC", DOGE_INDEX, 8, 0.10086039),
        ("FIL/USDC:USDC", FIL_INDEX, 18, 0.91901046),
        ("TAO/USDC:USDC", TAO_INDEX, 9, 266.63831204),
    ],
)
def test_builders_return_amount_in_base_tokens_not_usd_notional(symbol, index_token, decimals, price):
    """CCXT ``amount`` must be base tokens, not USD notional.

    Production regression motivating this assertion: pre-fix, the builders
    returned ``amount = sizeDeltaUsd / 1e30`` — i.e. raw USD notional — and
    that value got persisted into Freqtrade's ``orders.amount``.  The
    strict position-match arm (PR #1008) then compared ``order.amount``
    (USD) against ``position.contracts`` (base tokens), failed by orders
    of magnitude, and never adopted on-chain fills.  Post-fix, the builders
    derive base tokens via ``sizeDeltaInTokens / 10^token_decimals`` (when
    the GMX feed carries it) or ``sizeDeltaUsd / trigger_price`` (REST
    pending-order case), so the assertion locks the unit semantics.
    """
    markets = {symbol: _market(index_token)}
    metadata = {index_token: {"decimals": decimals}}
    size_usd = 5.0  # notional intent — strategy thinks this is a $5 limit
    expected_amount_tokens = size_usd / price  # what base-token amount strategy actually buys

    gmx_sync = _unit_gmx(markets, metadata)
    gmx_async = _unit_async_gmx(markets, metadata)

    # _build_order_from_rest_order has no sizeDeltaInTokens — it derives
    # amount as sizeDeltaUsd / trigger_price.
    rest_sync = gmx_sync._build_order_from_rest_order(
        {
            "key": "0xrestorder",
            "isLong": True,
            "triggerPrice": _raw_price(price, decimals),
            "sizeDeltaUsd": str(int(size_usd * 1e30)),
        },
        symbol,
    )
    rest_async = gmx_async._build_order_from_rest_order(
        {
            "key": "0xrestorder",
            "isLong": True,
            "triggerPrice": _raw_price(price, decimals),
            "sizeDeltaUsd": str(int(size_usd * 1e30)),
        },
        symbol,
    )

    assert rest_sync["amount"] == pytest.approx(expected_amount_tokens, rel=1e-6), (
        f"sync _build_order_from_rest_order amount must be base tokens "
        f"(expected {expected_amount_tokens:.6f} {symbol.split('/')[0]}, got {rest_sync['amount']})"
    )
    assert rest_sync["remaining"] == pytest.approx(expected_amount_tokens, rel=1e-6)
    assert rest_async["amount"] == pytest.approx(expected_amount_tokens, rel=1e-6)
    assert rest_async["remaining"] == pytest.approx(expected_amount_tokens, rel=1e-6)

    # _build_order_from_trade_action: when sizeDeltaInTokens is provided
    # (executed orders), amount must come from there (decimals-aware),
    # NOT from sizeDeltaUsd / 1e30.  The raw int is base tokens scaled
    # by token decimals.
    size_tokens_raw = str(int(expected_amount_tokens * (10**decimals)))
    trade_action_sync = gmx_sync._build_order_from_trade_action(
        {
            "eventName": "OrderExecuted",
            "executionPrice": _raw_price(price, decimals),
            "sizeDeltaUsd": str(int(size_usd * 1e30)),
            "sizeDeltaInTokens": size_tokens_raw,
            "timestamp": "1780000000",
            "orderKey": "0xorderkey",
            "isLong": True,
        },
        symbol,
    )
    trade_action_async = gmx_async._build_order_from_trade_action(
        {
            "eventName": "OrderExecuted",
            "executionPrice": _raw_price(price, decimals),
            "sizeDeltaUsd": str(int(size_usd * 1e30)),
            "sizeDeltaInTokens": size_tokens_raw,
            "timestamp": "1780000000",
            "orderKey": "0xorderkey",
            "isLong": True,
        },
        symbol,
    )

    assert trade_action_sync["amount"] == pytest.approx(expected_amount_tokens, rel=1e-6), (
        f"sync _build_order_from_trade_action amount must be base tokens from sizeDeltaInTokens "
        f"(expected {expected_amount_tokens:.6f}, got {trade_action_sync['amount']})"
    )
    assert trade_action_sync["filled"] == pytest.approx(expected_amount_tokens, rel=1e-6)
    assert trade_action_async["amount"] == pytest.approx(expected_amount_tokens, rel=1e-6)
    assert trade_action_async["filled"] == pytest.approx(expected_amount_tokens, rel=1e-6)


@pytest.mark.live
@pytest.mark.skipif(not os.getenv("JSON_RPC_ARBITRUM"), reason="requires JSON_RPC_ARBITRUM")
@pytest.mark.skipif(not os.getenv("GMX_BOT_WALLET_ADDRESS"), reason="requires GMX_BOT_WALLET_ADDRESS")
def test_resolver_returns_none_for_unknown_order_key(web3_arb):
    """All tiers miss → returns None so caller builds synthetic."""
    config = GMXConfig(web3_arb, user_wallet_address=BOT_WALLET or "0x" + "0" * 40)
    gmx = GMX(config)
    gmx.load_markets()

    dead_key = "0x" + "dead" * 16  # known-bad 32-byte key
    result = gmx._resolve_order_from_sources(
        order_key_hex=dead_key,
        symbol="BTC/USDC:USDC",
        receipt=None,
        tx=None,
    )
    assert result is None, "Unknown order_key must return None (not raise)"


@pytest.mark.asyncio
@pytest.mark.live
@pytest.mark.skipif(not os.getenv("JSON_RPC_ARBITRUM"), reason="requires JSON_RPC_ARBITRUM")
@pytest.mark.skipif(not os.getenv("GMX_BOT_WALLET_ADDRESS"), reason="requires GMX_BOT_WALLET_ADDRESS")
async def test_async_resolver_tier_a_returns_fill_from_subsquid(known_executed_order_key):
    """Async mirror: Tier A returns real fill data for a known executed order."""
    rpc = os.environ["JSON_RPC_ARBITRUM"]
    gmx = AsyncGMX({"rpcUrl": rpc})
    await gmx.load_markets()
    # Override wallet_address so tiered resolver can filter by account
    gmx.wallet_address = BOT_WALLET

    result = await gmx._resolve_order_from_sources(
        order_key_hex=known_executed_order_key,
        symbol="APE/USDC:USDC",
        receipt=None,
        tx=None,
    )
    assert result is not None, "Async Tier A must return a result for a known executed order"
    assert result["status"] in ("closed", "filled")
    avg = result.get("average") or result.get("price")
    assert avg is not None, "Fill price must be set"
    assert 0.15 < avg < 0.17, f"Expected APE price ~0.16033, got {avg}"
    ts = result.get("timestamp")
    assert ts is not None and ts > 1_700_000_000_000, f"Timestamp must be real epoch-ms, got {ts}"
