"""Tests for vault settlement scan orchestration."""

import datetime
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.settlement_data import VaultSettlement, VaultSettlementDatabase
from eth_defi.vault.settlement_scan import select_vault_settlement_scan_ranges
from eth_defi.vault.vaultdb import VaultDatabase


LAGOON_ADDRESS = "0xabc0000000000000000000000000000000000000"
D2_ADDRESS = "0xd200000000000000000000000000000000000000"
OTHER_ADDRESS = "0xdef0000000000000000000000000000000000000"


class EmptySettlementDb:
    """Minimal settlement database stand-in for range selection tests."""

    def get_latest_block_number(self, chain_id: int, address: str) -> int | None:
        """Return no existing settlement progress."""
        return None


def make_vault_db() -> VaultDatabase:
    """Create a minimal vault database with one Lagoon vault and one non-Lagoon vault."""
    now = datetime.datetime(2026, 2, 1, 0, 0, 0)
    lagoon_detection = ERC4262VaultDetection(
        chain=1,
        address=LAGOON_ADDRESS,
        first_seen_at_block=100,
        first_seen_at=now,
        features={ERC4626Feature.lagoon_like, ERC4626Feature.erc_7540_like},
        updated_at=now,
        deposit_count=10,
        redeem_count=5,
    )
    d2_detection = ERC4262VaultDetection(
        chain=1,
        address=D2_ADDRESS,
        first_seen_at_block=100,
        first_seen_at=now,
        features={ERC4626Feature.d2_like},
        updated_at=now,
        deposit_count=10,
        redeem_count=5,
    )
    other_detection = ERC4262VaultDetection(
        chain=1,
        address=OTHER_ADDRESS,
        first_seen_at_block=100,
        first_seen_at=now,
        features=set(),
        updated_at=now,
        deposit_count=10,
        redeem_count=5,
    )
    return VaultDatabase(
        rows={
            VaultSpec(1, LAGOON_ADDRESS): {
                "Address": LAGOON_ADDRESS,
                "Protocol": "Lagoon Finance",
                "features": lagoon_detection.features,
                "_detection_data": lagoon_detection,
            },
            VaultSpec(1, OTHER_ADDRESS): {
                "Address": OTHER_ADDRESS,
                "Protocol": "Generic",
                "features": other_detection.features,
                "_detection_data": other_detection,
            },
            VaultSpec(1, D2_ADDRESS): {
                "Address": D2_ADDRESS,
                "Protocol": "D2 Finance",
                "features": d2_detection.features,
                "_detection_data": d2_detection,
            },
        }
    )


def test_select_vault_settlement_scan_ranges_incremental_lagoon_only(tmp_path: Path) -> None:
    """Scan ranges start after the latest stored settlement block."""
    pytest.importorskip("duckdb")

    raw_prices = pd.DataFrame(
        [
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 100},
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 200},
            {"chain": 1, "address": OTHER_ADDRESS, "block_number": 100},
            {"chain": 1, "address": OTHER_ADDRESS, "block_number": 200},
        ]
    )
    db = VaultSettlementDatabase(tmp_path / "vault-settlements.duckdb")
    try:
        db.upsert_settlements(
            [
                VaultSettlement(
                    chain_id=1,
                    address=LAGOON_ADDRESS,
                    block_number=150,
                    protocol="Lagoon Finance",
                    block_hash="0x" + "11" * 32,
                    timestamp=datetime.datetime(2026, 2, 1, 12, 0, 0),
                    tx_hash="0x" + "22" * 32,
                )
            ]
        )

        ranges = select_vault_settlement_scan_ranges(
            make_vault_db(),
            raw_prices,
            db,
            supported_features={ERC4626Feature.lagoon_like},
        )

        assert len(ranges) == 1
        assert ranges[0].chain_id == 1
        assert ranges[0].address == LAGOON_ADDRESS
        assert ranges[0].start_block == 151
        assert ranges[0].end_block == 200
    finally:
        db.close()


def test_select_vault_settlement_scan_ranges_forced_lagoon_backfill(tmp_path: Path) -> None:
    """Forced backfill ranges are intersected with raw price block ranges."""
    pytest.importorskip("duckdb")

    raw_prices = pd.DataFrame(
        [
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 100},
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 200},
        ]
    )
    db = VaultSettlementDatabase(tmp_path / "vault-settlements.duckdb")
    try:
        db.upsert_settlements(
            [
                VaultSettlement(
                    chain_id=1,
                    address=LAGOON_ADDRESS,
                    block_number=150,
                    protocol="Lagoon Finance",
                    block_hash="0x" + "11" * 32,
                    timestamp=datetime.datetime(2026, 2, 1, 12, 0, 0),
                    tx_hash="0x" + "22" * 32,
                )
            ]
        )
        ranges = select_vault_settlement_scan_ranges(
            make_vault_db(),
            raw_prices,
            db,
            supported_features={ERC4626Feature.lagoon_like},
            forced_start_block=120,
            forced_end_block=180,
        )

        assert len(ranges) == 1
        assert ranges[0].start_block == 120
        assert ranges[0].end_block == 180
    finally:
        db.close()


def test_select_vault_settlement_scan_ranges_includes_supported_protocols() -> None:
    """Generic settlement scans include Lagoon and D2 vaults."""
    raw_prices = pd.DataFrame(
        [
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 100},
            {"chain": 1, "address": LAGOON_ADDRESS, "block_number": 200},
            {"chain": 1, "address": D2_ADDRESS, "block_number": 110},
            {"chain": 1, "address": D2_ADDRESS, "block_number": 210},
            {"chain": 1, "address": OTHER_ADDRESS, "block_number": 100},
            {"chain": 1, "address": OTHER_ADDRESS, "block_number": 200},
        ]
    )
    ranges = select_vault_settlement_scan_ranges(make_vault_db(), raw_prices, EmptySettlementDb())

    assert [(item.address, item.start_block, item.end_block) for item in ranges] == [
        (LAGOON_ADDRESS, 100, 200),
        (D2_ADDRESS, 110, 210),
    ]
