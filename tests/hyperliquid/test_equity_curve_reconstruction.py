"""Tests for Hyperliquid equity curve reconstruction.

Uses a DuckDB fixture extracted from the live trade history database
containing data for two accounts:

- Vault (0x15be61...): 200 fills, 38 funding, 481 ledger (vaultCreate/vaultDeposit/vaultWithdraw)
- Trader (0x18cde6...): 200 fills, 118 funding, 45 ledger (deposit/withdraw/etc.)
"""

from pathlib import Path

import pytest

from eth_defi.hyperliquid.equity_curve_reconstruction import (
    EquityCurveData,
    create_equity_curve_figure,
    reconstruct_account_value_curve,
    reconstruct_equity_curve,
    reconstruct_pnl_curve,
    reconstruct_vault_share_price,
)
from eth_defi.hyperliquid.trade_history_db import (
    HyperliquidTradeHistoryDatabase,
    LedgerEvent,
)

FIXTURE_DB = Path(__file__).parent / "fixtures" / "trade-history-sample.duckdb"
VAULT_ADDRESS = "0x15be61aef0ea4e4dc93c79b668f26b3f1be75a66"
TRADER_ADDRESS = "0x18cde66120c9195fb6e50a4b1e13bce4c85d1300"


@pytest.fixture()
def db(tmp_path):
    """Open the fixture DB as a fresh copy to avoid locking issues."""
    import shutil

    test_db = tmp_path / "test.duckdb"
    shutil.copy2(FIXTURE_DB, test_db)
    database = HyperliquidTradeHistoryDatabase(test_db)
    yield database
    database.close()


# ──────────────────────────────────────────────
# get_ledger() tests
# ──────────────────────────────────────────────


def test_get_ledger_vault(db):
    """get_ledger returns LedgerEvent objects for the vault account."""
    events = db.get_ledger(VAULT_ADDRESS)
    assert len(events) == 481
    assert all(isinstance(e, LedgerEvent) for e in events)

    # First event should be vaultCreate
    assert events[0].event_type == "vaultCreate"
    assert events[0].usdc > 0
    assert events[0].timestamp is not None
    assert events[0].timestamp_ms > 0


def test_get_ledger_trader(db):
    """get_ledger returns LedgerEvent objects for the trader account."""
    events = db.get_ledger(TRADER_ADDRESS)
    assert len(events) == 45
    assert all(isinstance(e, LedgerEvent) for e in events)

    # Should have deposit and withdraw events
    event_types = {e.event_type for e in events}
    assert "deposit" in event_types
    assert "withdraw" in event_types


