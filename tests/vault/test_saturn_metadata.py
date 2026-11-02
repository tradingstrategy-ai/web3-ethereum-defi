"""Verify Saturn's copy-only migration preserves historical data and other vaults."""

import datetime
from pathlib import Path

import pytest
from web3 import Web3

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.handwritten_metadata import SATURN_PT_USDAT_VAULT_METADATA
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

TARGET = VaultSpec(143, "0x1a5b77cef5e4b6f806aeac6f6ea9213a617dc7e3")
METADATA = SATURN_PT_USDAT_VAULT_METADATA[TARGET.chain_id, TARGET.vault_address]
SCRIPT_NAME = "026-saturn-pt-usdat-description.py"
FIELDS = ("_description", "_short_description")


@pytest.fixture
def saturn_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Prepare stale reference metadata and unrelated rows in isolated storage.

    Historical sentinel files expose accidental writes outside metadata. Only
    this task's migration is copied into the runner's test resource directory.

    :param tmp_path: Temporary directory owned by pytest.
    :param monkeypatch: Environment isolation for legacy database overrides.
    :return: Pipeline directory and single-migration resource directory.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    row = {"Protocol": "Accountable", "Name": "Keep name"}
    row.update({"_description": None, "_short_description": "Old provider summary.", "_notes": "Preserve existing note", "_manager_name": "Keep operator"})
    rows = {
        TARGET: row,
        VaultSpec(999, TARGET.vault_address): {"Protocol": "Other", "_description": "Keep other chain"},
        VaultSpec(143, "0x0000000000000000000000000000000000000001"): {"Protocol": "Accountable", "_description": "Keep unrelated vault"},
    }
    VaultDatabase(rows=rows, last_scanned_block={143: 12345}).write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "vault-reader-state-1h.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical data")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    return data_dir, scripts_dir


def test_saturn_migration_preview_apply_and_retry(saturn_migration: tuple[Path, Path]) -> None:
    """Repair exactly two fields with byte-preserving preview and safe retries.

    Compare migrated data with the normalised maintained copy. Keep all other
    rows, fields and scanner state, and verify the runner creates a valid backup.

    :param saturn_migration: Isolated pipeline and migration resources.
    :return: ``None`` after checking preview, apply, backup and repeat execution.
    """
    data_dir, scripts_dir = saturn_migration
    path = data_dir / "vault-metadata-db.pickle"
    original = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    before = VaultDatabase.read(path)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    assert len(runner.run_pending_migrations(dry_run=True, **options)) == 1
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == original
    assert len(runner.run_pending_migrations(dry_run=False, **options)) == 1
    after = VaultDatabase.read(path)
    fresh = {"_description": normalise_vault_description(METADATA.description), "_short_description": normalise_vault_short_description(METADATA.short_description)}
    for field in FIELDS:
        assert after.rows[TARGET][field] == fresh[field]
        before.rows[TARGET][field] = fresh[field]
    assert after == before
    assert all((data_dir / name).read_bytes() == value for name, value in original.items() if name != path.name)
    entry = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]
    assert entry["other_data"]["inspected_rows"] == 1
    assert entry["other_data"]["updated_fields"] == len(FIELDS)
    assert Path(entry["other_data"]["backup_paths"][0]).read_bytes() == original[path.name]
    applied = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == applied


@pytest.mark.parametrize("state", ["missing", "current", "wrong_protocol"])
def test_saturn_migration_target_states(saturn_migration: tuple[Path, Path], state: str) -> None:
    """Avoid absent/current rewrites and reject conflicting protocol evidence.

    Rejection must leave the database unchanged. No-op cases must report their
    scope without manufacturing a row or creating a backup.

    :param saturn_migration: Isolated pipeline and migration resources.
    :param state: Target condition for no-op or rejection coverage.
    :return: ``None`` after checking persisted bytes and migration reporting.
    """
    data_dir, scripts_dir = saturn_migration
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    if state == "missing":
        del database.rows[TARGET]
    elif state == "current":
        fresh = {"_description": normalise_vault_description(METADATA.description), "_short_description": normalise_vault_short_description(METADATA.short_description)}
        for field in FIELDS:
            database.rows[TARGET][field] = fresh[field]
    elif state == "wrong_protocol":
        database.rows[TARGET]["Protocol"] = "Other"
    database.write(path)
    before = path.read_bytes()
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "dry_run": False, "provenance": {"in_docker": False}}
    if state == "wrong_protocol":
        with pytest.raises(ValueError, match="not classified as Accountable"):
            runner.run_pending_migrations(**options)
    else:
        runner.run_pending_migrations(**options)
        entry = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]
        assert entry["other_data"]["updated_fields"] == 0
        assert entry["other_data"]["missing_specs"] == ([TARGET.as_string_id()] if state == "missing" else [])
    assert path.read_bytes() == before
    assert not list(data_dir.glob("*.bak*"))


