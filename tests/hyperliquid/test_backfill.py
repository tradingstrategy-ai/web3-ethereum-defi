"""Tests for Hyperliquid S3 vault data backfill.

Tests the two-stage backfill pipeline:
- Stage 1: LZ4 parsing and staging DB extraction
- Stage 2: Apply staged data to main metrics DB

All tests use synthetic data — no AWS or Hyperliquid API access required.
"""

import datetime
import io
from decimal import Decimal

import pandas as pd
import pytest

lz4_frame = pytest.importorskip("lz4.frame", reason="lz4 not installed (optional dep)")


from eth_defi.hyperliquid.backfill import (
    HyperliquidS3StagingDatabase,
    apply_backfill,
    apply_backfill_single_vault,
    parse_account_values_lz4,
    parse_s3_filename_date,
    run_s3_extract,
)
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase, HyperliquidDailyPriceRow, fetch_and_store_vault
from eth_defi.hyperliquid.vault import PortfolioHistory, VaultInfo, VaultSummary


VAULT_A = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
VAULT_B = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
NON_VAULT = "0xcccccccccccccccccccccccccccccccccccccccc"


def _make_lz4_csv(rows: list[str], header: str = "time,user,is_vault,account_value,cum_vlm,cum_ledger") -> bytes:
    """Create an LZ4-compressed CSV from rows."""
    csv_text = header + "\n" + "\n".join(rows) + "\n"
    return lz4_frame.compress(csv_text.encode("utf-8"))


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
            HyperliquidDailyPriceRow(VAULT_A, datetime.date(2024, 1, 1), 1.0, 100000.0, 0.0, 10000.0, 0.0, 0.0, 100, 50.0),
            HyperliquidDailyPriceRow(VAULT_A, datetime.date(2024, 1, 5), 1.04, 104000.0, 4000.0, 50000.0, 1000.0, 0.01, 105, 52.0),
            HyperliquidDailyPriceRow(VAULT_A, datetime.date(2024, 1, 10), 1.09, 109000.0, 9000.0, 100000.0, 1000.0, 0.01, 110, 55.0),
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
        assert api_day0["cumulative_volume"] == pytest.approx(10000.0)
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
        assert backfill_day1["cumulative_volume"] == pytest.approx(0.0)

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
            HyperliquidDailyPriceRow(
                vault_address=VAULT_A,
                date=datetime.date(2024, 1, 1),
                share_price=1.0,
                tvl=100000.0,
                cumulative_pnl=10000.0,
                daily_pnl=10000.0,
                daily_return=0.0,
                follower_count=100,
                apr=50.0,
                is_closed=False,
                allow_deposits=True,
                leader_fraction=0.1,
                leader_commission=500.0,
                daily_deposit_count=5,
                daily_withdrawal_count=2,
                daily_deposit_usd=50000.0,
                daily_withdrawal_usd=20000.0,
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
            HyperliquidDailyPriceRow(VAULT_A, datetime.date(2024, 1, 1), 1.0, 100000.0, 10000.0, daily_pnl=10000.0, daily_return=0.0, follower_count=100, apr=50.0),
            HyperliquidDailyPriceRow(VAULT_A, datetime.date(2024, 1, 3), 1.02, 102000.0, 12000.0, daily_pnl=1000.0, daily_return=0.01, follower_count=102, apr=51.0),
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


# ──────────────────────────────────────────────────────────────────────
# State recording tests: verify COALESCE preserves is_closed,
# allow_deposits, and leader_fraction across daily re-scans.
# ──────────────────────────────────────────────────────────────────────


def _make_daily_price_row(
    vault_address: str,
    date: datetime.date,
    share_price: float = 1.0,
    tvl: float = 100000.0,
    cumulative_pnl: float = 0.0,
    cumulative_volume: float | None = None,
    daily_pnl: float = 0.0,
    follower_count: int = 10,
    apr: float | None = 50.0,
    is_closed: bool | None = None,
    allow_deposits: bool | None = None,
    leader_fraction: float | None = None,
    leader_commission: float | None = None,
) -> HyperliquidDailyPriceRow:
    """Build a daily price row matching the Hyperliquid upsert schema."""
    return HyperliquidDailyPriceRow(
        vault_address=vault_address,
        date=date,
        share_price=share_price,
        tvl=tvl,
        cumulative_pnl=cumulative_pnl,
        cumulative_volume=cumulative_volume,
        daily_pnl=daily_pnl,
        daily_return=0.0,
        follower_count=follower_count,
        apr=apr,
        is_closed=is_closed,
        allow_deposits=allow_deposits,
        leader_fraction=leader_fraction,
        leader_commission=leader_commission,
    )


def test_leader_fraction_preserved_across_rescans(tmp_path):
    """COALESCE preserves leader_fraction from earlier scans when later scans write NULL."""
    metrics_db_path = tmp_path / "metrics.duckdb"
    db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        _setup_metrics_db_with_metadata(db, VAULT_A)

        # Day 1 scan: rows for Jan 1-5, leader_fraction only on Jan 5 (latest)
        day1_rows = [_make_daily_price_row(VAULT_A, datetime.date(2024, 1, d)) for d in range(1, 5)] + [
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 5), leader_fraction=0.15),
        ]
        db.upsert_daily_prices(day1_rows)
        db.save()

        # Day 2 scan: rows for Jan 1-6, leader_fraction only on Jan 6 (new latest)
        day2_rows = [_make_daily_price_row(VAULT_A, datetime.date(2024, 1, d)) for d in range(1, 6)] + [
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 6), leader_fraction=0.12),
        ]
        db.upsert_daily_prices(day2_rows)
        db.save()

        prices_df = db.get_vault_daily_prices(VAULT_A)
        assert len(prices_df) == 6

        # Jan 5 should still have leader_fraction=0.15 (preserved by COALESCE)
        jan5 = prices_df[prices_df["date"] == pd.Timestamp("2024-01-05")].iloc[0]
        assert jan5["leader_fraction"] == pytest.approx(0.15)

        # Jan 6 has new value
        jan6 = prices_df[prices_df["date"] == pd.Timestamp("2024-01-06")].iloc[0]
        assert jan6["leader_fraction"] == pytest.approx(0.12)

        # Jan 1-4 have NULL
        early = prices_df[prices_df["date"] < pd.Timestamp("2024-01-05")]
        assert early["leader_fraction"].isna().all()

        # get_leader_fraction_history returns exactly 2 rows
        history = db.get_leader_fraction_history(VAULT_A)
        assert len(history) == 2
        assert history.iloc[0]["leader_fraction"] == pytest.approx(0.15)
        assert history.iloc[1]["leader_fraction"] == pytest.approx(0.12)

    finally:
        db.close()


