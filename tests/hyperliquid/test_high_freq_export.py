"""Integration test: high-frequency export with raw timestamps.

Tests that build_raw_prices_dataframe_hf() produces correct output:

1. Pre-populate HF database with irregularly-spaced synthetic data
2. Export via build_raw_prices_dataframe_hf()
3. Verify raw timestamps are preserved (no resampling)
4. Verify flow columns mapped to daily_* names
5. Verify pct_change() produces returns between consecutive rows
"""

import datetime

import numpy as np
import pandas as pd
import pytest

from eth_defi.hyperliquid.high_freq_metrics import (
    HyperliquidHighFreqMetricsDatabase,
    HyperliquidHighFreqPriceRow,
)
from eth_defi.hyperliquid.vault_data_export import build_raw_prices_dataframe_hf, create_hyperliquid_vault_row
from eth_defi.research.vault_metrics import calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics, export_lifetime_row
from eth_defi.research.wrangle_vault_prices import process_raw_vault_scan_data
from eth_defi.compat import native_datetime_utc_now


@pytest.mark.timeout(30)
def test_hf_export_raw_timestamps(tmp_path):
    """Build raw prices DataFrame from HF DuckDB and verify raw timestamps.

    1. Insert 3 rows at irregular intervals for one vault
    2. Export via build_raw_prices_dataframe_hf()
    3. Verify output preserves raw timestamps (no resampling to 1h)
    4. Verify flow columns use daily_* naming for compatibility
    5. Verify pct_change() produces returns between consecutive rows
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

        # 2. Insert 3 rows at irregular intervals (matching real API behaviour)
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
                timestamp=base_ts + datetime.timedelta(hours=4, minutes=17),
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
                timestamp=base_ts + datetime.timedelta(hours=8, minutes=42),
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

        # 3. Export with raw timestamps (no resampling)
        result_df = build_raw_prices_dataframe_hf(db)

        assert len(result_df) > 0, "Export produced empty DataFrame"

        # 4. Verify raw timestamps preserved — exactly 3 rows, no interpolation
        assert len(result_df) == 3, f"Expected 3 raw rows, got {len(result_df)}"

        sorted_df = result_df.sort_values("timestamp").reset_index(drop=True)
        timestamps = pd.to_datetime(sorted_df["timestamp"])
        assert timestamps.iloc[0] == pd.Timestamp(base_ts)
        assert timestamps.iloc[1] == pd.Timestamp(base_ts + datetime.timedelta(hours=4, minutes=17))
        assert timestamps.iloc[2] == pd.Timestamp(base_ts + datetime.timedelta(hours=8, minutes=42))

        # 5. Verify share prices
        assert sorted_df.loc[0, "share_price"] == pytest.approx(1.000)
        assert sorted_df.loc[1, "share_price"] == pytest.approx(1.010)
        assert sorted_df.loc[2, "share_price"] == pytest.approx(1.020)

        # 6. Verify flow columns use daily_* naming
        assert "daily_deposit_count" in result_df.columns
        assert "daily_withdrawal_count" in result_df.columns
        assert "daily_deposit_usd" in result_df.columns
        assert "daily_withdrawal_usd" in result_df.columns

        # 7. Verify flow values are present on all rows (no synthetic fill rows)
        assert sorted_df.loc[0, "daily_deposit_count"] == pytest.approx(2.0)
        assert sorted_df.loc[1, "daily_deposit_count"] == pytest.approx(0.0)
        assert sorted_df.loc[2, "daily_deposit_count"] == pytest.approx(1.0)

        # 8. Verify pct_change gives returns between consecutive rows
        returns = sorted_df["share_price"].pct_change()
        # Row 1 return: (1.010 - 1.000) / 1.000 = 0.01
        assert returns.iloc[1] == pytest.approx(0.01, rel=1e-5)
        # Row 2 return: (1.020 - 1.010) / 1.010
        assert returns.iloc[2] == pytest.approx(0.0099, rel=1e-2)

    finally:
        db.close()


@pytest.mark.timeout(30)
def test_hf_export_forward_fills_sparse_metadata_snapshots(tmp_path):
    """HF export should forward-fill sparse metadata snapshots within observed history."""
    duckdb_path = tmp_path / "hf-export-forward-fill.duckdb"
    db = HyperliquidHighFreqMetricsDatabase(duckdb_path)

    try:
        vault_addr = "0xbbbb0000000000000000000000000000bbbbbbbb"
        base_ts = datetime.datetime(2025, 6, 1, 0, 0, 0)
        now = native_datetime_utc_now()

        rows = [
            HyperliquidHighFreqPriceRow(
                vault_address=vault_addr,
                timestamp=base_ts,
                share_price=1.000,
                tvl=100000.0,
                cumulative_pnl=0.0,
                written_at=now,
            ),
            HyperliquidHighFreqPriceRow(
                vault_address=vault_addr,
                timestamp=base_ts + datetime.timedelta(hours=4),
                share_price=1.010,
                tvl=101000.0,
                cumulative_pnl=1000.0,
                follower_count=7,
                is_closed=False,
                allow_deposits=True,
                leader_fraction=0.12,
                leader_commission=3.0,
                cumulative_volume=1000.0,
                written_at=now,
            ),
            HyperliquidHighFreqPriceRow(
                vault_address=vault_addr,
                timestamp=base_ts + datetime.timedelta(hours=8),
                share_price=1.020,
                tvl=102000.0,
                cumulative_pnl=2000.0,
                written_at=now,
            ),
            HyperliquidHighFreqPriceRow(
                vault_address=vault_addr,
                timestamp=base_ts + datetime.timedelta(hours=12),
                share_price=1.030,
                tvl=103000.0,
                cumulative_pnl=3000.0,
                written_at=now,
            ),
        ]
        db.upsert_high_freq_prices(rows)
        db.save()

        result_df = build_raw_prices_dataframe_hf(db).sort_values("timestamp").reset_index(drop=True)

        assert "apr" not in result_df.columns

        first_row = result_df.iloc[0]
        assert pd.isna(first_row["follower_count"])
        assert pd.isna(first_row["cumulative_volume"])
        assert pd.isna(first_row["leader_commission"])
        assert pd.isna(first_row["leader_fraction"])
        assert pd.isna(first_row["deposits_open"])

        for idx in range(1, 4):
            row = result_df.iloc[idx]
            assert row["follower_count"] == 7
            assert row["cumulative_volume"] == pytest.approx(1000.0)
            assert row["leader_commission"] == pytest.approx(3.0)
            assert row["leader_fraction"] == pytest.approx(0.12)
            assert row["deposits_open"] == "true"
            assert row["deposit_closed_reason"] is None

    finally:
        db.close()


@pytest.mark.timeout(30)
def test_hf_forward_filled_metadata_reaches_cleaned_and_lifetime_outputs(tmp_path):
    """HF metadata forward-fill should reach cleaned data and lifetime exports."""
    duckdb_path = tmp_path / "hf-cleaned-forward-fill.duckdb"
    db = HyperliquidHighFreqMetricsDatabase(duckdb_path)

    try:
        vault_addr = "0xcccc0000000000000000000000000000cccccccc"
        base_ts = datetime.datetime(2025, 6, 1, 0, 0, 0)
        now = native_datetime_utc_now()

        rows = [
            HyperliquidHighFreqPriceRow(
                vault_address=vault_addr,
                timestamp=base_ts,
                share_price=1.000,
                tvl=100000.0,
                cumulative_pnl=0.0,
                written_at=now,
            ),
            HyperliquidHighFreqPriceRow(
                vault_address=vault_addr,
                timestamp=base_ts + datetime.timedelta(hours=4),
                share_price=1.010,
                tvl=101000.0,
                cumulative_pnl=1000.0,
                follower_count=9,
                is_closed=False,
                allow_deposits=True,
                leader_fraction=0.15,
                leader_commission=5.0,
                cumulative_volume=1500.0,
                written_at=now,
            ),
            HyperliquidHighFreqPriceRow(
                vault_address=vault_addr,
                timestamp=base_ts + datetime.timedelta(hours=8),
                share_price=1.020,
                tvl=102000.0,
                cumulative_pnl=2000.0,
                written_at=now,
            ),
        ]
        db.upsert_high_freq_prices(rows)
        db.save()

        raw_df = build_raw_prices_dataframe_hf(db)
        spec, vault_row = create_hyperliquid_vault_row(
            vault_address=vault_addr,
            name="HF Forward Fill",
            description=None,
            tvl=102000.0,
            create_time=base_ts,
        )
        cleaned_df = process_raw_vault_scan_data(
            {spec: vault_row},
            raw_df,
            logger=lambda msg: None,
            display=lambda _: None,
        )
        vault_df = cleaned_df[cleaned_df["id"] == spec.as_string_id()].sort_index()
        latest_cleaned_row = vault_df.iloc[-1]

        assert latest_cleaned_row["follower_count"] == 9
        assert latest_cleaned_row["cumulative_volume"] == pytest.approx(1500.0)
        assert latest_cleaned_row["leader_commission"] == pytest.approx(5.0)
        assert latest_cleaned_row["leader_fraction"] == pytest.approx(0.15)

        returns_df = calculate_hourly_returns_for_all_vaults(cleaned_df)
        lifetime_df = calculate_lifetime_metrics(returns_df, {spec: vault_row})

        assert len(lifetime_df) == 1
        lifetime_row = lifetime_df.iloc[0]
        assert lifetime_row["follower_count"] == 9
        assert lifetime_row["cumulative_volume"] == pytest.approx(1500.0)
        assert lifetime_row["leader_commission"] == pytest.approx(5.0)
        assert lifetime_row["leader_fraction"] == pytest.approx(0.15)

        exported = export_lifetime_row(lifetime_row)
        assert exported["follower_count"] == 9
        assert exported["cumulative_volume"] == pytest.approx(1500.0)
        assert exported["leader_commission"] == pytest.approx(5.0)
        assert exported["leader_fraction"] == pytest.approx(0.15)

    finally:
        db.close()
