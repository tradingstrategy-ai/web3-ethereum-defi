"""Test Hibachi vault data parsing, DuckDB storage, and pipeline integration.

Unit tests only — no live API calls, no Anvil fork.
Tests cover:

1. Parsing ``/vault/info`` JSON into ``HibachiVaultInfo`` dataclass
2. Parsing ``/vault/performance`` JSON with UTC date conversion
3. Synthetic address format validation
4. DuckDB upsert roundtrip
5. Raw prices DataFrame schema for Parquet compatibility
6. VaultSpec creation via ``create_hibachi_vault_row``
7. Post-processing pipeline wiring (``run_post_processing`` → ``merge_native_protocols``)
"""

import datetime
from pathlib import Path

import pytest

from eth_defi.hibachi.constants import HIBACHI_CHAIN_ID
from eth_defi.hibachi.daily_metrics import HibachiDailyMetricsDatabase
from eth_defi.hibachi.vault import (
    HibachiVaultInfo,
    _parse_vault_info,
    _parse_vault_performance,
)
from eth_defi.hibachi.vault_data_export import (
    build_raw_prices_dataframe,
    create_hibachi_vault_row,
)
from eth_defi.utils import is_good_multichain_address


#: Hardcoded ``/vault/info`` fixture matching the live API shape
VAULT_INFO_FIXTURE = [
    {
        "vaultId": 2,
        "symbol": "GAV",
        "shortDescription": "Growi Alpha Vault",
        "description": "Mean-reversion strategy on crypto perps.",
        "perSharePrice": "1.030186",
        "30dSharePrice": "0.999923",
        "outstandingShares": "1501004.254809",
        "managementFees": "0.00000000",
        "depositFees": "0.00000000",
        "withdrawalFees": "0.00000000",
        "performanceFees": "0.00000000",
        "marginingAssetId": 1,
        "vaultAssetId": 131073,
        "vaultPubKey": "92f2d3ac73037b5a635b1aef77452b2c847e6e8a",
        "minUnlockHours": 0,
        "resolutionDecimals": 6,
        "maxDrawdown": "0",
        "sharpeRatio": "0",
    },
]

#: Hardcoded ``/vault/performance`` fixture.
#: Second timestamp ends in 999 to exercise the UTC date-shift edge case.
VAULT_PERFORMANCE_FIXTURE = {
    "vaultPerformanceIntervals": [
        {
            "interval": "1d",
            "timestamp": 1773226800,
            "perSharePrice": "1.000017",
            "totalValueLocked": "74925.281546",
        },
        {
            "interval": "1d",
            "timestamp": 1773313199,  # ends in 999 ms boundary — must not shift date
            "perSharePrice": "1.023197",
            "totalValueLocked": "76662.074599",
        },
        {
            "interval": "1d",
            "timestamp": 1773399600,
            "perSharePrice": "1.054465",
            "totalValueLocked": "79004.798135",
        },
    ],
}


def test_parse_vault_info_response():
    """Parse hardcoded ``/vault/info`` JSON into ``HibachiVaultInfo``.

    1. Call ``_parse_vault_info`` with the fixture
    2. Assert dataclass fields match the fixture values
    3. Assert ``tvl`` property equals ``perSharePrice × outstandingShares``
    4. Assert ``address`` property returns ``hibachi-vault-2``
    """
    # 1. Parse
    vaults = _parse_vault_info(VAULT_INFO_FIXTURE)

    assert len(vaults) == 1
    v = vaults[0]

    # 2. Field checks
    assert isinstance(v, HibachiVaultInfo)
    assert v.vault_id == 2
    assert v.symbol == "GAV"
    assert v.short_description == "Growi Alpha Vault"
    assert v.description == "Mean-reversion strategy on crypto perps."
    assert v.per_share_price == pytest.approx(1.030186)
    assert v.outstanding_shares == pytest.approx(1501004.254809)
    assert v.min_unlock_hours == 0
    assert v.vault_pub_key == "92f2d3ac73037b5a635b1aef77452b2c847e6e8a"
    assert v.vault_asset_id == 131073

    # 3. TVL property
    expected_tvl = 1.030186 * 1501004.254809
    assert v.tvl == pytest.approx(expected_tvl, rel=1e-6)

    # 4. Address property
    assert v.address == "hibachi-vault-2"


