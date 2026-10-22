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
from eth_defi.derive.constants import DERIVE_V3_MAINNET_API_URL
from eth_defi.derive.historical import DeriveFundingRateDatabase
from eth_defi.derive.session import create_derive_session

#: Verified 24 v3 hourly samples on 2026-10-22. V3 does not currently serve
#: the April v2 history used before the October mainnet migration.
FUNDING_HISTORY_START = datetime.datetime(2026, 10, 20)  # noqa: DTZ001 - Naive UTC API bounds.
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
def test_fetch_funding_rate_history_subwindow(session: Session) -> None:
    """Clip real v3 hourly candles to a requested partial-day window.

    Use a published v3 day instead of assuming that the replacement endpoint
    serves six-month-old v2 observations. Bucket starts outside the bounds
    must not enter the caller's dataset.

    :param session: Shared public Derive HTTP session.
    :return: ``None`` after checking the six in-window hourly samples.
    """
    start = FUNDING_HISTORY_START + datetime.timedelta(hours=6, minutes=30)
    end = FUNDING_HISTORY_START + datetime.timedelta(hours=12, minutes=30)
    rates = fetch_funding_rate_history(session, "ETH-PERP", start_time=start, end_time=end)

    assert [rate.timestamp for rate in rates] == [FUNDING_HISTORY_START + datetime.timedelta(hours=hour) for hour in range(7, 13)]
    assert all(isinstance(rate.funding_rate, Decimal) for rate in rates)


def test_funding_rate_db_preserves_v2_history(tmp_path: Path) -> None:
    """Preserve stored v2 rows and watermarks when synchronising v3 data.

    A file-backed database survives an empty API response, then appends v3
    observations without replacing legacy rows. Repeating the v3 window
    inserts no duplicates, and reopening retains both observations.

    :param tmp_path: Private directory for the database.
    :return: ``None`` after checking persistence and incremental state.
    """
    legacy_time = datetime.datetime(2026, 4, 1)  # noqa: DTZ001 - Naive UTC API bounds.
    legacy_ms = int(legacy_time.replace(tzinfo=datetime.UTC).timestamp() * 1000)
    v3_ms = int(FUNDING_HISTORY_START.replace(tzinfo=datetime.UTC).timestamp() * 1000)
    legacy_response = MagicMock()
    legacy_response.json.return_value = {"result": {"funding_rate_history": [{"timestamp": legacy_ms, "funding_rate": "0.00001"}]}}
    empty_response = MagicMock()
    empty_response.json.return_value = {"result": {"funding_rate_history": []}}
    v3_response = MagicMock()
    v3_response.json.return_value = {"result": {"funding_rate_history": [{"timestamp": v3_ms, "funding_rate": "0.0000125", "close": "0.0000125"}]}}
    session = MagicMock()
    session.post.side_effect = [legacy_response, empty_response, v3_response, v3_response]
    path = tmp_path / "funding-rates.duckdb"

    db = DeriveFundingRateDatabase(path)
    try:
        assert db.sync_instrument(session, "ETH-PERP", start_time=legacy_time, end_time=legacy_time + datetime.timedelta(days=1)) == 1
        legacy_state = db.get_sync_state("ETH-PERP")
    finally:
        db.close()

    db = DeriveFundingRateDatabase(path)
    try:
        assert db.sync_instrument(session, "ETH-PERP", end_time=legacy_time + datetime.timedelta(days=1)) == 0
        assert db.get_sync_state("ETH-PERP") == legacy_state
        assert db.get_row_count("ETH-PERP") == 1
        assert db.sync_instrument(session, "ETH-PERP", start_time=FUNDING_HISTORY_START, end_time=FUNDING_HISTORY_END) == 1
        assert db.sync_instrument(session, "ETH-PERP", end_time=FUNDING_HISTORY_END) == 0
        state = db.get_sync_state("ETH-PERP")
        assert state["oldest_ts"] == legacy_ms
        assert state["newest_ts"] == v3_ms
        assert state["row_count"] == 2
    finally:
        db.close()

    db = DeriveFundingRateDatabase(path)
    try:
        rates = db.get_funding_rates("ETH-PERP")
        assert [(rate.timestamp_ms, rate.funding_rate) for rate in rates] == [(legacy_ms, Decimal("0.00001")), (v3_ms, Decimal("0.0000125"))]
        assert db.get_sync_state("ETH-PERP") == state
    finally:
        db.close()

    assert all(call.args[0] == f"{DERIVE_V3_MAINNET_API_URL}/public/get_funding_rate_history" for call in session.post.call_args_list)
