"""Tests for the cached Axis StakedUSDx metadata migration."""

import datetime
import importlib.util
from pathlib import Path
from types import ModuleType

from eth_defi.erc_4626.classification import AXIS_HARDCODED_PROTOCOLS_BY_CHAIN
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.axis.constants import AXIS_ETHEREUM_CHAIN_ID, AXIS_NOTES_BY_CHAIN, AXIS_SHORT_DESCRIPTION, AXIS_STAKED_USDX_BY_CHAIN
from eth_defi.vault.base import VaultSpec, WithdrawalDelayType, WithdrawalPeriod
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.flag import get_notes
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase

EXPECTED_AXIS_DEPLOYMENTS = 2


def load_migration_module() -> ModuleType:
    """Load the Axis migration script as an importable test module.

    :return:
        Loaded migration module.
    """
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "migrate-axis-vault-metadata.py"
    spec = importlib.util.spec_from_file_location("migrate_axis_vault_metadata", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_detection(spec: VaultSpec, features: set[ERC4626Feature]) -> ERC4262VaultDetection:
    """Create the minimal scanner detection envelope used by the cache.

    :param spec:
        Vault identity for the cached detection.
    :param features:
        Feature set to persist.
    :return:
        Synthetic scanner detection data.
    """
    timestamp = datetime.datetime(2026, 7, 30, tzinfo=datetime.UTC).replace(tzinfo=None)
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


def create_row(spec: VaultSpec, features: set[ERC4626Feature]) -> dict:
    """Create a minimal persisted scanner row.

    :param spec:
        Vault identity for the metadata row.
    :param features:
        Existing cached features.
    :return:
        Minimal mutable metadata row.
    """
    return {
        "Name": f"Vault {spec.vault_address[-4:]}",
        "Protocol": "<protocol not yet identified>",
        "Features": ", ".join(sorted(feature.name for feature in features)),
        "features": set(features),
        "_detection_data": create_detection(spec, features),
        "_lockup": datetime.timedelta(days=7),
        "_withdrawal_period": WithdrawalPeriod(datetime.timedelta(days=7), datetime.timedelta(days=7), WithdrawalDelayType.delay),
        "_notes": "stale note",
        "_short_description": "stale description",
    }


def test_migrate_axis_metadata_updates_only_target_and_is_idempotent(tmp_path: Path) -> None:
    """Persist both Axis classifications without changing unrelated cache rows."""
    migration = load_migration_module()
    axis_specs = tuple(VaultSpec(chain_id, address) for chain_id, address in AXIS_STAKED_USDX_BY_CHAIN.items())
    unrelated_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(
        rows={
            **{axis_spec: create_row(axis_spec, {ERC4626Feature.erc_7540_like}) for axis_spec in axis_specs},
            unrelated_spec: create_row(unrelated_spec, {ERC4626Feature.morpho_like}),
        },
    ).write(vault_db_path)
    original_unrelated_row = VaultDatabase.read(vault_db_path).rows[unrelated_spec]

    result = migration.migrate_axis_metadata(vault_db_path, dry_run=False)

    assert result.matched_rows == EXPECTED_AXIS_DEPLOYMENTS
    assert result.migrated_rows == EXPECTED_AXIS_DEPLOYMENTS
    assert (tmp_path / "vault-metadata-db.pickle.bak-axis-metadata").exists()
    migrated_db = VaultDatabase.read(vault_db_path)
    for axis_spec in axis_specs:
        deployment_key = axis_spec.chain_id, axis_spec.vault_address
        expected_features = AXIS_HARDCODED_PROTOCOLS_BY_CHAIN[deployment_key]
        migrated_row = migrated_db.rows[axis_spec]
        assert migrated_row["features"] == expected_features
        assert migrated_row["_detection_data"].features == expected_features
        assert migrated_row["_short_description"] == AXIS_SHORT_DESCRIPTION
        assert migrated_row["_notes"] == AXIS_NOTES_BY_CHAIN[deployment_key]
        assert migrated_row["_fees"] == FeeData(VaultFeeMode.feeless, 0.0, 0.0, 0.0, 0.0)

    ethereum_spec = next(spec for spec in axis_specs if spec.chain_id == AXIS_ETHEREUM_CHAIN_ID)
    assert migrated_db.rows[ethereum_spec]["_lockup"] is None
    assert migrated_db.rows[ethereum_spec]["_withdrawal_period"] == WithdrawalPeriod(None, None, WithdrawalDelayType.delay)
    assert migrated_db.rows[ethereum_spec]["_strategy_tags"] == {
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.multistrategy,
        StrategyTag.perpetual_futures,
    }
    plasma_spec = next(spec for spec in axis_specs if spec.chain_id != AXIS_ETHEREUM_CHAIN_ID)
    assert "_lockup" not in migrated_db.rows[plasma_spec]
    assert "_withdrawal_period" not in migrated_db.rows[plasma_spec]
    assert migrated_db.rows[plasma_spec]["_strategy_tags"] == migrated_db.rows[ethereum_spec]["_strategy_tags"]
    assert migrated_db.rows[unrelated_spec] == original_unrelated_row

    repeat_result = migration.migrate_axis_metadata(vault_db_path, dry_run=False)

    assert repeat_result.matched_rows == EXPECTED_AXIS_DEPLOYMENTS
    assert repeat_result.migrated_rows == 0
    assert not (tmp_path / "vault-metadata-db.pickle.bak-axis-metadata.1").exists()


def test_axis_deployment_notes_are_available_to_every_rescan() -> None:
    """Expose the correct deployment-specific redemption note on every rescan."""
    assert all(get_notes(address, chain_id=chain_id, protocol_name="Axis") == note for (chain_id, address), note in AXIS_NOTES_BY_CHAIN.items())


def test_migrate_axis_metadata_dry_run_and_missing_row_do_not_write(tmp_path: Path) -> None:
    """Leave the cache untouched during a dry run or when Axis is not present."""
    migration = load_migration_module()
    axis_spec = VaultSpec(AXIS_ETHEREUM_CHAIN_ID, AXIS_STAKED_USDX_BY_CHAIN[AXIS_ETHEREUM_CHAIN_ID])
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={axis_spec: create_row(axis_spec, set())}).write(vault_db_path)
    original_contents = vault_db_path.read_bytes()

    dry_run_result = migration.migrate_axis_metadata(vault_db_path, dry_run=True)

    assert dry_run_result.matched_rows == 1
    assert dry_run_result.migrated_rows == 1
    assert vault_db_path.read_bytes() == original_contents
    assert not (tmp_path / "vault-metadata-db.pickle.bak-axis-metadata").exists()

    empty_db_path = tmp_path / "empty-vault-metadata-db.pickle"
    VaultDatabase(rows={}).write(empty_db_path)
    empty_contents = empty_db_path.read_bytes()

    missing_result = migration.migrate_axis_metadata(empty_db_path, dry_run=False)

    assert missing_result.matched_rows == 0
    assert missing_result.migrated_rows == 0
    assert empty_db_path.read_bytes() == empty_contents
    assert not (tmp_path / "empty-vault-metadata-db.pickle.bak-axis-metadata").exists()
