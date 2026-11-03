"""Exercise RockSteady's native strategy copy and its isolated metadata refresh."""

from pathlib import Path

import pytest
from eth_typing import HexAddress

from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.hyperliquid.vault_data_export import create_hyperliquid_vault_row
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.flag import HYPERCORE_VAULT_NOTE, ROCKSTEADY_VAULT_NOTE, VaultFlag, get_notes, get_vault_special_flags, is_flagged_vault
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

#: Frozen deployment and migration under test.
TARGET = VaultSpec(HYPERCORE_CHAIN_ID, "0xd321ad79041bdad719ba5ff42fe950e1977df439")
SCRIPT_NAME = "017-rocksteady-strategy-copy.py"

#: Later copy correction must run after the successful original migration.
LIVE_RECORD_SCRIPT_NAME = "028-nuwealth-live-record-copy.py"


def test_rocksteady_native_description_and_notes() -> None:
    """Replace launch copy on native exports while preserving protocol fields.

    Both address casings must use the maintained Markdown and listing summary.
    Keep native fees, identity, links and classification flags intact.

    :return: ``None`` after checking the current scanner's exported rows.
    """
    metadata = get_handwritten_vault_metadata(TARGET.chain_id, TARGET.vault_address)
    assert metadata is not None
    for address in (TARGET.vault_address, TARGET.vault_address.upper()):
        spec, row = create_hyperliquid_vault_row(HexAddress(address), "Renamed RockSteady", "800 agents tested. Powered by Claude.", 1_000, None)
        assert spec == TARGET
        assert row["_description"] == normalise_vault_description(metadata.description)
        assert "\n\n" in row["_description"]
        assert "mean-reversion" in row["_description"]
        assert "30 markets" in row["_description"]
        assert row["_short_description"] == normalise_vault_short_description(metadata.short_description)
        assert "\n" not in row["_short_description"]
        assert "800 agents" not in row["_short_description"]
        assert row["_notes"] == ROCKSTEADY_VAULT_NOTE
        assert "4 February 2026" in row["_description"]
        assert "Research and testing" not in row["_notes"]
        assert "23.7%" not in row["_notes"]
        assert "testing figures" not in row["_notes"]
        assert "[nuwealth.ai/check](https://nuwealth.ai/check)" in row["_notes"]
        assert "[nuwealth.ai/record](https://nuwealth.ai/record)" in row["_notes"]
        assert row["_notes"].count(HYPERCORE_VAULT_NOTE) == 1
        assert row["Name"] == "Renamed RockSteady"
        assert row["Link"] == f"https://app.hyperliquid.xyz/vaults/{TARGET.vault_address}"
        assert row["Perf fee"] == pytest.approx(0.10)
        assert row["Mgmt fee"] == 0
        assert row["_flags"] == {VaultFlag.perp_dex_trading_vault}


def test_rocksteady_copy_is_address_and_chain_scoped() -> None:
    """Keep the copy descriptive and prevent leakage into unrelated products.

    An unreviewed vault retains its provider description and generic PnL note.
    The reviewed note never flags RockSteady or applies on another chain.

    :return: ``None`` after checking fallback metadata and note isolation.
    """
    address = HexAddress("0x0000000000000000000000000000000000000001")
    _, row = create_hyperliquid_vault_row(address, "Unrelated vault", "Original provider copy.", 1_000, None)
    assert row["_description"] is None
    assert row["_short_description"] == "Original provider copy."
    assert "_notes" not in row
    assert get_notes(address, chain_id=HYPERCORE_CHAIN_ID) == HYPERCORE_VAULT_NOTE
    assert get_handwritten_vault_metadata(999, TARGET.vault_address) is None
    assert get_notes(TARGET.vault_address.upper(), chain_id=TARGET.chain_id, include_defaults=False) == ROCKSTEADY_VAULT_NOTE
    assert get_notes(TARGET.vault_address, chain_id=999, include_defaults=False) is None
    assert get_vault_special_flags(TARGET.vault_address) == set()
    assert not is_flagged_vault(TARGET.vault_address)


