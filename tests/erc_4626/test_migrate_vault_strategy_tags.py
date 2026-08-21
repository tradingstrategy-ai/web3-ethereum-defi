"""Tests for the all-vault strategy-tag metadata migration."""

import datetime
import importlib.util
from pathlib import Path

import pytest

from eth_defi.apex.constants import APEX_CHAIN_ID
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase

EXPECTED_INSPECTED_ROWS = 4
EXPECTED_UPDATED_ROWS = 2
EXPECTED_SKIPPED_ROWS = 2
EXPECTED_INVALID_TAG_ROWS = 0


def load_migration_module():
    """Load the strategy-tag migration script as a test module."""

    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "migrate-vault-strategy-tags.py"
    spec = importlib.util.spec_from_file_location("migrate_vault_strategy_tags", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_detection(spec: VaultSpec, features: set[ERC4626Feature]) -> ERC4262VaultDetection:
    """Create a persisted detection record for a migration fixture."""

    timestamp = datetime.datetime(2026, 8, 18, tzinfo=datetime.UTC).replace(tzinfo=None)
    return ERC4262VaultDetection(
        chain=spec.chain_id,
        address=spec.vault_address,
        first_seen_at_block=1,
        first_seen_at=timestamp,
        features=features,
        updated_at=timestamp,
        deposit_count=1,
        redeem_count=1,
    )


def test_migrate_vault_strategy_tags_updates_evm_and_native_rows(tmp_path: Path) -> None:
    """Current VaultBase and native resolvers populate the metadata pickle."""

    migration = load_migration_module()
    atoma_spec = VaultSpec(42_161, "0xcc56410e1a136af0eceb7241c6ae394f4d8b581c")
    hyperliquid_spec = VaultSpec(HYPERCORE_CHAIN_ID, "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303")
    unresolved_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    unmaintained_spec = VaultSpec(1, "0x0000000000000000000000000000000000000002")
    vault_db_path = tmp_path / "vault-metadata-db.pickle"

    VaultDatabase(
        rows={
            atoma_spec: {
                "Protocol": "Atoma",
                "_detection_data": create_detection(atoma_spec, {ERC4626Feature.atoma_like}),
                "_strategy_tags": None,
            },
            hyperliquid_spec: {
                "Protocol": "Hyperliquid",
                "_detection_data": create_detection(hyperliquid_spec, {ERC4626Feature.hypercore_native}),
                "_strategy_tags": {StrategyTag.unknown},
            },
            unresolved_spec: {
                "Protocol": "Unknown",
                "_strategy_tags": {StrategyTag.unknown},
            },
            unmaintained_spec: {
                "Protocol": "Lagoon Finance",
                "_detection_data": create_detection(unmaintained_spec, {ERC4626Feature.lagoon_like}),
                "_strategy_tags": {StrategyTag.unknown},
            },
        }
    ).write(vault_db_path)

    result = migration.migrate_vault_strategy_tags(vault_db_path, dry_run=False)

    assert result.inspected_rows == EXPECTED_INSPECTED_ROWS
    assert result.updated_rows == EXPECTED_UPDATED_ROWS
    assert result.skipped_rows == EXPECTED_SKIPPED_ROWS
    assert result.invalid_tag_rows == EXPECTED_INVALID_TAG_ROWS
    assert result.backup_path == tmp_path / "vault-metadata-db.pickle.bak-strategy-tags"
    assert result.backup_path.exists()

    migrated_db = VaultDatabase.read(vault_db_path)
    assert migrated_db.rows[atoma_spec]["_strategy_tags"] == {
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.perpetual_futures,
    }
    assert migrated_db.rows[hyperliquid_spec]["_strategy_tags"] == {
        StrategyTag.liquidity_provider,
        StrategyTag.market_maker,
        StrategyTag.market_making,
        StrategyTag.perpetual_futures,
    }
    assert migrated_db.rows[unresolved_spec]["_strategy_tags"] == {StrategyTag.unknown}
    assert migrated_db.rows[unmaintained_spec]["_strategy_tags"] == {StrategyTag.unknown}

    second_result = migration.migrate_vault_strategy_tags(vault_db_path, dry_run=False)
    assert second_result.updated_rows == 0
    assert second_result.backup_path is None


def test_migrate_vault_strategy_tags_dry_run_preserves_database(tmp_path: Path) -> None:
    """Dry-run mode reports changes without creating a backup or writing rows."""

    migration = load_migration_module()
    spec = VaultSpec(42_161, "0xcc56410e1a136af0eceb7241c6ae394f4d8b581c")
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(
        rows={
            spec: {
                "Protocol": "Atoma",
                "_detection_data": create_detection(spec, {ERC4626Feature.atoma_like}),
                "_strategy_tags": None,
            }
        }
    ).write(vault_db_path)

    result = migration.migrate_vault_strategy_tags(vault_db_path, dry_run=True)

    assert result.updated_rows == 1
    assert result.invalid_tag_rows == 0
    assert result.backup_path is None
    assert VaultDatabase.read(vault_db_path).rows[spec]["_strategy_tags"] is None


def test_migrate_vault_strategy_tags_repairs_legacy_values(tmp_path: Path) -> None:
    """Resolved rows replace legacy string values with current enum tags."""

    migration = load_migration_module()
    spec = VaultSpec(42_161, "0xcc56410e1a136af0eceb7241c6ae394f4d8b581c")
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(
        rows={
            spec: {
                "Protocol": "Atoma",
                "_detection_data": create_detection(spec, {ERC4626Feature.atoma_like}),
                "_strategy_tags": {"legacy_funding_tag"},
            }
        }
    ).write(vault_db_path)

    result = migration.migrate_vault_strategy_tags(vault_db_path, dry_run=False)

    assert result.updated_rows == 1
    assert result.invalid_tag_rows == 1
    assert VaultDatabase.read(vault_db_path).rows[spec]["_strategy_tags"] == {
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.perpetual_futures,
    }


def test_migrate_vault_strategy_tags_canonicalises_recognised_string_values(tmp_path: Path) -> None:
    """Recognised raw strings are rewritten as ``StrategyTag`` members."""

    migration = load_migration_module()
    spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(
        rows={
            spec: {
                "Protocol": "Aave",
                "_detection_data": create_detection(spec, {ERC4626Feature.aave_like}),
                "_strategy_tags": {"lending"},
            }
        }
    ).write(vault_db_path)

    result = migration.migrate_vault_strategy_tags(vault_db_path, dry_run=False)

    assert result.updated_rows == 1
    assert result.invalid_tag_rows == 1
    persisted_tags = VaultDatabase.read(vault_db_path).rows[spec]["_strategy_tags"]
    assert persisted_tags == {StrategyTag.lending}
    assert all(isinstance(tag, StrategyTag) for tag in persisted_tags)


def test_migrate_vault_strategy_tags_follows_adapter_priority() -> None:
    """The migration chooses IPOR before the later Spiko feature branch."""

    migration = load_migration_module()
    spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    row = {
        "_detection_data": create_detection(spec, {ERC4626Feature.ipor_like, ERC4626Feature.spiko_like}),
    }

    result = migration.resolve_strategy_tags(spec, row)

    assert result is not None
    assert result[1] == "IPOR Fusion tag resolver"


def test_migrate_vault_strategy_tags_resolves_apex_native_rows() -> None:
    """ApeX native rows receive the platform's perpetual-futures default."""

    migration = load_migration_module()
    spec = VaultSpec(APEX_CHAIN_ID, "apex-vault-10001")
    row = {
        "_detection_data": create_detection(spec, {ERC4626Feature.apex_native}),
    }

    result = migration.resolve_strategy_tags(spec, row)

    assert result == ({StrategyTag.perpetual_futures}, "ApeX native resolver")


def test_migrate_vault_strategy_tags_resolves_lagoon_rows() -> None:
    """Lagoon resolution includes the deployment chain to avoid address collisions."""
    migration = load_migration_module()
    spec = VaultSpec(8453, "0xb09f761cb13baca8ec087ac476647361b6314f98")
    row = {
        "_detection_data": create_detection(spec, {ERC4626Feature.lagoon_like}),
    }

    result = migration.resolve_strategy_tags(spec, row)

    assert result == (
        {
            StrategyTag.arbitrage,
            StrategyTag.delta_neutral,
            StrategyTag.lending_looping,
            StrategyTag.multistrategy,
            StrategyTag.yield_farming,
        },
        "Lagoon Finance tag resolver",
    )


def test_migrate_vault_strategy_tags_resolves_kiloex_rows() -> None:
    """KiloEx rows use their own transferred address mapping."""

    migration = load_migration_module()
    spec = VaultSpec(8453, "0x43e3e6ffb2e363e64cd480cbb7cd0cf47bc6b477")
    row = {
        "_detection_data": create_detection(spec, {ERC4626Feature.kiloex_like}),
    }

    result = migration.resolve_strategy_tags(spec, row)

    assert result == (
        {
            StrategyTag.amm,
            StrategyTag.liquidity_provider,
            StrategyTag.market_maker,
            StrategyTag.market_making,
            StrategyTag.market_making_amm,
            StrategyTag.perpetual_futures,
        },
        "KiloEx tag resolver",
    )


def test_migrate_vault_strategy_tags_resolves_liquid_royalty_rows() -> None:
    """Liquid Royalty rows use their maintained royalty-stream mapping."""

    migration = load_migration_module()
    spec = VaultSpec(80094, "0x09cea16a2563c2d7d807c86f5b8da760389b5915")
    row = {
        "_detection_data": create_detection(spec, {ERC4626Feature.liquid_royalty_like}),
    }

    result = migration.resolve_strategy_tags(spec, row)

    assert result == ({StrategyTag.rwa_royalties}, "Liquid Royalty tag resolver")


@pytest.mark.parametrize(
    "features",
    [
        {ERC4626Feature.mellow_like, ERC4626Feature.symbiotic_like},
        {ERC4626Feature.lagoon_like, ERC4626Feature.spiko_like},
        {ERC4626Feature.kiln_metavault_like, ERC4626Feature.gains_like},
    ],
)
def test_migrate_vault_strategy_tags_skips_unsupported_higher_priority_adapters(features: set[ERC4626Feature]) -> None:
    """The migration follows scanner adapter precedence before resolving tags."""

    migration = load_migration_module()
    spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    row = {"_detection_data": create_detection(spec, features)}

    assert migration.resolve_strategy_tags(spec, row) is None


def test_create_backup_path_avoids_existing_backups(tmp_path: Path) -> None:
    """Backup selection never overwrites an earlier migration backup."""

    migration = load_migration_module()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    first_backup = vault_db_path.with_suffix(".pickle.bak-strategy-tags")
    first_backup.touch()
    (tmp_path / "vault-metadata-db.pickle.bak-strategy-tags.1").touch()

    assert migration.create_backup_path(vault_db_path) == tmp_path / "vault-metadata-db.pickle.bak-strategy-tags.2"


def test_parse_boolean_env_requires_a_recognised_value() -> None:
    """Migration configuration rejects ambiguous boolean values."""

    migration = load_migration_module()

    assert migration.parse_boolean_env(None, default=True) is True
    assert migration.parse_boolean_env("OFF", default=True) is False
    with pytest.raises(ValueError, match="Expected a boolean"):
        migration.parse_boolean_env("maybe", default=True)
