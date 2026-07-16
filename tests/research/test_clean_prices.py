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

import eth_defi.research.wrangle_vault_prices as vault_price_wrangle
from eth_defi.research.wrangle_vault_prices import (
    approximate_hypercore_share_prices_from_pnl_nav,
    calculate_vault_returns,
    clean_by_tvl,
    clean_returns,
    discard_hypercore_pre_recapitalisation_history,
    fix_outlier_share_prices,
    generate_cleaned_vault_datasets,
    replace_cleaned_vault_histories,
)
from eth_defi.vault.base import VaultHistoricalRead
from eth_defi.vault.settlement_data import VaultSettlement, VaultSettlementDatabase
from eth_defi.version_info import PARQUET_VERSION_METADATA_KEY


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

    assert PARQUET_VERSION_METADATA_KEY in pq.read_metadata(dst).metadata


def test_clean_vault_price_data_with_settlement_markers(
    vault_db: Path,
    raw_price_df: Path,
    tmp_path: Path,
) -> None:
    """Settlement marker preservation works with the cleaner's timestamp index."""
    logger = logging.getLogger(__name__)
    baseline_dst = tmp_path / "baseline-cleaned-vault-prices.parquet"
    generate_cleaned_vault_datasets(
        vault_db_path=vault_db,
        price_df_path=raw_price_df,
        cleaned_price_df_path=baseline_dst,
        logger=logger.info,
    )

    baseline_prices = pd.read_parquet(baseline_dst)
    surviving_price = baseline_prices.iloc[0]
    surviving_timestamp = surviving_price["timestamp"] if "timestamp" in baseline_prices.columns else surviving_price.name

    settlement_db_path = tmp_path / "vault-settlements.duckdb"
    settlement_db = VaultSettlementDatabase(settlement_db_path)
    try:
        settlement_db.upsert_settlements(
            [
                VaultSettlement(
                    chain_id=int(surviving_price["chain"]),
                    address=str(surviving_price["address"]),
                    block_number=int(surviving_price["block_number"]),
                    protocol="test",
                    block_hash="0x" + "11" * 32,
                    timestamp=pd.Timestamp(surviving_timestamp).to_pydatetime(),
                    tx_hash="0x" + "22" * 32,
                    event_name="SettleDeposit",
                )
            ]
        )
    finally:
        settlement_db.close()

    dst = tmp_path / "cleaned-vault-prices.parquet"

    generate_cleaned_vault_datasets(
        vault_db_path=vault_db,
        price_df_path=raw_price_df,
        cleaned_price_df_path=dst,
        settlement_db_path=settlement_db_path,
        logger=logger.info,
    )

    df = pd.read_parquet(dst)
    assert "timestamp" in df.columns or df.index.name == "timestamp"
    assert "vault_settlement_at" in df.columns
    assert df["vault_settlement_at"].notna().any()


def test_replace_cleaned_vault_histories_preserves_unrelated_vaults(
    vault_db: Path,
    raw_price_df: Path,
    tmp_path: Path,
) -> None:
    """A targeted history repair does not re-clean or remove other vault ids."""
    cleaned_path = tmp_path / "cleaned-vault-prices.parquet"
    generate_cleaned_vault_datasets(
        vault_db_path=vault_db,
        price_df_path=raw_price_df,
        cleaned_price_df_path=cleaned_path,
    )
    before = pd.read_parquet(cleaned_path).reset_index(drop=True)
    target_id = str(before["id"].drop_duplicates().iloc[1])

    replaced_rows = replace_cleaned_vault_histories(
        {target_id},
        vault_db_path=vault_db,
        raw_price_df_path=raw_price_df,
        cleaned_price_df_path=cleaned_path,
    )
    after = pd.read_parquet(cleaned_path).reset_index(drop=True)

    assert replaced_rows == len(before[before["id"] == target_id])
    pd.testing.assert_frame_equal(after, before)


