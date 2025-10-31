"""Test vault metrics calculations and charts."""

import json
import os.path
import pickle
from pathlib import Path

import pandas as pd
import pytest

from plotly.graph_objects import Figure
import zstandard as zstd

from eth_defi.research.vault_benchmark import visualise_vault_return_benchmark
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.risk import VaultTechnicalRisk
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.research.vault_metrics import calculate_lifetime_metrics, display_vault_chart_and_tearsheet, format_lifetime_table, export_lifetime_row


@pytest.fixture(scope="module")
def vault_db() -> VaultDatabase:
    """Load sample vault database for testing.

    To generate:

    .. code-block:: shell

        zstd -22 --ultra -f -o tests/research/vault-metadata-db.pickle.zstd ~/.tradingstrategy/vaults/vault-metadata-db.pickle

    """
    path = Path(os.path.dirname(__file__)) / "vault-metadata-db.pickle.zstd"
    with zstd.open(path, "rb") as f:
        return pickle.load(f)


@pytest.fixture(scope="module")
def price_df() -> pd.DataFrame:
    """Load price data for testing.

    - Use a small sample of Hemi chain data taken with extract-single-chain.py
    """

    path = Path(os.path.dirname(__file__)) / "chain-hemi-prices-1h.parquet"
    return pd.read_parquet(path)


def test_calculate_lifetime_metrics(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
):
    """Test lifetime metrics calculation."""

    hemi_vaults = [row for row in vault_db.values() if row["_detection_data"].chain == 43111]
    assert len(hemi_vaults) > 0, "No Hemi vaults found in test data"

    ids = price_df["id"].unique()
    assert set(ids) == {"43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883", "43111-0x614eb485de3c6c49701b40806ac1b985ad6f0a2f", "43111-0x1324285bb2ddadfc9bebc2f8fc5049d7985312c0"}

    metrics = calculate_lifetime_metrics(
        price_df,
        vault_db,
    )

    # We should get data for 4 vaults
    assert len(metrics) == 3

    sample_row = metrics.set_index("id").loc["43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883"]
    assert sample_row["chain"] == "Hemi"
    assert sample_row["years"] == pytest.approx(0.11225188227241616)
    assert sample_row["name"] == "Clearstar USDC.e"
    assert sample_row["perf_fee"] == 0.15
    assert sample_row["mgmt_fee"] == 0
    assert sample_row["deposit_fee"] == 0
    assert sample_row["withdraw_fee"] == 0
    assert sample_row["risk"] == VaultTechnicalRisk.negligible
    assert sample_row["current_nav"] == pytest.approx(2345373.103418)
    assert sample_row["fee_label"] == "0% / 15%"

    assert sample_row["lifetime_return"] == pytest.approx(0.002758)
    assert sample_row["cagr"] == pytest.approx(0.02483940718068034)
    assert sample_row["cagr_net"] == pytest.approx(0.02107892820280277)

    assert sample_row["three_months_cagr"] == pytest.approx(0.02483940718068034)
    assert sample_row["three_months_cagr_net"] == pytest.approx(0.02107892820280277)
    assert sample_row["three_months_sharpe"] == pytest.approx(12.09936086372036)
    assert sample_row["three_months_sharpe_net"] == pytest.approx(12.09936086372036)

    assert sample_row["one_month_returns"] == pytest.approx(0.0017492385168136337)
    assert sample_row["one_month_returns_net"] == pytest.approx(0.0014868527392914999)
    assert sample_row["one_month_cagr"] == pytest.approx(0.02225616485623605)
    assert sample_row["one_month_cagr_net"] == pytest.approx(0.018888926446635645)

    # We can get human readable output
    formatted = format_lifetime_table(
        metrics,
        add_index=True,
        add_address=True,
    )
    assert len(formatted) == 3


def test_vault_charts(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
):
    """Draw vault chart figures."""

    spec = VaultSpec.parse_string("43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883")
    display_vault_chart_and_tearsheet(
        spec,
        prices_df=price_df,
        vault_db=vault_db,
        render=False,
    )


def test_vault_benchmark(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
):
    """Draw the vault chart benchmark chart.

    - Only 1 vault to benchmark
    """

    spec = VaultSpec.parse_string("43111-0x05c2e246156d37b39a825a25dd08d5589e3fd883")
    fig, df = visualise_vault_return_benchmark(
        [spec],
        prices_df=price_df,
        vault_db=vault_db,
    )
    assert isinstance(fig, Figure)
    assert isinstance(df, pd.DataFrame)


def test_export_lifetime_metrics(
    vault_db: VaultDatabase,
    price_df: pd.DataFrame,
):
    """Export lifetimemetrics for the frontend"""

    metrics = calculate_lifetime_metrics(
        price_df,
        vault_db,
    )
    rows = [export_lifetime_row(r) for _, r in metrics.iterrows()]
    # Ensure everything is JSON serializable
    json.dumps(rows)

    r = rows[0]
    assert r["name"]  == "Clearstar USDC.e"
    assert r["chain"] == "Hemi"

