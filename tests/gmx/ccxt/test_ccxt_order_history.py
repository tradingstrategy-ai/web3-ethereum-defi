"""Cover indexed-history merging without querying a public service for fork orders."""

from unittest.mock import Mock, patch

from eth_defi.gmx.ccxt.exchange import GMX


def test_fetch_orders_merges_history_without_duplicate_executions() -> None:
    """Retain pending orders and unique cached/indexed executions together.

    A public indexer cannot see private-fork transactions. Controlled history
    exercises the actual merge and order-key deduplication while the cancellation
    integration separately reads real pending orders from the fork.

    :return:
        None; assertions verify merge, deduplication and pending-only routing.
    """
    exchange = GMX(options={"disable_market_cache": True})
    symbol = "ETH/USDC:USDC"
    market = "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336"
    wallet = "0x0000000000000000000000000000000000000001"
    exchange.wallet_address = wallet
    exchange.markets = {symbol: {"symbol": symbol, "info": {"market_token": market}}}
    pending = {"id": "pending-sl", "status": "open", "symbol": symbol}
    cached = {"id": "cached-close", "status": "closed", "symbol": symbol, "info": {"order_key": "0xAbCd"}}
    exchange._orders = {cached["id"]: cached}
    change = {
        "id": "indexed-close",
        "orderKey": "0x1234",
        "market": market.lower(),
        "type": "decrease",
        "timestamp": "1790985600",
        "sizeDeltaUsd": str(100 * 10**30),
        "isLong": True,
    }
    exchange.subsquid = Mock()
    exchange.subsquid.get_position_changes.return_value = [
        {**change, "id": "duplicate-close", "orderKey": "abcd"},
        {**change, "id": "indexed-increase", "type": "increase"},
        change,
    ]
    with patch.object(exchange, "fetch_open_orders", return_value=[pending]) as fetch:
        orders = exchange.fetch_orders(symbol=symbol)
    assert [order["id"] for order in orders] == ["pending-sl", "cached-close", "indexed-close"]
    assert orders[:2] == [pending, cached]
    assert orders[2]["status"] == "closed"
    assert orders[2]["timestamp"] == 1_790_985_600_000
    assert orders[2]["side"] == "sell"
    assert orders[2]["cost"] == 100.0
    assert orders[2]["info"]["source"] == "subsquid_position_change"
    fetch.assert_called_once_with(symbol=symbol, since=None, limit=None, params={"pending_orders_only": True})
    exchange.subsquid.get_position_changes.assert_called_once_with(account=wallet, limit=50)