def test_replace_cleaned_vault_histories_rejects_vault_removed_by_cleaning(
    vault_db: Path,
    raw_price_df: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep existing history when cleaning would remove a selected vault.

    1. Create a normal cleaned price database and retain its contents.
    2. Mock the selected-vault cleaner to return no rows.
    3. Assert that replacement fails and the original Parquet remains intact.
    """

    # 1. Create a normal cleaned price database and retain its contents.
    cleaned_path = tmp_path / "cleaned-vault-prices.parquet"
    generate_cleaned_vault_datasets(
        vault_db_path=vault_db,
        price_df_path=raw_price_df,
        cleaned_price_df_path=cleaned_path,
    )
    before = pd.read_parquet(cleaned_path).reset_index(drop=True)
    target_id = str(before["id"].iloc[0])
    empty_cleaned_rows = pd.read_parquet(cleaned_path).iloc[0:0].copy()

    # 2. Mock the selected-vault cleaner to return no rows.
    monkeypatch.setattr(
        vault_price_wrangle,
        "process_raw_vault_scan_data",
        lambda *args, **kwargs: empty_cleaned_rows,
    )

    # 3. Assert that replacement fails and the original Parquet remains intact.
    with pytest.raises(ValueError, match="Cleaning removed all rows"):
        replace_cleaned_vault_histories(
            {target_id},
            vault_db_path=vault_db,
            raw_price_df_path=raw_price_df,
            cleaned_price_df_path=cleaned_path,
        )
    after = pd.read_parquet(cleaned_path).reset_index(drop=True)
    pd.testing.assert_frame_equal(after, before)


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


def test_approximate_hypercore_share_prices_from_pnl_nav() -> None:
    """PnL changes drive the clean index while capital flows leave it flat."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 4,
            "id": ["9999-0xeconomic"] * 4,
            "share_price": [1.0, 50.0, 0.01, 500.0],
            "total_assets": [100.0, 110.0, 200.0, 220.0],
            "total_supply": [100.0, 2.2, 20_000.0, 0.44],
            "account_pnl": [0.0, 10.0, 10.0, 30.0],
            "hypercore_source": ["daily"] * 4,
            "written_at": pd.to_datetime(["2026-01-05"] * 4),
        },
        index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]),
    )

    messages: list[str] = []
    result = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=messages.append)

    expected_prices = [1.0, 1.0 * (1 + 10.0 / 110.0), 1.0 * (1 + 10.0 / 110.0), 1.0 * (1 + 10.0 / 110.0) * (1 + 20.0 / 220.0)]
    assert result["share_price"].tolist() == pytest.approx(expected_prices)
    assert result["raw_share_price"].tolist() == prices_df["share_price"].tolist()
    assert (result["share_price"] * result["total_supply"]).tolist() == pytest.approx(result["total_assets"].tolist())
    assert result["hypercore_repair_status"].tolist() == ["approximated_pnl_nav"] * 4
    assert messages == ["Approximated Hypercore economic share prices for 1 vaults using 4 four-hour PnL/NAV checkpoints; carried 0 non-performance rows, repaired 0 delayed NAV confirmations, deferred 0 missing-input rows and 0 uncorroborated losses, capped 0 gains and recorded 0 terminal wipe-outs"]


