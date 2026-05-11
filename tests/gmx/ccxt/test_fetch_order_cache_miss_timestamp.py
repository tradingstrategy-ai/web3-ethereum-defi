"""Bug B regression: synthetic order from the cache-miss path must carry a real epoch-ms timestamp.

See tradingstrategy-ai/gmx-strategies#67.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_chain_state() -> dict:
    """Block 400_000_000 on Arbitrum, occurred ~epoch second 1_780_000_000 (May 2026)."""
    block_number = 400_000_000
    block_timestamp_s = 1_780_000_000
    receipt = {
        "status": 0,  # tx failed — drives the failed-tx synthetic branch
        "gasUsed": 100_000,
        "blockNumber": block_number,
        "logs": [],
    }
    tx = {"gasPrice": 100_000_000, "blockNumber": block_number}
    return {"receipt": receipt, "tx": tx, "block_timestamp_s": block_timestamp_s, "block_number": block_number}


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