@pytest.fixture
def rocksteady_copy_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Prepare stale copy and unrelated rows in an isolated pipeline directory.

    Sentinel historical files make accidental price or cursor changes visible.
    Copy only the new migration into its resource directory.

    :param tmp_path: Temporary directory owned by pytest.
    :param monkeypatch: Environment isolation for legacy database overrides.
    :return: Pipeline and single-migration resource directories.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _, row = create_hyperliquid_vault_row(HexAddress(TARGET.vault_address), "Keep name", None, 1_000, None)
    row.update({"_description": None, "_short_description": "800 agents tested.", "_notes": HYPERCORE_VAULT_NOTE})
    rows = {
        TARGET: row,
        VaultSpec(999, TARGET.vault_address): {"Protocol": "Other", "_description": "Keep other chain"},
        VaultSpec(9999, "0x0000000000000000000000000000000000000001"): {"Protocol": "Hyperliquid", "_description": "Keep unrelated vault"},
    }
    VaultDatabase(rows=rows, last_scanned_block={9999: 12345}).write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical data")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    return data_dir, scripts_dir


def test_rocksteady_copy_preview_apply_and_retry(rocksteady_copy_migration: tuple[Path, Path]) -> None:
    """Repair exactly three fields with byte-preserving preview and safe retries.

    Compare the result against fresh native export accessors and retain every
    other field, including fees, flags, tags and historical scanner state.

    :param rocksteady_copy_migration: Isolated pipeline and migration resources.
    :return: ``None`` after checking preview, apply, backup and repeat execution.
    """
    data_dir, scripts_dir = rocksteady_copy_migration
    path = data_dir / "vault-metadata-db.pickle"
    original = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    before = VaultDatabase.read(path)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    assert len(runner.run_pending_migrations(dry_run=True, **options)) == 1
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == original
    assert len(runner.run_pending_migrations(dry_run=False, **options)) == 1
    after = VaultDatabase.read(path)
    _, fresh = create_hyperliquid_vault_row(HexAddress(TARGET.vault_address), "Keep name", None, 1_000, None)
    for field in ("_description", "_short_description", "_notes"):
        assert after.rows[TARGET][field] == fresh[field]
        before.rows[TARGET][field] = fresh[field]
    assert after == before
    assert all((data_dir / name).read_bytes() == value for name, value in original.items() if name != path.name)
    entry = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]
    assert entry["other_data"]["inspected_rows"] == 1
    assert entry["other_data"]["updated_fields"] == 3  # noqa: PLR2004 - Description, summary and notes.
    assert Path(entry["other_data"]["backup_paths"][0]).read_bytes() == original[path.name]
    applied = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == applied


@pytest.mark.parametrize("state", ["missing", "current", "wrong_protocol", "missing_detection"])
def test_rocksteady_copy_target_states(rocksteady_copy_migration: tuple[Path, Path], state: str) -> None:
    """Avoid rewrites for absent/current rows and reject conflicting evidence.

    Rejections must leave metadata untouched; no-op cases must report their
    scope and must not manufacture a vault or create a backup.

    :param rocksteady_copy_migration: Isolated pipeline and migration resources.
    :param state: Target condition used to exercise no-op and rejection paths.
    :return: ``None`` after checking persisted bytes and migration reporting.
    """
    data_dir, scripts_dir = rocksteady_copy_migration
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    if state == "missing":
        del database.rows[TARGET]
    elif state == "current":
        _, fresh = create_hyperliquid_vault_row(HexAddress(TARGET.vault_address), "Keep name", None, 1_000, None)
        for field in ("_description", "_short_description", "_notes"):
            database.rows[TARGET][field] = fresh[field]
    elif state == "wrong_protocol":
        database.rows[TARGET]["Protocol"] = "Other"
    else:
        del database.rows[TARGET]["_detection_data"]
    database.write(path)
    before = path.read_bytes()
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "dry_run": False, "provenance": {"in_docker": False}}
    if state in {"wrong_protocol", "missing_detection"}:
        with pytest.raises(ValueError, match="not classified as a Hyperliquid native vault"):
            runner.run_pending_migrations(**options)
    else:
        runner.run_pending_migrations(**options)
        entry = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]
        assert entry["other_data"]["updated_fields"] == 0
        assert entry["other_data"]["missing_specs"] == ([TARGET.as_string_id()] if state == "missing" else [])
    assert path.read_bytes() == before
    assert not list(data_dir.glob("*.bak*"))


