"""Test the Enzyme current-metadata migration entry point."""

import importlib.util
import os
from pathlib import Path
from types import ModuleType

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "enzyme" / "migrate-current-metadata.py"


def load_migration_module() -> ModuleType:
    """Load the hyphenated metadata migration as a Python module.

    :return: Imported migration module.
    """

    spec = importlib.util.spec_from_file_location("enzyme_migrate_current_metadata", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_metadata_migration_forces_incremental_non_price_mode() -> None:
    """Prevent operator environment values from widening the migration scope."""

    module = load_migration_module()
    environment = {
        "ENZYME_SCAN_PRICES": "true",
        "ENZYME_CLEAN_PRICES": "true",
        "ENZYME_REFRESH_EXISTING_METADATA": "true",
    }

    module.configure_metadata_migration_environment(environment)

    assert environment == {
        "ENZYME_SCAN_PRICES": "false",
        "ENZYME_CLEAN_PRICES": "false",
        "ENZYME_REFRESH_EXISTING_METADATA": "false",
        "ENZYME_CHECKPOINT_PATH": str(module.DEFAULT_VAULT_DATABASE.with_name("enzyme-current-metadata-state.json")),
    }


def test_metadata_migration_delegates_to_shared_resumable_engine(monkeypatch) -> None:
    """Use the reviewed Enzyme backfill implementation instead of duplicating it."""

    module = load_migration_module()
    calls = []
    monkeypatch.setattr(module.runpy, "run_path", lambda path, run_name: calls.append((Path(path), run_name)))
    monkeypatch.setenv("ENZYME_SCAN_PRICES", "true")
    monkeypatch.setenv("ENZYME_CLEAN_PRICES", "true")
    monkeypatch.setenv("ENZYME_REFRESH_EXISTING_METADATA", "true")
    monkeypatch.delenv("ENZYME_CHECKPOINT_PATH", raising=False)

    module.main()

    assert calls == [(SCRIPT_PATH.with_name("backfill-history.py"), "__main__")]
    assert os.environ["ENZYME_SCAN_PRICES"] == "true"
    assert os.environ["ENZYME_CLEAN_PRICES"] == "true"
    assert os.environ["ENZYME_REFRESH_EXISTING_METADATA"] == "true"
    assert "ENZYME_CHECKPOINT_PATH" not in os.environ


def test_metadata_migration_preserves_explicit_checkpoint(tmp_path: Path) -> None:
    """Allow an operator to relocate the metadata-only checkpoint deliberately."""

    module = load_migration_module()
    checkpoint_path = tmp_path / "custom-enzyme-metadata-state.json"
    environment = {"ENZYME_CHECKPOINT_PATH": str(checkpoint_path)}

    module.configure_metadata_migration_environment(environment)

    assert environment["ENZYME_CHECKPOINT_PATH"] == str(checkpoint_path)
