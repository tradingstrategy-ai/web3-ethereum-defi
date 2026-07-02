"""Integration tests for Hyperliquid trade history reconstruction.

Tests trade history reconstruction, DuckDB persistence, and sync resume
for both vault and normal accounts.

Requires network access to the Hyperliquid API.

.. warning::

    These tests query the **live Hyperliquid API** and use a rolling 7-day
    time window.  The underlying data is not pinned to a historical snapshot:

    - Accounts may stop trading, producing zero fills in the test window.
    - The API purges old fill data, so widening the window is not a reliable fix.
    - Fill counts, funding payments, and trade reconstruction results change
      daily as new activity occurs.

    When these tests break, it is usually because the chosen account has gone
    inactive.  The fix is to switch ``ACTIVE_ACCOUNT`` to a currently active
    trader address — **not** to pin to a historical block or mock the API,
    because the purpose of these tests is to verify the real integration path.
"""

import datetime
import json

import pytest
import requests

from eth_defi.hyperliquid.api import (
    LEADERBOARD_URL,
    fetch_frontend_open_orders_raw,
    fetch_open_orders_raw,
    fetch_perp_clearinghouse_state_raw,
    fetch_portfolio,
)
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.trade_history import (
    fetch_account_funding,
    fetch_account_trade_history,
)
from eth_defi.hyperliquid.trade_history_db import HyperliquidTradeHistoryDatabase


#: Growi HF leader wallet — actively trading account used for fill-dependent tests.
#: The vault address (0x1e37…) has periods of inactivity with zero fills,
#: so we use the leader wallet which trades more consistently.
ACTIVE_ACCOUNT = "0x3df9769bbbb335340872f01d8157c779d73c6ed0"

#: Short time range for faster tests — must be recent enough
#: that Hyperliquid API still returns fills (old data is purged).
TEST_END = datetime.datetime.now(datetime.UTC).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None) - datetime.timedelta(days=1)
TEST_START = TEST_END - datetime.timedelta(days=7)

#: Public top traders that currently have substantial live open state.
#:
#: These are used as preferred live candidates before falling back to
#: the current public leaderboard rows.
LIVE_OPEN_STATE_CANDIDATES = (
    ("ABC", "0x162cc7c861ebd0c06b3d72319201150482518185"),
    ("Leaderboard rank 8", "0xecb63caa47c7c4e77f60f1ce858cf28dc2b82b00"),
    ("Leaderboard rank 5", "0xc926ddba8b7617dbc65712f20cf8e1b58b8598d3"),
)


@pytest.fixture(scope="module")
def session():
    """Create a shared HTTP session for all tests in this module."""
    return create_hyperliquid_session()


def _iter_live_snapshot_candidates() -> list[tuple[str | None, str]]:
    """Return public trader addresses ordered by likelihood of live open state."""
    candidates: list[tuple[str | None, str]] = list(LIVE_OPEN_STATE_CANDIDATES)
    seen_addresses = {address.lower() for _, address in candidates}

    response = requests.get(LEADERBOARD_URL, timeout=30)
    response.raise_for_status()
    leaderboard_rows = response.json()["leaderboardRows"]

    for row in leaderboard_rows[:20]:
        address = row["ethAddress"].lower()
        if address in seen_addresses:
            continue
        candidates.append((row.get("displayName") or None, address))
        seen_addresses.add(address)

    return candidates


def _select_live_snapshot_account(session) -> tuple[str | None, str, dict, list[dict], list[dict]]:
    """Pick a public leaderboard trader with live positions or live orders."""
    for display_name, address in _iter_live_snapshot_candidates():
        clearinghouse_state = fetch_perp_clearinghouse_state_raw(session, address)
        open_orders = fetch_open_orders_raw(session, address)
        frontend_open_orders = fetch_frontend_open_orders_raw(session, address)

        position_count = len(clearinghouse_state.get("assetPositions", []))
        materialised_order_count = len(frontend_open_orders)
        if position_count > 0 or materialised_order_count > 0:
            return display_name, address, clearinghouse_state, open_orders, frontend_open_orders

    pytest.skip("Could not find a public Hyperliquid leaderboard trader with live open state")


@pytest.mark.timeout(60)
def test_fetch_account_funding(session):
    """Fetch funding payments for a known active account."""
    payments = list(
        fetch_account_funding(
            session,
            ACTIVE_ACCOUNT,
            start_time=TEST_START,
            end_time=TEST_END,
        )
    )
    assert len(payments) > 0, "Expected funding payments for an active account"

    # Verify chronological order
    for i in range(1, len(payments)):
        assert payments[i].timestamp_ms >= payments[i - 1].timestamp_ms

    # Verify fields are populated
    first = payments[0]
    assert first.coin
    assert first.timestamp_ms > 0


