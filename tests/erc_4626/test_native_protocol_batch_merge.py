"""Tests for batched native-protocol Parquet price merging."""

import datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pyarrow as pa
import pytest

from eth_defi.apex.constants import APEX_CHAIN_ID
from eth_defi.grvt.constants import GRVT_CHAIN_ID
from eth_defi.hibachi.constants import HIBACHI_CHAIN_ID
from eth_defi.hyperliquid import vault_data_export
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from eth_defi.hyperliquid.high_freq_metrics import HyperliquidHighFreqMetricsDatabase, HyperliquidHighFreqPriceRow
from eth_defi.hyperliquid.permission import PERMISSION_FILENAME, PermissionObservation, append_permission_observation
from eth_defi.hyperliquid.vault_review_sync import ReviewStatus
from eth_defi.lighter.constants import LIGHTER_CHAIN_ID, LIGHTER_LEGACY_ROBINHOOD_CHAIN_ID
from eth_defi.vault import base, post_processing
from eth_defi.vault.base import ParquetVerificationError, VaultHistoricalRead, VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


def _prices(chain: int, address: str, timestamp: str) -> pd.DataFrame:
    """Create a minimal native-protocol price frame for merge tests.

    The canonical Parquet writer fills only the columns present in the source
    frame, which is sufficient to exercise replacement and atomic-write
    behaviour without coupling this test to every raw price field.

    :param chain:
        Chain partition for the test row.
    :param address:
        Synthetic vault address.
    :param timestamp:
        ISO timestamp for deterministic sorting.
    :return:
        One-row raw price DataFrame.
    """
    return pd.DataFrame(
        {
            "chain": pd.array([chain], dtype="uint32"),
            "address": [address],
            "timestamp": pd.to_datetime([timestamp]),
            "share_price": [1.0],
        }
    )


def test_hypercore_batch_exports_permissions_and_restores_missing_catalogue(tmp_path: Path) -> None:
    """Retained HF vaults and between-price permissions survive a normal export.

    Exercise both file-backed scanner owners through the production batch
    merger. Existing curated metadata must survive, even when the retained HF
    catalogue disagrees; restoring absent entries must not invent prices.

    :param tmp_path: Isolated scanner and output directory.
    :return: None; checks persisted prices, source receipts and shared metadata.
    """
    daily_path = tmp_path / "daily.duckdb"
    hf_path = tmp_path / "hf.duckdb"
    parquet_path = tmp_path / "vault-prices-1h.parquet"
    metadata_path = tmp_path / "custom-metadata.pickle"
    address = "0x" + "a" * 40
    first = datetime.datetime(2026, 9, 21, 10)
    spec, row = vault_data_export.create_hyperliquid_vault_row(address, "Curated name", description=None, tvl=100_000, create_time=first)
    row["_manual_review_status"] = ReviewStatus.avoid
    row["_description"] = "Retained human note"
    existing = VaultDatabase()
    existing.rows[spec] = row
    existing.write(metadata_path)
    daily = HyperliquidDailyMetricsDatabase(daily_path)
    hf = HyperliquidHighFreqMetricsDatabase(hf_path)
    missing_address = "0x" + "b" * 40
    try:
        for vault_address in (address, missing_address):
            hf.upsert_vault_metadata(vault_address=vault_address, name="HF catalogue", leader="0x" + "c" * 40, description=None, is_closed=False, allow_deposits=True, relationship_type="normal", create_time=first, commission_rate=None, follower_count=None, tvl=100_000, apr=None)
            hf.upsert_high_freq_prices([HyperliquidHighFreqPriceRow(vault_address, first, 1, 100_000, 0), HyperliquidHighFreqPriceRow(vault_address, first + datetime.timedelta(hours=2), 1.1, 110_000, 1)])
        receipt = first + datetime.timedelta(hours=1, seconds=7)
        shared = PermissionObservation("open", address, permission_observed_at=first, is_closed=False, allow_deposits=True, provenance="observed")
        append_permission_observation(daily.con, shared)
        append_permission_observation(hf.con, shared)
        append_permission_observation(hf.con, PermissionObservation("closed", address, permission_observed_at=receipt, is_closed=False, allow_deposits=False, provenance="observed"))
        append_permission_observation(hf.con, PermissionObservation("unknown", missing_address, permission_observed_at=receipt, provenance="observed_unknown"))
    finally:
        daily.close()
        hf.close()

    steps = post_processing.merge_native_protocols(merge_hypercore=True, uncleaned_parquet_path=parquet_path, hyperliquid_db_path=daily_path, hyperliquid_hf_db_path=hf_path, vault_db_path=metadata_path)
    assert steps["hypercore-price-merge"]
    permissions = pd.read_parquet(tmp_path / PERMISSION_FILENAME).set_index("observation_id")
    assert set(permissions.index) == {"open", "closed", "unknown"}
    assert permissions.loc["closed", "permission_observed_at"] == receipt
    assert pd.isna(permissions.loc["unknown", "allow_deposits"])
    prices = pd.read_parquet(parquet_path)
    assert len(prices) == 4
    assert receipt not in set(prices.timestamp)
    assert prices.loc[prices.address == address].sort_values("timestamp").deposits_open.tolist() == ["true", "false"]
    assert prices.loc[prices.address == missing_address].deposits_open.isna().all()
    metadata = VaultDatabase.read(metadata_path)
    assert metadata.rows[spec]["Name"] == "Curated name"
    assert metadata.rows[spec]["_description"] == "Retained human note"
    assert metadata.rows[spec]["_manual_review_status"] is ReviewStatus.avoid
    assert metadata.rows[VaultSpec(HYPERCORE_CHAIN_ID, missing_address)]["Name"] == "HF catalogue"
    previous_bytes = metadata_path.read_bytes()
    post_processing.merge_native_protocols(merge_hypercore=True, uncleaned_parquet_path=parquet_path, hyperliquid_db_path=daily_path, hyperliquid_hf_db_path=hf_path, vault_db_path=metadata_path)
    assert metadata_path.read_bytes() == previous_bytes


