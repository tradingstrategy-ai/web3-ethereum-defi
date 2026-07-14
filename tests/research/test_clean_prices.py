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
    clean_returns,
    discard_hypercore_pre_recapitalisation_history,
    fix_hypercore_flow_reconciled_share_price_paths,
    fix_hypercore_source_overlap_share_prices,
    fix_outlier_share_prices,
    generate_cleaned_vault_datasets,
    replace_cleaned_vault_histories,
    stitch_hypercore_high_freq_share_price_batches,
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


def test_fix_hypercore_source_overlap_share_prices() -> None:
    """Daily Hypercore excursions are repaired from overlapping HF anchors.

    The daily source contains one synthetic price spike between two stable HF
    observations. The wrangle repair must replace only that spike, preserve a
    plausible daily observation, and leave non-Hypercore data untouched.
    """
    hypercore_id = "9999-0xhypercore"
    evm_id = "1-0xevm"
    timestamps = pd.to_datetime(
        [
            "2026-02-01 12:00:00",
            "2026-02-02 00:00:00",
            "2026-02-03 00:00:00",
            "2026-02-04 12:00:00",
            "2026-02-02 00:00:00",
        ]
    )
    prices_df = pd.DataFrame(
        {
            "chain": [9999, 9999, 9999, 9999, 1],
            "id": [hypercore_id, hypercore_id, hypercore_id, hypercore_id, evm_id],
            "share_price": [1.0, 3.5, 1.1, 1.2, 4.0],
            "total_assets": [100.0, 105.0, 110.0, 120.0, 400.0],
            "hypercore_source": ["hf", "daily", "daily", "hf", pd.NA],
        },
        index=timestamps,
    )

    messages: list[str] = []
    result = fix_hypercore_source_overlap_share_prices(prices_df, logger=messages.append)

    hypercore_rows = result[result["id"] == hypercore_id]
    repaired = hypercore_rows.loc[pd.Timestamp("2026-02-02"), "share_price"]
    expected = np.exp(np.interp(pd.Timestamp("2026-02-02").value, [pd.Timestamp("2026-02-01 12:00:00").value, pd.Timestamp("2026-02-04 12:00:00").value], np.log([1.0, 1.2])))

    assert repaired == pytest.approx(expected)
    assert hypercore_rows.loc[pd.Timestamp("2026-02-02"), "raw_share_price"] == pytest.approx(3.5)
    assert hypercore_rows.loc[pd.Timestamp("2026-02-02"), "hypercore_repair_status"] == "repaired_hf"
    assert hypercore_rows.loc[pd.Timestamp("2026-02-03"), "share_price"] == pytest.approx(1.1)
    assert result[result["id"] == evm_id]["share_price"].iloc[0] == pytest.approx(4.0)
    assert messages == ["Repaired 1 conflicting daily Hypercore share prices across 1 vaults using HF anchors"]


def test_fix_hypercore_source_overlap_preserves_unbracketed_daily_rows() -> None:
    """Daily observations outside HF coverage cannot be safely repaired."""
    timestamps = pd.to_datetime(
        [
            "2026-01-01 00:00:00",
            "2026-02-01 12:00:00",
            "2026-02-04 12:00:00",
            "2026-03-01 00:00:00",
        ]
    )
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 4,
            "id": ["9999-0xhypercore"] * 4,
            "share_price": [10.0, 1.0, 1.2, 10.0],
            "total_assets": [100.0, 100.0, 120.0, 120.0],
            "hypercore_source": ["daily", "hf", "hf", "daily"],
        },
        index=timestamps,
    )

    result = fix_hypercore_source_overlap_share_prices(prices_df, logger=lambda _message: None)

    assert result["share_price"].tolist() == prices_df["share_price"].tolist()


