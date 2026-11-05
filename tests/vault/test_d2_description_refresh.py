"""Verify the later D2 description refresh without touching historical data."""

from pathlib import Path

import pytest

from eth_defi.erc_4626.vault_protocol.d2.vault import D2_GLOBAL_MACRO_DESCRIPTION, D2_UNDISCLOSED_SHORT_DESCRIPTION, D2_VAULT_SHORT_DESCRIPTIONS, format_d2_vault_note
from eth_defi.vault.base import VaultSpec, normalise_vault_description
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

SCRIPT_NAME = "035-d2-vault-descriptions.py"
HYPE_ETHENA = VaultSpec(1, "0x2406aacbdf8463176deb285adaa81768415b6c7e")
GLOBAL_MACRO = VaultSpec(4663, "0x3a87f340c7aa52694c3dd806ba13d1b2ebf48dd8")


@pytest.mark.parametrize("target_state,note_state", [("present", "legacy"), ("missing", "legacy"), ("conflicting", "legacy"), ("present", "manual"), ("present", "appended"), ("present", "empty")])
def test_d2_description_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_state: str, note_state: str) -> None:
    """Refresh every researched summary and handle undocumented deployments.

    Exercise the actual runner's preview, backup, apply and retry paths, with
    all reviewed products and distinct sentinels for unrelated fields and files.

    :param tmp_path: Isolated migration and pipeline directory.
    :param monkeypatch: Environment isolation for database paths.
    :param target_state: Present, missing or incorrectly attributed GlobalMacro.
    :param note_state: Previously rendered note, custom text, appended warning or empty.
    :return: ``None`` after verifying scope, persistence and idempotency.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    (scripts_dir / SCRIPT_NAME).write_bytes(script.source)
    rows = {VaultSpec(chain, address): {"Protocol": "D2 Finance"} for chain, address in D2_VAULT_SHORT_DESCRIPTIONS}
    unknown = VaultSpec(1, GLOBAL_MACRO.vault_address)
    other = VaultSpec(2, GLOBAL_MACRO.vault_address)
    rows[unknown] = {"Protocol": "D2 Finance"}
    rows[other] = {"Protocol": "Other"}
    for row in rows.values():
        row.update(_short_description=None, _description="Preserve long description", _notes="Preserve note", _flags={"sentinel"}, custom=123)
    legacy_note = format_d2_vault_note(HYPE_ETHENA.vault_address)
    rows[HYPE_ETHENA]["_notes"] = {"legacy": legacy_note, "manual": "Keep manual warning", "appended": legacy_note + "\n\nKeep incident warning", "empty": None}[note_state]
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
        with pytest.raises(ValueError, match="unexpected protocol"):
            runner.run_pending_migrations(dry_run=True, **options)
        assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == original
        with pytest.raises(ValueError, match="unexpected protocol"):
            runner.run_pending_migrations(dry_run=False, **options)
        assert all((data_dir / name).read_bytes() == value for name, value in original.items())
        assert not list(data_dir.glob("*.bak*"))
        return
    runner.run_pending_migrations(dry_run=True, **options)
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == original
    runner.run_pending_migrations(dry_run=False, **options)
    for spec, row in expected.rows.items():
        if row["Protocol"] == "D2 Finance":
            row["_short_description"] = D2_VAULT_SHORT_DESCRIPTIONS.get((spec.chain_id, spec.vault_address), D2_UNDISCLOSED_SHORT_DESCRIPTION)
    if target_state == "present":
        expected.rows[GLOBAL_MACRO]["_description"] = normalise_vault_description(D2_GLOBAL_MACRO_DESCRIPTION)
    refresh_note = note_state in {"legacy", "empty"}
    if refresh_note:
        expected.rows[HYPE_ETHENA]["_notes"] = format_d2_vault_note(HYPE_ETHENA.vault_address, chain_id=HYPE_ETHENA.chain_id)
    assert VaultDatabase.read(path) == expected
    assert all((data_dir / name).read_bytes() == value for name, value in original.items() if name != path.name)
    report = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]["other_data"]
    assert report["missing_specs"] == ([GLOBAL_MACRO.as_string_id()] if target_state == "missing" else [])
    assert report["changed_rows"] == len(rows) - 1
    assert report["updated_fields"] == len(rows) - 1 + (target_state == "present") + refresh_note
    assert report["preserved_note_specs"] == ([] if refresh_note else [HYPE_ETHENA.as_string_id()])
    assert Path(report["backup_paths"][0]).read_bytes() == original[path.name]
    applied = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    context = runner.MigrationContext(data_dir, False, script_name=SCRIPT_NAME)
    runner._execute(script, context)
    assert context.other_data["updated_fields"] == 0
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == applied
