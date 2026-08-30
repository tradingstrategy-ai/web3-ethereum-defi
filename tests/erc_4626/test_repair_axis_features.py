"""Tests for the cached Axis StakedUSDx metadata repair."""

import datetime
import importlib.util
from pathlib import Path

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.axis.constants import AXIS_ERC7575_VAULTS_BY_CHAIN, AXIS_ETHEREUM_CHAIN_ID, AXIS_NOTES, AXIS_SHORT_DESCRIPTION, AXIS_STAKED_USDX_BY_CHAIN, AXIS_VAULTS_BY_CHAIN
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.flag import get_notes
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase

EXPECTED_AXIS_DEPLOYMENTS = 2


def load_repair_module():
    """Load the Axis repair script as an importable test module.

    :return:
        Loaded repair module.
    """
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "repair-axis-features.py"
    spec = importlib.util.spec_from_file_location("repair_axis_features", script_path)
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
        "_notes": "stale note",
        "_short_description": "stale description",
    }


def test_repair_axis_features_updates_only_target_and_is_idempotent(tmp_path: Path) -> None:
    """Persist both Axis classifications without changing unrelated cache rows."""
    repair = load_repair_module()
    axis_specs = tuple(VaultSpec(chain_id, address) for chain_id, address in AXIS_STAKED_USDX_BY_CHAIN.items())
    unrelated_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    base_axis_features = {ERC4626Feature.axis_like, ERC4626Feature.erc_7540_like}
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(
        rows={
            **{axis_spec: create_row(axis_spec, {ERC4626Feature.erc_7540_like}) for axis_spec in axis_specs},
            unrelated_spec: create_row(unrelated_spec, {ERC4626Feature.morpho_like}),
        },
    ).write(vault_db_path)
    original_unrelated_row = VaultDatabase.read(vault_db_path).rows[unrelated_spec]

    result = repair.repair_axis_features(vault_db_path, dry_run=False)

    assert result.matched_rows == EXPECTED_AXIS_DEPLOYMENTS
    assert result.repaired_rows == EXPECTED_AXIS_DEPLOYMENTS
    assert (tmp_path / "vault-metadata-db.pickle.bak-axis-repair").exists()
    repaired_db = VaultDatabase.read(vault_db_path)
    for axis_spec in axis_specs:
        axis_features = base_axis_features | ({ERC4626Feature.erc_7575_like} if (axis_spec.chain_id, axis_spec.vault_address) in AXIS_ERC7575_VAULTS_BY_CHAIN else set())
        repaired_row = repaired_db.rows[axis_spec]
        assert repaired_row["features"] == axis_features
        assert repaired_row["_detection_data"].features == axis_features
        assert repaired_row["_short_description"] == AXIS_SHORT_DESCRIPTION
        assert repaired_row["_notes"] == AXIS_NOTES
        assert repaired_row["_lockup"] == datetime.timedelta(days=7)
        assert repaired_row["_fees"] == FeeData(VaultFeeMode.internalised_skimming, 0.0, 0.0, 0.0, 0.0)

    ethereum_spec = next(spec for spec in axis_specs if spec.chain_id == AXIS_ETHEREUM_CHAIN_ID)
    assert repaired_db.rows[ethereum_spec]["_strategy_tags"] == {
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.multistrategy,
        StrategyTag.perpetual_futures,
    }
    assert repaired_db.rows[unrelated_spec] == original_unrelated_row

    repeat_result = repair.repair_axis_features(vault_db_path, dry_run=False)

    assert repeat_result.matched_rows == EXPECTED_AXIS_DEPLOYMENTS
    assert repeat_result.repaired_rows == 0
    assert not (tmp_path / "vault-metadata-db.pickle.bak-axis-repair.1").exists()


def test_axis_note_is_available_to_every_rescan() -> None:
    """Expose the async-redemption warning through the scanner note registry."""
    assert all(get_notes(address, chain_id=chain_id, protocol_name="Axis") == AXIS_NOTES for chain_id, address in AXIS_VAULTS_BY_CHAIN)


def test_repair_axis_features_dry_run_and_missing_row_do_not_write(tmp_path: Path) -> None:
    """Leave the cache untouched during a dry run or when Axis is not present."""
    repair = load_repair_module()
    axis_spec = VaultSpec(AXIS_ETHEREUM_CHAIN_ID, AXIS_STAKED_USDX_BY_CHAIN[AXIS_ETHEREUM_CHAIN_ID])
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(rows={axis_spec: create_row(axis_spec, set())}).write(vault_db_path)
    original_contents = vault_db_path.read_bytes()

    dry_run_result = repair.repair_axis_features(vault_db_path, dry_run=True)

    assert dry_run_result.matched_rows == 1
    assert dry_run_result.repaired_rows == 1
    assert vault_db_path.read_bytes() == original_contents
    assert not (tmp_path / "vault-metadata-db.pickle.bak-axis-repair").exists()

    empty_db_path = tmp_path / "empty-vault-metadata-db.pickle"
    VaultDatabase(rows={}).write(empty_db_path)
    empty_contents = empty_db_path.read_bytes()

    missing_result = repair.repair_axis_features(empty_db_path, dry_run=False)

    assert missing_result.matched_rows == 0
    assert missing_result.repaired_rows == 0
    assert empty_db_path.read_bytes() == empty_contents
    assert not (tmp_path / "empty-vault-metadata-db.pickle.bak-axis-repair").exists()
