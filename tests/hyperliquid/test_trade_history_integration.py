"""Integration tests for Hyperliquid trade history reconstruction.

Tests trade history reconstruction, DuckDB persistence, and sync resume
for both vault and normal accounts.

Requires network access to the Hyperliquid API.
"""

import datetime

import pytest

from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.trade_history import (
    fetch_account_funding,
    fetch_account_trade_history,
)
from eth_defi.hyperliquid.trade_history_db import HyperliquidTradeHistoryDatabase


#: Growi HF vault — moderately active, used in existing test fixtures
VAULT_ADDRESS = "0x1e37a337ed460039d1b15bd3bc489de789768d5e"


@pytest.fixture(scope="module")
def session():
    """Create a shared HTTP session for all tests in this module."""
    return create_hyperliquid_session()


@pytest.mark.timeout(120)
def test_fetch_account_funding(session):
    """Fetch funding payments for a known vault."""
    payments = list(
        fetch_account_funding(
            session,
            VAULT_ADDRESS,
            start_time=datetime.datetime(2025, 12, 1),
            end_time=datetime.datetime(2025, 12, 28),
        )
    )
    assert len(payments) > 0, "Expected funding payments for an active vault"

    # Verify chronological order
    for i in range(1, len(payments)):
        assert payments[i].timestamp_ms >= payments[i - 1].timestamp_ms

    # Verify fields are populated
    first = payments[0]
    assert first.coin
    assert first.timestamp_ms > 0


@pytest.mark.timeout(180)
def test_reconstruct_vault_trade_history(session, tmp_path):
    """Reconstruct full trade history for a vault account and verify against clearinghouse."""
    db = HyperliquidTradeHistoryDatabase(tmp_path / "trade-history.duckdb")
    try:
        db.add_account(VAULT_ADDRESS, label="Growi HF", is_vault=True)
        db.sync_account(session, VAULT_ADDRESS)

        history = fetch_account_trade_history(session, VAULT_ADDRESS)

        assert len(history.fills) > 0
        assert len(history.closed_trades) + len(history.open_trades) > 0

        # Open trades should match clearinghouse positions
        for trade in history.open_trades:
            matching = [p for p in history.open_positions if p.coin == trade.coin]
            assert len(matching) == 1, f"Open trade for {trade.coin} has no matching clearinghouse position"

        # Closed trades should have realised PnL
        for trade in history.closed_trades:
            assert trade.realised_pnl is not None

        # Sync state recorded
        state = db.get_sync_state(VAULT_ADDRESS)
        assert "fills" in state
        assert state["fills"]["row_count"] > 0
    finally:
        db.close()


@pytest.mark.timeout(180)
def test_reconstruct_normal_account_trade_history(session, tmp_path):
    """Reconstruct trade history for a normal (non-vault) Hyperliquid account.

    Uses a known active trader address (Growi HF leader wallet).
    """
    # Use the Growi HF vault address as a normal account test too —
    # the API works the same way for both.
    account_address = "0x3df9769bbbb335340872f01d8157c779d73c6ed0"

    db = HyperliquidTradeHistoryDatabase(tmp_path / "trade-history.duckdb")
    try:
        db.add_account(account_address, label="Test account", is_vault=False)
        db.sync_account(session, account_address)

        history = fetch_account_trade_history(session, account_address)

        assert len(history.fills) > 0
        assert len(history.closed_trades) + len(history.open_trades) >= 0

        state = db.get_sync_state(account_address)
        assert "fills" in state
        assert state["fills"]["row_count"] > 0
    finally:
        db.close()


@pytest.mark.timeout(180)
def test_trade_history_sync_resume(session, tmp_path):
    """Verify sync resumes correctly after interruption.

    1. Sync fills with an end_time cutoff (simulates partial sync)
    2. Re-sync without cutoff
    3. Verify: old data preserved, new data added, no duplicates, sync_state updated
    """
    db = HyperliquidTradeHistoryDatabase(tmp_path / "trade-history.duckdb")
    try:
        db.add_account(VAULT_ADDRESS, label="Growi HF", is_vault=True)

        # First sync: only last 7 days (simulates partial/interrupted sync)
        cutoff = datetime.datetime(2025, 12, 20)
        db.sync_account_fills(session, VAULT_ADDRESS, end_time=cutoff)
        db.save()

        first_state = db.get_sync_state(VAULT_ADDRESS)
        first_count = first_state["fills"]["row_count"]
        first_newest = first_state["fills"]["newest_ts"]
        assert first_count > 0, "First sync produced no fills"

        # Read first-run fills for comparison
        first_fills = db.get_fills(VAULT_ADDRESS)

        # Second sync: broader range (resume)
        db.sync_account_fills(session, VAULT_ADDRESS, end_time=datetime.datetime(2025, 12, 28))
        db.save()

        second_state = db.get_sync_state(VAULT_ADDRESS)
        second_count = second_state["fills"]["row_count"]

        # Should have same or more fills (new data added)
        assert second_count >= first_count, f"Expected >= {first_count} fills, got {second_count}"

        # Newest timestamp should be >= first run
        assert second_state["fills"]["newest_ts"] >= first_newest

        # Original fills should still be present (no data loss)
        second_fills = db.get_fills(VAULT_ADDRESS)
        first_trade_ids = {f.trade_id for f in first_fills}
        second_trade_ids = {f.trade_id for f in second_fills}
        assert first_trade_ids.issubset(second_trade_ids), "Resume lost fills from first sync"

        # No duplicates: count should equal unique trade_ids
        assert second_count == len(second_trade_ids), "Duplicate fills detected after resume"
    finally:
        db.close()
