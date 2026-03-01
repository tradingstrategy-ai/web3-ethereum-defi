"""Integration test: Lighter daily metrics scanner resumes correctly.

Scans a pool with a date cutoff, then re-scans without the cutoff,
and verifies that:

- Data from the first run is preserved
- New data beyond the cutoff is added
- Overlapping dates have identical share prices

Requires network access to the Lighter API.
"""

import datetime

import pytest

from eth_defi.lighter.daily_metrics import (
    LighterDailyMetricsDatabase,
    fetch_and_store_pool,
)
from eth_defi.lighter.session import create_lighter_session
from eth_defi.lighter.vault import fetch_all_pools


@pytest.mark.timeout(120)
def test_daily_metrics_resume(tmp_path):
    """Scan LLP pool with a date cutoff, then re-scan without cutoff — verify resume."""

    duckdb_path = tmp_path / "daily-metrics.duckdb"

    session = create_lighter_session()

    # Find the LLP pool
    all_pools = fetch_all_pools(session)
    llp = None
    for p in all_pools:
        if p.is_llp:
            llp = p
            break

    assert llp is not None, "LLP pool not found in listing"

    # Use a cutoff 30 days ago — the LLP has ~379 days of data
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)).date()

    db = LighterDailyMetricsDatabase(duckdb_path)
    try:
        # First scan: store data up to cutoff only
        result = fetch_and_store_pool(
            session,
            db,
            llp,
            cutoff_date=cutoff,
        )
        assert result, "First scan: failed to fetch and store pool"
        db.save()

        first_run_count = db.get_pool_daily_price_count(llp.account_index)
        first_run_last_date = db.get_pool_last_date(llp.account_index)

        assert first_run_count > 0, "First scan produced no data"
        assert first_run_last_date is not None
        assert first_run_last_date <= cutoff, f"First scan stored data beyond cutoff: last_date={first_run_last_date}, cutoff={cutoff}"

        # Read first run share prices for comparison
        first_run_df = db.get_pool_daily_prices(llp.account_index)
        first_run_prices = dict(zip(first_run_df["date"], first_run_df["share_price"]))

        # Second scan: no cutoff — should add data beyond the original cutoff
        result = fetch_and_store_pool(
            session,
            db,
            llp,
            cutoff_date=None,
        )
        assert result, "Second scan: failed to fetch and store pool"
        db.save()

        second_run_count = db.get_pool_daily_price_count(llp.account_index)
        second_run_last_date = db.get_pool_last_date(llp.account_index)

        assert second_run_count > first_run_count, f"Second scan did not add data: first={first_run_count}, second={second_run_count}"
        assert second_run_last_date > cutoff, f"Second scan did not extend beyond cutoff: last_date={second_run_last_date}"

        # Verify data integrity: overlapping dates should have identical share prices
        second_run_df = db.get_pool_daily_prices(llp.account_index)
        second_run_prices = dict(zip(second_run_df["date"], second_run_df["share_price"]))

        for date_val, first_price in first_run_prices.items():
            second_price = second_run_prices.get(date_val)
            assert second_price is not None, f"Date {date_val} missing in second run"
            assert first_price == pytest.approx(second_price, rel=1e-10), f"Share price mismatch at {date_val}: first={first_price}, second={second_price}"

        # Verify dates are in ascending order (no duplicates or reversals)
        dates = sorted(second_run_df["date"].tolist())
        for i in range(1, len(dates)):
            assert dates[i] > dates[i - 1], f"Date not ascending: {dates[i - 1]} >= {dates[i]}"

    finally:
        db.close()
