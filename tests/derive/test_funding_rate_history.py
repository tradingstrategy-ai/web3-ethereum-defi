"""Test Derive funding rate history API and DuckDB storage.

Tests the public (unauthenticated) funding rate history endpoint
and DuckDB persistence with resume support.

No credentials required — uses public API only.
"""

import datetime
from decimal import Decimal

import pytest

from eth_defi.derive.api import FundingRateEntry, fetch_funding_rate_history, fetch_perpetual_instruments
from eth_defi.derive.historical import DeriveFundingRateDatabase
from eth_defi.derive.session import create_derive_session


@pytest.fixture(scope="module")
def session():
    """Create a shared HTTP session for all tests."""
    return create_derive_session()


@pytest.mark.timeout(60)
def test_fetch_perpetual_instruments(session):
    """Discover all active perpetual instruments from the live API."""
    instruments = fetch_perpetual_instruments(session)

    assert len(instruments) > 0, "Expected at least one perpetual instrument"
    assert "ETH-PERP" in instruments, "ETH-PERP should be an active instrument"
    assert "BTC-PERP" in instruments, "BTC-PERP should be an active instrument"

    # Verify sorted
    assert instruments == sorted(instruments)


@pytest.mark.timeout(60)
def test_fetch_funding_rate_history(session):
    """Fetch a small window of funding rate history from the live API."""
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    start = now - datetime.timedelta(days=1)

    rates = fetch_funding_rate_history(
        session,
        "ETH-PERP",
        start_time=start,
        end_time=now,
    )

    assert len(rates) > 0, "Expected at least one funding rate entry for last 24h"

    for r in rates:
        assert isinstance(r, FundingRateEntry)
        assert r.instrument == "ETH-PERP"
        assert r.timestamp_ms > 0
        assert isinstance(r.funding_rate, Decimal)

    # Verify chronological order
    for i in range(1, len(rates)):
        assert rates[i].timestamp_ms >= rates[i - 1].timestamp_ms


@pytest.mark.timeout(60)
def test_funding_rate_db_sync_and_resume(session, tmp_path):
    """Sync funding rates to DuckDB and verify resume produces zero new inserts."""
    db = DeriveFundingRateDatabase(tmp_path / "funding-rates.duckdb")
    try:
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        start = now - datetime.timedelta(days=1)

        # First sync
        inserted = db.sync_instrument(session, "ETH-PERP", start_time=start, end_time=now)
        assert inserted > 0, "Expected entries on first sync"

        # Verify data is stored
        count = db.get_row_count("ETH-PERP")
        assert count == inserted

        # Second sync (resume) — should insert zero or very few (race with new data)
        inserted_again = db.sync_instrument(session, "ETH-PERP", start_time=start, end_time=now)
        assert inserted_again == 0, f"Expected 0 new entries on re-sync with same window, got {inserted_again}"

        # Row count unchanged
        assert db.get_row_count("ETH-PERP") == count

        # Sync state recorded
        state = db.get_sync_state("ETH-PERP")
        assert state is not None
        assert state["row_count"] == count
        assert state["newest_ts"] > 0
    finally:
        db.close()


@pytest.mark.timeout(60)
def test_funding_rate_db_dataframe(session, tmp_path):
    """Verify DataFrame output has correct columns and data."""
    db = DeriveFundingRateDatabase(tmp_path / "funding-rates.duckdb")
    try:
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        start = now - datetime.timedelta(days=1)

        db.sync_instrument(session, "ETH-PERP", start_time=start, end_time=now)
        df = db.get_funding_rates_dataframe("ETH-PERP")

        assert len(df) > 0
        assert "timestamp" in df.columns
        assert "funding_rate" in df.columns
        assert "instrument" in df.columns
    finally:
        db.close()


@pytest.mark.timeout(60)
def test_fetch_early_history(session):
    """Verify that the API returns funding rates from well before the 30-day window.

    The Derive API uses ``start_timestamp`` / ``end_timestamp`` params
    (not ``start_time`` / ``end_time``) to query arbitrary historical
    windows.  This test fetches one month of data from ~6 months ago
    to confirm we can reach beyond the default 30-day window.

    1. Pick a 30-day window starting 6 months ago (clean day boundaries).
    2. Fetch funding rate history for ETH-PERP in that window.
    3. Assert we get hourly data and timestamps fall within the window.
    """
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    # 1. Pick a window 6 months ago, truncated to day boundaries
    start = (now - datetime.timedelta(days=180 + 30)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = (now - datetime.timedelta(days=180)).replace(hour=0, minute=0, second=0, microsecond=0)

    # 2. Fetch
    rates = fetch_funding_rate_history(session, "ETH-PERP", start_time=start, end_time=end)

    # 3. Assert we got data and it falls within the window
    assert len(rates) >= 24 * 28, f"Expected at least 28 days of hourly data, got {len(rates)} entries"

    for r in rates:
        assert r.timestamp >= start, f"Entry {r.timestamp} is before start {start}"
        assert r.timestamp <= end, f"Entry {r.timestamp} is after end {end}"
        assert isinstance(r.funding_rate, Decimal)