def test_cumulative_volume_preserved_across_rescans(tmp_path):
    """COALESCE preserves cumulative_volume snapshots from earlier scans."""
    metrics_db_path = tmp_path / "metrics.duckdb"
    db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        _setup_metrics_db_with_metadata(db, VAULT_A)

        day1_rows = [_make_daily_price_row(VAULT_A, datetime.date(2024, 1, d)) for d in range(1, 5)] + [
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 5), cumulative_volume=10000.0),
        ]
        db.upsert_daily_prices(day1_rows)
        db.save()

        day2_rows = [_make_daily_price_row(VAULT_A, datetime.date(2024, 1, d)) for d in range(1, 6)] + [
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 6), cumulative_volume=12000.0),
        ]
        db.upsert_daily_prices(day2_rows)
        db.save()

        prices_df = db.get_vault_daily_prices(VAULT_A)
        assert len(prices_df) == 6

        jan5 = prices_df[prices_df["date"] == pd.Timestamp("2024-01-05")].iloc[0]
        assert jan5["cumulative_volume"] == pytest.approx(10000.0)

        jan6 = prices_df[prices_df["date"] == pd.Timestamp("2024-01-06")].iloc[0]
        assert jan6["cumulative_volume"] == pytest.approx(12000.0)

        early = prices_df[prices_df["date"] < pd.Timestamp("2024-01-05")]
        assert early["cumulative_volume"].isna().all()

    finally:
        db.close()


