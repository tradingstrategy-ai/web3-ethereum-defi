"""Verify the reviewed illiquidity refresh using isolated persisted metadata."""

from pathlib import Path

import pytest

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import BAD_FLAGS, VaultFlag, get_notes, get_vault_special_flags, is_flagged_vault
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: Reviewed deployments, expected identities and public warning text.
TARGETS = ((VaultSpec(143, "0x6b343f7b797f1488aa48c49d540690f2b2c89751"), "Gearbox", "EDGE UltraYield USDC is illiquid."),)
SCRIPT_NAME = "025-edge-ultrayield-usdc-illiquid.py"
#: Only persisted flags and notes are refreshed.
EXPECTED_UPDATED_FIELDS = 2


def test_edge_ultrayield_illiquid_note_and_flag() -> None:
    """Exclude the Monad vault while preserving the Base review status.

    The public URL identifies the Gearbox deployment rather than the similarly
    named Morpho vault. Check both manual lookups to prevent a mistaken target.

    :return: ``None`` after validating flags and the exact public note.
    """
    spec, protocol, note = TARGETS[0]
    assert get_vault_special_flags(spec.vault_address, protocol) == {VaultFlag.illiquid}
    assert get_notes(spec.vault_address, chain_id=spec.chain_id, protocol_name=protocol) == note
    assert is_flagged_vault(spec.vault_address, protocol)
    base_address = "0x5435bc53f2c61298167cdb11cdf0db2bfa259ca0"
    assert get_vault_special_flags(base_address, "Morpho") == {VaultFlag.review_needed}
    assert not get_vault_special_flags(base_address, "Morpho") & BAD_FLAGS


@pytest.fixture
def edge_illiquid_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Isolate the reviewed refresh and historical sentinel files.

    Include same-address deployments on another chain to detect an accidentally
    widened update. All storage belongs to pytest, never the operator database.

    :param tmp_path: Temporary directory owned by pytest.
    :param monkeypatch: Environment isolation for legacy path overrides.
    :return: Pipeline data and single-migration resource directories.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = {spec: {"Protocol": protocol, "_flags": {VaultFlag.paused}, "_notes": "stale", "custom": 123} for spec, protocol, _ in TARGETS}
    rows.update({VaultSpec(8453, spec.vault_address): {"Protocol": protocol, "_notes": "untouched"} for spec, protocol, _ in TARGETS})
    rows[VaultSpec(8453, "0x5435bc53f2c61298167cdb11cdf0db2bfa259ca0")] = {
        "Protocol": "Morpho",
        "_flags": {VaultFlag.review_needed},
        "_notes": "Keep the Base deployment unchanged",
    }
    VaultDatabase(rows=rows, last_scanned_block={143: 12345}).write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical data")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    return data_dir, scripts_dir


def test_illiquid_preview_apply_and_retry(edge_illiquid_migration: tuple[Path, Path]) -> None:
    """Persist only reviewed warnings, with preview, backup and retry safety.

    Compare complete rows and historical bytes to guard against collateral data
    changes. A second application must be skipped without rewriting any file.

    :param edge_illiquid_migration: Isolated data and migration directories.
    :return: ``None`` after validating the runner's preview and apply paths.
    """
    data_dir, scripts_dir = edge_illiquid_migration
    path = data_dir / "vault-metadata-db.pickle"
    original = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    before = VaultDatabase.read(path)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    assert len(runner.run_pending_migrations(dry_run=True, **options)) == 1
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == original
    assert len(runner.run_pending_migrations(dry_run=False, **options)) == 1
    after = VaultDatabase.read(path)
    for spec, protocol, note in TARGETS:
        assert after.rows[spec]["_flags"] == {VaultFlag.paused, VaultFlag.illiquid}
        assert after.rows[spec]["_notes"] == note
        assert get_notes(spec.vault_address, chain_id=spec.chain_id, protocol_name=protocol) == note
        before.rows[spec].update(_flags={VaultFlag.paused, VaultFlag.illiquid}, _notes=note)
    assert after == before
    assert all((data_dir / name).read_bytes() == payload for name, payload in original.items() if name != path.name)
    entry = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]
    assert entry["other_data"]["changed_rows"] == 1
    assert entry["other_data"]["updated_fields"] == EXPECTED_UPDATED_FIELDS
    assert Path(entry["other_data"]["backup_paths"][0]).read_bytes() == original[path.name]
    applied = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == applied


@pytest.mark.parametrize("state", ["missing", "wrong_protocol", "current"])
def test_illiquid_target_validation(edge_illiquid_migration: tuple[Path, Path], state: str) -> None:
    """Reject invalid inventories before staging updates and avoid no-op writes.

    An invalid target must leave the staged database unchanged.
    Already-current rows need no new backup.

    :param edge_illiquid_migration: Isolated data and migration directories.
    :param state: Invalid or already-current metadata condition.
    :return: ``None`` after checking staging and persistence invariants.
    """
    data_dir, _ = edge_illiquid_migration
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    target = TARGETS[-1][0]
    if state == "missing":
        del database.rows[target]
    elif state == "wrong_protocol":
        database.rows[target]["Protocol"] = "Other"
    else:
        for spec, _, note in TARGETS:
            database.rows[spec].update(_flags={VaultFlag.paused, VaultFlag.illiquid}, _notes=note)
    database.write(path)
    original = path.read_bytes()
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    context = runner.MigrationContext(data_dir, False, script_name=script.name)
    if state == "current":
        runner._execute(script, context)
        assert context.other_data["updated_fields"] == 0
        assert context.other_data["changed_rows"] == 0
    else:
        with pytest.raises(ValueError, match="missing" if state == "missing" else "Unexpected protocol"):
            runner._execute(script, context)
        assert context.metadata() == database
    assert path.read_bytes() == original
    assert not list(data_dir.glob("*.bak*"))