@pytest.mark.parametrize(
    "chain_id,address,expected",
    [
        (143, TARGET.vault_address, {StrategyTag.lending_looping, StrategyTag.carry_trade, StrategyTag.amm}),
        (143, Web3.to_checksum_address(TARGET.vault_address), {StrategyTag.lending_looping, StrategyTag.carry_trade, StrategyTag.amm}),
        (1, TARGET.vault_address, None),
        (143, "0x593784188369e5e5f484025a1940cf437620c3fe", None),
        (143, "0x0000000000000000000000000000000000000001", None),
    ],
)
def test_saturn_strategy_tag_scope(chain_id: int, address: str, expected: set[StrategyTag] | None) -> None:
    """Classify only the share token on Monad and return independent sets.

    The loan address used by the web app must not gain the classification,
    nor may the same share address on a different chain.

    :param chain_id: Candidate deployment chain.
    :param address: Candidate share or loan address.
    :param expected: Reviewed tags, or missing information for other deployments.
    :return: ``None`` after checking scope and mutation isolation.
    """
    vault = AccountableVault(Web3(), VaultSpec(chain_id, address))
    tags = vault.get_strategy_tags()
    assert tags == expected
    if tags is not None:
        tags.clear()
        assert vault.get_strategy_tags() == expected


@pytest.mark.parametrize("state", ["stale", "current", "missing", "wrong_protocol", "missing_detection"])
def test_saturn_tag_migration(saturn_migration: tuple[Path, Path], state: str) -> None:
    """Preview and apply the tag-only repair without changing other stored data.

    Reuse isolated metadata and history sentinels, selecting only the new tag
    migration. Invalid classification must fail before any metadata is written.

    :param saturn_migration: Isolated pipeline and migration resources.
    :param state: Existing metadata condition to exercise.
    :return: ``None`` after checking safety, tags and idempotent reruns.
    """
    data_dir, scripts_dir = saturn_migration
    (scripts_dir / SCRIPT_NAME).unlink()
    script = next(spec for spec in runner.discover_migrations() if spec.name == "027-saturn-pt-usdat-strategy-tags.py")
    (scripts_dir / script.name).write_bytes(script.source)
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    row = database.rows[TARGET]
    timestamp = datetime.datetime(2026, 11, 2, tzinfo=datetime.UTC).replace(tzinfo=None)
    row["_detection_data"] = ERC4262VaultDetection(chain=143, address=TARGET.vault_address, first_seen_at_block=1, first_seen_at=timestamp, features={ERC4626Feature.accountable_like}, updated_at=timestamp, deposit_count=1, redeem_count=1)
    expected = {StrategyTag.lending_looping, StrategyTag.carry_trade, StrategyTag.amm}
    row["_strategy_tags"] = expected if state == "current" else None
    if state == "missing":
        del database.rows[TARGET]
    elif state == "wrong_protocol":
        row["Protocol"] = "Other"
    elif state == "missing_detection":
        del row["_detection_data"]
    database.write(path)
    original = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    if state in {"wrong_protocol", "missing_detection"}:
        with pytest.raises(ValueError, match="not classified as Accountable"):
            runner.run_pending_migrations(dry_run=False, **options)
        assert path.read_bytes() == original[path.name]
        return
    runner.run_pending_migrations(dry_run=True, **options)
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == original
    runner.run_pending_migrations(dry_run=False, **options)
    if state == "stale":
        database.rows[TARGET]["_strategy_tags"] = expected
    assert VaultDatabase.read(path) == database
    assert all((data_dir / name).read_bytes() == value for name, value in original.items() if name != path.name)
    entry = runner.load_state(data_dir / "migration.json")[script.name]
    assert entry["other_data"]["updated_fields"] == (1 if state == "stale" else 0)
    applied = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == applied
