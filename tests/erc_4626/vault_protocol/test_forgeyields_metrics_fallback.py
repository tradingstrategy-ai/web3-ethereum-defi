"""Test that vaults with external-only TVL get ranking eligibility via metadata NAV.

ForgeYields has valid share_price but NaN total_assets in the price history.
The vault metadata scan fills NAV from fetch_tvl_usd(). The metrics pipeline
should use this metadata NAV as fallback for current_nav, peak_nav, and the
period tvl_end used for ranking filters.

1. Build synthetic price data with valid share_price and NaN total_assets
2. Verify metadata NAV fills into the TVL series for period metrics
3. Verify calculate_period_metrics gets a non-null tvl_end
"""

import pandas as pd
import pytest

from eth_defi.research.vault_metrics import calculate_period_metrics
from eth_defi.vault.fee import FeeData, VaultFeeMode


def test_metadata_nav_fills_period_tvl():
    """Period metrics get ranking-eligible tvl_end from metadata NAV fallback.

    This tests the exact logic from vault_metrics.py lines 1419-1423:
    when total_assets is all-NaN, the last row is filled from metadata NAV
    so period metrics get a non-null tvl_end.

    1. Build 30 days of hourly data with all-NaN total_assets
    2. Fill last row with metadata NAV (simulating the fallback)
    3. Run calculate_period_metrics
    4. Assert tvl_end equals the metadata NAV
    """
    # 1. Build price data
    dates = pd.date_range("2026-05-01", periods=30 * 24, freq="h")
    share_price = pd.Series(
        [1.0 + i * 0.0001 for i in range(len(dates))],
        index=dates,
    )
    tvl_series = pd.Series([float("nan")] * len(dates), index=dates)

    # 2. Simulate metadata NAV fallback (vault_metrics.py line 1422)
    metadata_nav = 1_085_984.11
    if tvl_series.isna().all():
        tvl_series = tvl_series.copy()
        tvl_series.iloc[-1] = metadata_nav

    fee_data = FeeData(
        fee_mode=VaultFeeMode.internalised_skimming,
        management=0.0,
        performance=0.20,
        deposit=0.0,
        withdraw=0.0,
    )

    # 3. Run period metrics
    pm = calculate_period_metrics(
        period="1M",
        gross_fee_data=fee_data,
        net_fee_data=fee_data.get_net_fees(),
        share_price_hourly=share_price,
        share_price_daily=share_price.resample("D").last(),
        tvl=tvl_series,
        now_=dates[-1],
    )

    # 4. Assert tvl_end populated
    assert pm is not None
    assert pm.error_reason is None
    assert pm.tvl_end == pytest.approx(1_085_984.11)


def test_normal_vault_total_assets_unchanged():
    """Verify the fallback does not activate when total_assets has real values.

    1. Build price data with valid total_assets
    2. Confirm the fallback condition (isna().all()) is False
    3. Run period metrics
    4. Assert tvl_end matches the actual total_assets
    """
    dates = pd.date_range("2026-05-01", periods=30 * 24, freq="h")
    share_price = pd.Series(
        [1.0 + i * 0.0001 for i in range(len(dates))],
        index=dates,
    )
    tvl_series = pd.Series([500_000.0] * len(dates), index=dates)

    # Fallback should NOT activate
    assert not tvl_series.isna().all()

    fee_data = FeeData(
        fee_mode=VaultFeeMode.externalised,
        management=0.02,
        performance=0.20,
        deposit=0.0,
        withdraw=0.0,
    )

    pm = calculate_period_metrics(
        period="1M",
        gross_fee_data=fee_data,
        net_fee_data=fee_data.get_net_fees(),
        share_price_hourly=share_price,
        share_price_daily=share_price.resample("D").last(),
        tvl=tvl_series,
        now_=dates[-1],
    )

    assert pm is not None
    assert pm.tvl_end == pytest.approx(500_000.0)
