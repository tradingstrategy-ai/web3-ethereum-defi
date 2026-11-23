"""Tests for batched native-protocol Parquet price merging."""

import datetime
import logging
from collections import Counter
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
from eth_defi.solana import SOLANA_CHAIN_ID, make_solana_vault_address
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
    monkeypatch.setattr(post_processing, "HyperliquidHighFreqMetricsDatabase", lambda _, *, read_only: SimpleNamespace(close=lambda: closed.append(True)))
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


def test_native_price_freshness_uses_source_timestamp_and_tvl_hysteresis(caplog: pytest.LogCaptureFixture) -> None:
    """A recent file write cannot make an old meaningful source row fresh.

    Overdue vaults are reported in one summary warning, not one line each.
    """
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

    with caplog.at_level(logging.WARNING, logger=post_processing.logger.name):
        overdue = post_processing.audit_native_price_freshness(
            pa.Table.from_pandas(frame, preserve_index=False),
            datetime.datetime(2026, 1, 31),
        )

    assert overdue == {
        f"{GRVT_CHAIN_ID}-stale": "stale_source_timestamp",
        f"{GRVT_CHAIN_ID}-falling": "stale_source_timestamp",
        f"{GRVT_CHAIN_ID}-missing-price": "no_valid_source_observation",
    }
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].getMessage().startswith("3 native vaults have no real source price observation")


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

        def __init__(self, path: Path, *, read_only: bool) -> None:
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

        def __init__(self, _: Path, *, read_only: bool) -> None:
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


def test_append_native_history_matches_encoded_solana_and_preserves_distinct_accounts(tmp_path: Path) -> None:
    """Correct an encoded account while keeping its case-sensitive neighbour."""
    path = tmp_path / "raw.parquet"
    account = "J7qhMAKnB6G5dvoAN9281ufabajKbyGQxxd2bq6R7fPJ"
    neighbour = account[:-1] + "j"
    day = "2026-09-21"
    VaultHistoricalRead.write_uncleaned_parquet(_prices(SOLANA_CHAIN_ID, make_solana_vault_address(account), day), path)
    fresh = pd.concat([_prices(SOLANA_CHAIN_ID, account, day).assign(share_price=1.25), _prices(SOLANA_CHAIN_ID, neighbour, day)])
    merged = post_processing._merge_native_history_with_existing_parquet(path, fresh, SOLANA_CHAIN_ID)
    assert merged.set_index("address")["share_price"].to_dict() == {account: 1.25, neighbour: 1.0}


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