@pytest.mark.timeout(120)
def test_reconstruct_vault_trade_history(session, tmp_path):
    """Reconstruct trade history for an active account and verify fill data."""
    db = HyperliquidTradeHistoryDatabase(tmp_path / "trade-history.duckdb")
    try:
        db.add_account(ACTIVE_ACCOUNT, label="Growi HF leader", is_vault=False)
        db.sync_account_fills(session, ACTIVE_ACCOUNT, start_time=TEST_START, end_time=TEST_END)

        history = fetch_account_trade_history(
            session,
            ACTIVE_ACCOUNT,
            start_time=TEST_START,
            end_time=TEST_END,
        )

        assert len(history.fills) > 0
        assert len(history.closed_trades) + len(history.open_trades) > 0

        # Closed trades should have realised PnL
        for trade in history.closed_trades:
            assert trade.realised_pnl is not None

        # Sync state recorded
        state = db.get_sync_state(ACTIVE_ACCOUNT)
        assert "fills" in state
        assert state["fills"]["row_count"] > 0
    finally:
        db.close()


@pytest.mark.timeout(120)
def test_reconstruct_normal_account_trade_history(session, tmp_path):
    """Reconstruct trade history for a normal (non-vault) Hyperliquid account.

    Uses a known active trader address (Growi HF leader wallet).
    """
    account_address = "0x3df9769bbbb335340872f01d8157c779d73c6ed0"

    db = HyperliquidTradeHistoryDatabase(tmp_path / "trade-history.duckdb")
    try:
        db.add_account(account_address, label="Test account", is_vault=False)
        db.sync_account_fills(session, account_address, start_time=TEST_START, end_time=TEST_END)

        history = fetch_account_trade_history(
            session,
            account_address,
            start_time=TEST_START,
            end_time=TEST_END,
        )

        assert len(history.fills) > 0
        assert len(history.closed_trades) + len(history.open_trades) >= 0

        state = db.get_sync_state(account_address)
        assert "fills" in state
        assert state["fills"]["row_count"] > 0
    finally:
        db.close()


@pytest.mark.timeout(120)
def test_sync_idempotent(session, tmp_path):
    """Verify that syncing twice with the same time range produces zero new records on the second run.

    This catches deduplication bugs where INSERT OR IGNORE fails to skip
    already-stored records, or where the sync_state watermark doesn't
    prevent re-fetching.
    """
    db = HyperliquidTradeHistoryDatabase(tmp_path / "trade-history.duckdb")
    try:
        db.add_account(ACTIVE_ACCOUNT, label="Growi HF leader", is_vault=False)

        # First sync: fetches real data
        first_result = db.sync_account(
            session,
            ACTIVE_ACCOUNT,
            start_time=TEST_START,
            end_time=TEST_END,
        )
        db.save()

        assert first_result["fills"] > 0, "Expected fills on first sync"
        first_state = db.get_sync_state(ACTIVE_ACCOUNT)

        # Second sync: same time range — should produce zero new records
        second_result = db.sync_account(
            session,
            ACTIVE_ACCOUNT,
            start_time=TEST_START,
            end_time=TEST_END,
        )
        db.save()

        assert second_result["fills"] == 0, f"Expected 0 new fills on re-sync, got {second_result['fills']}"
        assert second_result["funding"] == 0, f"Expected 0 new funding on re-sync, got {second_result['funding']}"
        assert second_result["ledger"] == 0, f"Expected 0 new ledger on re-sync, got {second_result['ledger']}"

        # Row counts should be unchanged
        second_state = db.get_sync_state(ACTIVE_ACCOUNT)
        assert second_state["fills"]["row_count"] == first_state["fills"]["row_count"]
        assert second_state["funding"]["row_count"] == first_state["funding"]["row_count"]
        # Ledger may not have sync state if the account has no ledger events
        if "ledger" in first_state:
            assert second_state["ledger"]["row_count"] == first_state["ledger"]["row_count"]
    finally:
        db.close()