def test_approximate_hypercore_repairs_delayed_nav_confirmation() -> None:
    """Fish Market's jittered PnL return moves to its merged daily NAV."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 5,
            "id": ["9999-0xfish-market"] * 5,
            "share_price": [0.180973] * 5,
            "total_assets": [1812.411674, 1812.411674, 3981.846633, 3981.846633, 5171.054434],
            "total_supply": [10_000.0] * 5,
            "account_pnl": [-11486.308326, -9316.873367, -9316.873367, -9316.873367, -8127.665566],
        },
        index=pd.to_datetime(["2026-03-16 23:12:00.091", "2026-03-17 23:32:00.042", "2026-03-18 00:00:00.000", "2026-03-18 22:32:00.010", "2026-03-19 00:00:00.000"]),
    )

    provisional = approximate_hypercore_share_prices_from_pnl_nav(prices_df.iloc[:2], logger=lambda _message: None)
    assert provisional["share_price"].tolist() == [1.0, 2.0]
    assert provisional["hypercore_repair_status"].iloc[-1] == "approximated_pnl_nav_clipped"

    messages: list[str] = []
    result = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=messages.append)

    confirmed_return = 2169.434959 / 3981.846633
    following_return = 1189.207801 / 5171.054434
    assert result["share_price"].tolist() == pytest.approx([1.0, 1.0, 1.0 + confirmed_return, 1.0 + confirmed_return, (1.0 + confirmed_return) * (1.0 + following_return)])
    assert result["hypercore_repair_status"].tolist() == [
        "approximated_pnl_nav",
        "approximated_pnl_nav_carried",
        "approximated_pnl_nav_lag_repaired",
        "approximated_pnl_nav",
        "approximated_pnl_nav",
    ]
    assert messages == ["Approximated Hypercore economic share prices for 1 vaults using 5 four-hour PnL/NAV checkpoints; carried 1 non-performance rows, repaired 1 delayed NAV confirmations, deferred 0 missing-input rows and 0 uncorroborated losses, capped 0 gains and recorded 0 terminal wipe-outs"]


def test_approximate_hypercore_skips_flat_delayed_nav_checkpoints() -> None:
    """Flat four-hour observations do not block a matching NAV confirmation."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 5,
            "id": ["9999-0xflat-delay"] * 5,
            "share_price": [1.0] * 5,
            "total_assets": [100.0, 100.0, 100.0, 100.0, 200.0],
            "total_supply": [100.0] * 5,
            "account_pnl": [0.0, 100.0, 100.0, 100.0, 100.0],
        },
        index=pd.to_datetime(["2026-01-01 00:00", "2026-01-01 04:00", "2026-01-01 08:00", "2026-01-01 12:00", "2026-01-01 16:00"]),
    )

    result = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=lambda _message: None)

    assert result["share_price"].tolist() == [1.0, 1.0, 1.0, 1.0, 1.5]
    assert result["hypercore_repair_status"].tolist() == [
        "approximated_pnl_nav",
        "approximated_pnl_nav_carried",
        "approximated_pnl_nav",
        "approximated_pnl_nav",
        "approximated_pnl_nav_lag_repaired",
    ]


def test_approximate_hypercore_does_not_join_weekly_checkpoints() -> None:
    """Similar weekly values remain separate because API lag is daily."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 3,
            "id": ["9999-0xweekly"] * 3,
            "share_price": [1.0] * 3,
            "total_assets": [100.0, 100.0, 200.0],
            "total_supply": [100.0] * 3,
            "account_pnl": [0.0, 100.0, 100.0],
        },
        index=pd.to_datetime(["2026-01-01", "2026-01-08", "2026-01-15"]),
    )

    result = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=lambda _message: None)

    assert result["share_price"].tolist() == [1.0, 2.0, 2.0]
    assert result["hypercore_repair_status"].tolist() == ["approximated_pnl_nav", "approximated_pnl_nav", "approximated_pnl_nav"]


def test_approximate_hypercore_does_not_retime_delayed_loss() -> None:
    """The Fish Market repair does not change loss recognition timing."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 3,
            "id": ["9999-0xloss"] * 3,
            "share_price": [1.0] * 3,
            "total_assets": [100.0, 100.0, 50.0],
            "total_supply": [100.0] * 3,
            "account_pnl": [0.0, -50.0, -50.0],
        },
        index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
    )

    result = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=lambda _message: None)

    assert result["share_price"].tolist() == [1.0, 0.5, 0.5]
    assert result["hypercore_repair_status"].tolist() == ["approximated_pnl_nav"] * 3


def test_approximate_hypercore_rejects_delayed_nav_mismatch() -> None:
    """A delayed NAV outside the strict tolerance is not joined to PnL."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 3,
            "id": ["9999-0xmismatch"] * 3,
            "share_price": [1.0] * 3,
            "total_assets": [100.0, 100.0, 198.0],
            "total_supply": [100.0] * 3,
            "account_pnl": [0.0, 100.0, 100.0],
        },
        index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
    )

    result = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=lambda _message: None)

    assert result["share_price"].tolist() == [1.0, 2.0, 2.0]
    assert result["hypercore_repair_status"].tolist() == ["approximated_pnl_nav"] * 3


def test_approximate_hypercore_uses_freshest_four_hour_checkpoint() -> None:
    """A fresher daily row wins over a stale HF row in the same UTC bucket."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 3,
            "id": ["9999-0xfresh"] * 3,
            "share_price": [1.0, 20.0, 30.0],
            "total_assets": [100.0, 110.0, 200.0],
            "total_supply": [100.0, 5.5, 200.0 / 30.0],
            "account_pnl": [0.0, 10.0, 100.0],
            "hypercore_source": ["daily", "daily", "hf"],
            "written_at": pd.to_datetime(["2026-01-01", "2026-01-04", "2026-01-03"]),
        },
        index=pd.to_datetime(["2026-01-01", "2026-01-02 01:00", "2026-01-02 02:00"], format="mixed"),
    )

    result = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=lambda _message: None)

    expected_price = 1 + 10.0 / 110.0
    assert result["share_price"].tolist() == pytest.approx([1.0, expected_price, expected_price])
    assert result["hypercore_repair_status"].tolist() == ["approximated_pnl_nav", "approximated_pnl_nav", "approximated_pnl_nav_carried"]


