"""Test the targeted pToken metadata migration."""

import datetime
import importlib.util
from pathlib import Path

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.ptoken.constants import PTOKEN_BTC_3X_LONG_VAULT, PTOKEN_CHAIN_ID, PTOKEN_HOOD_3X_LONG_VAULT
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

TARGET_COUNT = 2
BTC_DEPOSIT_COUNT = 11
HOOD_DEPOSIT_COUNT = 13
FIRST_SEEN_AT = datetime.datetime(2026, 8, 1, 12, 0, 0, tzinfo=datetime.UTC).replace(tzinfo=None)
UPDATED_AT = datetime.datetime(2026, 8, 20, 12, 0, 0, tzinfo=datetime.UTC).replace(tzinfo=None)


def load_migration_module():
    """Load the hyphenated pToken script as a module.

    :return:
        Imported pToken migration module.
    """

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "erc-4626" / "migrate-ptoken-vault-metadata.py"
    spec = importlib.util.spec_from_file_location("migrate_ptoken_vault_metadata", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_detection(address: str, deposit_count: int) -> ERC4262VaultDetection:
    """Create an old persisted detector result.

    :param address:
        Reviewed pToken address.
    :param deposit_count:
        Historical deposit count to retain.
    :return:
        Generic ERC-7540 discovery result.
    """

    return ERC4262VaultDetection(
        chain=PTOKEN_CHAIN_ID,
        address=address,
        first_seen_at_block=30_000_000,
        first_seen_at=FIRST_SEEN_AT,
        features={ERC4626Feature.erc_7540_like},
        updated_at=FIRST_SEEN_AT,
        deposit_count=deposit_count,
        redeem_count=0,
    )


def test_migrate_ptoken_metadata_reclassifies_only_reviewed_rows(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Replace Arcus or generic rows without changing unrelated state.

    :param monkeypatch:
        Pytest RPC and scanner stubs.
    :param capsys:
        Captured migration report output.
    """

    module = load_migration_module()
    btc_spec = VaultSpec(PTOKEN_CHAIN_ID, PTOKEN_BTC_3X_LONG_VAULT)
    hood_spec = VaultSpec(PTOKEN_CHAIN_ID, PTOKEN_HOOD_3X_LONG_VAULT)
    other_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    btc_detection = create_detection(PTOKEN_BTC_3X_LONG_VAULT, BTC_DEPOSIT_COUNT)
    hood_detection = create_detection(PTOKEN_HOOD_3X_LONG_VAULT, HOOD_DEPOSIT_COUNT)
    unrelated_row = {"Name": "Unrelated vault", "Protocol": "Morpho"}
    vault_db = VaultDatabase(
        rows={
            btc_spec: {"Protocol": "Arcus", "features": set(btc_detection.features), "_detection_data": btc_detection, "_manager_name": "Arcus", "_description": "Old Arcus description."},
            hood_spec: {"Protocol": "<unknown ERC-7540>", "features": set(hood_detection.features), "_detection_data": hood_detection, "_manager_name": None, "_description": None},
            other_spec: unrelated_row,
        },
        leads={other_spec: object()},
        last_scanned_block={PTOKEN_CHAIN_ID: 50_000_000},
    )
    features = {ERC4626Feature.ptoken_like, ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like}
    monkeypatch.setattr(module, "detect_vault_features", lambda *_args, **_kwargs: features)
    monkeypatch.setattr(module, "create_vault_scan_record", lambda _web3, detection, *_args: {"Protocol": "pToken", "features": set(detection.features), "_detection_data": detection, "_manager_name": None, "_description": "Currently not yet identified."})
    web3 = type("Web3Stub", (), {"eth": type("EthStub", (), {"chain_id": PTOKEN_CHAIN_ID, "block_number": 50_000_010})()})()

    result = module.migrate_ptoken_metadata(web3, vault_db, token_cache=object(), dry_run=False, updated_at=UPDATED_AT)
    capsys.readouterr()

    assert result.inspected_rows == TARGET_COUNT
    assert result.migrated_rows == TARGET_COUNT
    assert result.seeded_leads == TARGET_COUNT
    assert vault_db.rows[btc_spec]["Protocol"] == "pToken"
    assert vault_db.rows[hood_spec]["Protocol"] == "pToken"
    assert vault_db.rows[btc_spec]["_detection_data"].deposit_count == BTC_DEPOSIT_COUNT
    assert vault_db.rows[hood_spec]["_detection_data"].deposit_count == HOOD_DEPOSIT_COUNT
    assert vault_db.rows[btc_spec]["_detection_data"].updated_at == UPDATED_AT
    assert vault_db.rows[other_spec] is unrelated_row
    assert vault_db.last_scanned_block == {PTOKEN_CHAIN_ID: 50_000_000}


def test_migrate_ptoken_metadata_dry_run_does_not_mutate(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Report pToken changes without mutating the database.

    :param monkeypatch:
        Pytest RPC and scanner stubs.
    :param capsys:
        Captured migration report output.
    """

    module = load_migration_module()
    btc_spec = VaultSpec(PTOKEN_CHAIN_ID, PTOKEN_BTC_3X_LONG_VAULT)
    hood_spec = VaultSpec(PTOKEN_CHAIN_ID, PTOKEN_HOOD_3X_LONG_VAULT)
    btc_detection = create_detection(PTOKEN_BTC_3X_LONG_VAULT, BTC_DEPOSIT_COUNT)
    hood_detection = create_detection(PTOKEN_HOOD_3X_LONG_VAULT, HOOD_DEPOSIT_COUNT)
    vault_db = VaultDatabase(rows={btc_spec: {"Protocol": "Arcus", "features": set(btc_detection.features), "_detection_data": btc_detection}, hood_spec: {"Protocol": "Arcus", "features": set(hood_detection.features), "_detection_data": hood_detection}})
    features = {ERC4626Feature.ptoken_like, ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like}
    monkeypatch.setattr(module, "detect_vault_features", lambda *_args, **_kwargs: features)
    monkeypatch.setattr(module, "create_vault_scan_record", lambda *_args: {"Protocol": "pToken"})
    web3 = type("Web3Stub", (), {"eth": type("EthStub", (), {"chain_id": PTOKEN_CHAIN_ID, "block_number": 50_000_010})()})()

    result = module.migrate_ptoken_metadata(web3, vault_db, token_cache=object(), dry_run=True, updated_at=UPDATED_AT)
    capsys.readouterr()

    assert result.migrated_rows == TARGET_COUNT
    assert vault_db.rows[btc_spec]["Protocol"] == "Arcus"
    assert btc_spec not in vault_db.leads


def test_create_backup_path_does_not_overwrite_existing_backups(tmp_path: Path) -> None:
    """Create incremented pToken backup names.

    :param tmp_path:
        Temporary metadata directory.
    """

    module = load_migration_module()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    (tmp_path / "vault-metadata-db.pickle.bak-ptoken-metadata").touch()
    (tmp_path / "vault-metadata-db.pickle.bak-ptoken-metadata.1").touch()

    assert module.create_backup_path(vault_db_path) == tmp_path / "vault-metadata-db.pickle.bak-ptoken-metadata.2"
