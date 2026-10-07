"""Integration test: high-frequency export with raw timestamps.

Tests that build_raw_prices_dataframe_hf() produces correct output:

1. Pre-populate HF database with irregularly-spaced synthetic data
2. Export via build_raw_prices_dataframe_hf()
3. Verify raw timestamps are preserved (no resampling)
4. Verify flow columns mapped to daily_* names
5. Verify pct_change() produces returns between consecutive rows
"""

import datetime
import math
import uuid
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eth_defi.compat import native_datetime_utc_now
from eth_defi.hyperliquid.high_freq_metrics import (
    HyperliquidHighFreqMetricsDatabase,
    HyperliquidHighFreqPriceRow,
)
from eth_defi.hyperliquid.permission import PermissionObservation, append_permission_observation
from eth_defi.hyperliquid.vault_data_export import _merge_hypercore_frame_to_parquet, _prepare_hypercore_export, build_raw_prices_dataframe_hf, create_hyperliquid_vault_row, open_and_merge_hypercore_prices
from eth_defi.research.vault_metrics import calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics, export_lifetime_row
from eth_defi.research.wrangle_vault_prices import process_raw_vault_scan_data


def store_permission_fixtures(db: HyperliquidHighFreqMetricsDatabase, rows: list[HyperliquidHighFreqPriceRow], relationship_type: str = "normal") -> None:
    """Store independently clocked source responses for export fixtures.

    Fixture prices coincide with synthetic genuine receipts. Legacy inferred
    price clocks are covered separately by the recovery tests.

    :param db: Open test database.
    :param rows: Synthetic source responses with known flags.
    :param relationship_type: Policy input from the same fixture response.
    :return: ``None``.
    """
    for row in rows:
        if row.is_closed is not None or row.allow_deposits is not None:
            append_permission_observation(db.con, PermissionObservation(uuid.uuid4().hex, row.vault_address, permission_observed_at=row.timestamp, capacity_observed_at=row.timestamp if row.leader_fraction is not None else None, is_closed=row.is_closed, allow_deposits=row.allow_deposits, relationship_type=relationship_type, leader_fraction=row.leader_fraction, provenance="observed"))


@pytest.mark.parametrize("timestamp_column", ["date", "timestamp"])
def test_export_keeps_timestamps_aligned_when_sorting(timestamp_column: str) -> None:
    """Sorting sparse observations must not move permission onto a different day.

    1. Supply out-of-order rows with duplicate DataFrame indices.
    2. Run the helper shared by daily and high-frequency exports.
    3. Check prices, permission and policy limits remain on the observed dates.
    """
    # 1. The first chronological row contains the only low-share observation.
    prices = pd.DataFrame(
        {
            "vault_address": ["0x01", "0x01"],
            timestamp_column: pd.to_datetime(["2026-09-24", "2026-09-23"]),
            "share_price": [1.2, 1.0],
            "tvl": [12000.0, 10000.0],
            "is_closed": [None, False],
            "allow_deposits": [None, True],
            "leader_fraction": [None, 0.05],
        },
        index=[0, 0],
    )

    # 2. The helper owns sorting and forward-fill alignment.
    exported = _prepare_hypercore_export(prices, timestamp_column, {}, "hf")

    # 3. Unobserved price rows retain unknown permission and capacity.
    assert exported["timestamp"].tolist() == list(pd.to_datetime(["2026-09-23", "2026-09-24"]))
    assert exported["share_price"].tolist() == [1.0, 1.2]
    assert exported["deposits_open"].iloc[0] == "true"
    assert pd.isna(exported["deposits_open"].iloc[1])
    assert exported["max_deposit"].iloc[0] == 0.0
    assert pd.isna(exported["max_deposit"].iloc[1])


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
        store_permission_fixtures(db, rows)
        db.upsert_high_freq_prices(rows)
        db.save()

        # 3. Export with raw timestamps (no resampling)
        result_df = build_raw_prices_dataframe_hf(db)
        assert (result_df["hypercore_source"] == "hf").all()

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
        assert sorted_df["performance_fee"].tolist() == pytest.approx([0.1, 0.1, 0.1])

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
        store_permission_fixtures(db, rows)
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