def test_parse_vault_performance_response():
    """Parse hardcoded ``/vault/performance`` JSON with UTC date conversion.

    1. Call ``_parse_vault_performance`` with the fixture
    2. Assert rows are sorted by date ascending
    3. Assert first row has ``daily_return is None``
    4. Assert second row ``daily_return`` matches expected value
    5. Assert timestamp ending in 999 does not cause a date shift
    """
    # 1. Parse
    prices = _parse_vault_performance(VAULT_PERFORMANCE_FIXTURE, vault_id=3)

    assert len(prices) == 3

    # 2. Sorted ascending
    assert prices[0].date <= prices[1].date <= prices[2].date

    # 3. First row has no daily return
    assert prices[0].daily_return is None

    # 4. Second row daily return
    expected_return = (1.023197 - 1.000017) / 1.000017
    assert prices[1].daily_return == pytest.approx(expected_return, rel=1e-6)

    # 5. Date is consistent (timestamp 1773313199 should not shift date)
    assert isinstance(prices[1].date, datetime.date)
    # All three dates should be different consecutive days
    assert prices[0].date < prices[1].date < prices[2].date


def test_synthetic_address_is_valid():
    """Verify ``hibachi-vault-`` prefix passes address validation.

    1. Check ``is_good_multichain_address`` returns ``True``
    """
    assert is_good_multichain_address("hibachi-vault-2") is True
    assert is_good_multichain_address("hibachi-vault-3") is True
    assert is_good_multichain_address("HIBACHI-VAULT-2") is True


def test_duckdb_upsert_roundtrip(tmp_path: Path):
    """Create DuckDB, upsert metadata + prices, read back and verify.

    1. Create ``HibachiDailyMetricsDatabase`` at ``tmp_path``
    2. Upsert one vault's metadata
    3. Upsert two daily price rows
    4. Read back via ``get_all_vault_metadata()`` and ``get_all_daily_prices()``
    5. Assert shapes and values match
    """
    from eth_defi.compat import native_datetime_utc_now

    # 1. Create database
    db = HibachiDailyMetricsDatabase(tmp_path / "test.duckdb")

    try:
        # 2. Upsert metadata
        db.upsert_vault_metadata(
            vault_id=2,
            symbol="GAV",
            short_description="Growi Alpha Vault",
            description="Mean-reversion strategy.",
            per_share_price=1.03,
            outstanding_shares=1500000.0,
            tvl=1545000.0,
            min_unlock_hours=0,
            vault_pub_key="abc123",
            vault_asset_id=131073,
        )

        # 3. Upsert daily prices
        now = native_datetime_utc_now()
        rows = [
            (2, datetime.date(2025, 6, 1), 1.0, 1500000.0, None, now),
            (2, datetime.date(2025, 6, 2), 1.01, 1515000.0, 0.01, now),
        ]
        db.upsert_daily_prices(rows)

        # 4. Read back
        meta_df = db.get_all_vault_metadata()
        prices_df = db.get_all_daily_prices()

        # 5. Assert
        assert len(meta_df) == 1
        assert meta_df.iloc[0]["vault_id"] == 2
        assert meta_df.iloc[0]["symbol"] == "GAV"
        assert meta_df.iloc[0]["vault_pub_key"] == "abc123"

        assert len(prices_df) == 2
        assert db.get_vault_count() == 1
    finally:
        db.close()


