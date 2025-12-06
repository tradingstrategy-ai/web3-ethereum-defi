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