def test_follower_count_and_apr_preserved_across_rescans(tmp_path):
    """COALESCE preserves follower_count and APR snapshots from earlier scans."""
    metrics_db_path = tmp_path / "metrics.duckdb"
    db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        _setup_metrics_db_with_metadata(db, VAULT_A)

        day1_rows = [_make_daily_price_row(VAULT_A, datetime.date(2024, 1, d), follower_count=None, apr=None) for d in range(1, 5)] + [
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 5), follower_count=100, apr=50.0),
        ]
        db.upsert_daily_prices(day1_rows)
        db.save()

        day2_rows = [_make_daily_price_row(VAULT_A, datetime.date(2024, 1, d), follower_count=None, apr=None) for d in range(1, 6)] + [
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 6), follower_count=120, apr=55.0),
        ]
        db.upsert_daily_prices(day2_rows)
        db.save()

        prices_df = db.get_vault_daily_prices(VAULT_A)
        assert len(prices_df) == 6

        jan5 = prices_df[prices_df["date"] == pd.Timestamp("2024-01-05")].iloc[0]
        assert jan5["follower_count"] == 100
        assert jan5["apr"] == pytest.approx(50.0)

        jan6 = prices_df[prices_df["date"] == pd.Timestamp("2024-01-06")].iloc[0]
        assert jan6["follower_count"] == 120
        assert jan6["apr"] == pytest.approx(55.0)

        early = prices_df[prices_df["date"] < pd.Timestamp("2024-01-05")]
        assert early["follower_count"].isna().all()
        assert early["apr"].isna().all()

    finally:
        db.close()


