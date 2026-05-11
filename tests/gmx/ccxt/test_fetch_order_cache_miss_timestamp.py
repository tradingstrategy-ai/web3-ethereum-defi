"""Bug B regression: synthetic order from the cache-miss path must carry a real epoch-ms timestamp.

See tradingstrategy-ai/gmx-strategies#67.

Two layers of coverage:

1. Direct unit tests for ``_block_timestamp_ms`` (sync + async) — lock the
   helper's contract: real epoch-ms on success, ``None`` on falsy block
   number, ``None`` on any RPC failure (never raises).
2. Integration test for the failed-tx synthetic branch inside
   ``GMX.fetch_order`` — pins the helper to the call site and asserts the
   resulting synthetic order's timestamp is real, not ``block.number * 1000``.

The grep-level invariant ``"timestamp": tx.get("blockNumber", 0) * 1000`` →
zero hits in both ``eth_defi/gmx/ccxt/exchange.py`` and
``eth_defi/gmx/ccxt/async_support/exchange.py`` is enforced by code review
and by the fact that all six wired sites now route through
``_block_timestamp_ms``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Layer 1 — direct unit tests for the helper
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_block_number() -> int:
    """Block 400_000_000 on Arbitrum, occurred ~epoch second 1_780_000_000 (May 2026)."""
    return 400_000_000


@pytest.fixture
def fake_block_timestamp_s() -> int:
    return 1_780_000_000


def test_sync_helper_returns_block_timestamp_in_ms(fake_block_number, fake_block_timestamp_s):
    from eth_defi.gmx.ccxt.exchange import _block_timestamp_ms

    web3 = MagicMock()
    web3.eth.get_block.return_value = {"timestamp": fake_block_timestamp_s}
    assert _block_timestamp_ms(web3, fake_block_number) == fake_block_timestamp_s * 1000
    web3.eth.get_block.assert_called_once_with(fake_block_number)


@pytest.mark.parametrize("falsy", [None, 0])
def test_sync_helper_returns_none_for_falsy_block_number(falsy):
    from eth_defi.gmx.ccxt.exchange import _block_timestamp_ms

    web3 = MagicMock()
    assert _block_timestamp_ms(web3, falsy) is None
    web3.eth.get_block.assert_not_called()


def test_sync_helper_swallows_rpc_failures_and_returns_none(fake_block_number):
    from eth_defi.gmx.ccxt.exchange import _block_timestamp_ms

    web3 = MagicMock()
    web3.eth.get_block.side_effect = ConnectionError("RPC down")
    assert _block_timestamp_ms(web3, fake_block_number) is None


@pytest.mark.asyncio
async def test_async_helper_returns_block_timestamp_in_ms(fake_block_number, fake_block_timestamp_s):
    from eth_defi.gmx.ccxt.async_support.exchange import _block_timestamp_ms

    web3 = MagicMock()
    web3.eth.get_block = AsyncMock(return_value={"timestamp": fake_block_timestamp_s})
    assert await _block_timestamp_ms(web3, fake_block_number) == fake_block_timestamp_s * 1000
    web3.eth.get_block.assert_awaited_once_with(fake_block_number)


@pytest.mark.asyncio
@pytest.mark.parametrize("falsy", [None, 0])
async def test_async_helper_returns_none_for_falsy_block_number(falsy):
    from eth_defi.gmx.ccxt.async_support.exchange import _block_timestamp_ms

    web3 = MagicMock()
    web3.eth.get_block = AsyncMock()
    assert await _block_timestamp_ms(web3, falsy) is None
    web3.eth.get_block.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_helper_swallows_rpc_failures_and_returns_none(fake_block_number):
    from eth_defi.gmx.ccxt.async_support.exchange import _block_timestamp_ms

    web3 = MagicMock()
    web3.eth.get_block = AsyncMock(side_effect=ConnectionError("RPC down"))
    assert await _block_timestamp_ms(web3, fake_block_number) is None


# ---------------------------------------------------------------------------
# Layer 2 — integration test pinning the failed-tx synthetic branch in
# GMX.fetch_order against block.timestamp * 1000 (not block.number * 1000).
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_chain_state(fake_block_number, fake_block_timestamp_s) -> dict:
    receipt = {
        "status": 0,  # tx failed — drives the failed-tx synthetic branch
        "gasUsed": 100_000,
        "blockNumber": fake_block_number,
        "logs": [],
    }
    tx = {"gasPrice": 100_000_000, "blockNumber": fake_block_number}
    return {
        "receipt": receipt,
        "tx": tx,
        "block_timestamp_s": fake_block_timestamp_s,
        "block_number": fake_block_number,
    }


def test_failed_tx_synthetic_uses_block_timestamp_not_block_number(fake_chain_state):
    """The fix: synthetic order timestamp must equal block.timestamp * 1000, not block.number * 1000."""
    from eth_defi.gmx.ccxt.exchange import GMX

    ex = GMX.__new__(GMX)  # bypass __init__
    ex._orders = {}
    ex.web3 = MagicMock()
    ex.web3.eth.get_transaction_receipt.return_value = fake_chain_state["receipt"]
    ex.web3.eth.get_transaction.return_value = fake_chain_state["tx"]
    ex.web3.eth.get_block.return_value = {"timestamp": fake_chain_state["block_timestamp_s"]}

    # Build a 66-char tx hash so the cache-miss path runs
    order = ex.fetch_order("0x" + "a" * 64, "BTC/USDC:USDC")

    assert order["status"] == "failed"
    expected_ms = fake_chain_state["block_timestamp_s"] * 1000
    assert order["timestamp"] == expected_ms, (
        f"timestamp should be block.timestamp*1000 = {expected_ms}, "
        f"got {order['timestamp']} (likely block.number*1000 = {fake_chain_state['block_number'] * 1000})"
    )
    # Sanity bound: must be within ±50 years of "now"
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    fifty_years_ms = 50 * 365 * 24 * 60 * 60 * 1000
    assert abs(now_ms - order["timestamp"]) < fifty_years_ms, "timestamp implausibly far from now"
