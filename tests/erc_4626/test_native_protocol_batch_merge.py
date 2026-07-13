"""Tests for batched native-protocol Parquet price merging."""

from pathlib import Path

import pandas as pd
import pytest

from eth_defi.grvt.constants import GRVT_CHAIN_ID
from eth_defi.hibachi.constants import HIBACHI_CHAIN_ID
from eth_defi.hyperliquid import vault_data_export
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.lighter.constants import LIGHTER_CHAIN_ID
from eth_defi.vault import base, post_processing
from eth_defi.vault.base import ParquetVerificationError, VaultHistoricalRead


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


def test_merge_native_protocols_rewrites_parquet_once_and_preserves_empty_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batch successful native sources and retain the prior empty-source partition.

    1. Create raw prices with EVM and stale native-protocol rows.
    2. Stub Hypercore, GRVT, and Lighter exports with fresh data and Hibachi
       with no data.
    3. Merge all four sources and count Parquet writes.
    4. Assert one write replaced fresh partitions but retained stale Hibachi data.
    """
    parquet_path = tmp_path / "vault-prices-1h.parquet"
    existing_df = pd.concat(
        [
            _prices(1, "0xevm", "2025-01-01"),
            _prices(HYPERCORE_CHAIN_ID, "hypercore-old", "2025-01-01"),
            _prices(GRVT_CHAIN_ID, "grvt-old", "2025-01-01"),
            _prices(LIGHTER_CHAIN_ID, "lighter-old", "2025-01-01"),
            _prices(HIBACHI_CHAIN_ID, "hibachi-old", "2025-01-01"),
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

    fresh_grvt = _prices(GRVT_CHAIN_ID, "grvt-new", "2025-01-02")
    fresh_lighter = _prices(LIGHTER_CHAIN_ID, "lighter-new", "2025-01-02")
    fresh_hypercore = _prices(HYPERCORE_CHAIN_ID, "hypercore-new", "2025-01-02")
    fresh_hypercore["account_pnl"] = 123.0
    monkeypatch.setattr(post_processing, "HyperliquidDailyMetricsDatabase", FakeDatabase)
    monkeypatch.setattr(post_processing, "HyperliquidHighFreqMetricsDatabase", FakeDatabase)
    monkeypatch.setattr(post_processing, "GRVTDailyMetricsDatabase", FakeDatabase)
    monkeypatch.setattr(post_processing, "LighterDailyMetricsDatabase", FakeDatabase)
    monkeypatch.setattr(post_processing, "HibachiDailyMetricsDatabase", FakeDatabase)
    monkeypatch.setattr(post_processing, "build_grvt_prices_dataframe", lambda _: fresh_grvt)
    monkeypatch.setattr(post_processing, "build_lighter_prices_dataframe", lambda _: fresh_lighter)
    monkeypatch.setattr(post_processing, "build_hibachi_prices_dataframe", lambda _: pd.DataFrame())
    monkeypatch.setattr(post_processing, "build_hypercore_prices_dataframe", lambda **_: fresh_hypercore)

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
        uncleaned_parquet_path=parquet_path,
        hyperliquid_db_path=hyperliquid_db_path,
        hyperliquid_hf_db_path=hyperliquid_hf_db_path,
        grvt_db_path=tmp_path / "grvt.duckdb",
        lighter_db_path=tmp_path / "lighter.duckdb",
        hibachi_db_path=tmp_path / "hibachi.duckdb",
    )

    result_df = pd.read_parquet(parquet_path)
    assert writes == 1
    assert steps == {
        "hypercore-price-merge": True,
        "grvt-price-merge": True,
        "lighter-price-merge": True,
        "hibachi-price-merge": True,
    }
    assert set(result_df["address"]) == {
        "0xevm",
        "hypercore-new",
        "grvt-new",
        "lighter-new",
        "hibachi-old",
    }
    assert result_df.loc[result_df["address"] == "hypercore-new", "account_pnl"].iloc[0] == pytest.approx(123.0)


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