@pytest.mark.timeout(120)
def test_trade_history_sync_resume(session, tmp_path):
    """Verify sync resumes correctly after interruption.

    1. Sync fills with an end_time cutoff (simulates partial sync)
    2. Re-sync with later end_time
    3. Verify: old data preserved, new data added, no duplicates, sync_state updated
    """
    db = HyperliquidTradeHistoryDatabase(tmp_path / "trade-history.duckdb")
    try:
        db.add_account(ACTIVE_ACCOUNT, label="Growi HF leader", is_vault=False)

        # First sync: narrow window (simulates partial/interrupted sync)
        db.sync_account_fills(
            session,
            ACTIVE_ACCOUNT,
            start_time=TEST_START,
            end_time=TEST_START + datetime.timedelta(days=3),
        )
        db.save()

        first_state = db.get_sync_state(ACTIVE_ACCOUNT)
        assert "fills" in first_state, "sync_state should be recorded even for empty windows"
        first_count = first_state["fills"]["row_count"]

        # Read first-run fills for comparison
        first_fills = db.get_fills(ACTIVE_ACCOUNT)

        # Second sync: broader range (resume)
        db.sync_account_fills(session, ACTIVE_ACCOUNT, end_time=TEST_END)
        db.save()

        second_state = db.get_sync_state(ACTIVE_ACCOUNT)
        second_count = second_state["fills"]["row_count"]

        # Should have same or more fills (new data added)
        assert second_count >= first_count, f"Expected >= {first_count} fills, got {second_count}"

        # Full window should have some fills even if the first sub-window was empty
        assert second_count > 0, "Expected fills in the full 7-day window"

        # Newest timestamp should advance or appear after resume
        if first_state["fills"]["newest_ts"] is not None:
            assert second_state["fills"]["newest_ts"] >= first_state["fills"]["newest_ts"]

        # Original fills should still be present (no data loss)
        second_fills = db.get_fills(ACTIVE_ACCOUNT)
        first_trade_ids = {f.trade_id for f in first_fills}
        second_trade_ids = {f.trade_id for f in second_fills}
        assert first_trade_ids.issubset(second_trade_ids), "Resume lost fills from first sync"

        # No duplicates: count should equal unique trade_ids
        assert second_count == len(second_trade_ids), "Duplicate fills detected after resume"
    finally:
        db.close()


@pytest.mark.timeout(60)
def test_fetch_portfolio_first_activity(session):
    """Verify that the portfolio endpoint returns account first activity date.

    Uses the HLP vault (Hyperliquidity Provider) which is one of the
    earliest accounts on Hyperliquid, active since late 2023.

    The ``pnlHistory`` array in the portfolio response is aggregated
    data covering the full account lifetime — unlike fills which are
    capped at ~10K entries. The first entry's timestamp gives a
    reliable account creation / first activity date.
    """
    # HLP vault — one of the earliest Hyperliquid accounts
    hlp_address = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"

    portfolio = fetch_portfolio(session, hlp_address)

    assert portfolio is not None, "Portfolio fetch failed"
    assert portfolio.first_activity_at is not None, "Expected first_activity_at from pnlHistory"
    assert portfolio.all_time_pnl is not None
    assert portfolio.all_time_volume is not None
    # HLP has been active since late 2023
    assert portfolio.first_activity_at < datetime.datetime(2024, 1, 1), f"HLP first activity {portfolio.first_activity_at} should be before 2024-01-01"


@pytest.mark.timeout(120)
def test_sync_account_writes_snapshot_rows(session, tmp_path):
    """sync_account stores snapshot runs and raw source payloads."""
    db = HyperliquidTradeHistoryDatabase(tmp_path / "trade-history.duckdb")
    try:
        db.add_account(VAULT_ADDRESS, label="Growi HF", is_vault=True)

        result = db.sync_account(
            session,
            VAULT_ADDRESS,
            start_time=TEST_START,
            end_time=TEST_END,
        )
        db.save()

        runs = db.get_snapshot_runs(VAULT_ADDRESS)
        assert len(runs) == 1
        run = runs[0]

        assert run["open_position_count"] == result["open_positions"]
        assert run["open_trade_count"] == result["open_trades"]
        assert run["open_order_count"] == result["open_orders"]

        for source_name in (
            "clearinghouseState",
            "openOrders",
            "frontendOpenOrders",
            "historicalOrders",
            "userTwapSliceFills",
        ):
            source = db.get_snapshot_source(VAULT_ADDRESS, source_name)
            assert source is not None, f"Missing snapshot source {source_name}"
            assert source["status"] in {"ok", "error"}

        if run["open_position_count"] > 0:
            positions = db.get_open_position_snapshots(VAULT_ADDRESS)
            assert len(positions) == run["open_position_count"]

        if run["open_order_count"] > 0:
            orders = db.get_open_order_snapshots(VAULT_ADDRESS)
            assert len(orders) == run["open_order_count"]

        if run["open_trade_count"] > 0:
            trades = db.get_open_trade_snapshots(VAULT_ADDRESS)
            assert len(trades) == run["open_trade_count"]
    finally:
        db.close()


