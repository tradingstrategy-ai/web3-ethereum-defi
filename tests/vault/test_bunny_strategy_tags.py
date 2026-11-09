"""Exercise the Bunny tag refresh through the numbered migration runner."""

import datetime
from pathlib import Path

import pytest
from eth_typing import HexAddress

from eth_defi.hyperliquid.tags import get_strategy_tags
from eth_defi.hyperliquid.vault_data_export import create_hyperliquid_vault_row
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: Reviewed deployment and numbered repair under test.
TARGET = VaultSpec(9999, "0x5ce0ce4387679de0335a3e8a1c9a7abb4f150a71")
SCRIPT_NAME = "053-bunny-strategy-tags.py"


@pytest.fixture
def bunny_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Create stale native metadata and isolate the new migration resources.

    Include unrelated native and same-address EVM rows plus historical sentinel
    files so the tests can detect an accidentally broadened refresh.

    :param tmp_path: Temporary directory owned by pytest.
    :param monkeypatch: Environment isolation for pipeline path overrides.
    :return: Isolated pipeline and single-script resource directories.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _, row = create_hyperliquid_vault_row(HexAddress(TARGET.vault_address), "Agent Bunny", "Keep original description", 1_000, datetime.datetime(2026, 2, 3))  # noqa: DTZ001 - Repository timestamps use naive UTC.
    row["_strategy_tags"] = {StrategyTag.perpetual_futures}
    rows = {
        TARGET: row,
        VaultSpec(999, TARGET.vault_address): {"Protocol": "Other", "_strategy_tags": {StrategyTag.lending}},
        VaultSpec(9999, "0x0000000000000000000000000000000000000001"): {"Protocol": "Hyperliquid", "_strategy_tags": {StrategyTag.unknown}},
    }
    VaultDatabase(rows=rows, last_scanned_block={9999: 12345}).write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical data")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    return data_dir, scripts_dir


def test_bunny_migration_preview_apply_and_retry(bunny_migration: tuple[Path, Path]) -> None:
    """Refresh one field while preserving preview bytes and all unrelated state.

    The runner must back up the old database and skip the successful migration
    on a repeat run without changing either metadata or historical files.

    :param bunny_migration: Isolated pipeline and migration resources.
    :return: ``None`` after preview, apply and repeat validation.
    """
    data_dir, scripts_dir = bunny_migration
    path = data_dir / "vault-metadata-db.pickle"
    original = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    before = VaultDatabase.read(path)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    assert len(runner.run_pending_migrations(dry_run=True, **options)) == 1
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == original

    assert len(runner.run_pending_migrations(dry_run=False, **options)) == 1
    after = VaultDatabase.read(path)
    assert after.rows[TARGET]["_strategy_tags"] == {StrategyTag.perpetual_futures, StrategyTag.algorithmic_trading, StrategyTag.high_frequency_high_win_rate, StrategyTag.btc_only}
    before.rows[TARGET]["_strategy_tags"] = after.rows[TARGET]["_strategy_tags"]
    assert after == before
    assert all((data_dir / name).read_bytes() == payload for name, payload in original.items() if name != path.name)
    entry = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]
    assert entry["other_data"]["inspected_rows"] == 1
    assert entry["other_data"]["updated_fields"] == 1
    assert entry["other_data"]["changed_rows"] == 1
    assert entry["other_data"]["missing_specs"] == []
    assert Path(entry["other_data"]["backup_paths"][0]).read_bytes() == original[path.name]
    applied = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == applied


@pytest.mark.parametrize("state", ["missing", "current", "wrong_protocol", "missing_detection", "malformed_detection"])
def test_bunny_migration_target_states(bunny_migration: tuple[Path, Path], state: str) -> None:
    """Handle absent or current rows and reject conflicting protocol evidence.

    Missing and already-current targets must not rewrite the database. Invalid
    targets must fail before committing any metadata or creating backups.

    :param bunny_migration: Isolated pipeline and migration resources.
    :param state: Target condition to exercise before applying the migration.
    :return: ``None`` after checking no-op or rejection behaviour.
    """
    data_dir, scripts_dir = bunny_migration
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    if state == "missing":
        del database.rows[TARGET]
    elif state == "current":
        database.rows[TARGET]["_strategy_tags"] = {StrategyTag.perpetual_futures, StrategyTag.algorithmic_trading, StrategyTag.high_frequency_high_win_rate, StrategyTag.btc_only}
    elif state == "wrong_protocol":
        database.rows[TARGET]["Protocol"] = "Other"
    elif state == "malformed_detection":
        database.rows[TARGET]["_detection_data"] = {}
    else:
        del database.rows[TARGET]["_detection_data"]
    database.write(path)
    before = path.read_bytes()
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "dry_run": False, "provenance": {"in_docker": False}}
    if state in {"wrong_protocol", "missing_detection", "malformed_detection"}:
        with pytest.raises(ValueError, match="not classified as a Hyperliquid native vault"):
            runner.run_pending_migrations(**options)
    else:
        runner.run_pending_migrations(**options)
        entry = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]
        assert entry["other_data"]["updated_fields"] == 0
        assert entry["other_data"]["missing_specs"] == ([TARGET.as_string_id()] if state == "missing" else [])
    assert path.read_bytes() == before
    assert not list(data_dir.glob("*.bak*"))


def test_bunny_export_strategy_tags() -> None:
    """Export the maintained high-win-rate classification with its required tags.

    Case variants must resolve identically, and callers must receive a fresh
    tag set so they cannot mutate future classifications.

    :return: ``None`` after validating exported and resolved tags.
    """
    expected = {StrategyTag.perpetual_futures, StrategyTag.algorithmic_trading, StrategyTag.high_frequency_high_win_rate, StrategyTag.btc_only}
    for address in (TARGET.vault_address, TARGET.vault_address.upper()):
        _, row = create_hyperliquid_vault_row(HexAddress(address), "Bunny", None, 1_000, datetime.datetime(2026, 11, 9))  # noqa: DTZ001 - Repository timestamps use naive UTC.
        assert row["_strategy_tags"] == expected
        assert get_strategy_tags(address) == expected
    get_strategy_tags(TARGET.vault_address).clear()
    assert get_strategy_tags(TARGET.vault_address) == expected
    assert get_strategy_tags("0x0000000000000000000000000000000000000001") == {StrategyTag.perpetual_futures}