def test_fetch_and_store_vault_preserves_historical_apr_on_resume(tmp_path, monkeypatch):
    """Resume scans should only write APR to the latest row."""
    metrics_db_path = tmp_path / "metrics.duckdb"
    db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        first_vault_info = VaultInfo(
            name="Test Vault",
            vault_address=VAULT_A,
            leader="0x0000000000000000000000000000000000000001",
            description="",
            followers=[],
            portfolio={
                "allTime": PortfolioHistory(
                    period="allTime",
                    account_value_history=[
                        (datetime.datetime(2024, 1, 1), Decimal("100000")),
                        (datetime.datetime(2024, 1, 2), Decimal("101000")),
                    ],
                    pnl_history=[
                        (datetime.datetime(2024, 1, 1), Decimal("0")),
                        (datetime.datetime(2024, 1, 2), Decimal("1000")),
                    ],
                    volume=Decimal("12000"),
                ),
            },
            max_distributable=Decimal("0"),
            max_withdrawable=Decimal("0"),
            is_closed=False,
            allow_deposits=True,
            relationship_type="normal",
            commission_rate=Decimal("0.1"),
            leader_fraction=Decimal("0.15"),
            leader_commission=10.0,
        )
        second_vault_info = VaultInfo(
            name="Test Vault",
            vault_address=VAULT_A,
            leader="0x0000000000000000000000000000000000000001",
            description="",
            followers=[],
            portfolio={
                "allTime": PortfolioHistory(
                    period="allTime",
                    account_value_history=[
                        (datetime.datetime(2024, 1, 1), Decimal("100000")),
                        (datetime.datetime(2024, 1, 2), Decimal("101000")),
                        (datetime.datetime(2024, 1, 3), Decimal("103000")),
                    ],
                    pnl_history=[
                        (datetime.datetime(2024, 1, 1), Decimal("0")),
                        (datetime.datetime(2024, 1, 2), Decimal("1000")),
                        (datetime.datetime(2024, 1, 3), Decimal("3000")),
                    ],
                    volume=Decimal("15000"),
                ),
            },
            max_distributable=Decimal("0"),
            max_withdrawable=Decimal("0"),
            is_closed=False,
            allow_deposits=True,
            relationship_type="normal",
            commission_rate=Decimal("0.1"),
            leader_fraction=Decimal("0.15"),
            leader_commission=10.0,
        )

        fetches = iter([first_vault_info, second_vault_info])
        monkeypatch.setattr("eth_defi.hyperliquid.daily_metrics.HyperliquidVault.fetch_info", lambda self: next(fetches))

        first_summary = VaultSummary(
            name="Test Vault",
            vault_address=VAULT_A,
            leader="0x0000000000000000000000000000000000000001",
            tvl=Decimal("103000"),
            is_closed=False,
            relationship_type="normal",
            create_time=datetime.datetime(2024, 1, 1),
            apr=Decimal("50"),
        )
        second_summary = VaultSummary(
            name="Test Vault",
            vault_address=VAULT_A,
            leader="0x0000000000000000000000000000000000000001",
            tvl=Decimal("103000"),
            is_closed=False,
            relationship_type="normal",
            create_time=datetime.datetime(2024, 1, 1),
            apr=Decimal("55"),
        )

        assert fetch_and_store_vault(None, db, first_summary, cutoff_date=None, flow_backfill_days=0)
        db.save()

        assert fetch_and_store_vault(None, db, second_summary, cutoff_date=None, flow_backfill_days=0)
        db.save()

        prices_df = db.get_vault_daily_prices(VAULT_A)
        assert len(prices_df) == 3

        jan1 = prices_df[prices_df["date"] == pd.Timestamp("2024-01-01")].iloc[0]
        jan2 = prices_df[prices_df["date"] == pd.Timestamp("2024-01-02")].iloc[0]
        jan3 = prices_df[prices_df["date"] == pd.Timestamp("2024-01-03")].iloc[0]

        assert pd.isna(jan1["apr"])
        assert jan2["apr"] == pytest.approx(50.0)
        assert jan3["apr"] == pytest.approx(55.0)

    finally:
        db.close()


def test_is_closed_allow_deposits_preserved_across_rescans(tmp_path):
    """COALESCE preserves is_closed and allow_deposits from earlier scans."""
    metrics_db_path = tmp_path / "metrics.duckdb"
    db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        _setup_metrics_db_with_metadata(db, VAULT_A)

        # Day 1 scan: Jan 1-3, last row has is_closed=False, allow_deposits=True
        day1_rows = [
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 1)),
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 2)),
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 3), is_closed=False, allow_deposits=True),
        ]
        db.upsert_daily_prices(day1_rows)
        db.save()

        # Day 2 scan: Jan 1-4, all historical rows NULL, Jan 4 has allow_deposits=False
        day2_rows = [_make_daily_price_row(VAULT_A, datetime.date(2024, 1, d)) for d in range(1, 4)] + [
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 4), is_closed=False, allow_deposits=False),
        ]
        db.upsert_daily_prices(day2_rows)
        db.save()

        prices_df = db.get_vault_daily_prices(VAULT_A)

        # Jan 3 should still have allow_deposits=True (preserved by COALESCE)
        jan3 = prices_df[prices_df["date"] == pd.Timestamp("2024-01-03")].iloc[0]
        assert jan3["allow_deposits"] == True
        assert jan3["is_closed"] == False

        # Jan 4 has the new state
        jan4 = prices_df[prices_df["date"] == pd.Timestamp("2024-01-04")].iloc[0]
        assert jan4["allow_deposits"] == False
        assert jan4["is_closed"] == False

        # Jan 1-2 have NULL
        early = prices_df[prices_df["date"] < pd.Timestamp("2024-01-03")]
        assert early["allow_deposits"].isna().all()
        assert early["is_closed"].isna().all()

    finally:
        db.close()


