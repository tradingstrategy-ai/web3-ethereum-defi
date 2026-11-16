"""Check the complete D2 description refresh preserves unrelated persisted state."""

from pathlib import Path

import pytest

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.d2.vault import D2_GLOBAL_MACRO_ADDRESS, D2_VAULT_DESCRIPTIONS, D2Vault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

SCRIPT_NAME = "046-d2-catalogue-descriptions.py"
GLOBAL_MACRO = VaultSpec(4663, D2_GLOBAL_MACRO_ADDRESS)
REVIEWED_TARGET_COUNT = 54


@pytest.mark.parametrize("target_state", ["present", "missing", "conflicting"])
def test_d2_catalogue_description_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_state: str) -> None:
    """Exercise preview, scoped apply, failure and retry for the frozen catalogue.

    Use the actual runner and file-backed metadata, including every reviewed
    deployment, an unknown D2 vault and the same address on an unrelated chain.
    Compare all rows and sentinel history files, not just changed-field counts.

    :param tmp_path: Isolated pipeline and migration directories.
    :param monkeypatch: Environment isolation for database paths.
    :param target_state: Existing, missing or incorrectly attributed target.
    :return: ``None`` after verifying persistence, backups and idempotency.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    (scripts_dir / SCRIPT_NAME).write_bytes(script.source)
    targets = {VaultSpec(chain, address) for chain, address in D2_VAULT_DESCRIPTIONS} | {GLOBAL_MACRO}
    assert len(targets) == REVIEWED_TARGET_COUNT
    rows = {spec: {"Protocol": "D2 Finance"} for spec in targets}
    rows[VaultSpec(1, GLOBAL_MACRO.vault_address)] = {"Protocol": "D2 Finance"}
    rows[VaultSpec(2, GLOBAL_MACRO.vault_address)] = {"Protocol": "Other"}
    for row in rows.values():
        row.update(_short_description="Old summary", _description="Old description", _notes="Preserve custom warning", _flags={"sentinel"}, custom=123)
    if target_state == "missing":
        del rows[GLOBAL_MACRO]
    elif target_state == "conflicting":
        rows[GLOBAL_MACRO]["Protocol"] = "Other"
    path = data_dir / "vault-metadata-db.pickle"
    VaultDatabase(rows=rows, last_scanned_block={4663: 12345}).write(path)
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve history")
    original = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    expected = VaultDatabase.read(path)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    if target_state == "conflicting":
        for dry_run in (True, False):
            with pytest.raises(ValueError, match="unexpected protocol"):
                runner.run_pending_migrations(dry_run=dry_run, **options)
            assert all((data_dir / name).read_bytes() == value for name, value in original.items())
            assert not list(data_dir.glob("*.bak*"))
        return
    runner.run_pending_migrations(dry_run=True, **options)
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == original
    runner.run_pending_migrations(dry_run=False, **options)
    for spec in targets & expected.rows.keys():
        vault = D2Vault(None, spec, features={ERC4626Feature.d2_like})
        expected.rows[spec].update(_short_description=vault.short_description, _description=vault.description)
    assert VaultDatabase.read(path) == expected
    assert all((data_dir / name).read_bytes() == value for name, value in original.items() if name != path.name)
    report = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]["other_data"]
    assert report["missing_specs"] == ([GLOBAL_MACRO.as_string_id()] if target_state == "missing" else [])
    assert report["changed_rows"] == len(targets) - (target_state == "missing")
    assert report["updated_fields"] == 2 * report["changed_rows"]
    assert Path(report["backup_paths"][0]).read_bytes() == original[path.name]
    applied = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    context = runner.MigrationContext(data_dir, False, script_name=SCRIPT_NAME)
    runner._execute(script, context)
    assert context.other_data["updated_fields"] == 0
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == applied
