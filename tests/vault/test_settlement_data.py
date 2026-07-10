"""Tests for generic vault settlement DuckDB storage."""

import datetime
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.vault.settlement_data import (
    VaultSettlement,
    VaultSettlementDatabase,
    annotate_prices_with_vault_settlements,
    preserve_vault_settlement_markers,
)


def test_vault_settlement_database_upsert(tmp_path: Path) -> None:
    """Insert settlement rows and verify idempotent replacement by transaction."""
    pytest.importorskip("duckdb")

    db = VaultSettlementDatabase(tmp_path / "vault-settlements.duckdb")
    try:
        timestamp = datetime.datetime(2026, 2, 1, 12, 0, 0)
        settlement = VaultSettlement(
            chain_id=1,
            address="0xabc0000000000000000000000000000000000000",
            block_number=123,
            protocol="Lagoon Finance",
            block_hash="0x" + "11" * 32,
            timestamp=timestamp,
            tx_hash="0x" + "22" * 32,
            event_name="SettleDeposit",
        )

        assert db.upsert_settlements([settlement]) == 1
        assert db.upsert_settlements([settlement]) == 1
        assert db.get_settlement_count() == 1

        same_block_settlement = VaultSettlement(
            chain_id=1,
            address="0xabc0000000000000000000000000000000000000",
            block_number=123,
            protocol="Lagoon Finance",
            block_hash="0x" + "11" * 32,
            timestamp=timestamp,
            tx_hash="0x" + "33" * 32,
            event_name="SettleRedeem",
        )
        assert db.upsert_settlements([same_block_settlement]) == 1
        assert db.get_settlement_count() == 2

        same_tx_redeem_settlement = VaultSettlement(
            chain_id=1,
            address="0xabc0000000000000000000000000000000000000",
            block_number=123,
            protocol="Lagoon Finance",
            block_hash="0x" + "11" * 32,
            timestamp=timestamp,
            tx_hash="0x" + "22" * 32,
            event_name="SettleRedeem",
        )
        assert db.upsert_settlements([same_tx_redeem_settlement]) == 1
        assert db.get_settlement_count() == 3
        assert db.upsert_settlements([settlement]) == 1
        assert db.get_settlement_count() == 3

        df = db.get_settlements(chain_id=1, address=settlement.address)
        assert len(df) == 3
        assert set(df["block_number"]) == {123}
        assert set(df["tx_hash"]) == {settlement.tx_hash, same_block_settlement.tx_hash}
        assert set(df["event_name"]) == {"SettleDeposit", "SettleRedeem"}
        assert set(df["protocol"]) == {"Lagoon Finance"}
        assert db.get_latest_block_number(1, settlement.address) == 123
    finally:
        db.close()


def test_vault_settlement_database_scan_state_upsert(tmp_path: Path) -> None:
    """Store settlement scan progress independently from sparse event rows."""
    pytest.importorskip("duckdb")

    db = VaultSettlementDatabase(tmp_path / "vault-settlements.duckdb")
    try:
        address = "0xabc0000000000000000000000000000000000000"

        assert db.get_latest_scanned_block_number(1, address) is None
        assert db.upsert_scan_state([(1, address, 200)]) == 1
        assert db.get_latest_scanned_block_number(1, address) == 200
        assert db.get_latest_block_number(1, address) is None

        assert db.upsert_scan_state([(1, address, 150), (1, address, 250)]) == 1
        assert db.get_latest_scanned_block_number(1, address) == 250

        assert db.upsert_scan_state([(1, address, 100)]) == 1
        assert db.get_latest_scanned_block_number(1, address) == 250
    finally:
        db.close()


def test_annotate_prices_with_vault_settlements() -> None:
    """Annotate price rows with latest settlement in each row interval."""
    prices = pd.DataFrame(
        [
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 0, 0, 0),
            },
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 1, 0, 0),
            },
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 2, 0, 0),
            },
        ]
    )
    settlements = pd.DataFrame(
        [
            {
                "chain_id": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 0, 30, 0),
            },
            {
                "chain_id": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 1, 0, 0),
            },
            {
                "chain_id": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 2, 0, 0),
            },
        ]
    )

    annotated = annotate_prices_with_vault_settlements(prices, settlements)

    assert pd.isna(annotated.iloc[0]["vault_settlement_at"])
    assert annotated.iloc[1]["vault_settlement_at"] == pd.Timestamp(datetime.datetime(2026, 2, 1, 1, 0, 0))
    assert annotated.iloc[2]["vault_settlement_at"] == pd.Timestamp(datetime.datetime(2026, 2, 1, 2, 0, 0))