def test_backfill_rows_have_null_leader_fraction(tmp_path):
    """Rows created by the real backfill pipeline have NULL state fields."""
    staging_db_path = tmp_path / "staging.duckdb"
    metrics_db_path = tmp_path / "metrics.duckdb"

    staging_db = HyperliquidS3StagingDatabase(staging_db_path)
    metrics_db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        # 5 days of staging data
        for i in range(5):
            date = datetime.date(2024, 1, 1) + datetime.timedelta(days=i)
            staging_db.insert_vault_rows([(date, VAULT_A, 100000.0 + i * 1000, 100000.0, 0.0)])
        staging_db.save()

        _setup_metrics_db_with_metadata(metrics_db, VAULT_A)

        apply_backfill_single_vault(
            staging_db=staging_db,
            metrics_db=metrics_db,
            vault_address=VAULT_A,
        )

        prices_df = metrics_db.get_vault_daily_prices(VAULT_A)
        assert len(prices_df) == 5

        # All backfilled rows should have NULL for state fields
        assert prices_df["leader_fraction"].isna().all()
        assert prices_df["is_closed"].isna().all()
        assert prices_df["allow_deposits"].isna().all()
        assert prices_df["leader_commission"].isna().all()

        # get_leader_fraction_history returns empty
        history = metrics_db.get_leader_fraction_history(VAULT_A)
        assert len(history) == 0

    finally:
        staging_db.close()
        metrics_db.close()


def test_leader_fraction_history_ordering(tmp_path):
    """get_leader_fraction_history returns rows in date order and values can be checked against threshold."""
    from eth_defi.hyperliquid.vault_data_export import LEADER_FRACTION_WARNING_THRESHOLD

    metrics_db_path = tmp_path / "metrics.duckdb"
    db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        _setup_metrics_db_with_metadata(db, VAULT_A)

        # Simulate 3 scan days with declining leader fraction
        fractions = [0.10, 0.06, 0.04]
        for i, frac in enumerate(fractions):
            date = datetime.date(2024, 1, 1) + datetime.timedelta(days=i)
            rows = [_make_daily_price_row(VAULT_A, date, leader_fraction=frac)]
            db.upsert_daily_prices(rows)
        db.save()

        history = db.get_leader_fraction_history(VAULT_A)
        assert len(history) == 3

        # Verify date ordering
        dates = history["date"].tolist()
        assert dates == sorted(dates)

        # Verify values
        values = history["leader_fraction"].tolist()
        assert values[0] == pytest.approx(0.10)
        assert values[1] == pytest.approx(0.06)
        assert values[2] == pytest.approx(0.04)

        # Can identify which values are below the warning threshold (0.055)
        below_threshold = history[history["leader_fraction"] < LEADER_FRACTION_WARNING_THRESHOLD]
        assert len(below_threshold) == 1  # only 0.04 is below 0.055

    finally:
        db.close()


def test_mark_vaults_disappeared_preserves_state(tmp_path):
    """mark_vaults_disappeared sets TVL=0 in metadata but does not modify daily price state."""
    metrics_db_path = tmp_path / "metrics.duckdb"
    db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        _setup_metrics_db_with_metadata(db, VAULT_A)
        _setup_metrics_db_with_metadata(db, VAULT_B)

        # Insert daily price rows with state on latest row for both vaults
        for addr in [VAULT_A, VAULT_B]:
            rows = [
                _make_daily_price_row(addr, datetime.date(2024, 1, 1)),
                _make_daily_price_row(addr, datetime.date(2024, 1, 2), is_closed=False, allow_deposits=True, leader_fraction=0.10),
            ]
            db.upsert_daily_prices(rows)
        db.save()

        # Vault A is still present in the API, vault B has disappeared
        db.mark_vaults_disappeared({VAULT_A.lower()})
        db.save()

        # Vault B metadata should have TVL=0 but is_closed unchanged
        metadata = db.get_all_vault_metadata()
        vault_b_meta = metadata[metadata["vault_address"] == VAULT_B.lower()].iloc[0]
        assert vault_b_meta["tvl"] == pytest.approx(0.0)
        assert vault_b_meta["is_closed"] == False  # not changed by mark_vaults_disappeared

        # Vault B daily price state values should be untouched
        prices_b = db.get_vault_daily_prices(VAULT_B)
        jan2_b = prices_b[prices_b["date"] == pd.Timestamp("2024-01-02")].iloc[0]
        assert jan2_b["is_closed"] == False
        assert jan2_b["allow_deposits"] == True
        assert jan2_b["leader_fraction"] == pytest.approx(0.10)

        # Vault A should be completely unaffected
        metadata_a = metadata[metadata["vault_address"] == VAULT_A.lower()].iloc[0]
        assert metadata_a["tvl"] == pytest.approx(10000.0)  # original value from _setup_metrics_db_with_metadata

    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────