def test_hypercore_permission_failure_preserves_prices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An incomplete Hypercore export retains the previous prices and closes owners.

    Fail permission export with a real existing Parquet file.
    The merger must report the failure without replacing historical prices
    with a snapshot whose catalogue or permission export did not complete.

    :param tmp_path: Isolated Parquet and scanner paths.
    :param monkeypatch: Replace scanner reads and inject the selected failure.
    :return: None; checks byte preservation and owner closure.
    """
    parquet_path = tmp_path / "vault-prices-1h.parquet"
    VaultHistoricalRead.write_uncleaned_parquet(_prices(HYPERCORE_CHAIN_ID, "retained", "2026-09-21"), parquet_path)
    previous = parquet_path.read_bytes()
    hf_path = tmp_path / "hf.duckdb"
    hf_path.touch()
    closed: list[bool] = []
    monkeypatch.setattr(post_processing, "HyperliquidHighFreqMetricsDatabase", lambda _: SimpleNamespace(close=lambda: closed.append(True)))
    monkeypatch.setattr(post_processing, "build_hypercore_prices_dataframe", lambda **_: _prices(HYPERCORE_CHAIN_ID, "fresh", "2026-09-22"))
    monkeypatch.setattr(post_processing, "merge_into_vault_database", lambda *_, **__: None)

    def fail(*_: object, **__: object) -> None:
        """Reject the current permission evidence for this test."""
        message = "Rejected evidence"
        raise ValueError(message)

    monkeypatch.setattr(post_processing, "export_hypercore_permission_history", fail)
    steps = post_processing.merge_native_protocols(merge_hypercore=True, uncleaned_parquet_path=parquet_path, hyperliquid_db_path=tmp_path / "missing-daily.duckdb", hyperliquid_hf_db_path=hf_path)
    assert steps["hypercore-price-merge"] is False
    assert parquet_path.read_bytes() == previous
    assert closed == [True]


def test_native_price_freshness_uses_source_timestamp_and_tvl_hysteresis() -> None:
    """A recent file write cannot make an old meaningful source row fresh."""
    frame = pd.concat(
        [
            _prices(GRVT_CHAIN_ID, "stale", "2026-01-01"),
            _prices(GRVT_CHAIN_ID, "fresh", "2026-01-30"),
            _prices(GRVT_CHAIN_ID, "tiny", "2026-01-01"),
            _prices(GRVT_CHAIN_ID, "falling", "2026-01-01"),
            _prices(GRVT_CHAIN_ID, "falling", "2026-01-15"),
            _prices(GRVT_CHAIN_ID, "missing-price", "2026-01-30"),
        ],
        ignore_index=True,
    )
    frame["total_assets"] = [2_000.0, 2_000.0, 900.0, 1_600.0, 1_200.0, 2_000.0]
    frame.loc[frame["address"] == "missing-price", "share_price"] = float("nan")
    frame["written_at"] = pd.Timestamp("2026-01-31")

    overdue = post_processing.audit_native_price_freshness(
        pa.Table.from_pandas(frame, preserve_index=False),
        datetime.datetime(2026, 1, 31),
    )

    assert overdue == {
        f"{GRVT_CHAIN_ID}-stale": "stale_source_timestamp",
        f"{GRVT_CHAIN_ID}-falling": "stale_source_timestamp",
        f"{GRVT_CHAIN_ID}-missing-price": "no_valid_source_observation",
    }


def test_merge_native_protocols_rewrites_parquet_once_and_preserves_empty_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batch successful native sources and retain the prior empty-source partition.

    1. Create raw prices with EVM and stale native-protocol rows.
    2. Stub Hypercore, GRVT, Lighter, and ApeX exports with fresh data and
       Hibachi with no data.
    3. Merge all five sources and count Parquet writes.
    4. Assert one write replaced fresh partitions, retained stale Hibachi
       data, and append-and-corrected ApeX history.
    """
    parquet_path = tmp_path / "vault-prices-1h.parquet"
    existing_apex_overlap = _prices(APEX_CHAIN_ID, "apex-vault-new", "2025-01-02")
    existing_apex_overlap["share_price"] = 0.9
    existing_df = pd.concat(
        [
            _prices(1, "0xevm", "2025-01-01"),
            _prices(HYPERCORE_CHAIN_ID, "hypercore-old", "2025-01-01"),
            _prices(GRVT_CHAIN_ID, "grvt-old", "2025-01-01"),
            _prices(LIGHTER_CHAIN_ID, "lighter-pool-100", "2025-01-01"),
            _prices(LIGHTER_CHAIN_ID, "lighter-pool-robinhood-100", "2025-01-01"),
            _prices(LIGHTER_LEGACY_ROBINHOOD_CHAIN_ID, "lighter-robinhood-legacy-old", "2025-01-01"),
            _prices(HIBACHI_CHAIN_ID, "hibachi-old", "2025-01-01"),
            _prices(APEX_CHAIN_ID, "apex-vault-old", "2025-01-01"),
            existing_apex_overlap,
        ],
        ignore_index=True,
    )
    VaultHistoricalRead.write_uncleaned_parquet(existing_df, parquet_path)

    class FakeDatabase:
        """Provide the close method required by the post-processing pipeline."""

        def __init__(self, path: Path) -> None:
            self.path = path

        def close(self) -> None:
            pass

    fresh_grvt = _prices(GRVT_CHAIN_ID, "grvt-new", "2025-01-02")
    fresh_lighter = pd.concat(
        [
            _prices(LIGHTER_CHAIN_ID, "lighter-pool-200", "2025-01-02"),
            _prices(LIGHTER_CHAIN_ID, "lighter-pool-robinhood-200", "2025-01-02"),
        ],
        ignore_index=True,
    )
    fresh_hypercore = _prices(HYPERCORE_CHAIN_ID, "hypercore-new", "2025-01-02")
    fresh_hypercore["account_pnl"] = 123.0
    fresh_apex = _prices(APEX_CHAIN_ID, "apex-vault-new", "2025-01-02")
    monkeypatch.setattr(post_processing, "HyperliquidDailyMetricsDatabase", FakeDatabase)
    monkeypatch.setattr(post_processing, "HyperliquidHighFreqMetricsDatabase", FakeDatabase)
    monkeypatch.setattr(post_processing, "GRVTDailyMetricsDatabase", FakeDatabase)
    monkeypatch.setattr(post_processing, "LighterDailyMetricsDatabase", FakeDatabase)
    monkeypatch.setattr(post_processing, "HibachiDailyMetricsDatabase", FakeDatabase)
    monkeypatch.setattr(post_processing, "ApexMetricsDatabase", FakeDatabase)
    monkeypatch.setattr(post_processing, "build_grvt_prices_dataframe", lambda _: fresh_grvt)
    monkeypatch.setattr(post_processing, "build_lighter_prices_dataframe", lambda _: fresh_lighter)
    monkeypatch.setattr(post_processing, "build_hibachi_prices_dataframe", lambda _: pd.DataFrame())
    monkeypatch.setattr(post_processing, "build_hypercore_prices_dataframe", lambda **_: fresh_hypercore)
    monkeypatch.setattr(post_processing, "export_hypercore_permission_history", lambda *_, **__: None)
    monkeypatch.setattr(post_processing, "merge_into_vault_database", lambda *_, **__: None)
    monkeypatch.setattr(post_processing, "build_apex_prices_dataframe", lambda _: fresh_apex)
    perp_snapshot_paths: list[Path] = []
    monkeypatch.setattr(post_processing, "_append_perp_metric_snapshots", lambda database, _: perp_snapshot_paths.append(database.path))

    writes = 0
    original_write = VaultHistoricalRead.write_uncleaned_arrow_table

    def count_writes(*args: object, **kwargs: object) -> None:
        """Count batch output writes while retaining production writer behaviour."""
        nonlocal writes
        writes += 1
        original_write(*args, **kwargs)

    monkeypatch.setattr(post_processing.VaultHistoricalRead, "write_uncleaned_arrow_table", count_writes)

    hyperliquid_db_path = tmp_path / "hyperliquid.duckdb"
    hyperliquid_hf_db_path = tmp_path / "hyperliquid-hf.duckdb"
    hyperliquid_db_path.touch()
    hyperliquid_hf_db_path.touch()

    steps = post_processing.merge_native_protocols(
        merge_hypercore=True,
        merge_grvt=True,
        merge_lighter=True,
        merge_hibachi=True,
        merge_apex=True,
        uncleaned_parquet_path=parquet_path,
        hyperliquid_db_path=hyperliquid_db_path,
        hyperliquid_hf_db_path=hyperliquid_hf_db_path,
        grvt_db_path=tmp_path / "grvt.duckdb",
        lighter_db_path=tmp_path / "lighter.duckdb",
        hibachi_db_path=tmp_path / "hibachi.duckdb",
        apex_db_path=tmp_path / "apex.duckdb",
    )

    result_df = pd.read_parquet(parquet_path)
    assert writes == 1
    assert steps == {
        "hypercore-price-merge": True,
        "grvt-price-merge": True,
        "lighter-price-merge": True,
        "hibachi-price-merge": True,
        "apex-price-merge": True,
    }
    assert set(result_df["address"]) == {
        "0xevm",
        "hypercore-new",
        "grvt-new",
        "lighter-pool-200",
        "lighter-pool-robinhood-200",
        "hibachi-old",
        "apex-vault-old",
        "apex-vault-new",
    }
    assert LIGHTER_LEGACY_ROBINHOOD_CHAIN_ID not in set(result_df["chain"])
    assert set(result_df.loc[result_df["address"].str.startswith("lighter-"), "chain"]) == {LIGHTER_CHAIN_ID}
    assert result_df.loc[result_df["address"] == "hypercore-new", "account_pnl"].iloc[0] == pytest.approx(123.0)
    assert result_df.loc[result_df["address"] == "apex-vault-new", "share_price"].iloc[0] == pytest.approx(1.0)
    assert {hyperliquid_db_path, hyperliquid_hf_db_path}.issubset(perp_snapshot_paths)