def test_approximate_hypercore_publishes_multiple_four_hour_checkpoints() -> None:
    """Each occupied four-hour bucket can add one economic price observation."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 5,
            "id": ["9999-0xfour-hour"] * 5,
            "share_price": [1.0] * 5,
            "total_assets": [100.0, 999.0, 110.0, 120.0, 130.0],
            "total_supply": [100.0] * 5,
            "account_pnl": [0.0, 999.0, 10.0, 20.0, 30.0],
            "hypercore_source": ["daily", "hf", "hf", "hf", "hf"],
            "written_at": pd.to_datetime(["2026-01-02", "2026-01-01", "2026-01-02", "2026-01-02", "2026-01-02"]),
        },
        index=pd.to_datetime(["2026-01-01 00:30", "2026-01-01 01:30", "2026-01-01 04:30", "2026-01-01 08:30", "2026-01-01 16:30"]),
    )

    result = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=lambda _message: None)

    first_return = 10.0 / 110.0
    second_return = 10.0 / 120.0
    third_return = 10.0 / 130.0
    expected_prices = [1.0, 1.0, 1.0 + first_return, (1.0 + first_return) * (1.0 + second_return), (1.0 + first_return) * (1.0 + second_return) * (1.0 + third_return)]
    assert result.index.tolist() == prices_df.index.tolist()
    assert result["share_price"].tolist() == pytest.approx(expected_prices)
    assert result["hypercore_repair_status"].tolist() == [
        "approximated_pnl_nav",
        "approximated_pnl_nav_carried",
        "approximated_pnl_nav",
        "approximated_pnl_nav",
        "approximated_pnl_nav",
    ]


@pytest.mark.parametrize(
    "timestamps",
    [
        ["2026-01-01 00:00", "2026-01-02 03:00", "2026-01-02 07:00"],
        ["2026-01-01 00:00", "2026-01-02 00:00", "2026-01-03 03:00"],
    ],
)
def test_approximate_hypercore_bounds_delayed_nav_confirmation(timestamps: list[str]) -> None:
    """Neither side of a delayed NAV repair may exceed the 26-hour window."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 3,
            "id": ["9999-0xdelay-boundary"] * 3,
            "share_price": [1.0] * 3,
            "total_assets": [100.0, 100.0, 200.0],
            "total_supply": [100.0] * 3,
            "account_pnl": [0.0, 100.0, 100.0],
        },
        index=pd.to_datetime(timestamps),
    )

    result = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=lambda _message: None)

    assert result["share_price"].tolist() == [1.0, 2.0, 2.0]
    assert result["hypercore_repair_status"].tolist() == ["approximated_pnl_nav"] * 3