# deposit_closed_reason tests: verify build_raw_prices_dataframe()
# produces correct per-row deposit state from DuckDB state columns.
# ──────────────────────────────────────────────────────────────────────


def test_build_raw_prices_deposits_open_healthy(tmp_path):
    """Healthy vault with deposits open has deposit_closed_reason=None and deposits_open='true'."""
    from eth_defi.hyperliquid.vault_data_export import build_raw_prices_dataframe

    metrics_db_path = tmp_path / "metrics.duckdb"
    db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        _setup_metrics_db_with_metadata(db, VAULT_A)

        rows = [_make_daily_price_row(VAULT_A, datetime.date(2024, 1, d)) for d in range(1, 5)] + [
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 5), is_closed=False, allow_deposits=True, leader_fraction=0.15),
        ]
        db.upsert_daily_prices(rows)
        db.save()

        result = build_raw_prices_dataframe(db)
        assert len(result) == 5
        assert "deposit_closed_reason" in result.columns
        assert "deposits_open" in result.columns

        # Latest row (Jan 5) has state — deposits are open
        latest = result.iloc[-1]
        assert latest["deposit_closed_reason"] is None
        assert latest["deposits_open"] == "true"

    finally:
        db.close()


def test_build_raw_prices_includes_account_pnl(tmp_path):
    """Raw Hyperliquid export exposes scalar passthrough metrics."""
    from eth_defi.hyperliquid.vault_data_export import build_raw_prices_dataframe

    metrics_db_path = tmp_path / "metrics.duckdb"
    db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        _setup_metrics_db_with_metadata(db, VAULT_A)

        rows = [
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 1), cumulative_pnl=0.0, cumulative_volume=10000.0, follower_count=7),
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 2), share_price=1.02, tvl=102000.0, cumulative_pnl=2000.0, cumulative_volume=15000.0, daily_pnl=2000.0, follower_count=8),
        ]
        db.upsert_daily_prices(rows)
        db.save()

        result = build_raw_prices_dataframe(db)
        assert "account_pnl" in result.columns
        assert "follower_count" in result.columns
        assert "cumulative_volume" in result.columns
        assert result["account_pnl"].tolist() == pytest.approx([0.0, 2000.0])
        assert result["follower_count"].tolist() == pytest.approx([7.0, 8.0])
        assert result["cumulative_volume"].tolist() == pytest.approx([10000.0, 15000.0])

    finally:
        db.close()


def test_build_raw_prices_deposit_closed_leader_fraction(tmp_path):
    """Leader fraction below threshold produces deposit_closed_reason with 'Leader share' message."""
    from eth_defi.hyperliquid.vault_data_export import build_raw_prices_dataframe

    metrics_db_path = tmp_path / "metrics.duckdb"
    db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        _setup_metrics_db_with_metadata(db, VAULT_A)

        rows = [
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 1), is_closed=False, allow_deposits=True, leader_fraction=0.04),
        ]
        db.upsert_daily_prices(rows)
        db.save()

        result = build_raw_prices_dataframe(db)
        assert len(result) == 1

        row = result.iloc[0]
        assert row["deposit_closed_reason"] is not None
        assert "Leader share" in row["deposit_closed_reason"]
        assert row["deposits_open"] == "false"

    finally:
        db.close()


