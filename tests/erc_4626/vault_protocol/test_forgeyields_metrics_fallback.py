"""Test that ForgeYields TVL from the historical reader feeds into period metrics.

The reader writes denomination-token TVL into total_assets for near-head rows.
These tests verify that calculate_period_metrics correctly reads tvl_end from
the total_assets series — no metadata fallback involved.
"""

import pandas as pd
import pytest

from eth_defi.research.vault_metrics import calculate_period_metrics
from eth_defi.vault.fee import FeeData, VaultFeeMode


def _make_fee_data() -> FeeData:
    return FeeData(
        fee_mode=VaultFeeMode.internalised_skimming,
        management=0.0,
        performance=0.20,
        deposit=0.0,
        withdraw=0.0,
    )


def test_period_metrics_reads_tvl_from_total_assets():
    """Period metrics reads tvl_end from the total_assets series.

    Simulates ForgeYields after the reader has been writing TVL for near-head
    rows: most rows have NaN (old catch-up rows), last row has the API TVL.

    1. Build 30 days of hourly data, last row has total_assets from API
    2. Run calculate_period_metrics
    3. Assert tvl_end equals the API value
    """
    dates = pd.date_range("2026-05-01", periods=30 * 24, freq="h")
    share_price = pd.Series([1.0 + i * 0.0001 for i in range(len(dates))], index=dates)
    tvl = pd.Series([float("nan")] * len(dates), index=dates)
    tvl.iloc[-1] = 1_069_435.71

    fee_data = _make_fee_data()
    pm = calculate_period_metrics(
        period="1M",
        gross_fee_data=fee_data,
        net_fee_data=fee_data.get_net_fees(),
        share_price_hourly=share_price,
        share_price_daily=share_price.resample("D").last(),
        tvl=tvl,
        now_=dates[-1],
    )

    assert pm is not None
    assert pm.error_reason is None
    assert pm.tvl_end == pytest.approx(1_069_435.71)


def test_period_metrics_with_full_tvl_history():
    """Period metrics with fully backfilled total_assets.

    Simulates ForgeYields after the backfill script has filled all rows.

    1. Build data with total_assets populated throughout
    2. Run calculate_period_metrics
    3. Assert tvl_end matches the last value
    """
    dates = pd.date_range("2026-05-01", periods=30 * 24, freq="h")
    share_price = pd.Series([1.0 + i * 0.0001 for i in range(len(dates))], index=dates)
    tvl = pd.Series([500_000.0] * len(dates), index=dates)

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
        tvl=tvl,
        now_=dates[-1],
    )

    assert pm is not None
    assert pm.tvl_end == pytest.approx(500_000.0)
