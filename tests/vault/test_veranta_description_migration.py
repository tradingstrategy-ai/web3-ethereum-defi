"""Exercise the vault-description repair without RPC or production state."""

import copy
import json
from pathlib import Path

import pytest

from eth_defi.vault.base import VaultSpec
from eth_defi.vault.handwritten_metadata import VERANTA_VAULT_METADATA
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

TARGET = VaultSpec(8453, "0x944766f715b51967e56afde5f0aa76ceacc9e7f9")
OTHER = VaultSpec(1, TARGET.vault_address)
SCRIPT_NAME = "031-veranta-vault-description.py"


@pytest.fixture
def pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Build an isolated runner directory with only migration 031 pending.

    Frozen historical-file sentinels detect unintended rewrites. The target
    includes unrelated operator metadata and a same-address wrong-chain row.

    :param tmp_path: Temporary directory for staged database and history.
    :param monkeypatch: Remove external database overrides for runner isolation.
    :return: Keyword arguments for the real migration runner.
    """
    for key in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(key, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database = VaultDatabase(
        rows={
            TARGET: {"Protocol": "Veranta", "Name": "Avantis USDC Vault", "_notes": "Preserve operator warning", "_flags": {"manual"}, "_fees": None, "Link": "https://www.avantisfi.com/earn", "_description": None, "_short_description": None},
            OTHER: {"Protocol": "Unrelated", "_description": "Unrelated description"},
        },
        last_scanned_block={8453: 123},
    )
    database.write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve existing historical state")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(item for item in runner.discover_migrations() if item.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    return {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}


@pytest.mark.parametrize("old_description", [None, "Outdated vault copy"])
def test_veranta_description_preview_apply_preserves_state(pipeline: dict, old_description: str | None) -> None:
    """Refresh only the two reviewed text fields and preserve historical bytes.

    Preview must not persist staged values; apply must retain all other row
    fields and scan boundaries. A recorded successful repair is then skipped.

    :param pipeline: Isolated runner directories with target and control row.
    :param old_description: Missing or stale persisted description.
    :return: None after preview, apply, preservation and idempotency assertions.
    """
    data_dir = pipeline["data_dir"]
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    database.rows[TARGET]["_description"] = old_description
    database.write(path)
    before_rows = copy.deepcopy(database.rows)
    before_files = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=True, **pipeline) == [SCRIPT_NAME]
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == before_files
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == [SCRIPT_NAME]
    report = json.loads((data_dir / "migration.json").read_text())[SCRIPT_NAME]["other_data"]
    assert report["updated_fields"] == 2
    assert report["missing_specs"] == []
    result = VaultDatabase.read(path)
    metadata = VERANTA_VAULT_METADATA[TARGET.chain_id, TARGET.vault_address]
    expected = before_rows[TARGET] | {"_description": metadata.description, "_short_description": metadata.short_description}
    assert result.rows == before_rows | {TARGET: expected}
    assert result.last_scanned_block == database.last_scanned_block
    for name, content in before_files.items():
        if name != path.name:
            assert (data_dir / name).read_bytes() == content
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == []
    # A fresh journal must still leave an already repaired row unchanged.
    (data_dir / "migration.json").unlink()
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == [SCRIPT_NAME]
    assert VaultDatabase.read(path).rows == result.rows
    record = json.loads((data_dir / "migration.json").read_text())[SCRIPT_NAME]
    assert record["other_data"]["updated_fields"] == 0
    assert record["other_data"].get("backup_paths", []) == []
    assert record["modified_files"] == []


def test_veranta_description_missing_target_is_not_created(pipeline: dict) -> None:
    """Report missing metadata without discovering or inserting a new vault.

    The repair is display-only and must not create synthetic scan metadata.

    :param pipeline: Isolated runner directories.
    :return: None after confirming the wrong-chain control is the only row.
    """
    path = pipeline["data_dir"] / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    del database.rows[TARGET]
    database.write(path)
    expected = copy.deepcopy(database.rows)
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == [SCRIPT_NAME]
    assert VaultDatabase.read(path).rows == expected
    report = json.loads((pipeline["data_dir"] / "migration.json").read_text())[SCRIPT_NAME]["other_data"]
    assert report["missing_specs"] == [TARGET.as_string_id()]
    assert report["updated_fields"] == 0


def test_veranta_description_conflicting_protocol_aborts(pipeline: dict) -> None:
    """Reject a conflicting identity before any description is persisted.

    The old onboarding migration is not rerun or used to override attribution.

    :param pipeline: Isolated runner directories.
    :return: None after confirming validation failure preserves database bytes.
    """
    path = pipeline["data_dir"] / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    database.rows[TARGET]["Protocol"] = "Other protocol"
    database.write(path)
    original = path.read_bytes()
    with pytest.raises(ValueError, match="unexpected protocol"):
        runner.run_pending_migrations(dry_run=False, **pipeline)
    assert path.read_bytes() == original