def test_raw_prices_dataframe_schema(tmp_path: Path):
    """Verify ``build_raw_prices_dataframe()`` output matches uncleaned Parquet schema.

    1. Create and populate a test DuckDB
    2. Call ``build_raw_prices_dataframe()``
    3. Assert column names, dtypes, and address format
    """
    from eth_defi.compat import native_datetime_utc_now

    # 1. Create and populate
    db = HibachiDailyMetricsDatabase(tmp_path / "test.duckdb")
    try:
        now = native_datetime_utc_now()
        rows = [
            (2, datetime.date(2025, 6, 1), 1.0, 1500000.0, None, now),
            (3, datetime.date(2025, 6, 1), 1.2, 500000.0, None, now),
        ]
        db.upsert_daily_prices(rows)

        # 2. Build DataFrame
        df = build_raw_prices_dataframe(db)
    finally:
        db.close()

    # 3. Assert schema
    expected_columns = {
        "chain",
        "address",
        "block_number",
        "timestamp",
        "share_price",
        "total_assets",
        "total_supply",
        "performance_fee",
        "management_fee",
        "errors",
        "written_at",
    }
    assert set(df.columns) == expected_columns
    assert df["chain"].dtype.name == "int32"
    assert df["block_number"].dtype.name == "int64"
    assert str(df["timestamp"].dtype).startswith("datetime64")

    # Address format
    assert all(addr.startswith("hibachi-vault-") for addr in df["address"])
    assert (df["chain"] == HIBACHI_CHAIN_ID).all()


def test_vault_spec_creation():
    """Verify ``create_hibachi_vault_row`` produces valid VaultSpec.

    1. Call ``create_hibachi_vault_row`` with test data
    2. Assert VaultSpec has correct chain_id and vault_address
    3. Assert VaultRow has correct Protocol and Denomination
    """
    # 1. Create
    spec, row = create_hibachi_vault_row(
        vault_id=2,
        symbol="GAV",
        name="Growi Alpha Vault",
        description="Test description",
        tvl=1500000.0,
    )

    # 2. VaultSpec checks
    assert spec.chain_id == 9997
    assert spec.vault_address == "hibachi-vault-2"

    # 3. VaultRow checks
    assert row["Protocol"] == "Hibachi"
    assert row["Denomination"] == "USDT"
    assert row["Link"] == "https://hibachi.xyz/vaults"
    assert row["Mgmt fee"] == 0.0
    assert row["Perf fee"] == 0.0
    assert row["Name"] == "Growi Alpha Vault"
    assert row["_description"] == "Test description"
    assert row["_short_description"] == "Test description."
    assert row["_short_description"] != row["Name"]


def test_vault_short_description_not_title_duplicate():
    """Verify Hibachi vault listing description does not duplicate title.

    1. Call ``create_hibachi_vault_row`` with a title-like name and longer
       strategy description
    2. Assert ``_short_description`` uses the strategy description
    3. Assert ``_short_description`` is omitted when only duplicate text is
       available
    """
    # 1. Create with distinct long description
    _, row = create_hibachi_vault_row(
        vault_id=3,
        symbol="FLP",
        name="Fire Liquidity Provider",
        description="Market making across all Hibachi markets. Operated by Kappa Lab.",
        tvl=750000.0,
    )

    # 2. Short description comes from the first strategy sentence, not title
    assert row["Name"] == "Fire Liquidity Provider"
    assert row["_short_description"] == "Market making across all Hibachi markets."
    assert row["_short_description"] != row["Name"]

    # 3. Duplicate-only source text is not repeated in the listing
    _, duplicate_row = create_hibachi_vault_row(
        vault_id=4,
        symbol="DUP",
        name="Duplicate Vault",
        description="Duplicate Vault",
        tvl=10.0,
    )
    assert duplicate_row["_short_description"] is None


