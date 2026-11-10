"""Exercise the Lagoon strategy refresh through the real numbered runner."""

import copy
import datetime
import json
from dataclasses import replace
from pathlib import Path

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon.tags import lookup_lagoon_strategy_tags
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

SCRIPT = "037-lagoon-strategy-tags.py"
TARGET = VaultSpec(1, "0xbb211be8664128e30c6adcd5998eca9592be272f")
NEW = VaultSpec(1, "0x5f9efcc03b95609346798596494cf9dbaf176312")
UNKNOWN = VaultSpec(1, "0x0000000000000000000000000000000000000123")
WRONG_CHAIN = VaultSpec(10, NEW.vault_address)
OTHER = VaultSpec(8453, NEW.vault_address)


@pytest.fixture
def pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Create an isolated pipeline with stale, missing and unresolved tags.

    Non-tag fields and historical files are sentinels for repair scope.
    No provider requests or production files participate in these tests.

    :param tmp_path: Isolated runner directory.
    :param monkeypatch: Remove legacy storage overrides.
    :return: Keyword arguments for the numbered migration runner.
    """
    for key in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(key, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = {}
    timestamp = datetime.datetime(2026, 11, 10)
    for spec in (TARGET, NEW, UNKNOWN, WRONG_CHAIN, OTHER):
        detection = ERC4262VaultDetection(chain=spec.chain_id, address=spec.vault_address, first_seen_at_block=1, first_seen_at=timestamp, features={ERC4626Feature.lagoon_like}, updated_at=timestamp, deposit_count=1, redeem_count=1)
        rows[spec] = {"Protocol": "Control" if spec == OTHER else "Lagoon Finance", "_detection_data": detection, "_description": "Preserve text", "_flags": {"unofficial"}, "_manager_name": "Existing curator", "_strategy_tags": {StrategyTag.delta_neutral, StrategyTag.options}}
    rows[NEW]["_strategy_tags"] = None
    VaultDatabase(rows=rows, last_scanned_block={1: 123}).write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical bytes")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = next(item for item in runner.discover_migrations() if item.name == SCRIPT)
    (scripts / SCRIPT).write_bytes(script.source)
    return {"data_dir": data_dir, "scripts_dir": scripts, "provenance": {"in_docker": False}}


def test_lagoon_strategy_preview_apply_and_idempotency(pipeline: dict) -> None:
    """Preview is read only; apply changes only reviewed existing tag fields.

    The repair replaces stale classifications, fills missing tags, preserves
    unresolved/manual and other-protocol rows, and is safe to repeat.

    :param pipeline: Isolated numbered-runner arguments.
    :return: ``None`` after checking metadata and historical byte preservation.
    """
    data_dir = pipeline["data_dir"]
    path = data_dir / "vault-metadata-db.pickle"
    before = copy.deepcopy(VaultDatabase.read(path))
    before_files = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=True, **pipeline) == [SCRIPT]
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == before_files
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == [SCRIPT]
    expected = copy.deepcopy(before.rows)
    for spec in (TARGET, NEW):
        expected[spec]["_strategy_tags"] = lookup_lagoon_strategy_tags(spec.chain_id, spec.vault_address)
    after = VaultDatabase.read(path)
    assert after.rows == expected
    assert after.last_scanned_block == before.last_scanned_block
    for name, content in before_files.items():
        if name != path.name:
            assert (data_dir / name).read_bytes() == content
    report = json.loads((data_dir / "migration.json").read_text())[SCRIPT]["other_data"]
    assert report["updated_fields"] == 2
    assert report["unresolved_rows"] == 2
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == []
    (data_dir / "migration.json").unlink()
    assert runner.run_pending_migrations(dry_run=False, **pipeline) == [SCRIPT]
    assert VaultDatabase.read(path).rows == expected


@pytest.mark.parametrize("features", [None, {ERC4626Feature.morpho_like}, {ERC4626Feature.mellow_like, ERC4626Feature.lagoon_like}])
def test_lagoon_strategy_invalid_detection_fails(pipeline: dict, features: set[ERC4626Feature] | None) -> None:
    """A known classification must not silently skip incompatible detection.

    An earlier-priority adapter must not supply unrelated tags even when the
    persisted feature set also contains Lagoon.

    :param pipeline: Isolated numbered-runner arguments.
    :param features: Missing or incompatible persisted detection evidence.
    :return: ``None`` after verifying an atomic failure.
    """
    path = pipeline["data_dir"] / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    if features is None:
        database.rows[TARGET].pop("_detection_data")
    else:
        database.rows[TARGET]["_detection_data"] = replace(database.rows[TARGET]["_detection_data"], features=features)
    database.write(path)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Lagoon"):
        runner.run_pending_migrations(dry_run=False, **pipeline)
    assert path.read_bytes() == before
