"""Verify the one-vault fee repair preserves unrelated persisted state."""

import copy
import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.veranta.constants import VERANTA_VAULT_ADDRESS, VERANTA_VAULT_MANAGER_ADDRESS
from eth_defi.erc_4626.vault_protocol.veranta.vault import VerantaVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.fee import FeeData, VaultFeeMode
from eth_defi.vault.handwritten_metadata import VERANTA_VAULT_METADATA
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner
from eth_defi.vault_protocols.repairs import veranta

TARGET = VaultSpec(8453, VERANTA_VAULT_ADDRESS)
OTHER = VaultSpec(1, VERANTA_VAULT_ADDRESS)
SCRIPT_NAME = "055-veranta-fees.py"


@pytest.fixture
def database() -> VaultDatabase:
    """Provide existing Veranta metadata with stale fees and manual enrichment.

    A same-address wrong-chain control guards the repair's deployment scope.
    Metadata state records are preserved independently of description changes.

    :return: Isolated two-row catalogue with a typed reviewed detection.
    """
    timestamp = datetime.datetime(2026, 1, 1)
    features = {ERC4626Feature.veranta_like, ERC4626Feature.gains_tranche_like}
    detection = ERC4262VaultDetection(8453, VERANTA_VAULT_ADDRESS, 1, timestamp, features, timestamp, 10, 20)
    return VaultDatabase(
        rows={
            TARGET: {"Protocol": "Veranta", "Address": VERANTA_VAULT_ADDRESS, "_detection_data": detection, "_fees": FeeData(VaultFeeMode.externalised, None, None, 0.0, 0.015), "Mgmt fee": None, "Perf fee": None, "_notes": "Manual warning", "_description": "Maintained copy", "_short_description": "Maintained summary.", "_flags": {"manual"}, "_risk": "Manual risk", "_deposit_manager": {"can_redeem": True}},
            OTHER: {"Protocol": "Unrelated", "_fees": None, "_notes": "Control row"},
        },
        last_scanned_block={8453: 123},
    )


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Supply reviewed implementation wiring and fee replies without RPC.

    Keep the real adapter's source gating, fee scaling and fee-mode logic;
    replace only its provider connection and ABI binding.

    :param monkeypatch: Scoped provider and contract-binding substitutions.
    :return: Base provider with mutable mocked storage and function replies.
    """
    web3 = Mock()
    web3.eth.chain_id = 8453
    web3.eth.block_number = 123
    web3.eth.get_storage_at.side_effect = lambda address, *args, **kwargs: bytes.fromhex("00" * 12 + ("f0207407beb82902186321e3dd84a7050add275a" if address.lower() == VERANTA_VAULT_MANAGER_ADDRESS else "e79184763b27fa8bb0eb2d6af1f0123d43af0e89"))
    contract = Mock()
    contract.functions.vaultManager.return_value.call.return_value = VERANTA_VAULT_MANAGER_ADDRESS
    contract.functions.feesOn.return_value.call.return_value = True
    contract.functions.balancingFee.return_value.call.return_value = 0
    contract.functions.collateralHealthFee.return_value.call.return_value = 1500
    web3.eth.contract.return_value = contract
    monkeypatch.setattr(veranta, "read_json_rpc_url", lambda _: "unused")
    monkeypatch.setattr(veranta, "create_multi_provider_web3", lambda _: web3)
    monkeypatch.setattr(VerantaVault, "vault_contract", property(lambda _: contract))
    return web3


def test_veranta_fee_migration_preview_apply_preserves_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, database: VaultDatabase, provider: Mock) -> None:
    """Refresh fee metadata through the real numbered preview/apply runner.

    Only six fee fields and the long description may change. Preview preserves every byte;
    apply must leave manual enrichment, discovery and historical files intact.

    :param tmp_path: Temporary persistent metadata and history directory.
    :param monkeypatch: Remove inherited database configuration overrides.
    :param database: Existing target and control catalogue rows.
    :param provider: Deterministic reviewed Base state.
    :return: None after checking preview, apply, idempotency and preservation.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "vault-metadata-db.pickle"
    database.write(path)
    for name in ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb"):
        (data_dir / name).write_bytes(b"Preserve all historical state")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(item for item in runner.discover_migrations() if item.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    before_files = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    before_rows = copy.deepcopy(database.rows)
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    assert runner.run_pending_migrations(dry_run=True, **options) == [SCRIPT_NAME]
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == before_files
    assert runner.run_pending_migrations(dry_run=False, **options) == [SCRIPT_NAME]
    result = VaultDatabase.read(path)
    row = result.rows[TARGET]
    updated_keys = {"_fees", "Mgmt fee", "Perf fee", "Deposit fee", "Withdraw fee", "Protocol fee", "_description"}
    assert {key: value for key, value in row.items() if key not in updated_keys} == {key: value for key, value in before_rows[TARGET].items() if key not in updated_keys}
    assert row["_description"] == VERANTA_VAULT_METADATA[8453, VERANTA_VAULT_ADDRESS].description
    assert "**0.5%**" in row["_description"] and "**dynamic**" in row["_description"]
    assert "https://docs.veranta.xyz/liquidity-providers/risk-management" in row["_description"]
    assert row["Mgmt fee"] == row["Perf fee"] == row["Deposit fee"] == 0.0
    assert row["Withdraw fee"] == pytest.approx(0.005)
    assert row["Protocol fee"] is None
    assert row["_fees"].can_calculate_investor_net_performance()
    assert row["_fees"].fee_mode is VaultFeeMode.externalised
    assert result.rows[OTHER] == before_rows[OTHER]
    assert result.last_scanned_block == database.last_scanned_block
    for name, content in before_files.items():
        if name != path.name:
            assert (data_dir / name).read_bytes() == content
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert veranta.refresh_veranta_fees(result, provider)["updated_fields"] == 0


@pytest.mark.parametrize("failure", ["upgrade", "read", "chain", "protocol", "identity"])
def test_veranta_fee_refresh_failure_leaves_rows_unchanged(database: VaultDatabase, provider: Mock, failure: str) -> None:
    """Abort before staging any fields when source verification cannot complete.

    Unknown upgrades and provider failures must not become zero-fee snapshots.
    Existing classification and chain identity are checked independently.

    :param database: Existing target and control catalogue rows.
    :param provider: Deterministic provider to fault.
    :param failure: Verification prerequisite to invalidate.
    :return: None after confirming the complete catalogue is unchanged.
    """
    if failure == "upgrade":
        provider.eth.get_storage_at.side_effect = None
        provider.eth.get_storage_at.return_value = bytes(32)
    elif failure == "read":
        provider.eth.get_storage_at.side_effect = TimeoutError("unavailable")
    elif failure == "chain":
        provider.eth.chain_id = 1
    elif failure == "protocol":
        database.rows[TARGET]["Protocol"] = "Other protocol"
    else:
        database.rows[TARGET]["_detection_data"] = None
    original = copy.deepcopy(database.rows)
    with pytest.raises(TimeoutError if failure == "read" else ValueError):
        veranta.refresh_veranta_fees(database, provider)
    assert database.rows == original


def test_veranta_fee_refresh_missing_target_does_not_create(database: VaultDatabase) -> None:
    """Report a missing target without needing RPC or inventing a new row.

    The repair does not discover deployments and must not affect the same
    address on another chain.

    :param database: Catalogue from which the reviewed target is removed.
    :return: None after confirming the control row and missing-target report.
    """
    del database.rows[TARGET]
    original = copy.deepcopy(database.rows)
    report = veranta.refresh_veranta_fees(database)
    assert report == {"inspected_rows": 0, "missing_specs": [TARGET.as_string_id()], "updated_fields": 0}
    assert database.rows == original
