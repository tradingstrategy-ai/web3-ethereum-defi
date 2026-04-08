"""Integration test: high-frequency export with 1h resampling.

Tests that build_raw_prices_dataframe_hf() produces correct output:

1. Pre-populate HF database with 4h-spaced synthetic data
2. Export via build_raw_prices_dataframe_hf()
3. Verify 1h resampled timestamps
4. Verify flow columns mapped to daily_* names
5. Verify pct_change() produces sensible returns
"""

import datetime

import numpy as np
import pandas as pd
import pytest

from eth_defi.hyperliquid.high_freq_metrics import (
    HyperliquidHighFreqMetricsDatabase,
    HyperliquidHighFreqPriceRow,
)
from eth_defi.hyperliquid.vault_data_export import build_raw_prices_dataframe_hf
from eth_defi.compat import native_datetime_utc_now


@pytest.mark.timeout(30)
def test_hf_export_1h_resampling(tmp_path):
    """Build raw prices DataFrame from HF DuckDB and verify 1h resampling.

    1. Insert 3 rows at 4h intervals for one vault
    2. Export via build_raw_prices_dataframe_hf()
    3. Verify output has 1h-spaced timestamps (forward-filled)
    4. Verify flow columns use daily_* naming for compatibility
    5. Verify pct_change() produces sensible returns_1h values
    """
    duckdb_path = tmp_path / "hf-export-test.duckdb"
    db = HyperliquidHighFreqMetricsDatabase(duckdb_path)

    try:
        # 1. Insert metadata
        vault_addr = "0xaaaa0000000000000000000000000000aaaaaaaa"
        db.upsert_vault_metadata(
            vault_address=vault_addr,
            name="Export Test Vault",
            leader="0xleader",
            description=None,
            is_closed=False,
            relationship_type="normal",
            create_time=None,
            commission_rate=None,
            follower_count=5,
            tvl=100000.0,
            apr=0.10,
        )

        # 2. Insert 3 rows at 4h intervals
        base_ts = datetime.datetime(2025, 6, 1, 0, 0, 0)
        now = native_datetime_utc_now()

        rows = [
            HyperliquidHighFreqPriceRow(
                vault_address=vault_addr,
                timestamp=base_ts,
                share_price=1.000,
                tvl=100000.0,
                cumulative_pnl=0.0,
                is_closed=False,
                allow_deposits=True,
                deposit_count=2,
                withdrawal_count=1,
                deposit_usd=5000.0,
                withdrawal_usd=1000.0,
                epoch_reset=False,
                written_at=now,
            ),
            HyperliquidHighFreqPriceRow(
                vault_address=vault_addr,
                timestamp=base_ts + datetime.timedelta(hours=4),
                share_price=1.010,
                tvl=101000.0,
                cumulative_pnl=1000.0,
                is_closed=False,
                allow_deposits=True,
                deposit_count=0,
                withdrawal_count=0,
                deposit_usd=0.0,
                withdrawal_usd=0.0,
                epoch_reset=False,
                written_at=now,
            ),
            HyperliquidHighFreqPriceRow(
                vault_address=vault_addr,
                timestamp=base_ts + datetime.timedelta(hours=8),
                share_price=1.020,
                tvl=102000.0,
                cumulative_pnl=2000.0,
                is_closed=False,
                allow_deposits=True,
                follower_count=5,
                deposit_count=1,
                withdrawal_count=0,
                deposit_usd=2000.0,
                withdrawal_usd=0.0,
                epoch_reset=False,
                written_at=now,
            ),
        ]
        db.upsert_high_freq_prices(rows)
        db.save()

        # 3. Export with 1h resampling
        result_df = build_raw_prices_dataframe_hf(db)

        assert len(result_df) > 0, "Export produced empty DataFrame"

        # 4. Verify 1h timestamps
        timestamps = pd.to_datetime(result_df["timestamp"]).sort_values()
        # Should have 9 rows: hours 0,1,2,3,4,5,6,7,8
        assert len(timestamps) == 9, f"Expected 9 hourly rows (0h-8h), got {len(timestamps)}"

        # Check consecutive timestamps are 1h apart
        diffs = timestamps.diff().dropna()
        expected_diff = pd.Timedelta(hours=1)
        for diff in diffs:
            assert diff == expected_diff, f"Expected 1h diff, got {diff}"

        # 5. Verify forward-filled prices
        prices = result_df.sort_values("timestamp")["share_price"].values
        # Hours 0-3 should be 1.000 (forward-filled from hour 0)
        assert prices[0] == pytest.approx(1.000)
        assert prices[1] == pytest.approx(1.000)  # Hour 1 (fill)
        assert prices[3] == pytest.approx(1.000)  # Hour 3 (fill)
        # Hour 4 should be 1.010
        assert prices[4] == pytest.approx(1.010)
        # Hour 8 should be 1.020
        assert prices[8] == pytest.approx(1.020)

        # 6. Verify flow columns use daily_* naming
        assert "daily_deposit_count" in result_df.columns, "Flow columns should use daily_* naming"
        assert "daily_withdrawal_count" in result_df.columns
        assert "daily_deposit_usd" in result_df.columns
        assert "daily_withdrawal_usd" in result_df.columns

        # 7. Verify flow values are NOT forward-filled (only on observation hours)
        sorted_df = result_df.sort_values("timestamp").reset_index(drop=True)
        # Hour 0 (observation) should have deposit_count=2
        assert sorted_df.loc[0, "daily_deposit_count"] == pytest.approx(2.0)
        # Hour 1 (fill) should be NaN
        assert pd.isna(sorted_df.loc[1, "daily_deposit_count"])

        # 8. Verify pct_change gives sensible returns
        returns = sorted_df["share_price"].pct_change()
        # Fill-hours should have 0 return (same price)
        assert returns.iloc[1] == pytest.approx(0.0)
        # Hour 4 should show the actual return
        assert returns.iloc[4] == pytest.approx(0.01, rel=1e-5)

    finally:
        db.close()
