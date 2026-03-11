"""Tests for Hyperliquid S3 vault data backfill.

Tests the two-stage backfill pipeline:
- Stage 1: LZ4 parsing and staging DB extraction
- Stage 2: Apply staged data to main metrics DB

All tests use synthetic data — no AWS or Hyperliquid API access required.
"""

import datetime
import io

import lz4.frame
import pandas as pd
import pytest

from eth_defi.hyperliquid.backfill import (
    HyperliquidS3StagingDatabase,
    apply_backfill,
    apply_backfill_single_vault,
    parse_account_values_lz4,
    parse_s3_filename_date,
    run_s3_extract,
)
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase


VAULT_A = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
VAULT_B = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
NON_VAULT = "0xcccccccccccccccccccccccccccccccccccccccc"


def _make_lz4_csv(rows: list[str], header: str = "time,user,is_vault,account_value,cum_vlm,cum_ledger") -> bytes:
    """Create an LZ4-compressed CSV from rows."""
    csv_text = header + "\n" + "\n".join(rows) + "\n"
    return lz4.frame.compress(csv_text.encode("utf-8"))


def _write_lz4_file(tmp_path, date: datetime.date, rows: list[str]) -> None:
    """Write a synthetic S3 account_values LZ4 file."""
    filename = f"{date.strftime('%Y%m%d')}.csv.lz4"
    data = _make_lz4_csv(rows)
    (tmp_path / filename).write_bytes(data)


def _make_vault_row(address: str, account_value: float, cum_ledger: float, cum_vlm: float = 0.0, is_vault: bool = True) -> str:
    """Create a CSV row for the account_values format."""
    return f"2026-01-01T00:00:00,{address},{str(is_vault).lower()},{account_value},{cum_vlm},{cum_ledger}"


def _setup_metrics_db_with_metadata(db, vault_address):
    """Insert minimal vault metadata so upsert_daily_prices works."""
    db.upsert_vault_metadata(
        vault_address=vault_address,
        name="Test Vault",
        leader="0x0000000000000000000000000000000000000001",
        description=None,
        is_closed=False,
        relationship_type="normal",
        create_time=datetime.datetime(2024, 1, 1),
        commission_rate=0.1,
        follower_count=5,
        tvl=10000.0,
        apr=50.0,
    )


def test_parse_s3_filename_date():
    """Filename date extraction works for valid and invalid names."""
    assert parse_s3_filename_date("20260301.csv.lz4") == datetime.date(2026, 3, 1)
    assert parse_s3_filename_date("20251115.csv.lz4") == datetime.date(2025, 11, 15)
    assert parse_s3_filename_date("not-a-date.csv.lz4") is None
    assert parse_s3_filename_date("readme.txt") is None


def test_parse_account_values_lz4(tmp_path):
    """LZ4 parsing extracts only vault rows and parses values correctly."""
    rows = [
        _make_vault_row(VAULT_A, 100000.0, 80000.0, 50000.0, is_vault=True),
        _make_vault_row(NON_VAULT, 5000.0, 5000.0, 1000.0, is_vault=False),
        _make_vault_row(VAULT_B, 200000.0, 150000.0, 100000.0, is_vault=True),
    ]
    _write_lz4_file(tmp_path, datetime.date(2026, 1, 15), rows)

    file_path = tmp_path / "20260115.csv.lz4"
    parsed = list(parse_account_values_lz4(file_path))

    # Only vault rows (is_vault=true) should be returned
    assert len(parsed) == 2

    # Check first vault row
    date, address, account_value, cum_ledger, cum_vlm = parsed[0]
    assert date == datetime.date(2026, 1, 15)
    assert address == VAULT_A.lower()
    assert account_value == pytest.approx(100000.0)
    assert cum_ledger == pytest.approx(80000.0)
    assert cum_vlm == pytest.approx(50000.0)

    # Check second vault row
    date, address, account_value, cum_ledger, cum_vlm = parsed[1]
    assert address == VAULT_B.lower()
    assert account_value == pytest.approx(200000.0)


