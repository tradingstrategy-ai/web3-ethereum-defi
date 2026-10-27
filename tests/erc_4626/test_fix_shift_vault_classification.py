"""Exercise the fixed-scope Shift repair against file-backed metadata."""

import importlib.util
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from web3 import Web3
from web3.exceptions import ContractLogicError

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


def test_shift_repair_dry_run_is_non_mutating(repair: ModuleType, database_path: Path, connections: dict[int, Web3]) -> None:
    """Inspect all three rows without changing the pickle or creating backups."""

    before = database_path.read_bytes()
    updates = repair.repair_shift_metadata(database_path, web3_by_chain=connections, vault_factory=create_vault)
    assert len(updates) == len(SHIFT_VAULT_PRODUCTS)
    assert all(update.new_values["_deposit_permission"] == "permissionless" for update in updates)
    assert database_path.read_bytes() == before
    assert list(database_path.parent.glob("*.bak-shift-*")) == []


def test_shift_repair_preserves_data_and_is_idempotent(repair: ModuleType, database_path: Path, connections: dict[int, Web3]) -> None:
    """Back up original bytes and change only reviewed classification fields."""

    before = database_path.read_bytes()
    original = VaultDatabase.read(database_path)
    updates = repair.repair_shift_metadata(database_path, dry_run=False, web3_by_chain=connections, vault_factory=create_vault)
    assert len(updates) == len(SHIFT_VAULT_PRODUCTS)
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


@pytest.mark.parametrize("invalid", ["missing_row", "wrong_protocol", "wrong_detection", "wrong_feature", "wrong_chain"])
def test_shift_repair_validates_before_writing(repair: ModuleType, database_path: Path, connections: dict[int, Web3], invalid: str) -> None:
    """Reject an invalid scope without invoking any adapter or writing data."""

    db = VaultDatabase.read(database_path)
    spec = next(iter(db.rows))
    row = db.rows[spec]
    if invalid == "missing_row":
        del db.rows[spec]
    elif invalid == "wrong_protocol":
        row["Protocol"] = "Other"
    elif invalid == "wrong_detection":
        row["_detection_data"] = None
    elif invalid == "wrong_feature":
        row["_detection_data"] = replace(row["_detection_data"], features={ERC4626Feature.erc_7540_like})
    else:
        connections[spec.chain_id] = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    db.write(database_path)
    before = database_path.read_bytes()
    factory = Mock(side_effect=AssertionError("Adapter must not run before validation"))
    with pytest.raises(ValueError):
        repair.repair_shift_metadata(database_path, dry_run=False, web3_by_chain=connections, vault_factory=factory)
    factory.assert_not_called()
    assert database_path.read_bytes() == before
    assert list(database_path.parent.glob("*.bak-shift-*")) == []


def test_shift_repair_aborts_on_late_rpc_failure(repair: ModuleType, database_path: Path, connections: dict[int, Web3]) -> None:
    """An unavailable final policy read must not persist earlier repairs."""

    before = database_path.read_bytes()

    def factory(web3: Web3, spec: VaultSpec) -> ShiftVault:
        """Fail the final chain's policy read after Base updates were collected."""

        vault = create_vault(web3, spec)
        if spec.chain_id == max(connections):
            vault.shift_contract.functions.whitelistEnabled().call.side_effect = ContractLogicError("unavailable")
        return vault

    with pytest.raises(ContractLogicError):
        repair.repair_shift_metadata(database_path, dry_run=False, web3_by_chain=connections, vault_factory=factory)
    assert database_path.read_bytes() == before
    assert list(database_path.parent.glob("*.bak-shift-*")) == []


@pytest.mark.parametrize("value, expected", [(None, True), ("true", True), ("false", False), ("invalid", None)])
def test_shift_repair_write_setting_is_explicit(repair: ModuleType, monkeypatch: pytest.MonkeyPatch, value: str | None, *, expected: bool | None) -> None:
    """Default to dry run and refuse ambiguous operator settings."""

    if value is None:
        monkeypatch.delenv("DRY_RUN", raising=False)
    else:
        monkeypatch.setenv("DRY_RUN", value)
    if expected is None:
        with pytest.raises(ValueError, match="DRY_RUN"):
            repair.parse_dry_run()
    else:
        assert repair.parse_dry_run() is expected
