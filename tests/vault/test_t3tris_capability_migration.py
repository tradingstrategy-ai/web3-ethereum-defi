"""Verify the numbered T3tris capability migration on isolated persistent state."""

import datetime
from pathlib import Path

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

SCRIPT_NAME = "036-t3tris-deposit-manager.py"
ARBITRUM_VAULT = VaultSpec(42161, "0x98e43a491a464f0886bc5e57207c340bbed0d01f")
ROBINHOOD_VAULT = VaultSpec(4663, "0x5b93dd3eb7fd224565498045f5e1a2ebda49e672")
OTHER = VaultSpec(42161, "0x0000000000000000000000000000000000000001")


def _row(spec: VaultSpec, features: set[ERC4626Feature]) -> dict:
    """Build a stored row with an old capability and unrelated enrichment.

    :param spec: Vault identity.
    :param features: Detected protocol features.
    :return: Catalogue row.
    """
    timestamp = datetime.datetime(2026, 1, 1)
    detection = ERC4262VaultDetection(spec.chain_id, spec.vault_address, 1, timestamp, features, timestamp, 10, 20)
    return {"Protocol": "T3tris", "_detection_data": detection, "_deposit_manager": None, "_notes": "Preserve note", "NAV": 999}


def test_t3tris_capability_preview_apply_and_preservation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Preview changes nothing, apply refreshes only T3tris capabilities, rerun skips.

    1. Write a database with two T3tris rows and one unrelated row.
    2. Preview leaves every file untouched.
    3. Apply sets the asynchronous capability and preserves everything else.
    """
    # 1. Write a database with two T3tris rows and one unrelated row.
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    other_row = {"Protocol": "Other", "_deposit_manager": {"can_deposit": True}, "_detection_data": ERC4262VaultDetection(42161, OTHER.vault_address, 1, datetime.datetime(2026, 1, 1), {ERC4626Feature.atoma_like}, datetime.datetime(2026, 1, 1), 1, 1)}
    database = VaultDatabase(
        rows={
            ARBITRUM_VAULT: _row(ARBITRUM_VAULT, {ERC4626Feature.t3tris_like}),
            ROBINHOOD_VAULT: _row(ROBINHOOD_VAULT, {ERC4626Feature.t3tris_like}),
            OTHER: other_row,
        },
        last_scanned_block={42161: 123},
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "vault-metadata-db.pickle"
    database.write(path)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(item for item in runner.discover_migrations() if item.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}

    # 2. Preview leaves every file untouched.
    original = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=True, **options) == [SCRIPT_NAME]
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == original

    # 3. Apply sets the asynchronous capability and preserves everything else.
    assert runner.run_pending_migrations(dry_run=False, **options) == [SCRIPT_NAME]
    result = VaultDatabase.read(path)
    for spec in (ARBITRUM_VAULT, ROBINHOOD_VAULT):
        row = result.rows[spec]
        assert row["_deposit_manager"] == {"can_deposit": True, "can_redeem": True, "deposit_flow": "asynchronous", "redemption_flow": "asynchronous", "supports_anvil_settlement": True}
        assert row["_notes"] == "Preserve note"
        assert row["NAV"] == 999
    assert result.rows[OTHER]["_deposit_manager"] == {"can_deposit": True}
    assert result.last_scanned_block == database.last_scanned_block
    assert runner.run_pending_migrations(dry_run=False, **options) == []