def test_stage1_extract(tmp_path):
    """Stage 1 extracts vault data from LZ4 files into staging DB, is resumable."""
    s3_dir = tmp_path / "s3_files"
    s3_dir.mkdir()
    staging_db_path = tmp_path / "staging.duckdb"

    # Create 5 days of synthetic data
    for day_offset in range(5):
        date = datetime.date(2026, 1, 1) + datetime.timedelta(days=day_offset)
        base_value = 100000.0 + day_offset * 1000
        rows = [
            _make_vault_row(VAULT_A, base_value, 80000.0, 50000.0),
            _make_vault_row(NON_VAULT, 5000.0, 5000.0, 1000.0, is_vault=False),
            _make_vault_row(VAULT_B, base_value * 2, 150000.0, 100000.0),
        ]
        _write_lz4_file(s3_dir, date, rows)

    # Run extraction (without deleting files for resumability test)
    result = run_s3_extract(
        staging_db_path=staging_db_path,
        s3_data_dir=s3_dir,
        delete_lz4=False,
    )

    assert result["dates_processed"] == 5
    assert result["dates_skipped"] == 0
    assert result["vault_rows"] == 10  # 2 vaults × 5 days

    # Verify staging DB contents
    staging_db = HyperliquidS3StagingDatabase(staging_db_path)
    try:
        assert staging_db.get_vault_count() == 2
        assert staging_db.get_total_rows() == 10
        assert len(staging_db.get_processed_dates()) == 5
        assert len(staging_db.get_all_vault_addresses()) == 2

        vault_a_data = staging_db.get_vault_data(VAULT_A)
        assert len(vault_a_data) == 5
        assert vault_a_data.iloc[0]["account_value"] == pytest.approx(100000.0)
    finally:
        staging_db.close()

    # Run again — should skip all dates (resumable)
    result2 = run_s3_extract(
        staging_db_path=staging_db_path,
        s3_data_dir=s3_dir,
        delete_lz4=False,
    )
    assert result2["dates_processed"] == 0
    assert result2["dates_skipped"] == 5


def test_stage1_extract_deletes_lz4(tmp_path):
    """Stage 1 deletes LZ4 files after extraction when delete_lz4=True."""
    s3_dir = tmp_path / "s3_files"
    s3_dir.mkdir()
    staging_db_path = tmp_path / "staging.duckdb"

    _write_lz4_file(
        s3_dir,
        datetime.date(2026, 1, 1),
        [
            _make_vault_row(VAULT_A, 100000.0, 80000.0),
        ],
    )
    assert len(list(s3_dir.glob("*.csv.lz4"))) == 1

    run_s3_extract(
        staging_db_path=staging_db_path,
        s3_data_dir=s3_dir,
        delete_lz4=True,
    )

    # LZ4 file should be deleted
    assert len(list(s3_dir.glob("*.csv.lz4"))) == 0


def test_stage2_apply_single_vault(tmp_path):
    """Stage 2 fills gaps in the main DB from staging data, recomputes share prices."""
    staging_db_path = tmp_path / "staging.duckdb"
    metrics_db_path = tmp_path / "metrics.duckdb"

    # Create staging DB with 10 consecutive days
    staging_db = HyperliquidS3StagingDatabase(staging_db_path)
    metrics_db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        # Insert 10 days of staging data: vault grows from 100k to 109k.
        # Day 0: 100k TVL, 100k cum_ledger → 0 PnL → SP starts at 1.0
        # Day 1+: TVL grows by 1k/day from trading, no deposits
        for i in range(10):
            date = datetime.date(2024, 1, 1) + datetime.timedelta(days=i)
            account_value = 100000.0 + i * 1000  # TVL grows by 1k/day
            cum_ledger = 100000.0  # No deposits/withdrawals
            staging_db.insert_vault_rows([(date, VAULT_A, account_value, cum_ledger, 0.0)])
        staging_db.save()

        # Insert metadata for the vault
        _setup_metrics_db_with_metadata(metrics_db, VAULT_A)

        # Insert 3 existing API rows (days 0, 4, 9) — these should NOT be overwritten.
        # Values are consistent with S3 data: cumulative_pnl = account_value - cum_ledger
        api_rows = [
            # (vault_address, date, share_price, tvl, cumulative_pnl, daily_pnl,
            #  daily_return, follower_count, apr, is_closed, allow_deposits,
            #  leader_fraction, leader_commission, dep_count, wd_count, dep_usd, wd_usd, epoch_reset)
            (VAULT_A, datetime.date(2024, 1, 1), 1.0, 100000.0, 0.0, 0.0, 0.0, 100, 50.0, None, None, None, None, None, None, None, None, None),
            (VAULT_A, datetime.date(2024, 1, 5), 1.04, 104000.0, 4000.0, 1000.0, 0.01, 105, 52.0, None, None, None, None, None, None, None, None, None),
            (VAULT_A, datetime.date(2024, 1, 10), 1.09, 109000.0, 9000.0, 1000.0, 0.01, 110, 55.0, None, None, None, None, None, None, None, None, None),
        ]
        metrics_db.upsert_daily_prices(api_rows)
        metrics_db.save()

        # Apply backfill
        result = apply_backfill_single_vault(
            staging_db=staging_db,
            metrics_db=metrics_db,
            vault_address=VAULT_A,
        )

        assert result["dates_found"] == 10
        assert result["dates_inserted"] == 7  # 10 - 3 existing
        assert result["dates_skipped"] == 3

        # Verify total rows
        prices_df = metrics_db.get_vault_daily_prices(VAULT_A)
        assert len(prices_df) == 10

        # Verify API rows preserved their follower_count and apr
        api_day0 = prices_df[prices_df["date"] == pd.Timestamp("2024-01-01")].iloc[0]
        assert api_day0["follower_count"] == 100
        assert api_day0["apr"] == pytest.approx(50.0)
        assert api_day0["data_source"] == "api"

        api_day4 = prices_df[prices_df["date"] == pd.Timestamp("2024-01-05")].iloc[0]
        assert api_day4["follower_count"] == 105
        assert api_day4["data_source"] == "api"

        # Verify backfilled rows have correct data_source
        backfill_day1 = prices_df[prices_df["date"] == pd.Timestamp("2024-01-02")].iloc[0]
        assert backfill_day1["data_source"] == "s3_backfill"
        assert backfill_day1["tvl"] == pytest.approx(101000.0)
        # cumulative_pnl = account_value - cum_ledger = 101000 - 100000 = 1000
        assert backfill_day1["cumulative_pnl"] == pytest.approx(1000.0)

        # Verify share prices were recomputed (not all 1.0 placeholders)
        # Day 0: 100k TVL, 100k cum_ledger, 0 PnL → SP = 1.0
        assert prices_df["share_price"].iloc[0] == pytest.approx(1.0)
        # Share price should be > 1.0 for later rows (vault is profitable)
        assert prices_df["share_price"].iloc[-1] > 1.0
        # All share prices should be positive
        assert (prices_df["share_price"] > 0).all()

    finally:
        staging_db.close()
        metrics_db.close()