def test_approximate_hypercore_does_not_skip_economic_change_for_nav_confirmation() -> None:
    """An intervening economic change disqualifies a later matching NAV."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 4,
            "id": ["9999-0xintervening-change"] * 4,
            "share_price": [1.0] * 4,
            "total_assets": [100.0, 100.0, 110.0, 200.0],
            "total_supply": [100.0] * 4,
            "account_pnl": [0.0, 100.0, 100.0, 100.0],
        },
        index=pd.to_datetime(["2026-01-01 00:00", "2026-01-02 00:00", "2026-01-02 04:00", "2026-01-02 08:00"]),
    )

    result = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=lambda _message: None)

    assert result["share_price"].tolist() == [1.0, 2.0, 2.0, 2.0]
    assert "approximated_pnl_nav_lag_repaired" not in result["hypercore_repair_status"].tolist()


def test_approximate_hypercore_applies_protections_per_four_hour_bucket() -> None:
    """Clips and funded-loss deferrals apply independently within one UTC date."""
    prices_df = pd.concat(
        [
            pd.DataFrame(
                {
                    "chain": [9999] * 3,
                    "id": ["9999-0xstacked-gains"] * 3,
                    "share_price": [1.0] * 3,
                    "total_assets": [100.0] * 3,
                    "total_supply": [100.0] * 3,
                    "account_pnl": [0.0, 200.0, 400.0],
                },
                index=pd.to_datetime(["2026-01-01 00:30", "2026-01-01 04:30", "2026-01-01 08:30"]),
            ),
            pd.DataFrame(
                {
                    "chain": [9999] * 3,
                    "id": ["9999-0xstacked-losses"] * 3,
                    "share_price": [1.0] * 3,
                    "total_assets": [100.0] * 3,
                    "total_supply": [100.0] * 3,
                    "account_pnl": [0.0, -150.0, -300.0],
                },
                index=pd.to_datetime(["2026-01-01 00:30", "2026-01-01 04:30", "2026-01-01 08:30"]),
            ),
        ]
    ).sort_values(["id"], kind="stable")

    result = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=lambda _message: None)
    gains = result[result["id"] == "9999-0xstacked-gains"]
    losses = result[result["id"] == "9999-0xstacked-losses"]

    assert gains["share_price"].tolist() == [1.0, 2.0, 4.0]
    assert gains["hypercore_repair_status"].tolist() == ["approximated_pnl_nav", "approximated_pnl_nav_clipped", "approximated_pnl_nav_clipped"]
    assert losses["share_price"].tolist() == [1.0, 1.0, 1.0]
    assert losses["hypercore_repair_status"].tolist() == ["approximated_pnl_nav", "deferred_pnl_nav_outlier", "deferred_pnl_nav_outlier"]


def test_approximate_hypercore_defers_missing_and_absorbing_losses() -> None:
    """Missing inputs and an uncorroborated loss carry a funded vault's price."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 4,
            "id": ["9999-0xdeferred"] * 4,
            "share_price": [1.0, 2.0, 3.0, 4.0],
            "total_assets": [100.0, np.nan, 50.0, 60.0],
            "total_supply": [100.0, 100.0, 100.0, 100.0],
            "account_pnl": [0.0, np.nan, -150.0, -150.0],
        },
        index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]),
    )

    result = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=lambda _message: None)

    assert result["share_price"].tolist() == [1.0] * 4
    assert result["hypercore_repair_status"].tolist() == ["approximated_pnl_nav", "deferred_pnl_nav", "deferred_pnl_nav_outlier", "approximated_pnl_nav"]


def test_approximate_hypercore_caps_gain_and_records_terminal_wipe_out() -> None:
    """Extreme gains are bounded and a terminal zero NAV can end the index."""
    prices_df = pd.concat(
        [
            pd.DataFrame(
                {
                    "chain": [9999, 9999],
                    "id": ["9999-0xgain"] * 2,
                    "share_price": [1.0, 50.0],
                    "total_assets": [100.0, 200.0],
                    "total_supply": [100.0, 4.0],
                    "account_pnl": [0.0, 500.0],
                },
                index=pd.to_datetime(["2026-01-01", "2026-01-02"]),
            ),
            pd.DataFrame(
                {
                    "chain": [9999, 9999],
                    "id": ["9999-0xwipe"] * 2,
                    "share_price": [1.0, 1.0],
                    "total_assets": [100.0, 0.0],
                    "total_supply": [100.0, 100.0],
                    "account_pnl": [0.0, -100.0],
                },
                index=pd.to_datetime(["2026-01-01", "2026-01-02"]),
            ),
        ]
    ).sort_values(["id"], kind="stable")

    result = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=lambda _message: None)
    gain = result[result["id"] == "9999-0xgain"]
    wipe = result[result["id"] == "9999-0xwipe"]

    assert gain["share_price"].tolist() == [1.0, 2.0]
    assert gain["hypercore_repair_status"].tolist() == ["approximated_pnl_nav", "approximated_pnl_nav_clipped"]
    assert wipe["share_price"].tolist() == [1.0, 0.0]
    assert wipe["total_supply"].tolist() == [100.0, 0.0]
    assert wipe["hypercore_repair_status"].tolist() == ["approximated_pnl_nav", "approximated_pnl_nav_wipe_out"]


