"""Clean vault price data"""

import logging
import os.path
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import zstandard as zstd

from eth_defi.research.wrangle_vault_prices import fix_outlier_share_prices, generate_cleaned_vault_datasets
from eth_defi.vault.base import VaultHistoricalRead


@pytest.fixture()
def vault_db(tmp_path) -> Path:
    """Load sample vault database for testing.

    To generate:

    .. code-block:: shell

        zstd -22 --ultra -f -o tests/research/vault-metadata-db.pickle.zstd ~/.tradingstrategy/vaults/vault-metadata-db.pickle

    """
    dst = tmp_path / "vault-metadata-db.pickle"
    path = Path(os.path.dirname(__file__)) / "vault-metadata-db.pickle.zstd"
    with zstd.open(path, "rb") as f:
        data = pickle.load(f)
        with open(dst, "wb") as f:
            pickle.dump(data, f)

    return dst


@pytest.fixture()
def raw_price_df() -> Path:
    """Load price data for testing.

    - Use a small sample of Hemi chain data taken with extract-single-chain.py
    """
    raw_prices = Path(os.path.dirname(__file__)) / "chain-hemi-raw-prices-1h.parquet"
    return raw_prices


def test_clean_vault_price_data(
    vault_db: Path,
    raw_price_df: Path,
    tmp_path: Path,
):
    """Test cleaning vault price data.

    - Use raw Hemi prices as test sample
    - See `extract-uncleaned-price-data-sample.py` for extraction script
    """

    dst = tmp_path / "cleaned-vault-prices.parquet"

    logger = logging.getLogger(__name__)

    generate_cleaned_vault_datasets(vault_db_path=vault_db, price_df_path=raw_price_df, cleaned_price_df_path=dst, logger=logger.info)

    assert dst.exists()
    df = pd.read_parquet(dst)

    assert "raw_share_price" in df.columns
    assert "share_price" in df.columns
    assert len(df["id"].unique()) == 4

    # Vault state columns should always be present in cleaned output,
    # even when raw scan data predates these fields
    assert "max_deposit" in df.columns
    assert "max_redeem" in df.columns
    assert "deposits_open" in df.columns
    assert "redemption_open" in df.columns
    assert "trading" in df.columns

    # Lending statistics columns should always be present in cleaned output
    assert "available_liquidity" in df.columns
    assert "utilisation" in df.columns

    # written_at column should always be present in cleaned output
    # (NaT for old data that predates the column)
    assert "written_at" in df.columns


def test_remove_inactive_lead_time():
    """Test removal of initial rows where total_supply hasn't changed."""
    from eth_defi.research.wrangle_vault_prices import remove_inactive_lead_time

    # Create test data with inactive lead time
    data = {
        "id": ["vault1"] * 5 + ["vault2"] * 4,
        "total_supply": [1000, 1000, 1000, 1500, 2000, 0, 100, 100, 200],
        "share_price": [1.0, 1.0, 1.0, 1.1, 1.2, 0, 1.0, 1.0, 1.1],
        "timestamp": pd.date_range("2024-01-01", periods=9, freq="h"),
    }
    df = pd.DataFrame(data).set_index("timestamp")

    result = remove_inactive_lead_time(df)

    # vault1: should start at index 3 (first change from 1000)
    # vault2: should skip row 0 (zero supply), start at index 2 (first change from 100)
    vault1_rows = result[result["id"] == "vault1"]
    vault2_rows = result[result["id"] == "vault2"]

    assert len(vault1_rows) == 2  # rows at index 3, 4
    assert len(vault2_rows) == 1  # row at index 3 (200)