def test_build_raw_prices_deposit_closed_allow_deposits(tmp_path):
    """allow_deposits=False produces correct deposit_closed_reason."""
    from eth_defi.hyperliquid.vault_data_export import build_raw_prices_dataframe

    metrics_db_path = tmp_path / "metrics.duckdb"
    db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        _setup_metrics_db_with_metadata(db, VAULT_A)

        rows = [
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 1), is_closed=False, allow_deposits=False, leader_fraction=0.15),
        ]
        db.upsert_daily_prices(rows)
        db.save()

        result = build_raw_prices_dataframe(db)
        row = result.iloc[0]
        assert row["deposit_closed_reason"] == "Vault deposits disabled by leader"
        assert row["deposits_open"] == "false"

    finally:
        db.close()


def test_build_raw_prices_unknown_state_rows(tmp_path):
    """Rows before first state observation have deposit_closed_reason=None (not misclassified)."""
    from eth_defi.hyperliquid.vault_data_export import build_raw_prices_dataframe

    metrics_db_path = tmp_path / "metrics.duckdb"
    db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        _setup_metrics_db_with_metadata(db, VAULT_A)

        # 5 rows, only the last has state
        rows = [_make_daily_price_row(VAULT_A, datetime.date(2024, 1, d)) for d in range(1, 5)] + [
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 5), is_closed=False, allow_deposits=True, leader_fraction=0.15),
        ]
        db.upsert_daily_prices(rows)
        db.save()

        result = build_raw_prices_dataframe(db)
        assert len(result) == 5

        # Rows before state (Jan 1-4): no forward-fill source → unknown
        early = result.iloc[:4]
        assert early["deposit_closed_reason"].isna().all(), "Early rows with no state should have None deposit_closed_reason"
        assert (early["deposits_open"].isna()).all(), "Early rows with no state should have None deposits_open"

        # Last row (Jan 5) has state
        latest = result.iloc[-1]
        assert latest["deposit_closed_reason"] is None
        assert latest["deposits_open"] == "true"

    finally:
        db.close()


def test_deposit_closed_reason_in_cleaned_data():
    """derive_deposit_closed_reason fills in reason for ERC-4626 rows with deposits_open='false'."""
    from eth_defi.research.wrangle_vault_prices import derive_deposit_closed_reason, ensure_vault_state_columns

    # Build a synthetic DataFrame resembling ERC-4626 cleaned data
    df = pd.DataFrame(
        {
            "deposits_open": ["true", "false", "true", "false", ""],
        }
    )
    df = ensure_vault_state_columns(df)
    df = derive_deposit_closed_reason(df)

    assert "deposit_closed_reason" in df.columns

    # "true" → None (deposits open)
    assert df.iloc[0]["deposit_closed_reason"] is None
    # "false" → reason filled in
    assert df.iloc[1]["deposit_closed_reason"] == "Vault deposits disabled"
    # "true" → None
    assert df.iloc[2]["deposit_closed_reason"] is None
    # "false" → reason filled in
    assert df.iloc[3]["deposit_closed_reason"] == "Vault deposits disabled"
    # "" (unknown) → None
    assert df.iloc[4]["deposit_closed_reason"] is None