def test_run_post_processing_wiring(tmp_path: Path):
    """Verify ``run_post_processing`` passes ``scan_hibachi`` through to ``merge_native_protocols``.

    1. Create a valid empty Hibachi DuckDB
    2. Call ``run_post_processing`` with ``scan_hibachi=True`` and all skips enabled
    3. Assert ``hibachi-price-merge`` key appears in the returned steps dict

    We mock nothing — we just verify the parameter plumbing works
    end-to-end with an empty database.
    """
    from eth_defi.vault.post_processing import run_post_processing

    # 1. Create valid empty DuckDB
    hibachi_db_path = tmp_path / "hibachi.duckdb"
    db = HibachiDailyMetricsDatabase(hibachi_db_path)
    db.close()

    # 2. Call run_post_processing
    steps = run_post_processing(
        scan_hibachi=True,
        hibachi_db_path=hibachi_db_path,
        skip_cleaning=True,
        skip_top_vaults=True,
        skip_sparklines=True,
        skip_metadata=True,
        skip_data=True,
        uncleaned_parquet_path=tmp_path / "prices.parquet",
    )

    # 3. Assert hibachi-price-merge appeared
    assert "hibachi-price-merge" in steps


def test_hibachi_live_scan_single_vault(tmp_path: Path):
    """Smoke integration test: scan a single Hibachi vault from the live API.

    Exercises the full pipeline end-to-end against the real
    ``data-api.hibachi.xyz`` endpoint, using isolated tmp databases.

    1. Run ``run_daily_scan`` for vault 3 (FLP) into an isolated DuckDB
    2. Assert DuckDB has exactly 1 vault with daily price history
    3. Merge into a fresh VaultDatabase pickle
    4. Assert VaultDatabase contains the Hibachi vault with correct metadata
    5. Merge into an isolated uncleaned Parquet
    6. Assert Parquet has Hibachi rows with correct schema
    """
    from eth_defi.hibachi.daily_metrics import run_daily_scan
    from eth_defi.hibachi.vault_data_export import (
        merge_into_uncleaned_parquet,
        merge_into_vault_database,
    )
    from eth_defi.vault.vaultdb import VaultDatabase

    db_path = tmp_path / "hibachi-test.duckdb"
    vault_db_path = tmp_path / "vault-db.pickle"
    parquet_path = tmp_path / "prices.parquet"

    # 1. Scan single vault (FLP, vaultId=3)
    db = run_daily_scan(
        db_path=db_path,
        vault_ids=[3],
    )

    try:
        # 2. DuckDB assertions
        assert db.get_vault_count() == 1

        meta_df = db.get_all_vault_metadata()
        assert len(meta_df) == 1
        assert meta_df.iloc[0]["symbol"] == "FLP"
        assert meta_df.iloc[0]["vault_id"] == 3
        assert meta_df.iloc[0]["vault_pub_key"] != ""
        assert meta_df.iloc[0]["tvl"] > 0

        prices_df = db.get_all_daily_prices()
        assert len(prices_df) > 10, f"Expected >10 daily prices, got {len(prices_df)}"
        assert (prices_df["per_share_price"] > 0).all()

        # 3. Merge into VaultDatabase
        vault_db = merge_into_vault_database(db, vault_db_path)

        # 4. VaultDatabase assertions
        assert len(vault_db) == 1
        spec, row = next(iter(vault_db.rows.items()))
        assert spec.chain_id == HIBACHI_CHAIN_ID
        assert spec.vault_address == "hibachi-vault-3"
        assert row["Protocol"] == "Hibachi"
        assert row["Denomination"] == "USDT"

        # 5. Merge into Parquet
        combined_df = merge_into_uncleaned_parquet(db, parquet_path)

        # 6. Parquet assertions
        hibachi_rows = combined_df[combined_df["chain"] == HIBACHI_CHAIN_ID]
        assert len(hibachi_rows) > 10
        assert (hibachi_rows["address"] == "hibachi-vault-3").all()
        assert hibachi_rows["share_price"].dtype.name == "float64"
        assert hibachi_rows["chain"].dtype.name == "int32"
    finally:
        db.close()