def test_hf_low_share_policy_cap_survives_price_only_updates(tmp_path: Path) -> None:
    """Retain the recorded low-share policy through later price-only rows.

    1. Store a low-share vault-details observation and a later price-only row.
    2. Export both rows and distinguish source permission from policy capacity.
    """
    db = HyperliquidHighFreqMetricsDatabase(tmp_path / "hf-low-share.duckdb")
    try:
        address = "0xcccc0000000000000000000000000000cccccccc"
        timestamp = datetime.datetime(2026, 9, 23, 12)
        # 1. The second row has no new leader-fraction observation.
        db.upsert_high_freq_prices(
            [
                HyperliquidHighFreqPriceRow(
                    vault_address=address,
                    timestamp=timestamp,
                    share_price=1.0,
                    tvl=100000.0,
                    cumulative_pnl=0.0,
                    is_closed=False,
                    allow_deposits=True,
                    leader_fraction=0.05,
                    written_at=native_datetime_utc_now(),
                ),
                HyperliquidHighFreqPriceRow(
                    vault_address=address,
                    timestamp=timestamp + datetime.timedelta(hours=4),
                    share_price=1.01,
                    tvl=101000.0,
                    cumulative_pnl=1000.0,
                    written_at=native_datetime_utc_now(),
                ),
            ]
        )

        append_permission_observation(db.con, PermissionObservation(uuid.uuid4().hex, address, permission_observed_at=timestamp, capacity_observed_at=timestamp, is_closed=False, allow_deposits=True, relationship_type="normal", leader_fraction=0.05, provenance="observed"))

        # 2. Price-only updates retain the cap and its original response clock.
        rows = build_raw_prices_dataframe_hf(db).sort_values("timestamp")
        assert rows["deposits_open"].tolist() == ["true", "true"]
        assert rows["deposit_closed_reason"].isna().all()
        assert rows.iloc[0]["max_deposit"] == pytest.approx(0.0)
        assert rows.iloc[1]["max_deposit"] == pytest.approx(0.0)
        assert rows["capacity_observed_at"].tolist() == [timestamp, timestamp]

        # A new sufficient-share response clears the old low-share cap.
        later = timestamp + datetime.timedelta(hours=8)
        db.upsert_high_freq_prices([HyperliquidHighFreqPriceRow(address, later, 1.02, 102000.0, 2000.0)])
        append_permission_observation(db.con, PermissionObservation(uuid.uuid4().hex, address, permission_observed_at=later, is_closed=False, allow_deposits=True, relationship_type="normal", leader_fraction=0.2, provenance="observed"))
        latest = build_raw_prices_dataframe_hf(db).sort_values("timestamp").iloc[-1]
        assert latest.leader_fraction == pytest.approx(0.2)
        assert pd.isna(latest.max_deposit)
    finally:
        db.close()


@pytest.mark.timeout(30)
def test_hf_export_hlp_parent_ignores_leader_fraction(tmp_path):
    """HLP parent HF rows stay deposit-open even with tiny leader_fraction."""
    duckdb_path = tmp_path / "hf-hlp-parent.duckdb"
    db = HyperliquidHighFreqMetricsDatabase(duckdb_path)

    try:
        vault_addr = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"
        db.upsert_vault_metadata(
            vault_address=vault_addr,
            name="Hyperliquidity Provider (HLP)",
            leader="0x677d831aef5328190852e24f13c46cac05f984e7",
            description=None,
            is_closed=False,
            relationship_type="parent",
            create_time=None,
            commission_rate=0.0,
            follower_count=1000,
            tvl=1000000.0,
            apr=0.10,
        )

        row = HyperliquidHighFreqPriceRow(
            vault_address=vault_addr,
            timestamp=datetime.datetime(2026, 3, 9, 0, 0, 0),
            share_price=1.0,
            tvl=1000000.0,
            cumulative_pnl=0.0,
            is_closed=False,
            allow_deposits=True,
            leader_fraction=0.001,
            leader_commission=0.0,
            written_at=native_datetime_utc_now(),
        )
        store_permission_fixtures(db, [row], relationship_type="parent")
        db.upsert_high_freq_prices([row])
        db.save()

        result_df = build_raw_prices_dataframe_hf(db)
        result_row = result_df.iloc[0]

        assert result_row["deposit_closed_reason"] is None
        assert result_row["deposits_open"] == "true"
        assert result_row["performance_fee"] == pytest.approx(0.0)

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
        store_permission_fixtures(db, rows)
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