def test_process_raw_vault_scan_data_preserves_hyperliquid_scalars(tmp_path):
    """Cleaned price data keeps Hyperliquid scalar passthrough columns intact."""
    from eth_defi.hyperliquid.vault_data_export import build_raw_prices_dataframe, create_hyperliquid_vault_row
    from eth_defi.research.wrangle_vault_prices import process_raw_vault_scan_data

    metrics_db_path = tmp_path / "metrics.duckdb"
    db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        _setup_metrics_db_with_metadata(db, VAULT_A)

        rows = [
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 1), cumulative_pnl=0.0, cumulative_volume=10000.0, follower_count=7),
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 2), share_price=1.01, tvl=101000.0, cumulative_pnl=1000.0, cumulative_volume=12000.0, daily_pnl=1000.0, follower_count=8),
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 3), share_price=1.03, tvl=103000.0, cumulative_pnl=3000.0, cumulative_volume=15000.0, daily_pnl=2000.0, follower_count=9),
        ]
        db.upsert_daily_prices(rows)
        db.save()

        raw_df = build_raw_prices_dataframe(db)
        spec, vault_row = create_hyperliquid_vault_row(
            vault_address=VAULT_A,
            name="Test Vault",
            description=None,
            tvl=103000.0,
            create_time=datetime.datetime(2024, 1, 1),
        )

        cleaned_df = process_raw_vault_scan_data(
            {spec: vault_row},
            raw_df,
            logger=lambda msg: None,
            display=lambda _: None,
        )

        assert "account_pnl" in cleaned_df.columns
        assert "follower_count" in cleaned_df.columns
        assert "cumulative_volume" in cleaned_df.columns
        assert cleaned_df["account_pnl"].tolist() == pytest.approx([0.0, 1000.0, 3000.0])
        assert cleaned_df["follower_count"].tolist() == pytest.approx([7.0, 8.0, 9.0])
        assert cleaned_df["cumulative_volume"].tolist() == pytest.approx([10000.0, 12000.0, 15000.0])

    finally:
        db.close()


def test_hyperliquid_scalars_reach_lifetime_metrics_export(tmp_path):
    """Hyperliquid scalar passthrough fields reach lifetime metrics and JSON export."""
    from eth_defi.hyperliquid.vault_data_export import build_raw_prices_dataframe, create_hyperliquid_vault_row
    from eth_defi.research.vault_metrics import calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics, export_lifetime_row
    from eth_defi.research.wrangle_vault_prices import process_raw_vault_scan_data

    metrics_db_path = tmp_path / "metrics.duckdb"
    db = HyperliquidDailyMetricsDatabase(metrics_db_path)
    try:
        _setup_metrics_db_with_metadata(db, VAULT_A)

        rows = [
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 1), cumulative_pnl=0.0, cumulative_volume=10000.0, follower_count=7),
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 2), share_price=1.01, tvl=101000.0, cumulative_pnl=1000.0, cumulative_volume=12000.0, daily_pnl=1000.0, follower_count=8),
            _make_daily_price_row(VAULT_A, datetime.date(2024, 1, 3), share_price=1.03, tvl=103000.0, cumulative_pnl=3000.0, cumulative_volume=15000.0, daily_pnl=2000.0, follower_count=9),
        ]
        db.upsert_daily_prices(rows)
        db.save()

        raw_df = build_raw_prices_dataframe(db)
        spec, vault_row = create_hyperliquid_vault_row(
            vault_address=VAULT_A,
            name="Test Vault",
            description=None,
            tvl=103000.0,
            create_time=datetime.datetime(2024, 1, 1),
        )

        cleaned_df = process_raw_vault_scan_data(
            {spec: vault_row},
            raw_df,
            logger=lambda msg: None,
            display=lambda _: None,
        )
        returns_df = calculate_hourly_returns_for_all_vaults(cleaned_df)
        lifetime_df = calculate_lifetime_metrics(returns_df, {spec: vault_row})

        assert len(lifetime_df) == 1
        row = lifetime_df.iloc[0]
        assert row["account_pnl"] == pytest.approx(3000.0)
        assert row["follower_count"] == 9
        assert row["cumulative_volume"] == pytest.approx(15000.0)

        exported = export_lifetime_row(row)
        assert exported["account_pnl"] == pytest.approx(3000.0)
        assert exported["follower_count"] == 9
        assert exported["cumulative_volume"] == pytest.approx(15000.0)

    finally:
        db.close()
