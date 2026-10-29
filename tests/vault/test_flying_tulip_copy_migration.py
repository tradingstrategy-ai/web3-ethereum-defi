"""Exercise the copy refresh through the numbered runner on isolated files."""

from pathlib import Path

import pytest
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import FLYING_TULIP_SFTUSD_BY_CHAIN, FLYING_TULIP_SSFTUSD_BY_CHAIN
from eth_defi.erc_4626.vault_protocol.flying_tulip.vault import FlyingTulipStableVault, FlyingTulipVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner


@pytest.fixture
def copy_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Build stale metadata and isolate the new script from earlier repairs.

    Include all four reviewed deployments, an unrelated deployment with the
    same address on another chain, and sentinel historical data.

    :param tmp_path: Temporary directory owned by pytest.
    :param monkeypatch: Environment isolation for pipeline path overrides.
    :return: Pipeline directory and single-script resource directory.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    specs = [VaultSpec(chain, address) for chain, address in (*FLYING_TULIP_SFTUSD_BY_CHAIN.items(), *FLYING_TULIP_SSFTUSD_BY_CHAIN.items())]
    rows = {spec: {"Protocol": "Flying Tulip", "Name": "Keep name", "_notes": "Old copy", "_flags": {VaultFlag.review_needed}, "_management_fee": 0} for spec in specs}
    rows[VaultSpec(56, FLYING_TULIP_SSFTUSD_BY_CHAIN[1])] = {"Protocol": "Other", "_notes": "Keep unrelated notes"}
    VaultDatabase(rows=rows, last_scanned_block={1: 12345}).write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical data")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == "010-flying-tulip-strategy-descriptions.py")
    (scripts_dir / script.name).write_bytes(script.source)
    return data_dir, scripts_dir


def test_copy_migration_preview_apply_and_retry(copy_migration: tuple[Path, Path]) -> None:
    """Refresh both reward products without changing unrelated state or preview bytes.

    Compare persisted values with actual adapter accessors, and exercise the
    runner's backup and successful-retry behaviour without provider access.

    :param copy_migration: Isolated metadata and migration resources.
    :return: ``None`` after preview, apply and repeat validation.
    """
    data_dir, scripts_dir = copy_migration
    original = {path.name: path.read_bytes() for path in data_dir.iterdir()}
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    assert len(runner.run_pending_migrations(dry_run=True, **options)) == 1
    assert {path.name: path.read_bytes() for path in data_dir.iterdir()} == original
    assert len(runner.run_pending_migrations(dry_run=False, **options)) == 1
    database = VaultDatabase.read(data_dir / "vault-metadata-db.pickle")
    assert database.last_scanned_block == {1: 12345}
    for spec, row in database.rows.items():
        if row["Protocol"] != "Flying Tulip":
            assert row == {"Protocol": "Other", "_notes": "Keep unrelated notes"}
            continue
        vault_class = FlyingTulipStableVault if spec.vault_address == FLYING_TULIP_SSFTUSD_BY_CHAIN[1].lower() else FlyingTulipVault
        vault = vault_class(Web3(), spec)
        assert row["_description"] == vault.description
        assert row["_short_description"] == vault.short_description
        assert row["_notes"] == vault.get_notes()
        if vault_class is FlyingTulipVault:
            assert row["_protocol_notes"] == vault.get_notes()
        else:
            assert "_protocol_notes" not in row
        assert row["Name"] == "Keep name"
        assert row["_flags"] == {VaultFlag.review_needed}
        assert row["_management_fee"] == 0
    assert all((data_dir / name).read_bytes() == payload for name, payload in original.items() if name != "vault-metadata-db.pickle")
    entry = next(iter(runner.load_state(data_dir / "migration.json").values()))
    assert entry["other_data"]["inspected_rows"] == 4  # noqa: PLR2004 - Frozen reviewed deployment count.
    assert entry["other_data"]["updated_fields"] == 15  # noqa: PLR2004 - Four sftUSD fields each and three ssftUSD fields.
    assert Path(entry["other_data"]["backup_paths"][0]).read_bytes() == original["vault-metadata-db.pickle"]
    applied = {path.name: path.read_bytes() for path in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert {path.name: path.read_bytes() for path in data_dir.iterdir()} == applied


@pytest.mark.parametrize("missing", [False, True])
def test_copy_migration_validates_targets(copy_migration: tuple[Path, Path], *, missing: bool) -> None:
    """Report absent deployments and reject unexpected classifications before writing.

    A missing row must not be manufactured. A conflicting row must stop the
    repair before any earlier target receives a partial update.

    :param copy_migration: Isolated metadata and migration resources.
    :param missing: Remove the last reviewed target instead of misclassifying it.
    :return: ``None`` after checking missing-row or failure handling.
    """
    data_dir, scripts_dir = copy_migration
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    target = VaultSpec(1, FLYING_TULIP_SSFTUSD_BY_CHAIN[1])
    if missing:
        del database.rows[target]
    else:
        database.rows[target]["Protocol"] = "Unexpected"
    database.write(path)
    before = path.read_bytes()
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "dry_run": False, "provenance": {"in_docker": False}}
    if missing:
        runner.run_pending_migrations(**options)
        assert target not in VaultDatabase.read(path).rows
        entry = next(iter(runner.load_state(data_dir / "migration.json").values()))
        assert entry["other_data"]["missing_specs"] == [target.as_string_id()]
        assert entry["other_data"]["inspected_rows"] == 3  # noqa: PLR2004 - Only the three sftUSD rows remain.
    else:
        with pytest.raises(ValueError, match="unexpected protocol"):
            runner.run_pending_migrations(**options)
        assert path.read_bytes() == before
        assert not list(data_dir.glob("*.bak*"))
