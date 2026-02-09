"""Clean vault price data"""

import logging
import os.path
import pickle
from pathlib import Path

import pandas as pd
import pytest

import zstandard as zstd

from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets


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