def test_annotate_prices_ignores_previous_row_boundary() -> None:
    """A settlement exactly at the previous row timestamp is not repeated."""
    prices = pd.DataFrame(
        [
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 1, 0, 0),
            },
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 2, 0, 0),
            },
        ]
    )
    settlements = pd.DataFrame(
        [
            {
                "chain_id": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 1, 0, 0),
            },
        ]
    )

    annotated = annotate_prices_with_vault_settlements(prices, settlements)

    assert annotated.iloc[0]["vault_settlement_at"] == pd.Timestamp(datetime.datetime(2026, 2, 1, 1, 0, 0))
    assert pd.isna(annotated.iloc[1]["vault_settlement_at"])


def test_preserve_vault_settlement_markers_carries_to_next_cleaned_row() -> None:
    """A marker on a dropped raw row is carried to the next surviving row."""
    raw_prices = pd.DataFrame(
        [
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 0, 0, 0),
                "vault_settlement_at": pd.NaT,
            },
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 1, 0, 0),
                "vault_settlement_at": pd.Timestamp(datetime.datetime(2026, 2, 1, 0, 30, 0)),
            },
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 2, 0, 0),
                "vault_settlement_at": pd.NaT,
            },
        ]
    )
    cleaned_prices = pd.DataFrame(
        [
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 0, 0, 0),
                "vault_settlement_at": pd.NaT,
            },
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 2, 0, 0),
                "vault_settlement_at": pd.NaT,
            },
        ]
    )

    preserved = preserve_vault_settlement_markers(raw_prices, cleaned_prices)

    assert pd.isna(preserved.iloc[0]["vault_settlement_at"])
    assert preserved.iloc[1]["vault_settlement_at"] == pd.Timestamp(datetime.datetime(2026, 2, 1, 0, 30, 0))


def test_preserve_vault_settlement_markers_accepts_timestamp_index() -> None:
    """Cleaned price frames may carry ``timestamp`` as the DatetimeIndex."""
    raw_prices = pd.DataFrame(
        [
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 0, 0, 0),
                "vault_settlement_at": pd.NaT,
            },
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 1, 0, 0),
                "vault_settlement_at": pd.Timestamp(datetime.datetime(2026, 2, 1, 0, 30, 0)),
            },
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 2, 0, 0),
                "vault_settlement_at": pd.NaT,
            },
        ]
    )
    cleaned_prices = pd.DataFrame(
        [
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 0, 0, 0),
                "vault_settlement_at": pd.NaT,
            },
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 2, 0, 0),
                "vault_settlement_at": pd.NaT,
            },
        ]
    ).set_index("timestamp")

    preserved = preserve_vault_settlement_markers(raw_prices, cleaned_prices)

    assert preserved.index.name is None
    assert "timestamp" in preserved.columns
    assert pd.isna(preserved.iloc[0]["vault_settlement_at"])
    assert preserved.iloc[1]["vault_settlement_at"] == pd.Timestamp(datetime.datetime(2026, 2, 1, 0, 30, 0))


def test_preserve_vault_settlement_markers_accepts_unnamed_timestamp_index() -> None:
    """Timestamp index normalisation should not depend on the index name."""
    raw_prices = pd.DataFrame(
        [
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 0, 0, 0),
                "vault_settlement_at": pd.Timestamp(datetime.datetime(2026, 2, 1, 0, 0, 0)),
            },
        ]
    )
    cleaned_prices = pd.DataFrame(
        [
            {
                "chain": 1,
                "address": "0xabc0000000000000000000000000000000000000",
                "timestamp": datetime.datetime(2026, 2, 1, 0, 0, 0),
                "vault_settlement_at": pd.NaT,
            },
        ]
    ).set_index("timestamp")
    cleaned_prices.index.name = None

    preserved = preserve_vault_settlement_markers(raw_prices, cleaned_prices)

    assert "timestamp" in preserved.columns
    assert preserved.iloc[0]["vault_settlement_at"] == pd.Timestamp(datetime.datetime(2026, 2, 1, 0, 0, 0))
