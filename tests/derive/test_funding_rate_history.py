"""Test Derive funding rate history API and DuckDB storage.

Tests the public (unauthenticated) funding rate history endpoint
and DuckDB persistence with resume support.

No credentials required — uses public API only.
Fetch and persistence checks use a verified historical day so delayed
publication of recent funding rates does not invalidate historical coverage.
"""

import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from requests import Session

from eth_defi.derive.api import FundingRateEntry, fetch_funding_rate_history, fetch_perpetual_instruments
from eth_defi.derive.historical import DeriveFundingRateDatabase
from eth_defi.derive.session import create_derive_session

#: Verified 24 hourly samples on 2026-10-08. The rolling last-day checks failed
#: on master, this PR and locally because the latest API sample was
#: 2026-10-06 17:00 UTC. Keep real API/storage coverage on a fixed published day.
FUNDING_HISTORY_START = datetime.datetime(2026, 4, 1)  # noqa: DTZ001 - Naive UTC API bounds.
FUNDING_HISTORY_END = FUNDING_HISTORY_START + datetime.timedelta(days=1)
#: Hourly observations in the verified published day.
FUNDING_HISTORY_SAMPLES = 24


def test_fetch_funding_rate_history_clips_api_boundary_samples() -> None:
    """Discard leading and trailing samples returned outside the requested window."""
    start = datetime.datetime(2026, 2, 26, 0, 0)  # noqa: DTZ001 - Naive UTC API bounds.
    end = datetime.datetime(2026, 2, 26, 2, 0)  # noqa: DTZ001 - Naive UTC API bounds.
    hourly_timestamps = [start - datetime.timedelta(hours=1), start, start + datetime.timedelta(hours=1), end, end + datetime.timedelta(hours=1)]
    response = MagicMock()
    response.json.return_value = {
        "result": {
            "funding_rate_history": [
                {
                    "timestamp": int(timestamp.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000),
                    "funding_rate": "0.00001",
                }
                for timestamp in hourly_timestamps
            ],
        },
    }
    session = MagicMock()
    session.post.return_value = response

    rates = fetch_funding_rate_history(session, "ETH-PERP", start_time=start, end_time=end)

    assert [rate.timestamp for rate in rates] == hourly_timestamps[1:4]


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
def test_fetch_funding_rate_history(session: Session) -> None:
    """Fetch a published day of funding rates from the real API.

    Verify all 24 hourly samples in a fixed UTC window, their types and their
    ordering using the public historical endpoint.

    :param session: Shared public Derive HTTP session.
    :return: ``None`` after checking the historical samples.
    """

    rates = fetch_funding_rate_history(
        session,
        "ETH-PERP",
        start_time=FUNDING_HISTORY_START,
        end_time=FUNDING_HISTORY_END,
    )

    assert len(rates) == FUNDING_HISTORY_SAMPLES
    assert [rate.timestamp for rate in rates] == [FUNDING_HISTORY_START + datetime.timedelta(hours=hour) for hour in range(FUNDING_HISTORY_SAMPLES)]

    for r in rates:
        assert isinstance(r, FundingRateEntry)
        assert r.instrument == "ETH-PERP"
        assert r.timestamp_ms > 0
        assert isinstance(r.funding_rate, Decimal)

    # Verify chronological order
    for i in range(1, len(rates)):
        assert rates[i].timestamp_ms >= rates[i - 1].timestamp_ms


@pytest.mark.timeout(60)
def test_funding_rate_db_sync_and_resume(session: Session, tmp_path: Path) -> None:
    """Sync a published day and verify that resume inserts no duplicates.

    Exercise real API reads, file-backed DuckDB persistence and saved sync
    state with a fixed 24-sample history window.

    :param session: Shared public Derive HTTP session.
    :param tmp_path: Private directory for the funding-rate database.
    :return: ``None`` after checking persistence and repeat synchronisation.
    """
    db = DeriveFundingRateDatabase(tmp_path / "funding-rates.duckdb")
    try:
        # First sync
        inserted = db.sync_instrument(session, "ETH-PERP", start_time=FUNDING_HISTORY_START, end_time=FUNDING_HISTORY_END)
        assert inserted == FUNDING_HISTORY_SAMPLES

        # Verify data is stored
        count = db.get_row_count("ETH-PERP")
        assert count == inserted

        # Second sync resumes the same fixed window without adding rows.
        inserted_again = db.sync_instrument(session, "ETH-PERP", start_time=FUNDING_HISTORY_START, end_time=FUNDING_HISTORY_END)
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
def test_funding_rate_db_dataframe(session: Session, tmp_path: Path) -> None:
    """Read a persisted published day as a DataFrame.

    Check that all 24 real hourly samples are exposed with the expected
    timestamp, funding-rate and instrument columns.

    :param session: Shared public Derive HTTP session.
    :param tmp_path: Private directory for the funding-rate database.
    :return: ``None`` after checking the DataFrame schema and row count.
    """
    db = DeriveFundingRateDatabase(tmp_path / "funding-rates.duckdb")
    try:
        db.sync_instrument(session, "ETH-PERP", start_time=FUNDING_HISTORY_START, end_time=FUNDING_HISTORY_END)
        df = db.get_funding_rates_dataframe("ETH-PERP")

        assert len(df) == FUNDING_HISTORY_SAMPLES
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
