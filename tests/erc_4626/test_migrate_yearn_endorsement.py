"""Tests for the Yearn public-registry exclusion migration."""

import datetime
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.vault_protocol.yearn.endorsement import YEARN_REGISTRY_EXCLUDED_VAULTS_BY_CHAIN, add_yearn_registry_exclusion
from eth_defi.erc_4626.vault_protocol.yearn.vault import YearnV3Vault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import identify_curator
from eth_defi.vault.vaultdb import VaultDatabase, VaultRow

MINIMUM_ETHEREUM_YEARN_REGISTRY_EXCLUSIONS = 100


def load_migration_module() -> ModuleType:
    """Load the Yearn registry-exclusion migration script as a test module.

    :return:
        Loaded migration module.
    """
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "migrate-yearn-endorsement.py"
    spec = importlib.util.spec_from_file_location("migrate_yearn_endorsement", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_detection(spec: VaultSpec, features: set[ERC4626Feature]) -> ERC4262VaultDetection:
    """Create persisted detection data for a vault fixture.

    :param spec:
        Fixture chain/address identifier.
    :param features:
        Persisted vault feature markers.
    :return:
        Minimal metadata detection envelope.
    """
    timestamp = datetime.datetime(2026, 9, 14, tzinfo=datetime.UTC).replace(tzinfo=None)
    return ERC4262VaultDetection(
        chain=spec.chain_id,
        address=spec.vault_address,
        first_seen_at_block=1,
        first_seen_at=timestamp,
        features=set(features),
        updated_at=timestamp,
        deposit_count=1,
        redeem_count=1,
    )


def create_row(spec: VaultSpec, features: set[ERC4626Feature]) -> VaultRow:
    """Create a cached Yearn row for the migration fixture.

    :param spec:
        Fixture chain/address identifier.
    :param features:
        Persisted vault feature markers.
    :return:
        Minimal vault metadata row.
    """
    return cast(
        VaultRow,
        {
            "Name": "Neutral vault name",
            "Protocol": "Yearn",
            "Features": ", ".join(sorted(feature.name for feature in features)),
            "features": set(features),
            "protocol_slug": "yearn",
            "_detection_data": create_detection(spec, features),
        },
    )


def test_yearn_registry_exclusion_snapshot_contains_known_predeposit_vault() -> None:
    """Keep the reviewed Katana pre-deposit vault in the primary-list exclusions."""
    assert len(YEARN_REGISTRY_EXCLUDED_VAULTS_BY_CHAIN[1]) > MINIMUM_ETHEREUM_YEARN_REGISTRY_EXCLUSIONS
    assert "0x7b5a0182e400b241b317e781a4e9dedfc1429822" in YEARN_REGISTRY_EXCLUDED_VAULTS_BY_CHAIN[1]


def test_migrate_yearn_registry_exclusions_remove_protocol_and_curator_attribution() -> None:
    """An excluded V3 vault becomes generic without losing its Yearn adapter feature."""
    migration = load_migration_module()
    excluded_spec = VaultSpec(1, "0x7b5a0182e400b241b317e781a4e9dedfc1429822")
    endorsed_spec = VaultSpec(747474, "0x93fec6639717b6215a48e5a72a162c50dcc40d68")
    features = {ERC4626Feature.yearn_v3_like}
    excluded_row = create_row(excluded_spec, features)
    excluded_row["protocol_slug"] = "stale-protocol-slug"
    vault_db = VaultDatabase(
        rows={
            excluded_spec: excluded_row,
            endorsed_spec: create_row(endorsed_spec, features),
        }
    )

    updates = migration.collect_yearn_registry_exclusion_updates(vault_db)
    assert [update.spec for update in updates] == [excluded_spec]
    assert updates[0].new_protocol == "ERC-4626"
    excluded_features = add_yearn_registry_exclusion(excluded_spec.chain_id, excluded_spec.vault_address, features)
    assert excluded_features == {ERC4626Feature.yearn_v3_like, ERC4626Feature.yearn_registry_excluded}
    assert get_vault_protocol_name(excluded_features) == "ERC-4626"
    assert get_vault_protocol_name(excluded_features | {ERC4626Feature.morpho_like}) == "Morpho"
    adapter = create_vault_instance(SimpleNamespace(eth=SimpleNamespace(chain_id=1)), excluded_spec.vault_address, features=excluded_features)
    assert isinstance(adapter, YearnV3Vault)
    assert adapter.get_link().lower() == f"https://routescan.io/address/{excluded_spec.vault_address}".lower()

    migration.apply_yearn_registry_exclusion_updates(vault_db, updates)

    excluded_row = vault_db.rows[excluded_spec]
    assert excluded_row["Protocol"] == "ERC-4626"
    assert excluded_row["protocol_slug"] == "erc-4626"
    assert excluded_row["Link"] == f"https://routescan.io/address/{excluded_spec.vault_address}"
    assert excluded_row["features"] == excluded_features
    assert excluded_row["_detection_data"].features == excluded_features
    assert identify_curator(excluded_spec.chain_id, "kpdUSDC", "Neutral vault name", excluded_spec.vault_address, excluded_row["protocol_slug"]) is None

    endorsed_row = vault_db.rows[endorsed_spec]
    assert endorsed_row["Protocol"] == "Yearn"
    assert identify_curator(endorsed_spec.chain_id, "yvAUSD", "Neutral vault name", endorsed_spec.vault_address, endorsed_row["protocol_slug"]) == "yearn"


def test_migration_uses_legacy_detection_features_and_skips_empty_write(tmp_path: Path) -> None:
    """Legacy rows are updated from detection data, while empty runs do not write a backup."""
    migration = load_migration_module()
    excluded_spec = VaultSpec(1, "0x7b5a0182e400b241b317e781a4e9dedfc1429822")
    legacy_row = create_row(excluded_spec, {ERC4626Feature.yearn_v3_like})
    del legacy_row["features"]
    vault_db = VaultDatabase(rows={excluded_spec: legacy_row})
    updates = migration.collect_yearn_registry_exclusion_updates(vault_db)
    assert updates[0].old_features == frozenset({ERC4626Feature.yearn_v3_like})

    database_path = tmp_path / "vault-db.pickle"
    no_updates_db = VaultDatabase(rows={VaultSpec(1, "0x0000000000000000000000000000000000000001"): {"Protocol": "ERC-4626"}})
    no_updates_db.write(database_path)
    original_data = database_path.read_bytes()

    assert migration.migrate_yearn_registry_exclusions(database_path, dry_run=False) == []
    assert database_path.read_bytes() == original_data
    assert not list(tmp_path.glob("*.bak-yearn-registry-exclusion*"))
