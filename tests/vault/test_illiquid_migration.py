"""Verify the reviewed illiquidity refresh using isolated persisted metadata."""

from pathlib import Path

import pytest

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import VaultFlag, get_notes
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: Reviewed deployments, expected identities and public warning text.
TARGETS = (
    (VaultSpec(1, "0xa6f6887ed116f3c41497a837a0319264a2eef3fa"), "Silo Finance", "Borrowable USDC Deposit, SiloId: 148 is illiquid."),
    (VaultSpec(1, "0xff5b00730ba463994fe0dbf574c538da258bc2ca"), "Euler", "Apostro USDC Core is illiquid."),
    (VaultSpec(146, "0x17271da949bbd4713c7c599759e2bf30604fc8da"), "Euler", "Mainstreet Greenhouse USDC is illiquid."),
)
SCRIPT_NAME = "021-reviewed-vaults-illiquid.py"


@pytest.fixture
def illiquid_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
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
    VaultDatabase(rows=rows, last_scanned_block={1: 12345, 146: 67890}).write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical data")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    return data_dir, scripts_dir


def test_illiquid_preview_apply_and_retry(illiquid_migration: tuple[Path, Path]) -> None:
    """Persist only reviewed warnings, with preview, backup and retry safety.

    Compare complete rows and historical bytes to guard against collateral data
    changes. A second application must be skipped without rewriting any file.

    :param illiquid_migration: Isolated data and migration directories.
    :return: ``None`` after validating the runner's preview and apply paths.
    """
    data_dir, scripts_dir = illiquid_migration
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
    assert entry["other_data"]["changed_rows"] == 3
    assert entry["other_data"]["updated_fields"] == 6
    assert Path(entry["other_data"]["backup_paths"][0]).read_bytes() == original[path.name]
    applied = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == applied


@pytest.mark.parametrize("state", ["missing", "wrong_protocol", "current"])
def test_illiquid_target_validation(illiquid_migration: tuple[Path, Path], state: str) -> None:
    """Reject invalid inventories before staging updates and avoid no-op writes.

    Corrupt the final target to ensure earlier valid rows are not changed in
    memory when validation fails. Already-current rows need no new backup.

    :param illiquid_migration: Isolated data and migration directories.
    :param state: Invalid or already-current metadata condition.
    :return: ``None`` after checking staging and persistence invariants.
    """
    data_dir, _ = illiquid_migration
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