def test_get_ledger_time_filter(db):
    """get_ledger respects start_time and end_time filters."""
    all_events = db.get_ledger(VAULT_ADDRESS)
    assert len(all_events) > 10

    # Filter to a narrow time window
    mid = all_events[len(all_events) // 2]
    filtered = db.get_ledger(
        VAULT_ADDRESS,
        start_time=mid.timestamp,
    )
    assert len(filtered) < len(all_events)
    assert all(e.timestamp >= mid.timestamp for e in filtered)


# ──────────────────────────────────────────────
# Trader PnL tests
# ──────────────────────────────────────────────


def test_reconstruct_pnl_curve_trader(db):
    """PnL curve from trader fills and funding has expected columns and non-zero values."""
    fills = db.get_fills(TRADER_ADDRESS)
    funding = db.get_funding(TRADER_ADDRESS)
    assert len(fills) == 200
    assert len(funding) > 0

    pnl = reconstruct_pnl_curve(fills, funding)

    assert not pnl.empty
    assert "cumulative_closed_pnl" in pnl.columns
    assert "cumulative_funding_pnl" in pnl.columns
    assert "cumulative_fees" in pnl.columns
    assert "cumulative_net_pnl" in pnl.columns

    # PnL should be non-zero (trader has actual trades)
    assert pnl["cumulative_closed_pnl"].iloc[-1] != 0
    # Fees should be positive
    assert pnl["cumulative_fees"].iloc[-1] > 0
    # Net PnL = closed + funding - fees
    final = pnl.iloc[-1]
    assert final["cumulative_net_pnl"] == pytest.approx(final["cumulative_closed_pnl"] + final["cumulative_funding_pnl"] - final["cumulative_fees"])


def test_reconstruct_account_value_curve_trader(db):
    """Account value curve for trader with deposit/withdraw ledger events."""
    fills = db.get_fills(TRADER_ADDRESS)
    funding = db.get_funding(TRADER_ADDRESS)
    ledger = db.get_ledger(TRADER_ADDRESS)

    pnl = reconstruct_pnl_curve(fills, funding)
    av = reconstruct_account_value_curve(ledger, pnl)

    assert not av.empty
    assert "account_value" in av.columns
    assert "net_deposits" in av.columns
    assert "cumulative_deposits" in av.columns

    # Should have positive deposits
    assert av["cumulative_deposits"].iloc[-1] > 0


def test_reconstruct_equity_curve_trader(db):
    """Full pipeline for trader returns EquityCurveData with no share price curve.

    Ledger events are clipped to the first fill timestamp. In this fixture
    all 45 ledger events predate the first fill, so ledger_count is 0.
    """
    data = reconstruct_equity_curve(db, TRADER_ADDRESS)

    assert data is not None
    assert isinstance(data, EquityCurveData)
    assert data.is_vault is False
    assert data.label == "Test Trader"
    assert data.share_price_curve is None
    assert data.fill_count == 200
    assert data.funding_count > 0
    # All 45 ledger events predate the first fill and are clipped
    assert data.ledger_count == 0
    assert data.data_start_at is not None
    assert not data.pnl_curve.empty


def test_create_figure_trader(db):
    """Plotly figure for trader has 3 subplot rows (account value, PnL, heatmap)."""
    data = reconstruct_equity_curve(db, TRADER_ADDRESS)
    assert data is not None

    fig = create_equity_curve_figure(data)

    # Should have traces for account value, PnL, and heatmap
    assert len(fig.data) > 0
    # Check layout has 3 y-axes (account value + PnL + heatmap)
    yaxis_keys = [k for k in fig.layout.to_plotly_json() if k.startswith("yaxis")]
    assert len(yaxis_keys) == 3


# ──────────────────────────────────────────────
# Vault PnL tests
# ──────────────────────────────────────────────


def test_reconstruct_pnl_curve_vault(db):
    """PnL curve from vault fills and funding."""
    fills = db.get_fills(VAULT_ADDRESS)
    funding = db.get_funding(VAULT_ADDRESS)
    assert len(fills) == 200

    pnl = reconstruct_pnl_curve(fills, funding)

    assert not pnl.empty
    assert pnl["cumulative_fees"].iloc[-1] > 0


def test_reconstruct_vault_share_price(db):
    """Vault share price starts at 1.0 and changes with deposits/PnL."""
    fills = db.get_fills(VAULT_ADDRESS)
    funding = db.get_funding(VAULT_ADDRESS)
    ledger = db.get_ledger(VAULT_ADDRESS)

    sp = reconstruct_vault_share_price(fills, funding, ledger)

    assert not sp.empty
    assert "share_price" in sp.columns
    assert "total_assets" in sp.columns
    assert "total_supply" in sp.columns

    # Share price should start at 1.0 (first deposit)
    assert sp["share_price"].iloc[0] == pytest.approx(1.0)
    # Total supply should be positive
    assert sp["total_supply"].iloc[-1] > 0


def test_reconstruct_equity_curve_vault(db):
    """Full pipeline for vault returns EquityCurveData with share price curve.

    Ledger events are clipped to the first fill timestamp. The vault has
    481 total ledger events but only 26 are after the first fill.
    """
    data = reconstruct_equity_curve(db, VAULT_ADDRESS)

    assert data is not None
    assert isinstance(data, EquityCurveData)
    assert data.is_vault is True
    assert data.label == "Test Vault"
    assert data.share_price_curve is not None
    assert not data.share_price_curve.empty
    assert data.fill_count == 200
    # Ledger clipped from 481 to 26 (only events after first fill)
    assert data.ledger_count == 26
    assert data.data_start_at is not None


def test_create_figure_vault(db):
    """Plotly figure for vault has 5 subplot rows (account value, PnL, share price, supply, heatmap)."""
    data = reconstruct_equity_curve(db, VAULT_ADDRESS)
    assert data is not None

    fig = create_equity_curve_figure(data)

    assert len(fig.data) > 0
    # Check layout has 5 y-axes (account value + PnL + share price + supply + heatmap)
    yaxis_keys = [k for k in fig.layout.to_plotly_json() if k.startswith("yaxis")]
    assert len(yaxis_keys) == 5


# ──────────────────────────────────────────────
# Edge case tests
# ──────────────────────────────────────────────


def test_reconstruct_equity_curve_not_found(db):
    """Unknown address returns None."""
    data = reconstruct_equity_curve(db, "0x0000000000000000000000000000000000000000")
    assert data is None


def test_reconstruct_pnl_curve_empty():
    """Empty fills and funding returns empty DataFrame."""
    pnl = reconstruct_pnl_curve([], [])
    assert pnl.empty
    assert "cumulative_net_pnl" in pnl.columns


def test_reconstruct_account_value_curve_no_ledger():
    """No ledger events returns empty DataFrame."""
    import pandas as pd

    empty_pnl = pd.DataFrame(columns=["cumulative_net_pnl"])
    av = reconstruct_account_value_curve([], empty_pnl)
    assert av.empty
    assert "account_value" in av.columns