def test_incremental_hypercore_merge_retries_failed_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip unchanged histories and retry an unacknowledged source correction."""
    raw = tmp_path / "raw.parquet"
    source = tmp_path / "hf.duckdb"
    address = "0x" + "a" * 40
    timestamp = datetime.datetime(2026, 9, 21, 10)
    VaultHistoricalRead.write_uncleaned_parquet(_prices(8453, address.upper().replace("0X", "0x"), str(timestamp)), raw)
    database = HyperliquidHighFreqMetricsDatabase(source)
    try:
        database.upsert_vault_metadata(vault_address=address, name="Native vault", leader="0x" + "c" * 40, description=None, is_closed=False, allow_deposits=True, relationship_type="normal", create_time=timestamp, commission_rate=None, follower_count=None, tvl=100_000, apr=None)
        database.upsert_high_freq_prices([HyperliquidHighFreqPriceRow(address, timestamp, 1.0, 100_000, 0)])
    finally:
        database.close()
    options = dict(merge_hypercore=True, incremental=True, uncleaned_parquet_path=raw, hyperliquid_db_path=tmp_path / "missing.duckdb", hyperliquid_hf_db_path=source)
    assert post_processing.merge_native_protocols(**options) == {"hypercore-price-merge": True}

    original_raw = raw.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(post_processing, "build_hypercore_prices_dataframe", lambda **_kwargs: pytest.fail("Unchanged source rebuilt"))
        assert post_processing.merge_native_protocols(**options) == {"hypercore-price-merge": True}
    assert raw.read_bytes() == original_raw

    # A restored catalogue can exist yet omit all HyperCore vaults.
    catalogue_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase().write(catalogue_path)
    assert post_processing.merge_native_protocols(**options)["hypercore-price-merge"]
    assert VaultSpec(HYPERCORE_CHAIN_ID, address) in VaultDatabase.read(catalogue_path).rows
    original_raw = raw.read_bytes()

    database = HyperliquidHighFreqMetricsDatabase(source)
    try:
        database.upsert_high_freq_prices([HyperliquidHighFreqPriceRow(address, timestamp, 1.5, 150_000, 0)])
    finally:
        database.close()
    replace = base.os.replace

    def fail_raw_rename(temp_path: str, destination: str) -> None:
        if Path(destination) == raw:
            raise OSError("simulated native rename failure")
        replace(temp_path, destination)

    with monkeypatch.context() as patch:
        patch.setattr(base.os, "replace", fail_raw_rename)
        assert post_processing.merge_native_protocols(**options) == {"hypercore-price-merge": False}
    assert raw.read_bytes() == original_raw

    assert post_processing.merge_native_protocols(**options) == {"hypercore-price-merge": True}
    prices = pd.read_parquet(raw).set_index("chain")
    assert prices.loc[8453, "share_price"] == 1.0
    assert prices.loc[HYPERCORE_CHAIN_ID, "total_assets"] == 150_000
    assert len(prices) == 2


def test_incremental_lighter_rebuilds_only_changed_deployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A same-count Ethereum correction leaves the Robinhood scope intact."""
    raw = tmp_path / "raw.parquet"
    source = tmp_path / "lighter.duckdb"
    date = datetime.date(2026, 9, 21)
    VaultHistoricalRead.write_uncleaned_parquet(_prices(8453, "0x" + "b" * 40, str(date)), raw)
    database = post_processing.LighterDailyMetricsDatabase(source)
    try:
        for deployment in ("ethereum", "robinhood"):
            database.con.execute("INSERT INTO pool_daily_prices (deployment, account_index, date, share_price, tvl) VALUES (?, 100, ?, 1.0, 10000)", [deployment, date])
    finally:
        database.close()
    options = dict(merge_lighter=True, incremental=True, uncleaned_parquet_path=raw, lighter_db_path=source)
    assert post_processing.merge_native_protocols(**options) == {"lighter-price-merge": True}

    before = pd.read_parquet(raw)
    database = post_processing.LighterDailyMetricsDatabase(source)
    try:
        database.con.execute("UPDATE pool_daily_prices SET share_price = 1.25 WHERE deployment = 'ethereum'")
    finally:
        database.close()
    original_builder = post_processing.build_lighter_prices_dataframe
    builds = []

    def record_build(db: post_processing.LighterDailyMetricsDatabase, deployment: str | None = None) -> pd.DataFrame:
        builds.append(deployment)
        return original_builder(db, deployment=deployment)

    monkeypatch.setattr(post_processing, "build_lighter_prices_dataframe", record_build)
    assert post_processing.merge_native_protocols(**options) == {"lighter-price-merge": True}
    assert builds == ["ethereum"]
    after = pd.read_parquet(raw)
    pd.testing.assert_frame_equal(after.loc[after.address != "lighter-pool-100"], before.loc[before.address != "lighter-pool-100"])
    assert after.loc[after.address == "lighter-pool-100", "share_price"].tolist() == [1.25]

    # A restored source missing an entire previously published deployment
    # must keep publication pending and its sibling's prices untouched.
    database = post_processing.LighterDailyMetricsDatabase(source)
    try:
        database.con.execute("DELETE FROM pool_daily_prices WHERE deployment = 'robinhood'")
    finally:
        database.close()
    committed = raw.read_bytes()
    assert post_processing.merge_native_protocols(**options) == {"lighter-price-merge": False}
    assert raw.read_bytes() == committed


@pytest.mark.parametrize(
    ("correction_time", "duplicate", "error"),
    [
        ("2026-09-21", True, "duplicate canonical price keys"),
        ("2026-09-22", False, "shrink published history"),
    ],
)
def test_incremental_native_rejects_invalid_replacements(tmp_path: Path, correction_time: str, duplicate: bool, error: str) -> None:
    """A conflicting alias or shortened archive cannot replace the committed file."""
    path = tmp_path / "prices.parquet"
    address = "0x" + "a" * 40
    VaultHistoricalRead.write_uncleaned_parquet(_prices(HYPERCORE_CHAIN_ID, address, "2026-09-21"), path)
    committed = path.read_bytes()
    correction = _prices(HYPERCORE_CHAIN_ID, address, correction_time)
    if duplicate:
        correction = pd.concat([correction, correction.assign(address="0x" + "A" * 40, share_price=1.25)])
    with pytest.raises(ValueError, match=error):
        post_processing._write_native_partitions_to_uncleaned_parquet(path, {HYPERCORE_CHAIN_ID: correction}, revisions={})
    assert path.read_bytes() == committed