def test_native_protocol_columns_survive_evm_scan_rewrite(tmp_path: Path):
    """Native protocol columns must survive the EVM scanner's parquet rewrite.

    Reproduces the bug where:

    1. Native merge (Hyperliquid) writes uncleaned parquet with extra columns
       (account_pnl, leader_fraction, etc.) via write_uncleaned_parquet()
    2. EVM scanner reads the file, runs migrate_parquet_schema(), and rewrites
    3. Previously, migrate_parquet_schema() dropped all non-canonical columns
       and the writer used only the canonical schema, destroying native data

    Verifies that:

    1. write_uncleaned_parquet() preserves canonical column types
    2. migrate_parquet_schema() preserves non-canonical columns
    3. A simulated EVM rewrite (read → migrate → filter → write) keeps native data
    """

    parquet_path = tmp_path / "vault-prices-1h.parquet"
    canonical_schema = VaultHistoricalRead.to_pyarrow_schema()

    # 1. Simulate a native merge writing to the uncleaned parquet.
    #    The DataFrame has canonical columns plus native-only columns,
    #    as produced by Hyperliquid's build_raw_prices_dataframe().
    native_df = pd.DataFrame(
        {
            "chain": pd.array([9999, 9999, 1, 1], dtype="uint32"),
            "address": ["0xaaa", "0xaaa", "0xbbb", "0xbbb"],
            "block_number": pd.array([100, 200, 1000, 2000], dtype="uint64"),
            "timestamp": pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-01", "2025-01-02"]),
            "share_price": [1.0, 1.01, 1.0, 1.005],
            "total_assets": [1000.0, 1010.0, 5000.0, 5025.0],
            "total_supply": [1000.0, 1000.0, 5000.0, 5000.0],
            "performance_fee": pd.array([0.1, 0.1, 0.0, 0.0], dtype="float32"),
            "management_fee": pd.array([0.0, 0.0, 0.02, 0.02], dtype="float32"),
            "errors": ["", "", "", ""],
            # Native-only columns from Hyperliquid
            "account_pnl": [100.0, 110.0, np.nan, np.nan],
            "leader_fraction": [0.5, 0.5, np.nan, np.nan],
            "deposit_closed_reason": ["", "", "", ""],
        },
    )

    VaultHistoricalRead.write_uncleaned_parquet(native_df, parquet_path)

    # 2. Verify the written file has correct canonical types
    table = pq.read_table(parquet_path)
    assert table.schema.field("chain").type == pa.uint32()
    assert table.schema.field("block_number").type == pa.uint64()
    assert table.schema.field("timestamp").type == pa.timestamp("ms")
    # Native columns present
    assert "account_pnl" in table.schema.names
    assert "leader_fraction" in table.schema.names
    assert "deposit_closed_reason" in table.schema.names

    # 3. Simulate an EVM scanner reading the file and migrating
    migrated = VaultHistoricalRead.migrate_parquet_schema(table)

    # All canonical columns present with correct types
    for field in canonical_schema:
        assert field.name in migrated.schema.names
        assert migrated.schema.field(field.name).type == field.type, f"Column {field.name}: expected {field.type}, got {migrated.schema.field(field.name).type}"

    # Native columns survived migration
    assert "account_pnl" in migrated.schema.names
    assert "leader_fraction" in migrated.schema.names
    assert "deposit_closed_reason" in migrated.schema.names
    assert migrated.num_rows == 4

    # Legacy __index_level_0__ is NOT present
    assert "__index_level_0__" not in migrated.schema.names

    # 4. Simulate the EVM rewrite: filter chain 1 rows, write back with new data.
    #    The writer schema must include native columns.
    import pyarrow.compute as pc

    # Remove chain 1 rows (they'll be "rescanned")
    kept = migrated.filter(pc.not_equal(migrated["chain"], 1))
    assert kept.num_rows == 2  # Only Hypercore rows remain

    # Build writer schema: canonical + extras from existing
    canonical_names = set(canonical_schema.names)
    extra_fields = [f for f in kept.schema if f.name not in canonical_names]
    writer_schema = canonical_schema
    for f in extra_fields:
        writer_schema = writer_schema.append(f)

    # Write: existing rows + new EVM rows (padded with null native columns)
    output_path = tmp_path / "rewritten.parquet"
    new_evm = pa.Table.from_pylist(
        [
            {"chain": 1, "address": "0xbbb", "block_number": 1000, "timestamp": pd.Timestamp("2025-01-01"), "share_price": 1.0, "total_assets": 5000.0, "total_supply": 5000.0, "performance_fee": 0.0, "management_fee": 0.02, "errors": ""},
            {"chain": 1, "address": "0xbbb", "block_number": 2000, "timestamp": pd.Timestamp("2025-01-02"), "share_price": 1.005, "total_assets": 5025.0, "total_supply": 5000.0, "performance_fee": 0.0, "management_fee": 0.02, "errors": ""},
        ],
        schema=canonical_schema,
    )
    # Pad new EVM rows with null native columns
    for field in writer_schema:
        if field.name not in new_evm.schema.names:
            new_evm = new_evm.append_column(field, pa.nulls(len(new_evm), type=field.type))

    with pq.ParquetWriter(str(output_path), writer_schema) as writer:
        writer.write_table(kept)
        writer.write_table(new_evm)

    # 5. Verify final result
    final = pq.read_table(output_path)
    assert final.num_rows == 4

    # Native protocol data intact for Hypercore rows
    hl_rows = final.filter(pc.equal(final["chain"], 9999))
    assert hl_rows.column("account_pnl").to_pylist() == [100.0, 110.0]
    assert hl_rows.column("leader_fraction").to_pylist() == [0.5, 0.5]

    # EVM rows have null for native columns
    evm_rows = final.filter(pc.equal(final["chain"], 1))
    assert all(v is None for v in evm_rows.column("account_pnl").to_pylist())
    assert all(v is None for v in evm_rows.column("leader_fraction").to_pylist())


