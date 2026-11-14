"""Check the scoped HyperNeutral strategy-tag migration."""

import datetime
from pathlib import Path

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner


@pytest.mark.parametrize("state", ["stale", "current", "missing", "wrong_protocol", "missing_detection"])
def test_hyperneutral_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    """Preview and apply only the reviewed vault's strategy field safely.

    Isolated storage verifies byte-preserving previews, unrelated row and field
    preservation, safe reruns and rejection of incorrect protocol evidence.

    :param tmp_path: Temporary migration storage.
    :param monkeypatch: Environment isolation for legacy database overrides.
    :param state: Target metadata condition.
    :return: ``None`` after checking persistence and reporting.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    target = VaultSpec(9999, "0x8bb033130be354eed1110ce7228d7095f001d9fe")
    expected = {StrategyTag.perpetual_futures, StrategyTag.long_short_portfolio}
    timestamp = datetime.datetime(2026, 11, 14, tzinfo=datetime.UTC).replace(tzinfo=None)
    detection = ERC4262VaultDetection(chain=9999, address=target.vault_address, first_seen_at_block=1, first_seen_at=timestamp, features={ERC4626Feature.hypercore_native}, updated_at=timestamp, deposit_count=1, redeem_count=1)
    row = {"Protocol": "Hyperliquid", "Name": "Preserve name", "_detection_data": detection, "_strategy_tags": expected if state == "current" else {StrategyTag.perpetual_futures}}
    unrelated = VaultSpec(999, target.vault_address)
    database = VaultDatabase(rows={target: row, unrelated: {"Name": "Preserve unrelated vault"}})
    if state == "missing":
        del database.rows[target]
    elif state == "wrong_protocol":
        row["Protocol"] = "Other"
    elif state == "missing_detection":
        del row["_detection_data"]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "vault-metadata-db.pickle"
    database.write(path)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == "076-hyperneutral-strategy-tags.py")
    (scripts_dir / script.name).write_bytes(script.source)
    original = path.read_bytes()
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    if state in {"wrong_protocol", "missing_detection"}:
        with pytest.raises(ValueError, match="not classified as Hyperliquid"):
            runner.run_pending_migrations(dry_run=False, **options)
        assert path.read_bytes() == original
        return
    runner.run_pending_migrations(dry_run=True, **options)
    assert path.read_bytes() == original
    assert {file.name for file in data_dir.iterdir()} == {path.name}
    runner.run_pending_migrations(dry_run=False, **options)
    if state == "stale":
        database.rows[target]["_strategy_tags"] = expected
    assert VaultDatabase.read(path) == database
    entry = runner.load_state(data_dir / "migration.json")[script.name]
    assert entry["other_data"]["updated_fields"] == (1 if state == "stale" else 0)
    applied = path.read_bytes()
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert path.read_bytes() == applied
