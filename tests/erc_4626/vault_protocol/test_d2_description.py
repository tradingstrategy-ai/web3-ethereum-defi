"""Verify GlobalMacro's deployment-scoped display copy and metadata repair."""

from pathlib import Path

import pytest
from web3 import Web3

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.d2.vault import (
    D2_GLOBAL_MACRO_ADDRESS,
    D2_GLOBAL_MACRO_BLOG_URL,
    D2_GLOBAL_MACRO_DESCRIPTION,
    D2_GLOBAL_MACRO_UPDATE_URL,
    D2Vault,
)
from eth_defi.vault.base import VaultSpec, normalise_vault_description
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner

TARGET = VaultSpec(4663, D2_GLOBAL_MACRO_ADDRESS)
SCRIPT_NAME = "033-d2-globalmacro-description.py"


@pytest.mark.parametrize("address", [D2_GLOBAL_MACRO_ADDRESS, Web3.to_checksum_address(D2_GLOBAL_MACRO_ADDRESS)])
def test_globalmacro_description(address: str) -> None:
    """Resolve Markdown without network access for either address casing.

    The long description must preserve source links and paragraph structure
    while retaining announcement links and existing withdrawal notes.

    :param address: Lowercase or checksummed deployment address.
    :return: ``None`` after checking the display fields.
    """
    vault = D2Vault(None, VaultSpec(4663, address), features={ERC4626Feature.d2_like})
    description = vault.description
    assert description == normalise_vault_description(D2_GLOBAL_MACRO_DESCRIPTION)
    assert "\n\n#### " in description
    assert "**10% loss-limit policy per trading cycle**" in description
    assert "6% net" not in description
    assert "100,000" not in description
    assert "5 to 20 November" not in description
    assert D2_GLOBAL_MACRO_BLOG_URL in description
    assert D2_GLOBAL_MACRO_UPDATE_URL in description
    assert vault.short_description == "Trades equity indices, commodities and currencies alongside income from USDG lending."
    assert "**Deposits and withdrawals are closed during the trading phase.**" in vault.get_notes()


@pytest.mark.parametrize("spec", [VaultSpec(1, D2_GLOBAL_MACRO_ADDRESS), VaultSpec(4663, "0x825216a661e05baf68c8bc8b936a135de3f149d3")])
def test_other_d2_description(spec: VaultSpec) -> None:
    """Keep inherited behaviour outside the reviewed chain and address.

    Neither a matching address on another chain nor another Robinhood vault
    should receive GlobalMacro's description.

    :param spec: Deployment outside the reviewed pair.
    :return: ``None`` after checking the inherited empty description.
    """
    assert D2Vault(None, spec, features={ERC4626Feature.d2_like}).description is None


@pytest.fixture
def globalmacro_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Prepare an isolated stale record and historical sentinel files.

    Make only the GlobalMacro migration available to the real runner, including
    another chain with the same address to verify deployment isolation.

    :param tmp_path: Temporary directory owned by pytest.
    :param monkeypatch: Environment isolation for legacy database overrides.
    :return: Pipeline data and migration resource directories.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = {
        TARGET: {"Protocol": "D2 Finance", "_description": "stale", "_short_description": "Keep", "_notes": "Keep note", "_flags": set(), "custom": 123},
        VaultSpec(1, TARGET.vault_address): {"Protocol": "D2 Finance", "_description": "Keep other chain"},
        VaultSpec(4663, "0x0000000000000000000000000000000000000001"): {"Protocol": "D2 Finance", "_description": "Keep unrelated vault"},
    }
    VaultDatabase(rows=rows, last_scanned_block={4663: 12345}).write(data_dir / "vault-metadata-db.pickle")
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve historical data")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    return data_dir, scripts_dir


def test_globalmacro_preview_apply_and_retry(globalmacro_migration: tuple[Path, Path]) -> None:
    """Check byte-preserving preview, isolated apply and idempotency.

    Compare the complete database and sentinel files, including backup contents.
    Re-execute directly to test idempotency independently of journal skipping.

    :param globalmacro_migration: Isolated pipeline and migration resources.
    :return: ``None`` after checking persistence and repeat execution.
    """
    data_dir, scripts_dir = globalmacro_migration
    path = data_dir / "vault-metadata-db.pickle"
    original = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    before = VaultDatabase.read(path)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    assert len(runner.run_pending_migrations(dry_run=True, **options)) == 1
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == original
    assert len(runner.run_pending_migrations(dry_run=False, **options)) == 1
    before.rows[TARGET]["_description"] = normalise_vault_description(D2_GLOBAL_MACRO_DESCRIPTION)
    assert VaultDatabase.read(path) == before
    assert all((data_dir / name).read_bytes() == value for name, value in original.items() if name != path.name)
    entry = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]
    assert entry["other_data"]["updated_fields"] == 1
    assert Path(entry["other_data"]["backup_paths"][0]).read_bytes() == original[path.name]
    applied = {file.name: file.read_bytes() for file in data_dir.iterdir()}
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    script = next(spec for spec in runner.discover_migrations() if spec.name == SCRIPT_NAME)
    context = runner.MigrationContext(data_dir, False, script_name=script.name)
    runner._execute(script, context)
    assert context.other_data["updated_fields"] == 0
    assert {file.name: file.read_bytes() for file in data_dir.iterdir()} == applied


@pytest.mark.parametrize("state", ["missing", "wrong_protocol"])
def test_globalmacro_target_states(globalmacro_migration: tuple[Path, Path], state: str) -> None:
    """Report absent targets and reject conflicting protocol identities.

    Neither condition should rewrite metadata or create a backup.

    :param globalmacro_migration: Isolated pipeline and migration resources.
    :param state: Missing target or conflicting protocol attribution.
    :return: ``None`` after checking failure/reporting and unchanged storage.
    """
    data_dir, scripts_dir = globalmacro_migration
    path = data_dir / "vault-metadata-db.pickle"
    database = VaultDatabase.read(path)
    if state == "missing":
        del database.rows[TARGET]
    else:
        database.rows[TARGET]["Protocol"] = "Other"
    database.write(path)
    before = path.read_bytes()
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "dry_run": False, "provenance": {"in_docker": False}}
    if state == "wrong_protocol":
        with pytest.raises(ValueError, match="unexpected protocol"):
            runner.run_pending_migrations(**options)
    else:
        runner.run_pending_migrations(**options)
        entry = runner.load_state(data_dir / "migration.json")[SCRIPT_NAME]
        assert entry["other_data"]["missing_specs"] == [TARGET.as_string_id()]
        assert entry["other_data"]["updated_fields"] == 0
    assert path.read_bytes() == before
    assert not list(data_dir.glob("*.bak*"))


@pytest.mark.parametrize("chain_id,expected", [(8453, "seven US stocks"), (10, "Korean stock-market")])
def test_d2_same_address_different_products(chain_id: int, expected: str) -> None:
    """Keep Hot7 and Kimchi Premium distinct despite their shared address.

    A checksum spelling must resolve the same reviewed strategy, while an
    unknown chain must not inherit either investment mandate.

    :param chain_id: Reviewed deployment chain.
    :param expected: Distinguishing words in the strategy summary.
    :return: ``None`` after checking chain isolation and casing.
    """
    address = "0xafd240a8e564ad34663b6c42475027ed083088a2"
    vault = D2Vault(None, VaultSpec(chain_id, Web3.to_checksum_address(address)), features={ERC4626Feature.d2_like})
    assert expected in vault.short_description
    unknown = D2Vault(None, VaultSpec(1, address), features={ERC4626Feature.d2_like})
    assert unknown.short_description == "Strategy details are unavailable for this vault."