def test_apex_merge_replays_at_stored_precision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Correct a replayed ranking point while retaining raw-only and EVM rows."""
    path = tmp_path / "prices.parquet"
    address = "apex-vault-100"
    evm = _prices(42161, "0x" + "a" * 40, "2026-11-22 08:26:55")
    raw_only = _prices(APEX_CHAIN_ID, address, "2026-07-25")
    VaultHistoricalRead.write_uncleaned_parquet(pd.concat([evm.assign(block_number=100), evm.assign(block_number=101), raw_only.assign(block_number=0)]), path)
    source_path = tmp_path / "apex.duckdb"
    source_path.touch()
    fresh = _prices(APEX_CHAIN_ID, address, "2026-11-22 08:26:55.999500")
    writes = []
    operations = Counter()
    write = VaultHistoricalRead.write_uncleaned_arrow_table
    read = post_processing.pq.read_table
    sort = post_processing.pc.sort_indices

    def write_prices(table: pa.Table, path: Path) -> None:
        writes.append(table.num_rows)
        write(table, path)

    def read_prices(path: Path, **kwargs: object) -> pa.Table:
        operations["body reads"] += 1
        return read(path, **kwargs)

    def sort_prices(table: pa.Table, **kwargs: object) -> pa.Array:
        operations["sorts"] += 1
        return sort(table, **kwargs)

    def open_source(path: Path, *, read_only: bool) -> SimpleNamespace:
        assert path == source_path and read_only
        operations["opens"] += 1
        return SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(VaultHistoricalRead, "write_uncleaned_arrow_table", write_prices)
    monkeypatch.setattr(post_processing.pq, "read_table", read_prices)
    monkeypatch.setattr(post_processing.pc, "sort_indices", sort_prices)
    monkeypatch.setattr(post_processing, "ApexMetricsDatabase", open_source)
    monkeypatch.setattr(post_processing, "build_apex_prices_dataframe", lambda _: fresh)
    options = {"merge_apex": True, "uncleaned_parquet_path": path, "apex_db_path": source_path, "incremental": True}

    assert post_processing.merge_native_protocols(**options) == {"apex-price-merge": True}
    fresh["share_price"] = 1.25
    source_path.write_bytes(b"new revision")
    assert post_processing.merge_native_protocols(**options) == {"apex-price-merge": True}
    assert post_processing.merge_native_protocols(**options) == {"apex-price-merge": True}
    assert writes == [4, 4]
    assert operations == {"body reads": 4, "sorts": 2, "opens": 2}
    stored = pd.read_parquet(path)
    assert stored.loc[stored["chain"].eq(42161), "block_number"].tolist() == [100, 101]
    apex = stored.loc[stored["chain"].eq(APEX_CHAIN_ID)]
    assert list(apex["timestamp"]) == [pd.Timestamp("2026-07-25"), pd.Timestamp("2026-11-22 08:26:55.999")]
    assert list(apex["share_price"]) == pytest.approx([1.0, 1.25])


@pytest.mark.parametrize("validate_keys", [True, False])
def test_apex_checks_source_collisions_before_first_write(tmp_path: Path, *, validate_keys: bool) -> None:
    """Two real points in one stored millisecond have no automatic winner."""
    fresh = pd.concat(
        [
            _prices(APEX_CHAIN_ID, "apex-vault-100", "2026-11-22 08:26:55.999500"),
            _prices(APEX_CHAIN_ID, "apex-vault-100", "2026-11-22 08:26:55.999900"),
        ],
        ignore_index=True,
    )
    path = tmp_path / "missing.parquet"
    with pytest.raises(ValueError, match=r"999500.+999900"):
        post_processing._merge_native_history_with_existing_parquet(path, fresh, APEX_CHAIN_ID, validate_keys=validate_keys)
    assert not path.exists()


@pytest.mark.parametrize("collision", ["source", "stored"])
def test_apex_collision_keeps_independent_scope_publishable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, collision: str) -> None:
    """Ambiguity in either ApeX input leaves its receipt pending and retains every old price."""
    path = tmp_path / "prices.parquet"
    apex_address = "apex-vault-100"
    old_apex = _prices(APEX_CHAIN_ID, apex_address, "2026-07-25")
    old_grvt = _prices(GRVT_CHAIN_ID, "grvt-old", "2026-07-25")
    if collision == "stored":
        old_apex = pd.concat([old_apex, old_apex.assign(share_price=1.125)])
    VaultHistoricalRead.write_uncleaned_parquet(pd.concat([old_apex, old_grvt]), path)

    apex_source = tmp_path / "apex.duckdb"
    grvt_source = tmp_path / "grvt.duckdb"
    apex_source.touch()
    grvt_source.touch()
    fresh_apex = _prices(APEX_CHAIN_ID, apex_address, "2026-07-25 00:00:00.000500")
    if collision == "source":
        fresh_apex = pd.concat([fresh_apex, _prices(APEX_CHAIN_ID, apex_address, "2026-07-25 00:00:00.000900")])
    monkeypatch.setattr(post_processing, "ApexMetricsDatabase", lambda *_, **__: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(post_processing, "GRVTDailyMetricsDatabase", lambda *_, **__: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(post_processing, "build_apex_prices_dataframe", lambda _: fresh_apex)
    monkeypatch.setattr(post_processing, "build_grvt_prices_dataframe", lambda _: old_grvt.assign(share_price=1.25))

    assert post_processing.merge_native_protocols(
        merge_apex=True,
        merge_grvt=True,
        apex_db_path=apex_source,
        grvt_db_path=grvt_source,
        uncleaned_parquet_path=path,
        incremental=True,
    ) == {"apex-price-merge": False, "grvt-price-merge": True}
    assert post_processing._read_native_revisions(path).keys() == {"grvt"}
    stored = pd.read_parquet(path)
    assert stored.loc[stored["address"].eq("grvt-old"), "share_price"].tolist() == pytest.approx([1.25])
    assert stored.loc[stored["address"].eq(apex_address), "share_price"].tolist() == pytest.approx(old_apex["share_price"].tolist())
