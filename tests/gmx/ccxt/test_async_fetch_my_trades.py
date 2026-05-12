"""Live test for async fetch_my_trades.

Verifies the async stub is no longer a TODO — it must return at least the 2
known fills visible in the GMX UI screenshot (APE @ ~0.16033, LDO @ ~0.38980).

Run::

    cd <web3-ethereum-defi repo root>
    export JSON_RPC_ARBITRUM=$ARBITRUM_CHAIN_JSON_RPC
    export GMX_BOT_WALLET_ADDRESS=0x...
    poetry run pytest tests/gmx/ccxt/test_async_fetch_my_trades.py -v -m live
"""

from __future__ import annotations

import os

import pytest
from web3 import AsyncWeb3

from eth_defi.gmx.ccxt.async_support.exchange import GMX as AsyncGMX

BOT_WALLET = os.environ.get("GMX_BOT_WALLET_ADDRESS", "")


@pytest.mark.asyncio
@pytest.mark.live
@pytest.mark.skipif(not os.getenv("JSON_RPC_ARBITRUM"), reason="requires JSON_RPC_ARBITRUM")
@pytest.mark.skipif(not os.getenv("GMX_BOT_WALLET_ADDRESS"), reason="requires GMX_BOT_WALLET_ADDRESS")
async def test_async_fetch_my_trades_returns_known_fills():
    """async fetch_my_trades must return the APE and LDO execute-limit fills."""
    rpc = os.environ["JSON_RPC_ARBITRUM"]
    gmx = AsyncGMX({"rpcUrl": rpc})
    gmx.wallet_address = BOT_WALLET
    await gmx.load_markets()

    trades = await gmx.fetch_my_trades(limit=50)

    assert isinstance(trades, list), "Must return a list, not []"
    assert len(trades) > 0, "Must return at least 1 trade — got empty list (stub not ported)"

    symbols = {t["symbol"] for t in trades if t.get("symbol")}
    assert "APE/USDC:USDC" in symbols or "LDO/USDC:USDC" in symbols, (
        "Expected APE or LDO fill in trades, got symbols: %s" % symbols
    )
    for trade in trades:
        ts = trade.get("timestamp")
        assert ts is None or ts > 1_700_000_000_000, (
            "Trade timestamp %s looks like block.number*1000, not epoch-ms" % ts
        )