def test_backfill_preserves_api_data(tmp_path):
    """Backfill with overwrite_existing=False preserves all API-sourced columns."""
    staging_db_path = tmp_path / "staging.duckdb"
    metrics_db_path = tmp_path / "metrics.duckdb"

    staging_db = HyperliquidS3StagingDatabase(staging_db_path)
    metrics_db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        # Staging has data for day 1
        staging_db.insert_vault_rows(
            [
                (datetime.date(2024, 1, 1), VAULT_A, 100000.0, 90000.0, 50000.0),
            ]
        )
        staging_db.save()

        _setup_metrics_db_with_metadata(metrics_db, VAULT_A)

        # Main DB already has day 1 with rich API data
        api_rows = [
            (
                VAULT_A,
                datetime.date(2024, 1, 1),
                1.0,
                100000.0,
                10000.0,
                10000.0,
                0.0,
                100,
                50.0,
                False,
                True,
                0.1,
                500.0,
                5,
                2,
                50000.0,
                20000.0,
                None,
            )
        ]
        metrics_db.upsert_daily_prices(api_rows)
        metrics_db.save()

        # Apply backfill — should skip existing date
        result = apply_backfill_single_vault(
            staging_db=staging_db,
            metrics_db=metrics_db,
            vault_address=VAULT_A,
            overwrite_existing=False,
        )

        assert result["dates_inserted"] == 0
        assert result["dates_skipped"] == 1

        # Verify original data is intact
        prices_df = metrics_db.get_vault_daily_prices(VAULT_A)
        assert len(prices_df) == 1
        row = prices_df.iloc[0]
        assert row["follower_count"] == 100
        assert row["apr"] == pytest.approx(50.0)
        assert row["daily_deposit_count"] == 5
        assert row["daily_deposit_usd"] == pytest.approx(50000.0)
        assert row["data_source"] == "api"

    finally:
        staging_db.close()
        metrics_db.close()


