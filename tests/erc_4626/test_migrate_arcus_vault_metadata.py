"""Tests for the targeted Arcus vault metadata migration."""

import datetime
import importlib.util
from pathlib import Path

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.arcus.constants import ARCUS_BTC_3X_LONG_VAULT, ARCUS_CHAIN_ID, ARCUS_HOOD_3X_LONG_VAULT
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

#: Number of reviewed Arcus pTokens in this migration.
ARCUS_TARGET_COUNT = 2

#: Historical event counts retained from the public vault JSON.
BTC_DEPOSIT_COUNT = 11
HOOD_DEPOSIT_COUNT = 13
BTC_REDEEM_COUNT = 3
HOOD_REDEEM_COUNT = 5

#: Naive UTC fixture timestamps.
FIRST_SEEN_AT = datetime.datetime(2026, 8, 1, 12, 0, 0, tzinfo=datetime.UTC).replace(tzinfo=None)
UPDATED_AT = datetime.datetime(2026, 8, 17, 12, 0, 0, tzinfo=datetime.UTC).replace(tzinfo=None)


def load_migration_module():
    """Load the hyphenated Arcus maintenance script as a module.

    :return:
        Imported migration module.
    """

    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "migrate-arcus-vault-metadata.py"
    spec = importlib.util.spec_from_file_location("migrate_arcus_vault_metadata", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_detection(address: str, deposit_count: int, redeem_count: int) -> ERC4262VaultDetection:
    """Create a persisted pre-Arcus detection fixture.

    :param address:
        Arcus pToken address.
    :param deposit_count:
        Observed deposit event count.
    :param redeem_count:
        Observed redemption event count.
    :return:
        Generic ERC-7540 detection saved before Arcus support.
    """

    return ERC4262VaultDetection(
        chain=ARCUS_CHAIN_ID,
        address=address,
        first_seen_at_block=30_000_000,
        first_seen_at=FIRST_SEEN_AT,
        features={ERC4626Feature.erc_7540_like},
        updated_at=FIRST_SEEN_AT,
        deposit_count=deposit_count,
        redeem_count=redeem_count,
    )


def create_stale_arcus_row(detection: ERC4262VaultDetection) -> dict:
    """Create the legacy export metadata row for one pToken.

    :param detection:
        Stale generic persisted detector state.
    :return:
        Minimal old scanner row.
    """

    return {
        "Name": "Legacy Arcus pToken",
        "Protocol": "<unknown ERC-7540>",
        "features": set(detection.features),
        "_detection_data": detection,
        "_manager_name": None,
        "_short_description": None,
        "_description": None,
        "_notes": None,
    }


def create_refreshed_arcus_row(detection: ERC4262VaultDetection) -> dict:
    """Create the scanner row produced by the Arcus adapter.

    :param detection:
        Reclassified Arcus detection.
    :return:
        Minimal current scanner row.
    """

    return {
        "Name": "Arcus pToken",
        "Protocol": "Arcus",
        "features": set(detection.features),
        "_detection_data": detection,
        "_manager_name": None,
        "_short_description": "Reviewed Arcus pToken.",
        "_description": "Reviewed Arcus pToken metadata.",
        "_notes": "Reviewed Arcus pToken mechanics.",
    }


def test_migrate_arcus_metadata_reclassifies_only_reviewed_rows(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Refresh stale Arcus rows without changing unrelated scanner state."""

    module = load_migration_module()
    btc_spec = VaultSpec(ARCUS_CHAIN_ID, ARCUS_BTC_3X_LONG_VAULT)
    hood_spec = VaultSpec(ARCUS_CHAIN_ID, ARCUS_HOOD_3X_LONG_VAULT)
    other_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    btc_detection = create_detection(ARCUS_BTC_3X_LONG_VAULT, deposit_count=BTC_DEPOSIT_COUNT, redeem_count=BTC_REDEEM_COUNT)
    hood_detection = create_detection(ARCUS_HOOD_3X_LONG_VAULT, deposit_count=HOOD_DEPOSIT_COUNT, redeem_count=HOOD_REDEEM_COUNT)
    unrelated_row = {"Name": "Unrelated vault", "Protocol": "Morpho"}
    vault_db = VaultDatabase(
        rows={
            btc_spec: create_stale_arcus_row(btc_detection),
            hood_spec: create_stale_arcus_row(hood_detection),
            other_spec: unrelated_row,
        },
        leads={other_spec: object()},
        last_scanned_block={ARCUS_CHAIN_ID: 50_000_000},
    )

    monkeypatch.setattr(module, "detect_vault_features", lambda *_args, **_kwargs: {ERC4626Feature.erc_7540_like, ERC4626Feature.arcus_like})
    monkeypatch.setattr(module, "create_vault_scan_record", lambda _web3, detection, *_args: create_refreshed_arcus_row(detection))
    web3 = type("Web3Stub", (), {"eth": type("EthStub", (), {"chain_id": ARCUS_CHAIN_ID, "block_number": 50_000_010})()})()

    result = module.migrate_arcus_metadata(
        web3,
        vault_db,
        token_cache=object(),
        dry_run=False,
        updated_at=UPDATED_AT,
    )
    capsys.readouterr()

    assert result.inspected_rows == ARCUS_TARGET_COUNT
    assert result.migrated_rows == ARCUS_TARGET_COUNT
    assert result.seeded_leads == ARCUS_TARGET_COUNT
    assert vault_db.rows[btc_spec]["Protocol"] == "Arcus"
    assert vault_db.rows[hood_spec]["Protocol"] == "Arcus"
    assert vault_db.rows[btc_spec]["_detection_data"].deposit_count == BTC_DEPOSIT_COUNT
    assert vault_db.rows[hood_spec]["_detection_data"].redeem_count == HOOD_REDEEM_COUNT
    assert vault_db.rows[btc_spec]["_detection_data"].updated_at == UPDATED_AT
    assert vault_db.rows[btc_spec]["_manager_name"] is None
    assert vault_db.rows[btc_spec]["_notes"] == "Reviewed Arcus pToken mechanics."
    assert vault_db.leads[btc_spec].deposit_count == BTC_DEPOSIT_COUNT
    assert vault_db.leads[hood_spec].withdrawal_count == HOOD_REDEEM_COUNT
    assert vault_db.rows[other_spec] is unrelated_row
    assert vault_db.leads[other_spec] is not None
    assert vault_db.last_scanned_block == {ARCUS_CHAIN_ID: 50_000_000}


def test_migrate_arcus_metadata_dry_run_does_not_mutate(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Report target rows without changing metadata or lead records in dry-run mode."""

    module = load_migration_module()
    btc_spec = VaultSpec(ARCUS_CHAIN_ID, ARCUS_BTC_3X_LONG_VAULT)
    hood_spec = VaultSpec(ARCUS_CHAIN_ID, ARCUS_HOOD_3X_LONG_VAULT)
    btc_detection = create_detection(ARCUS_BTC_3X_LONG_VAULT, deposit_count=BTC_DEPOSIT_COUNT, redeem_count=BTC_REDEEM_COUNT)
    hood_detection = create_detection(ARCUS_HOOD_3X_LONG_VAULT, deposit_count=HOOD_DEPOSIT_COUNT, redeem_count=HOOD_REDEEM_COUNT)
    vault_db = VaultDatabase(
        rows={
            btc_spec: create_stale_arcus_row(btc_detection),
            hood_spec: create_stale_arcus_row(hood_detection),
        }
    )
    original_btc_row = vault_db.rows[btc_spec]

    monkeypatch.setattr(module, "detect_vault_features", lambda *_args, **_kwargs: {ERC4626Feature.erc_7540_like, ERC4626Feature.arcus_like})
    monkeypatch.setattr(module, "create_vault_scan_record", lambda _web3, detection, *_args: create_refreshed_arcus_row(detection))
    web3 = type("Web3Stub", (), {"eth": type("EthStub", (), {"chain_id": ARCUS_CHAIN_ID, "block_number": 50_000_010})()})()

    result = module.migrate_arcus_metadata(
        web3,
        vault_db,
        token_cache=object(),
        dry_run=True,
        updated_at=UPDATED_AT,
    )
    capsys.readouterr()

    assert result.migrated_rows == ARCUS_TARGET_COUNT
    assert result.seeded_leads == ARCUS_TARGET_COUNT
    assert vault_db.rows[btc_spec] is original_btc_row
    assert vault_db.rows[btc_spec]["Protocol"] == "<unknown ERC-7540>"
    assert btc_spec not in vault_db.leads
    assert hood_spec not in vault_db.leads


def test_create_backup_path_does_not_overwrite_existing_backups(tmp_path: Path) -> None:
    """Create incremented Arcus backup names when a prior backup exists."""

    module = load_migration_module()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    first_backup = tmp_path / "vault-metadata-db.pickle.bak-arcus-metadata"
    second_backup = tmp_path / "vault-metadata-db.pickle.bak-arcus-metadata.1"
    first_backup.touch()
    second_backup.touch()

    assert module.create_backup_path(vault_db_path) == tmp_path / "vault-metadata-db.pickle.bak-arcus-metadata.2"
