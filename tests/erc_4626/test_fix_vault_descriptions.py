"""Test persisted Markdown descriptions and single-line first-sentence summaries."""

import importlib.util
from copy import deepcopy
from pathlib import Path
from types import ModuleType

import pytest

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.fixture
def repair_module() -> ModuleType:
    """Load the hyphenated repair script for focused metadata tests.

    :return:
        Module exposing the repair function and environment-driven entrypoint.
    """
    script = Path(__file__).resolve().parents[2] / "eth_defi" / "vault_protocols" / "repairs" / "descriptions.py"
    spec = importlib.util.spec_from_file_location("fix_vault_descriptions", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def vault_db() -> VaultDatabase:
    """Create mixed-protocol rows alongside scanner state to preserve.

    :return:
        Small metadata database containing dirty, missing and empty fields.
    """
    nest_spec = VaultSpec(43114, "0xd258029cf5a177e3306e09fbea63424543a505c0")
    return VaultDatabase(
        rows={
            nest_spec: {
                "Protocol": "Nest",
                "_description": r"First.\n\nSecond.",
                "_short_description": r"Short.\nSummary.",
                "_notes": "Preserved\nnotes.",
                "manual_field": "preserve",
            },
            VaultSpec(1, "0x0000000000000000000000000000000000000001"): {
                "Protocol": "Morpho",
                "_description": "Leave\r\nunchanged.",
                "_short_description": r"Keep\nthis.",
            },
            VaultSpec(1, "0x0000000000000000000000000000000000000002"): {
                "Protocol": "Nest",
                "_description": None,
                "_short_description": "",
            },
            VaultSpec(1, "0x0000000000000000000000000000000000000003"): {"Protocol": "Nest"},
            VaultSpec(1, "0x09252d2c4afca9b1479efdd39faa53de9ff23114"): {
                "Protocol": "Lagoon Finance",
                "_description": "Full Coinshift strategy. Keep all details.",
                "_short_description": "Coinshift leveraged strategy. More details belong in the full description.",
                "_notes": "Preserve Coinshift notes.",
            },
        },
        leads={nest_spec: "preserved lead"},
        last_scanned_block={43114: 90_379_027},
    )


def test_repair_vault_descriptions_dry_run(repair_module: ModuleType, vault_db: VaultDatabase) -> None:
    """Report dirty fields across protocols without changing rows or state.

    :param repair_module:
        Imported migration script.
    :param vault_db:
        Mixed-protocol metadata fixture.
    """
    before = deepcopy(vault_db)
    updates = repair_module.repair_vault_descriptions(vault_db, dry_run=True)
    assert tuple(update.field for update in updates) == ("_description", "_short_description", "_description", "_short_description", "_short_description")
    assert vault_db == before


def test_repair_vault_descriptions_preserves_state(repair_module: ModuleType, vault_db: VaultDatabase) -> None:
    """Change only description fields and make repeated application a no-op.

    :param repair_module:
        Imported migration script.
    :param vault_db:
        Mixed-protocol metadata fixture.
    """
    before = deepcopy(vault_db)
    updates = repair_module.repair_vault_descriptions(vault_db, dry_run=False)
    assert tuple(update.field for update in updates) == ("_description", "_short_description", "_description", "_short_description", "_short_description")
    nest_spec = updates[0].spec
    expected = deepcopy(before.rows)
    expected[nest_spec]["_description"] = "First.\n\nSecond."
    expected[nest_spec]["_short_description"] = "Short."
    morpho_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    expected[morpho_spec]["_description"] = "Leave\nunchanged."
    expected[morpho_spec]["_short_description"] = "Keep this."
    coinshift_spec = VaultSpec(1, "0x09252d2c4afca9b1479efdd39faa53de9ff23114")
    expected[coinshift_spec]["_short_description"] = "Coinshift leveraged strategy."
    assert vault_db.rows == expected
    assert vault_db.leads == before.leads
    assert vault_db.last_scanned_block == before.last_scanned_block
    assert repair_module.repair_vault_descriptions(vault_db, dry_run=False) == ()


def test_repair_vault_description_file(repair_module: ModuleType, vault_db: VaultDatabase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default to a read-only dry run and persist only an explicitly applied repair.

    :param repair_module:
        Imported migration script.
    :param vault_db:
        Mixed-protocol metadata fixture.
    :param tmp_path:
        Isolated directory for the real pickle write.
    :param monkeypatch:
        Fixture configuring the script's environment variables.
    """
    path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(path)
    original_bytes = path.read_bytes()
    monkeypatch.setenv("VAULT_DB_PATH", str(path))
    monkeypatch.delenv("DRY_RUN", raising=False)

    repair_module.main()
    assert path.read_bytes() == original_bytes

    monkeypatch.setenv("DRY_RUN", "false")
    repair_module.main()
    persisted = VaultDatabase.read(path)
    repair_module.repair_vault_descriptions(vault_db, dry_run=False)
    assert persisted == vault_db

    written_at = path.stat().st_mtime_ns
    repair_module.main()
    assert path.stat().st_mtime_ns == written_at


def test_repair_vault_description_rejects_invalid_dry_run(repair_module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject a misspelt boolean before opening any metadata file.

    :param repair_module:
        Imported migration script.
    :param monkeypatch:
        Fixture configuring the invalid environment value.
    """
    monkeypatch.setenv("DRY_RUN", "treu")
    with pytest.raises(ValueError, match="DRY_RUN"):
        repair_module.main()