def test_fix_hypercore_source_overlap_infers_legacy_sources() -> None:
    """Legacy Parquet rows infer daily/HF provenance from timestamp precision."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 3,
            "id": ["9999-0xhypercore"] * 3,
            "share_price": [1.0, 3.5, 1.2],
            "total_assets": [100.0, 110.0, 120.0],
        },
        index=pd.to_datetime(
            [
                "2026-02-01 12:01:02.003",
                "2026-02-02 00:00:00",
                "2026-02-03 12:01:02.003",
            ],
            format="mixed",
        ),
    )

    messages: list[str] = []
    result = fix_hypercore_source_overlap_share_prices(prices_df, logger=messages.append)

    assert result["hypercore_source"].tolist() == ["hf", "daily", "hf"]
    assert result.loc[pd.Timestamp("2026-02-02"), "share_price"] < 1.2
    assert result.loc[pd.Timestamp("2026-02-02"), "hypercore_repair_status"] == "repaired_hf"
    assert messages[0] == "Inferred Hypercore source provenance for 3 legacy price rows"
    assert messages[1] == "Repaired 1 conflicting daily Hypercore share prices across 1 vaults using HF anchors"


def test_fix_hypercore_source_overlap_uses_refreshed_daily_anchors() -> None:
    """Daily-only legacy vaults use the latest refresh batch as anchors.

    Older rolling-window rows may remain between canonical observations from
    the latest allTime refresh. The repair must interpolate only those stale
    rows and preserve every row in the latest refresh batch.
    """
    latest_refresh = pd.Timestamp("2026-04-10 17:10:32")
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 5,
            "id": ["9999-0xdaily-only"] * 5,
            "share_price": [1.0, 2.5, 1.1, 10.0, 1.2],
            "total_assets": [100.0, 103.0, 105.0, 108.0, 110.0],
            "hypercore_source": ["daily"] * 5,
            "written_at": [latest_refresh, pd.NaT, latest_refresh, pd.Timestamp("2026-02-05"), latest_refresh],
        },
        index=pd.to_datetime(
            [
                "2026-01-21",
                "2026-01-25",
                "2026-01-28",
                "2026-02-01",
                "2026-02-04",
            ]
        ),
    )

    messages: list[str] = []
    result = fix_hypercore_source_overlap_share_prices(prices_df, logger=messages.append)

    assert result.loc[pd.Timestamp("2026-01-25"), "share_price"] < 1.1
    assert result.loc[pd.Timestamp("2026-02-01"), "share_price"] < 1.2
    assert result.loc[pd.Timestamp("2026-01-25"), "hypercore_repair_status"] == "repaired_daily"
    assert result.loc[pd.Timestamp("2026-01-25"), "raw_share_price"] == pytest.approx(2.5)
    assert result.loc[prices_df["written_at"] == latest_refresh, "share_price"].tolist() == [1.0, 1.1, 1.2]
    assert messages == ["Repaired 2 stale daily Hypercore share prices across 1 vaults using refreshed daily anchors"]


def test_fix_hypercore_source_overlap_preserves_daily_lifecycle_change() -> None:
    """Daily fallback does not interpolate across a genuine NAV boundary."""
    latest_refresh = pd.Timestamp("2026-04-10 17:10:32")
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 3,
            "id": ["9999-0xlifecycle"] * 3,
            "share_price": [1.0, 10.0, 1.1],
            "total_assets": [100.0, 1_000.0, 110.0],
            "hypercore_source": ["daily"] * 3,
            "written_at": [latest_refresh, pd.NaT, latest_refresh],
        },
        index=pd.to_datetime(["2026-01-21", "2026-01-25", "2026-01-28"]),
    )

    result = fix_hypercore_source_overlap_share_prices(prices_df, logger=lambda _message: None)

    assert result.loc[pd.Timestamp("2026-01-25"), "share_price"] == pytest.approx(10.0)
    assert result.loc[pd.Timestamp("2026-01-25"), "hypercore_repair_status"] == "deferred_daily_nav"


def test_fix_hypercore_source_overlap_defers_unsafe_hf_repairs() -> None:
    """HF candidates remain raw when NAV, gap, or boundary evidence is unsafe."""
    vault_ids = ["9999-0xnav", "9999-0xgap", "9999-0xepoch", "9999-0xzero"]
    frames = [
        pd.DataFrame(
            {
                "chain": [9999] * 3,
                "id": [vault_ids[0]] * 3,
                "share_price": [1.0, 10.0, 1.1],
                "total_assets": [100.0, 1_000.0, 110.0],
                "hypercore_source": ["hf", "daily", "hf"],
                "epoch_reset": [False] * 3,
            },
            index=pd.to_datetime(["2026-01-01 12:00", "2026-01-02", "2026-01-03 12:00"], format="mixed"),
        ),
        pd.DataFrame(
            {
                "chain": [9999] * 4,
                "id": [vault_ids[3]] * 4,
                "share_price": [1.0, 10.0, 1.05, 1.1],
                "total_assets": [100.0, 105.0, 0.0, 110.0],
                "hypercore_source": ["hf", "daily", "daily", "hf"],
                "epoch_reset": [False] * 4,
            },
            index=pd.to_datetime(["2026-01-01 12:00", "2026-01-02", "2026-01-02 12:00", "2026-01-03 12:00"], format="mixed"),
        ),
        pd.DataFrame(
            {
                "chain": [9999] * 3,
                "id": [vault_ids[1]] * 3,
                "share_price": [1.0, 10.0, 1.1],
                "total_assets": [100.0, 105.0, 110.0],
                "hypercore_source": ["hf", "daily", "hf"],
                "epoch_reset": [False] * 3,
            },
            index=pd.to_datetime(["2026-01-01 12:00", "2026-01-08", "2026-01-11 12:00"], format="mixed"),
        ),
        pd.DataFrame(
            {
                "chain": [9999] * 3,
                "id": [vault_ids[2]] * 3,
                "share_price": [1.0, 10.0, 1.1],
                "total_assets": [100.0, 105.0, 110.0],
                "hypercore_source": ["hf", "daily", "hf"],
                "epoch_reset": [False, True, False],
            },
            index=pd.to_datetime(["2026-01-01 12:00", "2026-01-02", "2026-01-03 12:00"], format="mixed"),
        ),
    ]
    prices_df = pd.concat(frames).sort_index(kind="stable")

    result = fix_hypercore_source_overlap_share_prices(prices_df, logger=lambda _message: None)

    for vault_id, expected_status in zip(vault_ids, ["deferred_hf_nav", "deferred_hf_gap", "deferred_hf_boundary", "deferred_hf_boundary"]):
        candidate = result[(result["id"] == vault_id) & (result["hypercore_source"] == "daily")].iloc[0]
        assert candidate["share_price"] == pytest.approx(10.0)
        assert candidate["raw_share_price"] == pytest.approx(10.0)
        assert candidate["hypercore_repair_status"] == expected_status


def test_fix_hypercore_flow_reconciled_share_price_paths() -> None:
    """Ledger-proven withdrawals repair a synthetic daily price unit.

    The daily price of 20.0 conflicts with both HF anchors, but its $20
    withdrawal and -$10 PnL exactly explain the NAV change. The repair must
    follow PnL, preserve the raw value, and rescale a non-zero derived supply.
    """
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 4,
            "id": ["9999-0xflow-reconciled"] * 4,
            "hypercore_source": ["hf", "daily", "daily", "hf"],
            "share_price": [1.0, 20.0, 1.3, 1.0],
            "total_assets": [100.0, 70.0, 80.0, 80.0],
            "account_pnl": [0.0, -10.0, 0.0, 0.0],
            "daily_deposit_usd": [np.nan, 0.0, 0.0, np.nan],
            "daily_withdrawal_usd": [np.nan, 20.0, 0.0, np.nan],
            "total_supply": [100.0, 5.0, 5.0, 100.0],
        },
        index=pd.to_datetime(["2026-01-01 12:00", "2026-01-02", "2026-01-03", "2026-01-04 12:00"], format="mixed"),
    )

    messages: list[str] = []
    result = fix_hypercore_flow_reconciled_share_price_paths(prices_df, logger=messages.append)

    first_daily = pd.Timestamp("2026-01-02")
    second_daily = pd.Timestamp("2026-01-03")
    endpoint_correction = np.log(1.0 / (0.9 * (1 + 10.0 / 70.0)))
    assert result.loc[first_daily, "raw_share_price"] == pytest.approx(20.0)
    assert result.loc[first_daily, "share_price"] == pytest.approx(0.9 * np.exp(endpoint_correction / 6))
    assert result.loc[second_daily, "share_price"] == pytest.approx(1.3)
    assert result.loc[first_daily, "hypercore_repair_status"] == "repaired_hf_pnl_flow"
    assert result.loc[second_daily, "hypercore_repair_status"] == ""
    assert result.loc[first_daily, "total_supply"] > 100.0
    assert messages == ["Repaired 1 flow-reconciled Hypercore daily prices across 1 vaults using PnL paths"]


def test_fix_hypercore_flow_reconciled_paths_require_hf_endpoint_agreement() -> None:
    """A PnL path that misses its HF endpoint remains available for fallback."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 3,
            "id": ["9999-0xendpoint"] * 3,
            "hypercore_source": ["hf", "daily", "hf"],
            "share_price": [1.0, 20.0, 2.0],
            "total_assets": [100.0, 70.0, 70.0],
            "account_pnl": [0.0, -10.0, -10.0],
            "daily_deposit_usd": [np.nan, 0.0, np.nan],
            "daily_withdrawal_usd": [np.nan, 20.0, np.nan],
        },
        index=pd.to_datetime(["2026-01-01 12:00", "2026-01-02", "2026-01-03 12:00"], format="mixed"),
    )

    result = fix_hypercore_flow_reconciled_share_price_paths(prices_df, logger=lambda _message: None)

    assert result.loc[pd.Timestamp("2026-01-02"), "share_price"] == pytest.approx(20.0)
    assert result.loc[pd.Timestamp("2026-01-02"), "hypercore_repair_status"] == ""


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
    assert recapitalised["share_price"].iloc[0] == pytest.approx(1.0)
    assert recapitalised["share_price"].iloc[1] == pytest.approx(1.05)
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


