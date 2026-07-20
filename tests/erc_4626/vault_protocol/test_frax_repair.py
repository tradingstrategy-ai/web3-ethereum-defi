"""Test the historical Frax metadata migration."""

import datetime
import importlib.util
from pathlib import Path
from types import ModuleType

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.frax.constants import FRAXLEND_PROTOCOL_FEE
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
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


def create_detection(spec: VaultSpec, features: set[ERC4626Feature] | None = None) -> ERC4262VaultDetection:
    """Create generic historical detection metadata for a test row.

    :param spec: Vault identifier for the detection object.
    :param features: Optional stale protocol features to repair.
    :return: Generic ERC-4626 detection record.
    """

    timestamp = datetime.datetime(2026, 7, 20, tzinfo=datetime.UTC).replace(tzinfo=None)
    return ERC4262VaultDetection(
        chain=spec.chain_id,
        address=spec.vault_address,
        first_seen_at_block=1,
        first_seen_at=timestamp,
        features=set(features or set()),
        updated_at=timestamp,
        deposit_count=1,
        redeem_count=0,
    )


def test_repair_frax_features_routes_each_family(tmp_path: Path) -> None:
    """Repair complete Frax metadata without changing unrelated data."""

    repair = load_frax_repair_module()
    fraxlend_spec = VaultSpec(1, "0x0601b72bef2b3f09e9f48b7d60a8d7d2d3800c6e")
    staking_spec = VaultSpec(1, "0xa663b02cf0a4b149d2ad41910cb81e23e1c41c32")
    unrelated_spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    vault_db = VaultDatabase(
        rows={
            fraxlend_spec: {
                "Name": "Fraxlend Interest Bearing FRAX (Lido DAO Token) - 29",
                "Protocol": "ERC-4626",
                "features": {ERC4626Feature.morpho_like},
                "_detection_data": create_detection(fraxlend_spec, {ERC4626Feature.morpho_like}),
                "Mgmt fee": None,
                "Perf fee": None,
                "_fees": FeeData(VaultFeeMode.externalised, None, None, 0.0, 0.0),
                "Link": "https://routescan.io/address/stale",
                "_short_description": None,
                "_notes": None,
            },
            staking_spec: {
                "Name": "Staked FRAX",
                "Protocol": "ERC-4626",
                "features": set(),
                "_detection_data": create_detection(staking_spec),
                "Mgmt fee": None,
                "Perf fee": None,
                "_fees": FeeData(VaultFeeMode.externalised, None, None, 0.0, 0.0),
                "Link": "https://routescan.io/address/stale",
                "_short_description": None,
                "_notes": None,
            },
            unrelated_spec: {
                "Name": "Unrelated vault",
                "Protocol": "ERC-4626",
                "features": set(),
                "_detection_data": create_detection(unrelated_spec),
                "_fees": FeeData(VaultFeeMode.externalised, None, None, 0.0, 0.0),
                "_notes": "Preserve this note",
            },
        }
    )
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    vault_db.write(vault_db_path)
    first_backup_path = vault_db_path.with_suffix(".pickle.bak-frax-repair")
    first_backup_path.write_bytes(b"existing backup")

    result = repair.repair_frax_features(vault_db_path, dry_run=False)

    assert result.matched_rows == EXPECTED_REPAIRED_FAMILY_ROWS
    assert result.repaired_rows == EXPECTED_REPAIRED_FAMILY_ROWS
    repaired_db = VaultDatabase.read(vault_db_path)
    repaired_row = repaired_db.rows[fraxlend_spec]
    assert repaired_row["Protocol"] == "Frax"
    assert repaired_row["features"] == {ERC4626Feature.frax_like}
    assert repaired_row["_detection_data"].features == {ERC4626Feature.frax_like}
    assert repaired_row["Features"] == "frax_like"
    assert repaired_row["Mgmt fee"] == 0.0
    assert repaired_row["Perf fee"] == FRAXLEND_PROTOCOL_FEE
    assert repaired_row["_fees"] == FeeData(VaultFeeMode.internalised_skimming, 0.0, FRAXLEND_PROTOCOL_FEE, 0.0, 0.0)
    assert repaired_row["Link"] == f"https://app.frax.finance/fraxlend/pair/{fraxlend_spec.vault_address}"
    assert repaired_row["_lockup"] == datetime.timedelta(0)
    assert repaired_row["_short_description"] == "Earn interest by lending assets to an isolated Fraxlend borrowing market."
    assert "lenders can absorb bad debt" in repaired_row["_notes"]

    staking_row = repaired_db.rows[staking_spec]
    assert staking_row["Protocol"] == "Frax"
    assert staking_row["features"] == {ERC4626Feature.frax_staking_like}
    assert staking_row["_detection_data"].features == {ERC4626Feature.frax_staking_like}
    assert staking_row["Features"] == "frax_staking_like"
    assert staking_row["Mgmt fee"] == 0.0
    assert staking_row["Perf fee"] == 0.0
    assert staking_row["_fees"] == FeeData(VaultFeeMode.feeless, 0.0, 0.0, 0.0, 0.0)
    assert staking_row["Link"] == "https://frax.com/earn"
    assert staking_row["_lockup"] == datetime.timedelta(0)
    assert staking_row["_short_description"] == "Stake FRAX to receive weekly Frax protocol yield through sFRAX."
    assert "IORB benchmark rate" in staking_row["_notes"]

    unrelated_row = repaired_db.rows[unrelated_spec]
    assert unrelated_row["Protocol"] == "ERC-4626"
    assert unrelated_row["features"] == set()
    assert unrelated_row["_detection_data"].features == set()
    assert unrelated_row["_fees"] == FeeData(VaultFeeMode.externalised, None, None, 0.0, 0.0)
    assert unrelated_row["_notes"] == "Preserve this note"

    assert first_backup_path.read_bytes() == b"existing backup"
    numbered_backup_path = Path(f"{first_backup_path}.1")
    assert numbered_backup_path.exists()
    backup_db = VaultDatabase.read(numbered_backup_path)
    assert backup_db.rows[fraxlend_spec]["Protocol"] == "ERC-4626"

    second_result = repair.repair_frax_features(vault_db_path, dry_run=False)
    assert second_result.matched_rows == EXPECTED_REPAIRED_FAMILY_ROWS
    assert second_result.repaired_rows == 0
    assert not Path(f"{first_backup_path}.2").exists()