def test_approximate_hypercore_carries_rows_after_terminal_wipe_out() -> None:
    """Later API baselines cannot add performance after a complete loss."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 4,
            "id": ["9999-0xterminal"] * 4,
            "share_price": [1.0] * 4,
            "total_assets": [100.0, 0.0, 0.0, 0.0],
            "total_supply": [100.0] * 4,
            "account_pnl": [0.0, -100.0, 500.0, -200.0],
        },
        index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]),
    )

    result = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=lambda _message: None)
    result = calculate_vault_returns(result, logger=lambda _message: None)

    assert result["share_price"].tolist() == [1.0, 0.0, 0.0, 0.0]
    assert result["returns_1h"].iloc[1:].tolist() == [-1.0, 0.0, 0.0]
    assert result["hypercore_repair_status"].tolist() == [
        "approximated_pnl_nav",
        "approximated_pnl_nav_wipe_out",
        "approximated_pnl_nav_carried",
        "approximated_pnl_nav_carried",
    ]


def test_approximate_hypercore_prevents_partial_repair_spike() -> None:
    """One repaired and one raw synthetic unit cannot create a clean return."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999, 9999, 9999],
            "id": ["9999-0xorder-block-hunter"] * 3,
            "share_price": [1.0, 0.860920, 3.231962],
            "total_assets": [100.0, 105.0, 100.0],
            "total_supply": [100.0, 105.0 / 0.860920, 100.0 / 3.231962],
            "account_pnl": [0.0, 5.0, 0.0],
            "hypercore_repair_status": ["", "repaired_hf", "deferred_hf_nav"],
        },
        index=pd.to_datetime(["2026-01-31", "2026-02-01", "2026-02-02"]),
    )

    result = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=lambda _message: None)

    assert result["share_price"].tolist() == pytest.approx([1.0, 1 + 5.0 / 105.0, (1 + 5.0 / 105.0) * (1 - 5.0 / 105.0)])
    assert result["share_price"].pct_change().iloc[-1] == pytest.approx(-5.0 / 105.0)
    assert result["raw_share_price"].tolist() == prices_df["share_price"].tolist()


