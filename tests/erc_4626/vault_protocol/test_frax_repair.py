"""Test the historical Frax metadata migration."""

import datetime
import importlib.util
from pathlib import Path
from types import ModuleType

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

EXPECTED_REPAIRED_FAMILY_ROWS = 2


def load_frax_repair_module() -> ModuleType:
    """Load the Frax repair script as an importable module.

    :return: Loaded repair script module.
    """

    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "erc-4626" / "repair-frax-features.py"
    spec = importlib.util.spec_from_file_location("repair_frax_features", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_detection(spec: VaultSpec) -> ERC4262VaultDetection:
    """Create generic historical detection metadata for a test row.

    :param spec: Vault identifier for the detection object.
    :return: Generic ERC-4626 detection record.
    """

    timestamp = datetime.datetime(2026, 7, 20, tzinfo=datetime.UTC).replace(tzinfo=None)
    return ERC4262VaultDetection(
        chain=spec.chain_id,
        address=spec.vault_address,
        first_seen_at_block=1,
        first_seen_at=timestamp,
        features=set(),
        updated_at=timestamp,
        deposit_count=1,
        redeem_count=0,
    )


def test_repair_frax_features_routes_each_family(tmp_path: Path) -> None:
    """Repair known Fraxlend and staking rows without changing unrelated data."""

    repair = load_frax_repair_module()
    fraxlend_spec = VaultSpec(1, "0x0601b72bef2b3f09e9f48b7d60a8d7d2d3800c6e")
    staking_spec = VaultSpec(1, "0xa663b02cf0a4b149d2ad41910cb81e23e1c41c32")
    unrelated_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    vault_db = VaultDatabase(
        rows={
            fraxlend_spec: {
                "Name": "Fraxlend Interest Bearing FRAX (Lido DAO Token) - 29",
                "Protocol": "ERC-4626",
                "features": set(),
                "_detection_data": create_detection(fraxlend_spec),
            },
            staking_spec: {
                "Name": "Staked FRAX",
                "Protocol": "ERC-4626",
                "features": set(),
                "_detection_data": create_detection(staking_spec),
            },
            unrelated_spec: {
                "Name": "Unrelated vault",
                "Protocol": "ERC-4626",
                "features": set(),
                "_detection_data": create_detection(unrelated_spec),
            },
        }
    )
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(vault_db_path)

    result = repair.repair_frax_features(vault_db_path, dry_run=False)

    assert result.matched_rows == EXPECTED_REPAIRED_FAMILY_ROWS
    assert result.repaired_rows == EXPECTED_REPAIRED_FAMILY_ROWS
    repaired_db = VaultDatabase.read(vault_db_path)
    repaired_row = repaired_db.rows[fraxlend_spec]
    assert repaired_row["Protocol"] == "Frax"
    assert ERC4626Feature.frax_like in repaired_row["features"]
    assert ERC4626Feature.frax_like in repaired_row["_detection_data"].features

    staking_row = repaired_db.rows[staking_spec]
    assert staking_row["Protocol"] == "Frax"
    assert staking_row["features"] == {ERC4626Feature.frax_staking_like}
    assert staking_row["_detection_data"].features == {ERC4626Feature.frax_staking_like}

    unrelated_row = repaired_db.rows[unrelated_spec]
    assert unrelated_row["Protocol"] == "ERC-4626"
    assert unrelated_row["features"] == set()
    assert unrelated_row["_detection_data"].features == set()
