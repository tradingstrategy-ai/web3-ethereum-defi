"""Check the AXI description override and its isolated persisted-copy repair."""

from pathlib import Path

import pytest
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.nest.vault import NestVault
from eth_defi.vault.base import VaultSpec, normalise_vault_description, normalise_vault_short_description
from eth_defi.vault.handwritten_metadata import get_handwritten_vault_metadata
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

TARGET = VaultSpec(98866, "0xe154b281bd9ef3bd94df2364799d0b22b1d4ebd0")
SCRIPT_NAME = "023-nest-axi-description.py"


@pytest.mark.parametrize("cached_metadata", [None, {"description": "Old marketing copy", "short_description": "Old summary"}])
def test_nest_axi_override(cached_metadata: dict | None) -> None:
    """Prefer reviewed copy even when the provider cache is absent or stale.

    The override supplies both fields with their respective output formats.
    A checksum address must resolve to the same reviewed entrypoint.

    :param cached_metadata: Missing or stale first-party cache record.
    :return: ``None`` after checking the rendered text.
    """
    vault = NestVault(web3=None, spec=TARGET)
    vault.__dict__["nest_metadata"] = cached_metadata
    metadata = get_handwritten_vault_metadata(TARGET.chain_id, Web3.to_checksum_address(TARGET.vault_address))
    assert metadata is not None
    assert vault.description == metadata.description
    assert vault.short_description == metadata.short_description
    assert vault.description == normalise_vault_description(vault.description)
    assert vault.short_description == normalise_vault_short_description(vault.short_description)
    assert "\n\n" in vault.description


def test_nest_axi_override_avoids_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep reviewed descriptions available without a provider metadata read.

    Make provider metadata access fail so the test covers an uncached adapter,
    rather than just a cached empty response.

    :param monkeypatch: Replace the provider metadata accessor.
    :return: ``None`` after reading both descriptions without provider access.
    """

    def unavailable(_vault: NestVault) -> None:
        """Reject an unexpected provider read during override resolution.

        :param _vault: Adapter whose descriptions must use maintained copy.
        :return: Never returns; a provider read is a test failure.
        """
        pytest.fail("AXI descriptions must not read provider metadata")

    monkeypatch.setattr(NestVault, "nest_metadata", property(unavailable))
    vault = NestVault(web3=None, spec=TARGET)
    assert vault.description
    assert vault.short_description


@pytest.mark.parametrize("spec", [VaultSpec(1, TARGET.vault_address), VaultSpec(98866, "0x0000000000000000000000000000000000000001")])
def test_nest_axi_override_scope(spec: VaultSpec) -> None:
    """Retain provider descriptions for other chains and vault addresses.

    Reusing the AXI address on another chain must not apply the Plume override.

    :param spec: A deployment outside the reviewed chain/address pair.
    :return: ``None`` after checking provider fallback.
    """
    assert get_handwritten_vault_metadata(spec.chain_id, spec.vault_address) is None
    vault = NestVault(web3=None, spec=spec)
    vault.__dict__["nest_metadata"] = {"description": "Provider description.", "short_description": "Provider summary."}
    assert vault.description == "Provider description."
    assert vault.short_description == "Provider summary."


@pytest.fixture
def axi_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Isolate the numbered copy repair and sentinel pipeline files.

    Include an unrelated deployment at the same address on a different chain.
    All persisted state is created under the test's temporary directory.

    :param tmp_path: Temporary directory owned by pytest.
    :param monkeypatch: Remove external database path overrides.
    :return: Isolated pipeline and single-script resource directories.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = {
        TARGET: {"Protocol": "Nest", "Name": "Keep name", "_description": "Old description", "_short_description": "Old summary", "_notes": "Keep note", "_flags": set(), "_management_fee": 0},
        VaultSpec(1, TARGET.vault_address): {"Protocol": "Other", "_description": "Keep unrelated copy"},
    }
    VaultDatabase(rows=rows, last_scanned_block={98866: 12345}).write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical data")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    return data_dir, scripts_dir


def test_nest_axi_migration_preview_apply_retry(axi_migration: tuple[Path, Path]) -> None:
    """Refresh both text fields while preserving preview bytes and other state.

    Exercise the actual runner with isolated files, then verify an already
    applied repair is skipped without writing any file again.

    :param axi_migration: Isolated metadata and migration resources.
    :return: ``None`` after checking preview, apply and successful retry.
    """
    data_dir, scripts_dir = axi_migration
    path = data_dir / "vault-metadata-db.pickle"
    original = {item.name: item.read_bytes() for item in data_dir.iterdir()}
    before = VaultDatabase.read(path)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    assert len(runner.run_pending_migrations(dry_run=True, **options)) == 1
    assert {item.name: item.read_bytes() for item in data_dir.iterdir()} == original
    assert len(runner.run_pending_migrations(dry_run=False, **options)) == 1
    after = VaultDatabase.read(path)
    vault = NestVault(web3=None, spec=TARGET)
    expected_descriptions = {"_description": vault.description, "_short_description": vault.short_description}
    before.rows[TARGET].update(expected_descriptions)
    assert after.rows == before.rows
    assert after.last_scanned_block == before.last_scanned_block
    assert all((data_dir / name).read_bytes() == payload for name, payload in original.items() if name != path.name)
    entry = next(iter(runner.load_state(data_dir / "migration.json").values()))
    assert entry["other_data"]["updated_fields"] == len(expected_descriptions)
    assert Path(entry["other_data"]["backup_paths"][0]).read_bytes() == original[path.name]
    applied = {item.name: item.read_bytes() for item in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert {item.name: item.read_bytes() for item in data_dir.iterdir()} == applied


@pytest.mark.parametrize("missing", [True, False])
def test_nest_axi_migration_validates_target(axi_migration: tuple[Path, Path], *, missing: bool) -> None:
    """Report a missing vault and refuse an unexpected protocol classification.

    A missing row is never created, and a classification mismatch must fail
    before the runner commits any metadata changes.

    :param axi_migration: Isolated metadata and migration resources.
    :param missing: Remove the target instead of changing its classification.
    :return: ``None`` after checking absence or failure handling.
    """
    data_dir, scripts_dir = axi_migration
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    if missing:
        del database.rows[TARGET]
    else:
        database.rows[TARGET]["Protocol"] = "Unexpected"
    database.write(path)
    before = path.read_bytes()
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "dry_run": False, "provenance": {"in_docker": False}}
    if missing:
        runner.run_pending_migrations(**options)
        entry = next(iter(runner.load_state(data_dir / "migration.json").values()))
        assert entry["other_data"]["missing_specs"] == [TARGET.as_string_id()]
        assert entry["other_data"]["updated_fields"] == 0
        assert TARGET not in VaultDatabase.read(path).rows
    else:
        with pytest.raises(ValueError, match="unexpected protocol"):
            runner.run_pending_migrations(**options)
    assert path.read_bytes() == before
    assert not list(data_dir.glob("*.bak*"))
