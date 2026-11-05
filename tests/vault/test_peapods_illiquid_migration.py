"""Verify the Peapods USDC-65 warning and its isolated metadata refresh."""

from pathlib import Path

import pytest

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import BAD_FLAGS, PEAPODS_ILLIQUID, VaultFlag, get_notes, get_vault_special_flags
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: Reviewed deployment and numbered repair under test.
TARGET = VaultSpec(8453, "0x2e5109aa82a4ebcc776e89abeb68f5d0b91ccea1")
SCRIPT_NAME = "032-peapods-usdc-65-illiquid.py"
#: The persisted flags and note are the only fields refreshed.
EXPECTED_UPDATED_FIELDS = 2


def test_peapods_illiquid_note_and_flag() -> None:
    """Resolve the maintained warning and exclude the vault from investment.

    The existing shared Peapods message must accompany the exclusionary flag.

    :return: ``None`` after checking manual resolver results.
    """
    flags = get_vault_special_flags(TARGET.vault_address, "Peapods")
    assert flags == {VaultFlag.illiquid}
    assert flags & BAD_FLAGS
    assert get_notes(TARGET.vault_address, chain_id=TARGET.chain_id, protocol_name="Peapods") == PEAPODS_ILLIQUID


@pytest.fixture
def peapods_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Prepare stale metadata and historical sentinel files in temporary storage.

    Include another chain with the same address to detect an overly broad repair.
    Only this task's script is made available to the runner.

    :param tmp_path: Temporary directory owned by pytest.
    :param monkeypatch: Environment isolation for legacy database overrides.
    :return: Pipeline data and single-migration resource directories.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = {
        TARGET: {"Protocol": "Peapods", "_flags": {VaultFlag.paused}, "_notes": "stale", "custom": 123},
        VaultSpec(1, TARGET.vault_address): {"Protocol": "Other", "_notes": "Keep other chain"},
        VaultSpec(8453, "0x0000000000000000000000000000000000000001"): {"Protocol": "Peapods", "_notes": "Keep unrelated vault"},
    }
    VaultDatabase(rows=rows, last_scanned_block={8453: 12345}).write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical data")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    return data_dir, scripts_dir


def test_peapods_preview_apply_and_retry(peapods_migration: tuple[Path, Path]) -> None:
    """Persist only the warning with byte-preserving preview and safe retries.

    Compare the entire database and historical files, and check backup contents.
    Re-execute the transformation directly to verify idempotency beyond the journal.

    :param peapods_migration: Isolated pipeline and migration resources.
    :return: ``None`` after checking preview, apply, backup and repeat execution.
    """
    data_dir, scripts_dir = peapods_migration
    path = data_dir / "vault-metadata-db.pickle"
    original = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    before = VaultDatabase.read(path)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    assert len(runner.run_pending_migrations(dry_run=True, **options)) == 1
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == original
    assert len(runner.run_pending_migrations(dry_run=False, **options)) == 1
    after = VaultDatabase.read(path)
    before.rows[TARGET].update(_flags={VaultFlag.paused, VaultFlag.illiquid}, _notes=PEAPODS_ILLIQUID)
    assert after == before
    assert all((data_dir / name).read_bytes() == value for name, value in original.items() if name != path.name)
    entry = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]
    assert entry["other_data"]["inspected_rows"] == 1
    assert entry["other_data"]["updated_fields"] == EXPECTED_UPDATED_FIELDS
    assert Path(entry["other_data"]["backup_paths"][0]).read_bytes() == original[path.name]
    applied = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    context = runner.MigrationContext(data_dir, False, script_name=script.name)
    runner._execute(script, context)
    assert context.other_data["updated_fields"] == 0
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == applied


@pytest.mark.parametrize("state", ["missing", "wrong_protocol", "no_flags"])
def test_peapods_target_states(peapods_migration: tuple[Path, Path], state: str) -> None:
    """Report missing rows, reject conflicting identities and handle absent flags.

    Missing or invalid targets must leave metadata bytes unchanged. A row without
    stored flags must still receive the maintained warning.

    :param peapods_migration: Isolated pipeline and migration resources.
    :param state: Target metadata condition to exercise.
    :return: ``None`` after checking persistence and reporting.
    """
    data_dir, scripts_dir = peapods_migration
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    if state == "missing":
        del database.rows[TARGET]
    elif state == "wrong_protocol":
        database.rows[TARGET]["Protocol"] = "Other"
    else:
        database.rows[TARGET].pop("_flags")
    database.write(path)
    before = path.read_bytes()
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "dry_run": False, "provenance": {"in_docker": False}}
    if state == "wrong_protocol":
        with pytest.raises(ValueError, match="Unexpected protocol"):
            runner.run_pending_migrations(**options)
    else:
        runner.run_pending_migrations(**options)
        entry = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]
        assert entry["other_data"]["missing_specs"] == ([TARGET.as_string_id()] if state == "missing" else [])
        if state == "no_flags":
            row = VaultDatabase.read(path).rows[TARGET]
            assert row["_flags"] == {VaultFlag.illiquid}
            assert row["_notes"] == PEAPODS_ILLIQUID
            return
        assert entry["other_data"]["updated_fields"] == 0
    assert path.read_bytes() == before
    assert not list(data_dir.glob("*.bak*"))