def test_approximate_hypercore_is_idempotent_and_append_stable() -> None:
    """An identical rerun and a future append preserve earlier checkpoints."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999, 9999],
            "id": ["9999-0xstable"] * 2,
            "share_price": [10.0, 20.0],
            "total_assets": [100.0, 110.0],
            "total_supply": [10.0, 5.5],
            "account_pnl": [0.0, 10.0],
        },
        index=pd.to_datetime(["2026-01-01", "2026-01-02"]),
    )

    first = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=lambda _message: None)
    second = approximate_hypercore_share_prices_from_pnl_nav(first, logger=lambda _message: None)
    appended_input = pd.concat(
        [
            prices_df,
            pd.DataFrame(
                {
                    "chain": [9999],
                    "id": ["9999-0xstable"],
                    "share_price": [30.0],
                    "total_assets": [120.0],
                    "total_supply": [4.0],
                    "account_pnl": [20.0],
                },
                index=pd.to_datetime(["2026-01-03"]),
            ),
        ]
    )
    appended = approximate_hypercore_share_prices_from_pnl_nav(appended_input, logger=lambda _message: None)

    pd.testing.assert_series_equal(first["share_price"], second["share_price"])
    assert first["raw_share_price"].tolist() == second["raw_share_price"].tolist()
    assert appended["share_price"].iloc[:2].tolist() == first["share_price"].tolist()


def test_approximate_hypercore_future_recovery_revises_terminal_loss() -> None:
    """Later positive NAV can disprove a provisional terminal wipe-out."""
    initial = pd.DataFrame(
        {
            "chain": [9999, 9999],
            "id": ["9999-0xrecovery"] * 2,
            "share_price": [1.0, 1.0],
            "total_assets": [100.0, 0.0],
            "total_supply": [100.0, 0.0],
            "account_pnl": [0.0, -100.0],
        },
        index=pd.to_datetime(["2026-01-01", "2026-01-02"]),
    )
    recovered = pd.concat(
        [
            initial,
            pd.DataFrame(
                {
                    "chain": [9999],
                    "id": ["9999-0xrecovery"],
                    "share_price": [1.0],
                    "total_assets": [50.0],
                    "total_supply": [50.0],
                    "account_pnl": [-50.0],
                },
                index=pd.to_datetime(["2026-01-03"]),
            ),
        ]
    )

    provisional = approximate_hypercore_share_prices_from_pnl_nav(initial, logger=lambda _message: None)
    revised = approximate_hypercore_share_prices_from_pnl_nav(recovered, logger=lambda _message: None)

    assert provisional["hypercore_repair_status"].iloc[-1] == "approximated_pnl_nav_wipe_out"
    assert revised["hypercore_repair_status"].iloc[1] == "deferred_pnl_nav_outlier"
    assert revised["share_price"].iloc[1] == pytest.approx(1.0)


def test_approximate_hypercore_leaves_other_protocols_unchanged() -> None:
    """The Hypercore approximation does not alter another protocol's rows."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999, 1],
            "id": ["9999-0xhypercore", "1-0xevm"],
            "share_price": [20.0, 5.0],
            "total_assets": [100.0, 500.0],
            "total_supply": [5.0, 100.0],
            "account_pnl": [0.0, np.nan],
            "hypercore_repair_status": ["old", "evm-status"],
        },
        index=pd.to_datetime(["2026-01-01", "2026-01-01"]),
    )

    result = approximate_hypercore_share_prices_from_pnl_nav(prices_df, logger=lambda _message: None)
    evm = result[result["chain"] == 1].iloc[0]

    assert evm["share_price"] == pytest.approx(5.0)
    assert evm["total_supply"] == pytest.approx(100.0)
    assert evm["hypercore_repair_status"] == "evm-status"


def test_approximate_hypercore_rejects_invalid_configuration() -> None:
    """Invalid approximation inputs fail explicitly instead of publishing data."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999],
            "id": ["9999-0xinvalid"],
            "share_price": [1.0],
            "total_assets": [100.0],
            "account_pnl": [0.0],
        },
        index=pd.to_datetime(["2026-01-01"]),
    )

    with pytest.raises(ValueError, match="max_positive_return must be positive"):
        approximate_hypercore_share_prices_from_pnl_nav(prices_df, max_positive_return=0.0)

    with pytest.raises(ValueError, match=r"missing columns:.*cumulative_pnl"):
        approximate_hypercore_share_prices_from_pnl_nav(prices_df.drop(columns="account_pnl"))


def test_discard_hypercore_pre_recapitalisation_history() -> None:
    """A durable wipe-out starts a new cleaned Hypercore performance epoch."""
    recapitalised_id = "9999-0xrecapitalised"
    short_blip_id = "9999-0xshort-blip"
    evm_id = "1-0xevm"
    timestamps = pd.to_datetime(
        [
            "2026-01-01",  # Existing capital
            "2026-01-02",  # Zero-NAV epoch begins
            "2026-01-03",
            "2026-01-09",
            "2026-01-10",  # New capital exists, but below tracking threshold
            "2026-01-11",
            "2026-01-12",  # New epoch becomes meaningful
            "2026-01-13",
            "2026-01-01",  # Isolated zero blip must remain untouched
            "2026-01-02",
            "2026-01-03",
            "2026-01-01",  # Non-Hypercore data is untouched
        ]
    )
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 11 + [1],
            "id": [recapitalised_id] * 8 + [short_blip_id] * 3 + [evm_id],
            "total_assets": [2_000.0, 0.0, 0.0, 0.0, 100.0, 999.0, 1_000.0, 1_050.0, 2_000.0, 0.0, 1_500.0, 0.0],
            "share_price": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 10.0, 10.5, 1.0, 1.0, 1.0, 1.0],
        },
        index=timestamps,
    )

    messages: list[str] = []
    result = discard_hypercore_pre_recapitalisation_history(prices_df, logger=messages.append)

    recapitalised = result[result["id"] == recapitalised_id]
    assert recapitalised.index.tolist() == [pd.Timestamp("2026-01-12"), pd.Timestamp("2026-01-13")]
    assert recapitalised["epoch_reset"].tolist() == [True, False]
    assert recapitalised["raw_share_price"].tolist() == [10.0, 10.5]
    assert recapitalised["share_price"].tolist() == [10.0, 10.5]
    assert len(result[result["id"] == short_blip_id]) == 3
    assert len(result[result["id"] == evm_id]) == 1
    assert messages == ["Discarded 6 pre-recapitalisation Hypercore price rows across 1 vaults; new epochs start once NAV reaches $1,000 after 7 days 00:00:00"]


def test_discard_hypercore_history_measures_delay_to_first_positive_nav() -> None:
    """A prompt small deposit cannot become a delayed recapitalisation reset."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 4,
            "id": ["9999-0xprompt-recovery"] * 4,
            "total_assets": [2_000.0, 0.0, 900.0, 1_000.0],
            "share_price": [1.0] * 4,
        },
        index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-09"]),
    )

    result = discard_hypercore_pre_recapitalisation_history(prices_df, logger=lambda _message: None)

    assert result.index.tolist() == prices_df.index.tolist()
    assert result["epoch_reset"].tolist() == [False] * 4


