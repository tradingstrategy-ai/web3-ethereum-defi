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
from web3 import Web3
from eth_defi.gmx.graphql.client import GMXSubsquidClient
from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.config import GMXConfig

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
        price = float(ch.get("executionPrice", 0)) / 1e30  # 30-decimal GMX price
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
