"""The real migration runner fills only reviewed Lagoon description fields."""

import copy
import json
import logging
from pathlib import Path

import pytest

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon import offchain_metadata
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

TARGET = VaultSpec(1, "0x16b85563902556d205f5c2b4485df3d26b153f96")
STRADA = VaultSpec(1, "0xcb58582b0d52ce5feecb06ba9ce66598b0d57886")
OTHER = VaultSpec(8453, TARGET.vault_address)
SCRIPT = "036-lagoon-offchain-metadata.py"


@pytest.fixture
def pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Build isolated metadata, journal and provider inputs for the real runner.

    A short-only target detects over-strict completion checks. A same-address
    wrong-chain control and historical sentinels protect repair boundaries.

    :param tmp_path: Isolated runner storage.
    :param monkeypatch: Replace public provider responses, not migration logic.
    :return: Keyword arguments accepted by the migration runner.
    """
    for key in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(key, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    row = {"Protocol": "Lagoon Finance", "Name": "Existing vault", "_description": None, "_short_description": None, "_flags": {"unofficial"}, "_notes": "Manual note", "_manager_name": "Existing curator", "features": {ERC4626Feature.lagoon_like}}
    database = VaultDatabase(rows={TARGET: copy.deepcopy(row), STRADA: copy.deepcopy(row), OTHER: {"Protocol": "Control", "_description": "Preserve wrong-chain text"}}, last_scanned_block={1: 123})
    database.write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical bytes")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = next(item for item in runner.discover_migrations() if item.name == SCRIPT)
    (scripts / SCRIPT).write_bytes(script.source)

    def detail(chain: int, address: str, api_base_url: str) -> offchain_metadata.LagoonDetailResponse:
        """Supply the two reviewed source shapes without requests or RPC."""
        assert chain == 1
        assert address in {TARGET.vault_address, STRADA.vault_address}
        return offchain_metadata.LagoonDetailResponse({"name": "Example", "chain": {"id": chain}, "address": address, "description": "Full strategy." if address == TARGET.vault_address else None, "shortDescription": "One sentence. More detail.", "curators": []}, "success")

    monkeypatch.setattr(offchain_metadata, "_fetch_detail_response", detail)
    return {"data_dir": data_dir, "scripts_dir": scripts, "provenance": {"in_docker": False}}


def test_lagoon_preview_apply_and_idempotency(pipeline: dict) -> None:
    """Preview writes no state and apply changes only three missing fields.

    A second execution after discarding the test journal remains idempotent.
    The real numbered runner supplies staging, backups and guarded commits.
    """
    data_dir = pipeline["data_dir"]
    path = data_dir / "vault-metadata-db.pickle"
    before = copy.deepcopy(VaultDatabase.read(path).rows)
    before_files = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=True, **pipeline) == [SCRIPT]
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == before_files
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == [SCRIPT]
    after = VaultDatabase.read(path)
    expected = copy.deepcopy(before)
    expected[TARGET].update(_description="Full strategy.", _short_description="One sentence.")
    expected[STRADA]["_short_description"] = "One sentence."
    assert after.rows == expected
    assert after.last_scanned_block == {1: 123}
    for name, content in before_files.items():
        if name != path.name:
            assert (data_dir / name).read_bytes() == content
    record = json.loads((data_dir / "migration.json").read_text())[SCRIPT]["other_data"]
    assert record["updated_fields"] == 3
    assert len(record["missing_specs"]) == 20
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == []
    (data_dir / "migration.json").unlink()
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == [SCRIPT]
    assert VaultDatabase.read(path).rows == after.rows


@pytest.mark.parametrize("failure", ["unavailable", "not_found", "empty_required", "wrong_protocol"])
def test_lagoon_failed_batch_does_not_commit(pipeline: dict, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    """Provider failures or changed identities cannot mark a partial batch done.

    Rate-limit outcomes carry a retry deadline but cause no repeated burst.
    """
    data_dir = pipeline["data_dir"]
    path = data_dir / "vault-metadata-db.pickle"
    if failure == "wrong_protocol":
        database = VaultDatabase.read(path)
        database.rows[STRADA]["Protocol"] = "Other"
        database.write(path)
    calls = []

    def detail(chain: int, address: str, api_base_url: str) -> offchain_metadata.LagoonDetailResponse:
        """Return a bounded negative or successful missing-field response."""
        calls.append(address)
        if failure == "empty_required":
            return offchain_metadata.LagoonDetailResponse({"description": None, "shortDescription": None}, "success")
        return offchain_metadata.LagoonDetailResponse(outcome=failure, retry_after=7200)

    monkeypatch.setattr(offchain_metadata, "_fetch_detail_response", detail)
    before = path.read_bytes()
    with pytest.raises((RuntimeError, ValueError), match="batch deferred|source changed|conflicting protocol"):
        runner.run_pending_migrations(dry_run=False, **pipeline)
    assert path.read_bytes() == before
    assert len(calls) <= 1


@pytest.mark.parametrize("outcome", ["not_found", "unavailable", "success"])
def test_scanner_retains_repaired_text_after_empty_read(outcome: str, caplog: pytest.LogCaptureFixture) -> None:
    """A fresh production cache cannot erase a migration-filled description.

    Only Lagoon rows opt into this merge; normal provider deletion of text is
    reported for review rather than silently wiping a published description.
    """
    caplog.set_level(logging.INFO)
    row = {"Name": "Known vault", "Protocol": "Lagoon Finance", "features": {ERC4626Feature.lagoon_like}, "_description": "Repaired long text.", "_short_description": "Repaired summary.", "_notes": "Operator note"}
    database = VaultDatabase(rows={TARGET: copy.deepcopy(row)})
    new = row | {"_description": None, "_short_description": "", "_lagoon_metadata_outcome": outcome, "_lagoon_metadata_fetched_at": None}
    database._merge_rows({TARGET: new})
    assert database.rows[TARGET]["_description"] == row["_description"]
    assert database.rows[TARGET]["_short_description"] == row["_short_description"]
    assert database.rows[TARGET]["_notes"] == row["_notes"]
    assert "age unknown" in caplog.text
    database._merge_rows({TARGET: new | {"_description": "Updated provider strategy."}})
    assert database.rows[TARGET]["_description"] == "Updated provider strategy."
    # Other protocols retain their previous replacement semantics.
    database.rows[OTHER] = row | {"features": set()}
    database._merge_rows({OTHER: new | {"features": set()}})
    assert database.rows[OTHER]["_description"] is None