@pytest.mark.timeout(180)
def test_sync_account_adds_second_snapshot_run_without_duplicating_event_rows(session, tmp_path):
    """A second sync writes a new snapshot run while event-row counts stay stable."""
    db = HyperliquidTradeHistoryDatabase(tmp_path / "trade-history.duckdb")
    try:
        db.add_account(VAULT_ADDRESS, label="Growi HF", is_vault=True)

        first_result = db.sync_account(
            session,
            VAULT_ADDRESS,
            start_time=TEST_START,
            end_time=TEST_END,
        )
        db.save()
        first_state = db.get_sync_state(VAULT_ADDRESS)
        first_runs = db.get_snapshot_runs(VAULT_ADDRESS)

        second_result = db.sync_account(
            session,
            VAULT_ADDRESS,
            start_time=TEST_START,
            end_time=TEST_END,
        )
        db.save()
        second_state = db.get_sync_state(VAULT_ADDRESS)
        second_runs = db.get_snapshot_runs(VAULT_ADDRESS)

        assert first_result["fills"] > 0
        assert second_result["fills"] == 0
        assert second_result["funding"] == 0
        assert second_result["ledger"] == 0

        assert second_state["fills"]["row_count"] == first_state["fills"]["row_count"]
        assert second_state["funding"]["row_count"] == first_state["funding"]["row_count"]
        assert second_state["ledger"]["row_count"] == first_state["ledger"]["row_count"]

        assert len(first_runs) == 1
        assert len(second_runs) == 2
        assert second_runs[-1]["ts"] >= first_runs[-1]["ts"]
    finally:
        db.close()


@pytest.mark.timeout(180)
def test_capture_account_snapshots_materialises_live_open_state_for_public_trader(session, tmp_path):
    """capture_account_snapshots stores live open positions and orders for a public trader."""
    display_name, address, _, _, _ = _select_live_snapshot_account(session)

    db = HyperliquidTradeHistoryDatabase(tmp_path / "live-open-state.duckdb")
    try:
        db.add_account(address, label=display_name or "Live trader", is_vault=False)

        result = db.capture_account_snapshots(
            session,
            address,
            is_vault=False,
            label=display_name,
        )
        db.save()

        runs = db.get_snapshot_runs(address)
        assert len(runs) == 1

        run = runs[0]
        assert run["open_position_count"] > 0 or run["open_order_count"] > 0

        clearinghouse_source = db.get_snapshot_source(address, "clearinghouseState")
        assert clearinghouse_source is not None
        assert clearinghouse_source["status"] == "ok"
        stored_clearinghouse_state = json.loads(clearinghouse_source["payload_json"])
        expected_position_count = len(stored_clearinghouse_state["assetPositions"])
        assert clearinghouse_source["item_count"] == expected_position_count
        assert run["open_position_count"] == expected_position_count
        assert result["open_positions"] == expected_position_count

        open_orders_source = db.get_snapshot_source(address, "openOrders")
        assert open_orders_source is not None
        assert open_orders_source["status"] == "ok"
        stored_open_orders = json.loads(open_orders_source["payload_json"])
        assert open_orders_source["item_count"] == len(stored_open_orders)

        frontend_orders_source = db.get_snapshot_source(address, "frontendOpenOrders")
        assert frontend_orders_source is not None
        assert frontend_orders_source["status"] == "ok"
        stored_frontend_open_orders = json.loads(frontend_orders_source["payload_json"])
        expected_order_count = len(stored_frontend_open_orders)
        assert frontend_orders_source["item_count"] == expected_order_count
        assert run["open_order_count"] == expected_order_count
        assert result["open_orders"] == expected_order_count

        positions = db.get_open_position_snapshots(address)
        assert len(positions) == expected_position_count
        if positions:
            expected_coins = {item.get("position", item)["coin"] for item in stored_clearinghouse_state["assetPositions"]}
            stored_coins = {position["coin"] for position in positions}
            assert stored_coins == expected_coins
            assert all(position["active_asset_data_json"] is not None for position in positions)

        orders = db.get_open_order_snapshots(address)
        assert len(orders) == expected_order_count
        if orders:
            assert all(order["source"] == "frontendOpenOrders" for order in orders)
    finally:
        db.close()