def test_partial_lighter_merge_preserves_other_deployment_and_legacy_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace only fresh Lighter deployment addresses within shared chain 9998.

    1. Create current Ethereum and Robinhood rows plus legacy Robinhood 9996.
    2. Export only fresh Ethereum data, modelling a partial or standalone scan.
    3. Run the native merge.
    4. Assert Ethereum is replaced while both Robinhood histories survive.
    """
    parquet_path = tmp_path / "vault-prices-1h.parquet"
    existing_df = pd.concat(
        [
            _prices(LIGHTER_CHAIN_ID, "lighter-pool-100", "2025-01-01"),
            _prices(LIGHTER_CHAIN_ID, "lighter-pool-robinhood-100", "2025-01-01"),
            _prices(LIGHTER_LEGACY_ROBINHOOD_CHAIN_ID, "lighter-pool-robinhood-legacy", "2025-01-01"),
        ],
        ignore_index=True,
    )
    VaultHistoricalRead.write_uncleaned_parquet(existing_df, parquet_path)

    class FakeDatabase:
        """Provide the close method required by the post-processing pipeline."""

        def __init__(self, _: Path) -> None:
            pass

        def close(self) -> None:
            pass

    fresh_ethereum = _prices(LIGHTER_CHAIN_ID, "lighter-pool-200", "2025-01-02")
    monkeypatch.setattr(post_processing, "LighterDailyMetricsDatabase", FakeDatabase)
    monkeypatch.setattr(post_processing, "build_lighter_prices_dataframe", lambda _: fresh_ethereum)

    steps = post_processing.merge_native_protocols(
        merge_lighter=True,
        uncleaned_parquet_path=parquet_path,
        lighter_db_path=tmp_path / "lighter.duckdb",
    )

    result_df = pd.read_parquet(parquet_path)
    assert steps == {"lighter-price-merge": True}
    assert set(result_df["address"]) == {
        "lighter-pool-200",
        "lighter-pool-robinhood-100",
        "lighter-pool-robinhood-legacy",
    }
    assert set(result_df.loc[result_df["address"] == "lighter-pool-robinhood-legacy", "chain"]) == {LIGHTER_LEGACY_ROBINHOOD_CHAIN_ID}


def test_build_hypercore_prices_dataframe_prefers_high_frequency_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the high-frequency row when it overlaps the daily export.

    1. Stub daily and high-frequency exports with the same vault timestamp.
    2. Build the combined Hypercore frame without writing a parquet file.
    3. Assert the high-frequency share price is retained.
    """
    daily_df = _prices(HYPERCORE_CHAIN_ID, "hypercore-vault", "2025-01-01")
    daily_df["hypercore_source"] = "daily"
    hf_df = _prices(HYPERCORE_CHAIN_ID, "hypercore-vault", "2025-01-01")
    hf_df["share_price"] = 1.1
    hf_df["hypercore_source"] = "hf"
    monkeypatch.setattr(vault_data_export, "build_raw_prices_dataframe", lambda _: daily_df)
    monkeypatch.setattr(vault_data_export, "build_raw_prices_dataframe_hf", lambda _: hf_df)

    result_df = vault_data_export.build_hypercore_prices_dataframe(daily_db=object(), hf_db=object())

    assert len(result_df) == 1
    assert result_df.iloc[0]["share_price"] == pytest.approx(1.1)


def test_native_arrow_merge_preserves_original_parquet_when_verification_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the original parquet when the new Arrow output cannot be verified.

    1. Write a known-good raw parquet.
    2. Force the shared atomic writer's verification step to fail.
    3. Attempt a native partition replacement.
    4. Assert the original file bytes remain unchanged.
    """
    parquet_path = tmp_path / "vault-prices-1h.parquet"
    original_df = _prices(1, "0xevm", "2025-01-01")
    VaultHistoricalRead.write_uncleaned_parquet(original_df, parquet_path)
    original_bytes = parquet_path.read_bytes()

    def fail_verification(*_: object, **__: object) -> None:
        """Simulate a corrupt temporary parquet output."""
        raise ParquetVerificationError("simulated verification failure")

    monkeypatch.setattr(base, "verify_parquet_file", fail_verification)

    with pytest.raises(ParquetVerificationError, match="simulated verification failure"):
        post_processing._write_native_partitions_to_uncleaned_parquet(
            parquet_path,
            {HYPERCORE_CHAIN_ID: _prices(HYPERCORE_CHAIN_ID, "hypercore-new", "2025-01-02")},
        )

    assert parquet_path.read_bytes() == original_bytes