def test_apply_backfill_multiple_vaults(tmp_path):
    """Apply backfill processes multiple vaults and reports correct totals."""
    staging_db_path = tmp_path / "staging.duckdb"
    metrics_db_path = tmp_path / "metrics.duckdb"

    staging_db = HyperliquidS3StagingDatabase(staging_db_path)
    metrics_db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        # Create staging data for 2 vaults, 5 days each
        for i in range(5):
            date = datetime.date(2024, 1, 1) + datetime.timedelta(days=i)
            staging_db.insert_vault_rows(
                [
                    (date, VAULT_A, 100000.0 + i * 1000, 90000.0, 0.0),
                    (date, VAULT_B, 200000.0 + i * 2000, 180000.0, 0.0),
                ]
            )
        staging_db.save()

        _setup_metrics_db_with_metadata(metrics_db, VAULT_A)
        _setup_metrics_db_with_metadata(metrics_db, VAULT_B)

        # Vault A has 2 existing rows, Vault B has none
        api_rows = [
            (VAULT_A, datetime.date(2024, 1, 1), 1.0, 100000.0, 10000.0, 10000.0, 0.0, 100, 50.0, None, None, None, None, None, None, None, None, None),
            (VAULT_A, datetime.date(2024, 1, 3), 1.02, 102000.0, 12000.0, 1000.0, 0.01, 102, 51.0, None, None, None, None, None, None, None, None, None),
        ]
        metrics_db.upsert_daily_prices(api_rows)
        metrics_db.save()

        result = apply_backfill(
            staging_db=staging_db,
            metrics_db=metrics_db,
        )

        assert result["vaults_processed"] == 2
        assert result["vaults_with_new_data"] == 2
        # Vault A: 5 staged - 2 existing = 3 inserted
        # Vault B: 5 staged - 0 existing = 5 inserted
        assert result["total_inserted"] == 8
        assert result["total_skipped"] == 2

        # Verify both vaults have all 5 days
        assert metrics_db.get_vault_daily_price_count(VAULT_A) == 5
        assert metrics_db.get_vault_daily_price_count(VAULT_B) == 5

    finally:
        staging_db.close()
        metrics_db.close()


def test_stage1_date_range_filter(tmp_path):
    """Stage 1 respects start_date and end_date filters."""
    s3_dir = tmp_path / "s3_files"
    s3_dir.mkdir()
    staging_db_path = tmp_path / "staging.duckdb"

    # Create files for days 1-5
    for i in range(5):
        date = datetime.date(2026, 1, 1) + datetime.timedelta(days=i)
        _write_lz4_file(s3_dir, date, [_make_vault_row(VAULT_A, 100000.0, 90000.0)])

    # Only process days 2-4
    result = run_s3_extract(
        staging_db_path=staging_db_path,
        s3_data_dir=s3_dir,
        start_date=datetime.date(2026, 1, 2),
        end_date=datetime.date(2026, 1, 4),
        delete_lz4=False,
    )

    assert result["dates_processed"] == 3
    assert result["vault_rows"] == 3


def test_backfill_share_price_continuity(tmp_path):
    """Backfilled share prices form a continuous series with existing API data."""
    staging_db_path = tmp_path / "staging.duckdb"
    metrics_db_path = tmp_path / "metrics.duckdb"

    staging_db = HyperliquidS3StagingDatabase(staging_db_path)
    metrics_db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        # Create 20 days of steadily growing vault data.
        # Day 0: 100k TVL, 100k cum_ledger → 0 cumulative_pnl → SP = 1.0
        # Day 1+: TVL grows by 1k/day (trading PnL), cum_ledger stays at 100k
        for i in range(20):
            date = datetime.date(2024, 1, 1) + datetime.timedelta(days=i)
            account_value = 100000.0 + i * 1000
            cum_ledger = 100000.0  # No deposits/withdrawals after day 0
            staging_db.insert_vault_rows(
                [
                    (date, VAULT_A, account_value, cum_ledger, 0.0),
                ]
            )
        staging_db.save()

        _setup_metrics_db_with_metadata(metrics_db, VAULT_A)

        # Apply all as backfill (no existing API data)
        apply_backfill_single_vault(
            staging_db=staging_db,
            metrics_db=metrics_db,
            vault_address=VAULT_A,
        )

        prices_df = metrics_db.get_vault_daily_prices(VAULT_A)
        assert len(prices_df) == 20

        # Share prices should monotonically increase (no deposits, steady PnL growth)
        share_prices = prices_df["share_price"].values
        for i in range(1, len(share_prices)):
            assert share_prices[i] >= share_prices[i - 1], f"Share price decreased at day {i}: {share_prices[i - 1]} -> {share_prices[i]}"

        # First day: 100k TVL, 100k cum_ledger, 0 PnL
        # netflow = 100k - 0 = 100k → mint 100k shares at SP=1.0
        # SP = 100k/100k = 1.0
        assert share_prices[0] == pytest.approx(1.0)

        # Last day: 119k TVL, 100k cum_ledger, 19k PnL
        # No additional deposits → SP = 119k / 100k = 1.19
        assert share_prices[-1] == pytest.approx(1.19)

    finally:
        staging_db.close()
        metrics_db.close()
