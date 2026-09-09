"""Test the Yearn yDaemon metadata migration."""

import importlib.util
from pathlib import Path

import pytest

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.vault_protocol.yearn.offchain_metadata import YearnDetectedVaultMetadata, YearnVaultMetadata
from eth_defi.erc_4626.vault_protocol.yearn.vault import create_yearn_vault_link
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import NOT_IN_YEARN_FRONTEND, VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase

UNENDORSED_VAULT = "0x1111111111111111111111111111111111111111"
ENDORSED_PARTNER_VAULT = "0x2222222222222222222222222222222222222222"
MANUALLY_UNOFFICIAL_VAULT = "0x3333333333333333333333333333333333333333"
EXPECTED_TARGET_ROWS = 3
EXPECTED_SKIPPED_ROWS = 1
STRATEGY_VAULT = "0x6666666666666666666666666666666666666666"
FLEX_DESCRIPTION = "Flex USDC is an allocator vault managed by the Yearn Curation team. It lends USDC across several Flex markets."


def load_migration_module():
    """Load the hyphenated Yearn migration script as a Python module.

    :return:
        Imported Yearn migration module.
    """

    repository_root = Path(__file__).resolve().parents[2]
    script_path = repository_root / "scripts" / "erc-4626" / "migrate-yearn-vault-metadata.py"
    module_spec = importlib.util.spec_from_file_location("migrate_yearn_vault_metadata", script_path)
    assert module_spec is not None
    assert module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def create_vault_database() -> tuple[VaultDatabase, VaultSpec]:
    """Create representative Yearn adapter rows and one unrelated row.

    :return:
        In-memory metadata database and the unrelated row specification.
    """

    yearn_features = {ERC4626Feature.yearn_v3_like}
    rows = {
        VaultSpec(1, UNENDORSED_VAULT): {
            "Protocol": "Yearn",
            "Address": UNENDORSED_VAULT,
            "Link": "https://yearn.fi/vaults/old-route",
            "features": yearn_features,
            "_flags": set(),
            "_notes": None,
        },
        VaultSpec(1, ENDORSED_PARTNER_VAULT): {
            "Protocol": "Yearn",
            "Address": ENDORSED_PARTNER_VAULT,
            "Link": "https://yearn.fi/vaults/old-route",
            "features": yearn_features,
            "_flags": {VaultFlag.unofficial},
            "_notes": NOT_IN_YEARN_FRONTEND,
        },
        VaultSpec(1, MANUALLY_UNOFFICIAL_VAULT): {
            "Protocol": "Yearn",
            "Address": MANUALLY_UNOFFICIAL_VAULT,
            "Link": "https://yearn.fi/vaults/old-route",
            "features": yearn_features,
            "_flags": {VaultFlag.unofficial},
            "_notes": "Manual warning.",
        },
        VaultSpec(1, STRATEGY_VAULT): {
            "Protocol": "Yearn",
            "Address": STRATEGY_VAULT,
            "Link": "https://yearn.fi/vaults/old-route",
            "features": {ERC4626Feature.yearn_compounder_like},
            "_flags": {VaultFlag.unofficial},
            "_notes": NOT_IN_YEARN_FRONTEND,
        },
        VaultSpec(1, "0x4444444444444444444444444444444444444444"): {
            "Protocol": "Yearn",
            "Address": "0x4444444444444444444444444444444444444444",
            "Link": "https://yearn.fi/vaults/legacy-v2",
            "features": set(),
            "_flags": set(),
            "_notes": None,
        },
    }
    unrelated_spec = VaultSpec(1, "0x5555555555555555555555555555555555555555")
    rows[unrelated_spec] = {
        "Protocol": "Morpho",
        "Address": unrelated_spec.vault_address,
        "Link": "https://morpho.org/",
        "features": set(),
        "_flags": set(),
        "_notes": "Unchanged.",
    }
    return VaultDatabase(rows=rows, leads={unrelated_spec: object()}, last_scanned_block={1: 23_000_000}), unrelated_spec


def create_ydaemon_index() -> dict[str, YearnVaultMetadata]:
    """Create the relevant endorsement decisions for the fixed migration scope.

    :return:
        Lowercase address-keyed yDaemon metadata index.
    """

    return {
        UNENDORSED_VAULT: YearnVaultMetadata(endorsed=False, is_yearn=False),
        ENDORSED_PARTNER_VAULT: YearnVaultMetadata(endorsed=True, is_yearn=False),
        MANUALLY_UNOFFICIAL_VAULT: YearnVaultMetadata(endorsed=True, is_yearn=True),
    }