@pytest.fixture
def live_record_copy_migration(rocksteady_copy_migration: tuple[Path, Path]) -> tuple[Path, Path]:
    """Simulate cached legacy copy after the original migration succeeded.

    Use the real runner to record migration 017, then seed the pre-correction
    description and test figures. Add the later migration to the same resource
    directory so a run must skip 017 and apply only the new correction.

    :param rocksteady_copy_migration: Isolated metadata and original resources.
    :return: Pipeline and resource directories containing both migrations.
    """
    data_dir, scripts_dir = rocksteady_copy_migration
    runner.run_pending_migrations(data_dir=data_dir, scripts_dir=scripts_dir, dry_run=False, provenance={"in_docker": False})
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    database.rows[TARGET]["_description"] = database.rows[TARGET]["_description"].replace("4 February 2026", "3 February 2026")
    database.rows[TARGET]["_notes"] = "#### Research and testing\n\n2021 to 2025; 23.7% maximum drawdown in testing."
    database.rows[TARGET]["_short_description"] = "Keep the previously stored summary."
    database.write(path)
    script = next(spec for spec in runner.discover_migrations() if spec.name == LIVE_RECORD_SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    return data_dir, scripts_dir


def test_live_record_copy_preview_apply_and_retry(live_record_copy_migration: tuple[Path, Path]) -> None:
    """Apply the later correction without rerunning the original migration.

    Preserve preview bytes and every field except description and notes. Check
    that the journal retains 017's successful entry and the new script is safe
    to repeat without another write.

    :param live_record_copy_migration: Isolated pipeline and both resources.
    :return: ``None`` after checking preview, apply, backups and repeat execution.
    """
    data_dir, scripts_dir = live_record_copy_migration
    path = data_dir / "vault-metadata-db.pickle"
    original = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    before = VaultDatabase.read(path)
    prior_entry = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    preview = runner.run_pending_migrations(dry_run=True, **options)
    assert len(preview) == 1
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == original
    assert len(runner.run_pending_migrations(dry_run=False, **options)) == 1
    after = VaultDatabase.read(path)
    _, fresh = create_hyperliquid_vault_row(HexAddress(TARGET.vault_address), "Keep name", None, 1_000, None)
    for field in ("_description", "_notes"):
        assert after.rows[TARGET][field] == fresh[field]
        before.rows[TARGET][field] = fresh[field]
    assert after == before
    assert after.rows[TARGET]["_short_description"] == "Keep the previously stored summary."
    state = runner.load_state(data_dir / "migration.json")
    assert state[SCRIPT_NAME] == prior_entry
    entry = state[LIVE_RECORD_SCRIPT_NAME]["other_data"]
    assert entry["inspected_rows"] == 1
    assert entry["updated_fields"] == 2  # noqa: PLR2004 - Full description and notes only.
    assert Path(entry["backup_paths"][0]).read_bytes() == original[path.name]
    assert all((data_dir / name).read_bytes() == value for name, value in original.items() if name not in {path.name, "migration.json"})
    applied = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == applied


@pytest.mark.parametrize("target_state", ["missing", "current", "wrong_protocol", "missing_detection"])
def test_live_record_copy_target_states(live_record_copy_migration: tuple[Path, Path], target_state: str) -> None:
    """Report no-op targets and reject conflicting classifications before writes.

    An absent vault must not be created, and unchanged descriptions must not
    trigger a metadata backup or rewrite. Invalid protocol evidence fails closed.

    :param live_record_copy_migration: Isolated pipeline and both resources.
    :param target_state: Target condition to exercise before applying the repair.
    :return: ``None`` after checking bytes, backups and scope reporting.
    """
    data_dir, scripts_dir = live_record_copy_migration
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    if target_state == "missing":
        del database.rows[TARGET]
    elif target_state == "current":
        _, fresh = create_hyperliquid_vault_row(HexAddress(TARGET.vault_address), "Keep name", None, 1_000, None)
        for field in ("_description", "_notes"):
            database.rows[TARGET][field] = fresh[field]
    elif target_state == "wrong_protocol":
        database.rows[TARGET]["Protocol"] = "Other"
    else:
        del database.rows[TARGET]["_detection_data"]
    database.write(path)
    before = path.read_bytes()
    backups = set(data_dir.glob("*.bak*"))
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "dry_run": False, "provenance": {"in_docker": False}}
    if target_state in {"wrong_protocol", "missing_detection"}:
        with pytest.raises(ValueError, match="not classified as a Hyperliquid native vault"):
            runner.run_pending_migrations(**options)
    else:
        runner.run_pending_migrations(**options)
        entry = runner.load_state(data_dir / "migration.json")[LIVE_RECORD_SCRIPT_NAME]["other_data"]
        assert entry["updated_fields"] == 0
        assert entry["missing_specs"] == ([TARGET.as_string_id()] if target_state == "missing" else [])
    assert path.read_bytes() == before
    assert set(data_dir.glob("*.bak*")) == backups
