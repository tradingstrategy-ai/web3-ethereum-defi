"""Integration test: Hyperliquid daily metrics scanner resumes correctly.

Scans a vault with a date cutoff, then re-scans without the cutoff,
and verifies that:
- Data from the first run is preserved
- New data beyond the cutoff is added
- Overlapping dates have identical share prices

Requires network access to the Hyperliquid API.
"""

import datetime

import pytest

from eth_defi.hyperliquid.daily_metrics import (
    HyperliquidDailyMetricsDatabase,
    fetch_and_store_vault,
)
from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.vault import fetch_all_vaults


@pytest.mark.timeout(120)
def test_daily_metrics_resume(tmp_path):
    """Scan a vault with a date cutoff, then re-scan without cutoff — verify resume."""

    duckdb_path = tmp_path / "daily-metrics.duckdb"
    vault_address = "0x3df9769bbbb335340872f01d8157c779d73c6ed0"

    session = create_hyperliquid_session()

    # Find the vault in the bulk listing
    all_vaults = list(fetch_all_vaults(session))
    target_summary = None
    for s in all_vaults:
        if s.vault_address.lower() == vault_address.lower():
            target_summary = s
            break

    assert target_summary is not None, f"Vault {vault_address} not found in bulk listing"

    # First scan: store data up to cutoff only
    cutoff = datetime.date(2025, 12, 15)

    db = HyperliquidDailyMetricsDatabase(duckdb_path)
    try:
        result = fetch_and_store_vault(
            session,
            db,
            target_summary,
            cutoff_date=cutoff,
        )
        assert result, "First scan: failed to fetch and store vault"
        db.save()

        first_run_count = db.get_vault_daily_price_count(vault_address)
        first_run_last_date = db.get_vault_last_date(vault_address)

        assert first_run_count > 0, "First scan produced no data"
        assert first_run_last_date is not None
        assert first_run_last_date <= cutoff, f"First scan stored data beyond cutoff: last_date={first_run_last_date}, cutoff={cutoff}"

        # Read first run share prices for comparison
        first_run_df = db.get_vault_daily_prices(vault_address)
        first_run_prices = dict(zip(first_run_df["date"], first_run_df["share_price"]))

        # Second scan: no cutoff — should add data beyond the original cutoff
        result = fetch_and_store_vault(
            session,
            db,
            target_summary,
            cutoff_date=None,
        )
        assert result, "Second scan: failed to fetch and store vault"
        db.save()

        second_run_count = db.get_vault_daily_price_count(vault_address)
        second_run_last_date = db.get_vault_last_date(vault_address)

        assert second_run_count > first_run_count, f"Second scan did not add data: first={first_run_count}, second={second_run_count}"
        assert second_run_last_date > cutoff, f"Second scan did not extend beyond cutoff: last_date={second_run_last_date}"

        # Verify data integrity: overlapping dates should have identical share prices
        second_run_df = db.get_vault_daily_prices(vault_address)
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