def test_migrate_yearn_vault_metadata_updates_only_dynamic_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Update links and dynamic endorsement fields while preserving manual decisions."""

    module = load_migration_module()
    vault_db, unrelated_spec = create_vault_database()
    manually_unofficial_spec = VaultSpec(1, MANUALLY_UNOFFICIAL_VAULT)

    def get_manual_flags(address: str, protocol_name: str) -> set[VaultFlag]:
        """Return the synthetic manual flag only for the reviewed Yearn row."""

        assert protocol_name == "Yearn"
        if address.lower() == MANUALLY_UNOFFICIAL_VAULT:
            return {VaultFlag.unofficial}
        return set()

    monkeypatch.setattr(
        module,
        "get_vault_special_flags",
        get_manual_flags,
    )

    detected_vaults = {
        (1, ENDORSED_PARTNER_VAULT): YearnDetectedVaultMetadata(description=FLEX_DESCRIPTION),
    }
    result = module.migrate_yearn_vault_metadata(vault_db, {1: create_ydaemon_index()}, dry_run=False, detected_vaults=detected_vaults)

    assert result.inspected_rows == EXPECTED_TARGET_ROWS + 1
    assert result.updated_rows == EXPECTED_TARGET_ROWS + 1
    assert result.unavailable_metadata_rows == 0
    assert result.skipped_rows == EXPECTED_SKIPPED_ROWS
    unendorsed_row = vault_db.rows[VaultSpec(1, UNENDORSED_VAULT)]
    assert unendorsed_row["_flags"] == {VaultFlag.unofficial}
    assert unendorsed_row["_notes"] == NOT_IN_YEARN_FRONTEND
    assert unendorsed_row["Link"] == create_yearn_vault_link(1, UNENDORSED_VAULT)
    partner_row = vault_db.rows[VaultSpec(1, ENDORSED_PARTNER_VAULT)]
    assert partner_row["_flags"] == set()
    assert partner_row["_notes"] is None
    assert partner_row["Link"] == create_yearn_vault_link(1, ENDORSED_PARTNER_VAULT)
    assert partner_row["_description"] == FLEX_DESCRIPTION
    assert partner_row["_short_description"] == "Flex USDC is an allocator vault managed by the Yearn Curation team."
    assert vault_db.rows[manually_unofficial_spec]["_flags"] == {VaultFlag.unofficial}
    assert vault_db.rows[manually_unofficial_spec]["_notes"] == "Manual warning."
    strategy_row = vault_db.rows[VaultSpec(1, STRATEGY_VAULT)]
    assert strategy_row["_flags"] == set()
    assert strategy_row["_notes"] is None
    assert vault_db.rows[unrelated_spec]["Link"] == "https://morpho.org/"
    assert vault_db.leads[unrelated_spec] is not None
    assert vault_db.last_scanned_block == {1: 23_000_000}


def test_migrate_yearn_vault_metadata_dry_run_and_missing_source_do_not_mutate() -> None:
    """Keep dry runs and unavailable yDaemon metadata non-mutating."""

    module = load_migration_module()
    vault_db, _ = create_vault_database()
    original_rows = {spec: row.copy() for spec, row in vault_db.rows.items()}

    result = module.migrate_yearn_vault_metadata(vault_db, {1: None}, dry_run=True)

    assert result.inspected_rows == EXPECTED_TARGET_ROWS + 1
    assert result.unavailable_metadata_rows == EXPECTED_TARGET_ROWS
    assert result.updated_rows == EXPECTED_TARGET_ROWS + 1
    assert vault_db.rows == original_rows


def test_migrate_yearn_vault_metadata_removes_stale_strategy_flag_but_keeps_manual_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the old adapter-owned flag even when a manual note had priority."""

    module = load_migration_module()
    vault_db, _ = create_vault_database()
    strategy_row = vault_db.rows[VaultSpec(1, STRATEGY_VAULT)]
    strategy_row["_notes"] = "Manual operational warning."
    monkeypatch.setattr(module, "get_vault_special_flags", lambda *_args, **_kwargs: set())

    module.migrate_yearn_vault_metadata(vault_db, {1: create_ydaemon_index()}, dry_run=False, detected_vaults={})

    assert strategy_row["_flags"] == set()
    assert strategy_row["_notes"] == "Manual operational warning."


def test_migrate_yearn_vault_metadata_clears_removed_public_description() -> None:
    """Clear stale website copy when Yearn retains the page without a description."""

    module = load_migration_module()
    vault_db, _ = create_vault_database()
    row = vault_db.rows[VaultSpec(1, ENDORSED_PARTNER_VAULT)]
    row["_description"] = "Outdated description."
    row["_short_description"] = "Outdated description."
    detected_vaults = {
        (1, ENDORSED_PARTNER_VAULT): YearnDetectedVaultMetadata(description=None),
    }

    module.migrate_yearn_vault_metadata(vault_db, {1: create_ydaemon_index()}, dry_run=False, detected_vaults=detected_vaults)

    assert row["_description"] is None
    assert row["_short_description"] is None


def test_yearn_metadata_migration_main_creates_backup_only_when_applying(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Exercise dry-run and atomic persistent paths without a live yDaemon fetch."""

    module = load_migration_module()
    vault_db, _ = create_vault_database()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(vault_db_path)
    monkeypatch.setattr(module, "setup_console_logging", lambda **_kwargs: None)
    monkeypatch.setattr(module, "fetch_yearn_metadata_by_chain", lambda chain_ids, _cache_path: {chain_id: create_ydaemon_index() for chain_id in chain_ids})
    monkeypatch.setattr(module, "fetch_yearn_detected_vaults", lambda: {})
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VAULT_DB_PATH", str(vault_db_path))
    monkeypatch.setenv("DRY_RUN", "true")

    module.main()
    capsys.readouterr()
    assert not (tmp_path / "vault-metadata-db.pickle.bak-yearn-vault-metadata").exists()
    assert VaultDatabase.read(vault_db_path).rows[VaultSpec(1, UNENDORSED_VAULT)]["_flags"] == set()

    monkeypatch.setenv("DRY_RUN", "false")
    module.main()
    capsys.readouterr()
    assert (tmp_path / "vault-metadata-db.pickle.bak-yearn-vault-metadata").exists()
    assert VaultDatabase.read(vault_db_path).rows[VaultSpec(1, UNENDORSED_VAULT)]["_flags"] == {VaultFlag.unofficial}
