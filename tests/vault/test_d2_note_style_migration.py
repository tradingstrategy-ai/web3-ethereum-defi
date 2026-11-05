"""Exercise the D2 note refresh through the real migration runner."""

import runpy
from pathlib import Path

import pytest

from eth_defi.erc_4626.vault_protocol.d2.vault import D2_TEXAS_HEDGE_ADDRESS, format_d2_texas_hedge_note, format_d2_vault_note
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

SCRIPT_NAME = "034-d2-vault-note-style.py"


def test_d2_note_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: PLR0914 - Seven independent preservation cases share one real runner invocation.
    """Refresh legacy notes without losing stored warnings or other fields.

    Exercise both legacy versions, empty notes, manual overrides, appended
    warnings, protocol isolation, byte-preserving preview and idempotency.

    :param tmp_path: Temporary pipeline and migration resources.
    :param monkeypatch: Isolated environment for the migration runner.
    :return: ``None`` after checking persistence, backups and export inputs.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    legacy = runpy.run_path(str(script.path))["legacy_notes"]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / SCRIPT_NAME).write_bytes(script.source)
    generic = VaultSpec(4663, "0x3a87f340c7aa52694c3dd806ba13d1b2ebf48dd8")
    texas = VaultSpec(999, D2_TEXAS_HEDGE_ADDRESS)
    empty = VaultSpec(42161, "0x0000000000000000000000000000000000000001")
    manual = VaultSpec(42161, "0x64167cd42859f64cff2aa4b63c3175ccef9659dd")
    custom = VaultSpec(42161, "0x0000000000000000000000000000000000000002")
    other = VaultSpec(1, generic.vault_address)
    hxxi = VaultSpec(999, "0xf44f49e6577b3934f981c6f0629d15154d2606e6")
    # Literal legacy output includes a hyphenated article URL; prose spelling
    # normalisation must never change link targets during recognition.
    hxxi_note = """D2 Finance strategy vault.

**Summary:** This vault is a D2 Finance managed strategy vault. D2 strategies are tokenised derivative strategies executed on-chain with proprietary off-chain trading logic and epoch-based funding, trading and withdrawal phases.

The authoritative D2 source for this vault is the [D2 strategy page](https://d2.finance/strategies/0xf44f49e6577b3934f981c6f0629d15154d2606e6).

D2 also describes this strategy in its [D2 strategy article](https://medium.com/@D2.Finance/introducing-hyperliquid-xxi-hxxi-the-future-of-bitcoin-accumulation-on-chain-499c488e67ca).
"""
    rows = {
        hxxi: {"Protocol": "D2 Finance", "_notes": hxxi_note},
        generic: {"Protocol": "D2 Finance", "_notes": min(legacy(generic.vault_address), key=len).replace("onchain", "on-chain").replace("offchain", "off-chain")},
        texas: {"Protocol": "D2 Finance", "_notes": max(legacy(texas.vault_address), key=len)},
        empty: {"Protocol": "D2 Finance", "_notes": None},
        manual: {"Protocol": "D2 Finance", "_notes": "Manual warning kept verbatim"},
        custom: {"Protocol": "D2 Finance", "_notes": max(legacy(custom.vault_address), key=len) + "\n\nIncident warning"},
        other: {"Protocol": "Other", "_notes": min(legacy(other.vault_address), key=len)},
    }
    for row in rows.values():
        row.update(_description="Keep description", _short_description="Keep summary", _flags={VaultFlag.trading}, custom=123)
    path = data_dir / "vault-metadata-db.pickle"
    VaultDatabase(rows=rows, last_scanned_block={4663: 12345}).write(path)
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical data")
    before = VaultDatabase.read(path)
    original = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    runner.run_pending_migrations(dry_run=True, **options)
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == original
    runner.run_pending_migrations(dry_run=False, **options)
    before.rows[hxxi]["_notes"] = format_d2_vault_note(hxxi.vault_address)
    before.rows[generic]["_notes"] = format_d2_vault_note(generic.vault_address)
    before.rows[texas]["_notes"] = format_d2_texas_hedge_note()
    before.rows[empty]["_notes"] = format_d2_vault_note(empty.vault_address)
    assert VaultDatabase.read(path) == before
    assert all((data_dir / name).read_bytes() == value for name, value in original.items() if name != path.name)
    report = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]["other_data"]
    assert report["updated_fields"] == len((generic, texas, empty, hxxi))
    assert set(report["preserved_note_specs"]) == {manual.as_string_id(), custom.as_string_id()}
    assert Path(report["backup_paths"][0]).read_bytes() == original[path.name]
    applied = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    context = runner.MigrationContext(data_dir, False, script_name=SCRIPT_NAME)
    runner._execute(script, context)
    assert context.other_data["updated_fields"] == 0
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == applied