def test_fix_outlier_ipor_tau_yield_bond_spike():
    """Fix asymmetric spike detection for IPOR TAU Yield Bond ETF vault.

    Reproduces two bugs in fix_outlier_share_prices():

    1. A real on-chain spike at block 24700201 (share price 1.455 vs normal ~1.057)
       was missed because the percentage formula |candidate/spike - 1| understated
       the deviation when the spike sat in the denominator
    2. The last data point (block 24822001, correct price 1.057) was corrupted to
       1.256 by averaging with the uncleaned spike, because the row-based lookback
       landed on the spike row and the alignment check had the same asymmetry

    Steps:

    1. Load the extracted uncleaned price fixture for this vault
    2. Prepare the dataframe with id column and timestamp index
    3. Run fix_outlier_share_prices on the data
    4. Assert the spike is cleaned to the average of its neighbours (~1.059)
    5. Assert the last data point is NOT corrupted (remains ~1.057)
    6. Assert raw_share_price preserves original values
    """

    # 1. Load the extracted uncleaned price fixture
    path = Path(os.path.dirname(__file__)) / "vault-ipor-tau-yield-bond-etf-prices-1h.parquet"
    df = pd.read_parquet(path)

    # 2. Prepare the dataframe
    df["id"] = df["chain"].astype(str) + "-" + df["address"]
    if "timestamp" in df.columns:
        df = df.set_index("timestamp")

    # 3. Run the outlier fixer
    result = fix_outlier_share_prices(df)

    # 4. The spike at block 24700201 should be cleaned to the average of its
    #    time-based neighbours (shift=2 for this vault's ~10h median interval)
    spike_row = result[result["block_number"] == 24700201]
    assert len(spike_row) == 1, "Spike row not found"
    assert spike_row["raw_share_price"].iloc[0] == pytest.approx(1.455, abs=0.01), "Raw spike value should be preserved"
    assert spike_row["share_price"].iloc[0] == pytest.approx(1.0535, abs=0.005), "Spike should be fixed to average of neighbours"

    # 5. The last data point (block 24822001) must NOT be corrupted
    last_row = result[result["block_number"] == 24822001]
    assert len(last_row) == 1, "Last row not found"
    assert last_row["share_price"].iloc[0] == pytest.approx(1.057, abs=0.005), "Last row must not be corrupted"
    assert last_row["raw_share_price"].iloc[0] == pytest.approx(1.057, abs=0.005), "Last row raw value should match"

    # 6. Normal rows should be unchanged
    normal_rows = result[(result["block_number"] != 24700201)]
    changed = normal_rows[normal_rows["share_price"] != normal_rows["raw_share_price"]]
    assert len(changed) == 0, f"Expected no changes to normal rows, but {len(changed)} rows were modified"
