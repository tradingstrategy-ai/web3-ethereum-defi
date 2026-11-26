"""Exercise the fixed-scope Shift repair against file-backed metadata."""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from web3 import Web3

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.shift.constants import SHIFT_VAULT_PRODUCTS
from eth_defi.erc_4626.vault_protocol.shift.vault import ShiftVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.fixture(scope="module")
def repair() -> ModuleType:
    """Load the operator script without running its CLI.

    :return:
        Repair functions and their fixed reviewed scope.
    """

    path = Path(__file__).resolve().parents[2] / "scripts/erc-4626/fix-shift-vault-classification.py"
    spec = importlib.util.spec_from_file_location("fix_shift_classification", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    """Persist the three old Shift rows and an unrelated catalogue row.

    :param tmp_path:
        Isolated fixture directory.
    :return:
        Metadata pickle path containing representative legacy fields.
    """

    db = VaultDatabase()
    for product in SHIFT_VAULT_PRODUCTS.values():
        spec = VaultSpec(product.chain_id, product.vault_address)
        detection = ERC4262VaultDetection(
            chain=product.chain_id,
            address=product.vault_address,
            first_seen_at_block=product.first_seen_at_block,
            first_seen_at=product.first_seen_at,
            features={ERC4626Feature.shift_like},
            updated_at=product.first_seen_at,
            deposit_count=1,
            redeem_count=1,
        )
        db.rows[spec] = {
            "Name": product.product_name,
            "Protocol": "Shift",
            "_detection_data": detection,
            "_flags": {VaultFlag.tokenised_fund.value, VaultFlag.long_duration},
            "_deposit_permission": "whitelisted",
            "_deposits_open": False,
            "_deposit_closed_reason": "Shift deposit requests require executor approval; no public deposit manager is implemented",
            "_redemption_closed_reason": "Shift withdrawals require executor batch resolution and a timelock; no public redemption manager is implemented",
            "_notes": "Retain this manual note",
            "NAV": 123,
        }
    db.rows[VaultSpec(1, "0x1111111111111111111111111111111111111111")] = {"Name": "Unrelated", "_flags": {VaultFlag.tokenised_fund}}
    db.last_scanned_block = {8453: 123, 42161: 456}
    path = tmp_path / "vault-metadata-db.pickle"
    db.write(path)
    return path


@pytest.fixture
def connections() -> dict[int, Web3]:
    """Create correctly identified offline chain connections.

    :return:
        Base and Arbitrum clients without RPC access.
    """

    return {chain_id: SimpleNamespace(eth=SimpleNamespace(chain_id=chain_id)) for chain_id in (8453, 42161)}


def create_vault(web3: Web3, spec: VaultSpec) -> ShiftVault:
    """Inject contract responses into the actual production Shift adapter.

    :param web3:
        Offline chain connection.
    :param spec:
        Reviewed vault specification.
    :return:
        Unpaused adapter with its investor allow-list disabled.
    """

    vault = ShiftVault(web3, spec)
    vault.__dict__["shift_contract"] = contract = Mock()
    contract.functions.whitelistEnabled.return_value.call.return_value = False
    contract.functions.paused.return_value.call.return_value = False
    return vault


def test_shift_repair_preview_apply_and_repeat(repair: ModuleType, database_path: Path, connections: dict[int, Web3]) -> None:
    """Preview the changes, apply with a backup and repeat without rewriting."""
    before = database_path.read_bytes()
    original = VaultDatabase.read(database_path)
    preview = repair.repair_shift_metadata(database_path, web3_by_chain=connections, vault_factory=create_vault)
    assert {update.spec for update in preview} == {spec for spec in original.rows if spec.chain_id in connections}
    assert all(update.new_values["_deposit_permission"] == "permissionless" for update in preview)
    assert database_path.read_bytes() == before
    assert not list(database_path.parent.glob("*.bak-shift-*"))

    updates = repair.repair_shift_metadata(database_path, dry_run=False, web3_by_chain=connections, vault_factory=create_vault)
    assert updates == preview
    backups = list(database_path.parent.glob("*.bak-shift-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == before
    repaired = VaultDatabase.read(database_path)
    for spec, row in repaired.rows.items():
        if spec.chain_id == 1:
            assert row == original.rows[spec]
        else:
            assert row["_flags"] == {VaultFlag.long_duration}
            assert row["_deposit_permission"] == "permissionless"
            assert row["_deposit_closed_reason"] is None
            assert row["_redemption_closed_reason"] is None
            assert row["_deposits_open"] is None
            assert row["_deposit_manager"] == {"can_deposit": False, "can_redeem": False}
            assert row["_whitelist_notes"] == ShiftVault.whitelist_notes
            assert row["NAV"] == original.rows[spec]["NAV"]
            assert row["_notes"] == original.rows[spec]["_notes"]
            assert row["_detection_data"] == original.rows[spec]["_detection_data"]
    assert repaired.last_scanned_block == original.last_scanned_block
    unchanged_bytes = database_path.read_bytes()
    assert repair.repair_shift_metadata(database_path, dry_run=False, web3_by_chain=connections, vault_factory=create_vault) == ()
    assert database_path.read_bytes() == unchanged_bytes
    assert list(database_path.parent.glob("*.bak-shift-*")) == backups