def test_capacity_receipt_between_price_rows_and_hlp_fee_identity(tmp_path):
    """Realistic receipt times govern capacity while stable HLP fees survive gaps."""
    db = HyperliquidHighFreqMetricsDatabase(tmp_path / "receipt-between-prices.duckdb")
    try:
        address = "0x" + "a" * 40
        first = datetime.datetime(2026, 9, 21, 10)
        db.upsert_vault_metadata(vault_address=address, name="HLP", leader="0x" + "b" * 40, description=None, is_closed=None, relationship_type="parent", create_time=None, commission_rate=None, follower_count=None, tvl=None, apr=None)
        db.upsert_high_freq_prices([HyperliquidHighFreqPriceRow(address, first, 1, 10, 0), HyperliquidHighFreqPriceRow(address, first + datetime.timedelta(hours=2), 1.1, 11, 1), HyperliquidHighFreqPriceRow(address, first + datetime.timedelta(hours=4), 1.2, 12, 2)])
        receipt = first + datetime.timedelta(hours=1, seconds=7)
        append_permission_observation(db.con, PermissionObservation("between", address, permission_observed_at=receipt, is_closed=False, allow_deposits=True, relationship_type="normal", leader_fraction=0.01, capacity_observed_at=receipt, provenance="observed"))
        exported = build_raw_prices_dataframe_hf(db)
        assert exported.performance_fee.tolist() == [0.0, 0.0, 0.0]
        assert pd.isna(exported.iloc[0].deposits_open)
        assert exported.iloc[1].max_deposit == 0
        assert exported.iloc[2].max_deposit == 0
        assert exported.iloc[2].capacity_observed_at == receipt
        assert exported.iloc[1].capacity_observed_at == receipt
    finally:
        db.close()


def test_open_merge_exports_sidecar_with_scanner_still_open(tmp_path):
    """Standalone scanners can merge and export before their finally closes DBs."""
    db_path = tmp_path / "active.duckdb"
    db = HyperliquidHighFreqMetricsDatabase(db_path)
    try:
        address = "0x" + "a" * 40
        timestamp = datetime.datetime(2026, 9, 21, 10)
        db.upsert_high_freq_prices([HyperliquidHighFreqPriceRow(address, timestamp, 1, 10, 0)])
        append_permission_observation(db.con, PermissionObservation("active", address, permission_observed_at=timestamp, is_closed=False, allow_deposits=True, provenance="observed"))
        result = open_and_merge_hypercore_prices(tmp_path / "vault-prices-1h.parquet", daily_db_path=tmp_path / "missing-daily.duckdb", hf_db_path=db_path)
        assert len(result) == 1
        sidecar = pd.read_parquet(tmp_path / "hypercore-vault-permissions.parquet")
        assert sidecar.observation_id.tolist() == ["active"]
        assert len(db.get_all_high_freq_prices()) == 1
    finally:
        db.close()


def test_hypercore_merge_preserves_evm_nan_null_and_extra_types(tmp_path: Path) -> None:
    """A native merge preserves distinct historical EVM NaNs and nulls.

    Include a double-precision extra column whose new Hypercore values are
    entirely null, and a retained capability receipt from the EVM scanner.
    """
    path = tmp_path / "vault-prices-1h.parquet"
    original = pa.table(
        {
            "chain": pa.array([143, 143], type=pa.uint32()),
            "address": ["0xold", "0xold"],
            "timestamp": pa.array([datetime.datetime(2026, 9, 1), datetime.datetime(2026, 9, 2)], type=pa.timestamp("ms")),
            "share_price": pa.array([float("nan"), None], type=pa.float64()),
            "leader_fraction": pa.array([None, None], type=pa.float64()),
        }
    ).replace_schema_metadata({b"perp_dex.test": b"retained"})
    pq.write_table(original, path)
    fresh = pd.DataFrame({"chain": [9999], "address": ["0xnew"], "timestamp": [datetime.datetime(2026, 9, 3)], "share_price": [1.0], "leader_fraction": [None]})
    result = _merge_hypercore_frame_to_parquet(path, fresh)
    assert len(result) == 3
    after = pq.read_table(path)
    # Arrow equality treats NaN as unequal to itself; verify it explicitly.
    retained = after.select(original.column_names).slice(0, 2)
    assert math.isnan(retained["share_price"][0].as_py())
    assert retained["share_price"][1].as_py() is None
    assert retained.drop(["share_price"]).equals(original.drop(["share_price"]), check_metadata=False)
    assert after.schema.field("leader_fraction").type == pa.float64()
    assert after.schema.metadata[b"perp_dex.test"] == b"retained"