def test_stitch_hypercore_high_freq_share_price_batches() -> None:
    """A scanner batch unit jump follows PnL, not a 50x investor return."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999] * 3,
            "id": ["9999-0xstitch"] * 3,
            "hypercore_source": ["hf"] * 3,
            "written_at": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-02"]),
            "total_assets": [100.0, 101.0, 102.01],
            # Exported Hypercore parquet calls this cumulative metric account_pnl.
            "account_pnl": [0.0, 1.0, 2.01],
            "share_price": [1.0, 50.0, 50.5],
            "total_supply": [100.0, 2.02, 2.02],
        },
        index=pd.to_datetime(["2026-01-01 12:00", "2026-01-01 13:00", "2026-01-01 14:00"]),
    )

    result = stitch_hypercore_high_freq_share_price_batches(prices_df, logger=lambda _message: None)

    assert result["share_price"].tolist() == pytest.approx([1.0, 1.01, 1.0201])
    assert result["total_supply"].tolist() == pytest.approx([100.0, 100.0, 100.0])
    assert result["hypercore_repair_status"].tolist() == ["", "repaired_hf_batch_scale", ""]


def test_stitch_hypercore_batch_keeps_pnl_supported_return() -> None:
    """A genuine 50 percent PnL gain must not be mistaken for a unit jump."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999, 9999],
            "id": ["9999-0xreal-gain"] * 2,
            "hypercore_source": ["hf", "hf"],
            "written_at": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "total_assets": [100.0, 150.0],
            "cumulative_pnl": [0.0, 50.0],
            "share_price": [1.0, 1.5],
        },
        index=pd.to_datetime(["2026-01-01 12:00", "2026-01-01 13:00"]),
    )

    result = stitch_hypercore_high_freq_share_price_batches(prices_df, logger=lambda _message: None)

    assert result["share_price"].tolist() == [1.0, 1.5]
    assert result["hypercore_repair_status"].tolist() == ["", ""]


def test_clean_returns_keeps_large_hypercore_return() -> None:
    """Hypercore continuity repairs own synthetic prices before generic cleaning."""
    prices_df = pd.DataFrame(
        {
            "chain": [9999, 1],
            "name": ["Hypercore", "EVM"],
            "returns_1h": [1.0, 1.0],
        }
    )

    result = clean_returns({}, prices_df, logger=lambda _message: None)

    assert result["returns_1h"].tolist() == [1.0, 0.0]


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
