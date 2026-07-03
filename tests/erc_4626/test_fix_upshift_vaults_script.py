"""Tests for the Upshift targeted repair script."""

import importlib.util
from pathlib import Path
from types import ModuleType

UPSHIFT_EVM_SNAPSHOT_VAULT_COUNT = 104


def load_fix_upshift_vaults_module() -> ModuleType:
    """Load the hyphenated maintenance script as a normal Python module.

    The script lives under ``scripts/`` and is intended to be called from the
    command line, so it cannot be imported with a normal dotted module path.

    :return:
        Loaded script module.
    """
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "fix-upshift-vaults.py"
    spec = importlib.util.spec_from_file_location("fix_upshift_vaults_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fix_upshift_vaults_snapshot_contains_all_known_evm_vaults() -> None:
    """Check that the baked Upshift snapshot has the full EVM vault list."""
    module = load_fix_upshift_vaults_module()

    refs = module.parse_snapshot_csv(module.UPSHIFT_VAULT_SNAPSHOT_CSV)
    specs = {ref.get_spec() for ref in refs}

    assert len(refs) == UPSHIFT_EVM_SNAPSHOT_VAULT_COUNT
    assert all(ref.chain_id > 0 for ref in refs)
    assert all(ref.first_seen_at_block >= 1 for ref in refs)
    assert len(specs) == len(refs)
    assert module.VaultSpec(1, "0xcd69123b3fbbfc666e1f6a501da27b564c00de54") in specs
    assert module.VaultSpec(1, "0xc87dbbb8c67e4f19fcd2e297c05937567b2572ce") in specs


def test_fix_upshift_vaults_filters_status_and_visibility(monkeypatch) -> None:
    """Check operator filters for status and visible API rows."""
    module = load_fix_upshift_vaults_module()

    refs = module.parse_snapshot_csv(module.UPSHIFT_VAULT_SNAPSHOT_CSV)
    monkeypatch.setenv("UPSHIFT_STATUS", "active")
    monkeypatch.setenv("UPSHIFT_VISIBLE_ONLY", "true")

    filtered = module.filter_references(refs)

    assert filtered
    assert all(ref.status == "active" for ref in filtered)
    assert all(ref.is_visible is True for ref in filtered)
