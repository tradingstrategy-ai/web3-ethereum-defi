"""Test the Enzyme Blue current-fee migration entry point."""

import importlib.util
import os
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "enzyme" / "migrate-blue-fees.py"


def load_migration_module() -> ModuleType:
    """Load the hyphenated Blue-fee migration as a Python module.

    :return: Imported migration module.
    """

    spec = importlib.util.spec_from_file_location("enzyme_migrate_blue_fees", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_blue_fee_migration_forces_current_blue_only_mode() -> None:
    """Prevent an operator environment from widening the migration scope."""

    module = load_migration_module()
    environment = {
        "ENZYME_SCAN_PRICES": "true",
        "ENZYME_CLEAN_PRICES": "true",
        "ENZYME_REFRESH_EXISTING_METADATA": "true",
        "ENZYME_REFRESH_BLUE_FEES": "false",
    }

    module.configure_blue_fee_migration_environment(environment)

    assert environment == {
        "ENZYME_SCAN_PRICES": "false",
        "ENZYME_CLEAN_PRICES": "false",
        "ENZYME_REFRESH_EXISTING_METADATA": "false",
        "ENZYME_REFRESH_BLUE_FEES": "true",
        "ENZYME_CHECKPOINT_PATH": str(module.migration.DEFAULT_VAULT_DATABASE.with_name("enzyme-blue-fees-state.json")),
    }


def test_blue_fee_migration_delegates_to_shared_resumable_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the reviewed Enzyme backfill implementation without price writes."""

    module = load_migration_module()
    calls = []
    monkeypatch.setattr(module.migration.runpy, "run_path", lambda path, run_name: calls.append((Path(path), run_name)))
    monkeypatch.setenv("ENZYME_SCAN_PRICES", "true")
    monkeypatch.setenv("ENZYME_CLEAN_PRICES", "true")
    monkeypatch.setenv("ENZYME_REFRESH_EXISTING_METADATA", "true")
    monkeypatch.setenv("ENZYME_REFRESH_BLUE_FEES", "false")
    monkeypatch.delenv("ENZYME_CHECKPOINT_PATH", raising=False)

    module.main()

    assert calls == [(SCRIPT_PATH.with_name("backfill-history.py"), "__main__")]
    assert os.environ["ENZYME_SCAN_PRICES"] == "true"
    assert os.environ["ENZYME_CLEAN_PRICES"] == "true"
    assert os.environ["ENZYME_REFRESH_EXISTING_METADATA"] == "true"
    assert os.environ["ENZYME_REFRESH_BLUE_FEES"] == "false"
    assert "ENZYME_CHECKPOINT_PATH" not in os.environ