def test_discard_hypercore_history_rebuilds_scanner_epoch_markers() -> None:
    """Synthetic scanner resets cannot split the cleaned economic index."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999, 9999, 9999, 1],
            "id": ["9999-0xfunded"] * 3 + ["1-0xevm"],
            "total_assets": [1_000.0, 1_100.0, 1_200.0, 100.0],
            "share_price": [1.0, 1.0, 1.0, 1.0],
            "epoch_reset": [False, True, False, True],
        },
        index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-01"]),
    )

    result = discard_hypercore_pre_recapitalisation_history(prices_df, logger=lambda _message: None)

    assert result[result["chain"] == 9999]["epoch_reset"].tolist() == [False, False, False]
    assert result[result["chain"] == 1]["epoch_reset"].tolist() == [True]


def test_clean_returns_keeps_large_hypercore_return() -> None:
    """The Hypercore economic index owns its bounded return cleaning."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999, 1],
            "name": ["Hypercore", "EVM"],
            "returns_1h": [1.0, 1.0],
        }
    )

    result = clean_returns({}, prices_df, logger=lambda _message: None)

    assert result["returns_1h"].tolist() == [1.0, 0.0]


def test_clean_by_tvl_keeps_hypercore_price_return_consistent() -> None:
    """Low NAV flags Hypercore suitability without rewriting its price return."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999, 1],
            "id": ["9999-0xhypercore", "1-0xevm"],
            "total_assets": [100.0, 100.0],
            "returns_1h": [0.25, 0.25],
        }
    )

    result = clean_by_tvl({}, prices_df, logger=lambda _message: None)

    assert result["returns_1h"].tolist() == [0.25, 0.0]
    assert result["tvl_filtering_mask"].tolist() == [True, True]


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
            "hypercore_source": ["daily", "hf", None, None],
        },
    )

    VaultHistoricalRead.write_uncleaned_parquet(native_df, parquet_path)

    assert PARQUET_VERSION_METADATA_KEY in pq.read_metadata(parquet_path).metadata

    # 2. Verify the written file has correct canonical types
    table = pq.read_table(parquet_path)
    assert table.schema.field("chain").type == pa.uint32()
    assert table.schema.field("block_number").type == pa.uint64()
    assert table.schema.field("timestamp").type == pa.timestamp("ms")
    # Native columns present
    assert "account_pnl" in table.schema.names
    assert "leader_fraction" in table.schema.names
    assert "deposit_closed_reason" in table.schema.names
    assert "hypercore_source" in table.schema.names

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
    assert "hypercore_source" in migrated.schema.names
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
    assert hl_rows.column("hypercore_source").to_pylist() == ["daily", "hf"]

    # EVM rows have null for native columns
    evm_rows = final.filter(pc.equal(final["chain"], 1))
    assert all(v is None for v in evm_rows.column("account_pnl").to_pylist())
    assert all(v is None for v in evm_rows.column("leader_fraction").to_pylist())
    assert all(v is None for v in evm_rows.column("hypercore_source").to_pylist())


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
