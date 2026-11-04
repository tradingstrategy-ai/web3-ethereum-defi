"""Verify the numbered Avantis migration on isolated persistent state."""

import copy
import dataclasses
import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.avantis.constants import AVANTIS_VAULT_ADDRESS
from eth_defi.erc_4626.vault_protocol.avantis.vault import AvantisVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault_protocols import migration as runner
from eth_defi.vault_protocols.repairs import avantis

TARGET = VaultSpec(8453, AVANTIS_VAULT_ADDRESS)
OTHER = VaultSpec(1, AVANTIS_VAULT_ADDRESS)
SCRIPT_NAME = "030-avantis-vault-metadata.py"


@pytest.fixture
def database() -> VaultDatabase:
    """Construct an existing generic detection with unrelated enrichment.

    :return: In-memory database with target and same-address wrong-chain control.
    """
    timestamp = datetime.datetime(2026, 1, 1)
    flags = {ERC4626Feature.gains_tranche_like}
    detection = ERC4262VaultDetection(8453, AVANTIS_VAULT_ADDRESS, 1, timestamp, flags, timestamp, 10, 20)
    return VaultDatabase(
        rows={
            TARGET: {"Protocol": "<protocol not yet identified>", "Address": AVANTIS_VAULT_ADDRESS, "_detection_data": detection, "_notes": "Preserve operator warning", "NAV": 999, "_flags": {"manual sentinel"}, "_description": "Maintained description"},
            OTHER: {"Protocol": "Unrelated", "manual": 7},
        },
        last_scanned_block={8453: 123},
    )


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Supply source-reviewed adapter replies without replacing its logic.

    :param monkeypatch: Scoped constructor and provider overrides.
    :return: Offline Base provider used by the real repair.
    """
    web3 = Mock()
    web3.eth.chain_id = 8453
    web3.eth.block_number = 123
    web3.eth.get_storage_at.return_value = bytes.fromhex("00" * 12 + "e79184763b27fa8bb0eb2d6af1f0123d43af0e89")
    contract = Mock()
    contract.functions.asset.return_value.call.return_value = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
    contract.functions.feesOn.return_value.call.return_value = True
    contract.functions.balancingFee.return_value.call.return_value = 0
    contract.functions.collateralHealthFee.return_value.call.return_value = 500
    web3.eth.contract.return_value = contract
    monkeypatch.setattr(avantis, "read_json_rpc_url", lambda _: "unused")
    monkeypatch.setattr(avantis, "create_multi_provider_web3", lambda _: web3)
    # Keep real adapter methods; only replace the ABI binding in this offline test.
    monkeypatch.setattr(AvantisVault, "vault_contract", property(lambda _: contract))
    return web3


def test_avantis_numbered_preview_apply_and_preservation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, database: VaultDatabase, provider: Mock) -> None:
    """Exercise runner preview, atomic apply, journal skip and history preservation.

    :param tmp_path: Isolated persistent directory.
    :param monkeypatch: Environment isolation.
    :param database: Existing metadata with control row.
    :param provider: Offline Base replies for actual adapter reads.
    :return: None; byte and field comparisons prove the bounded repair.
    """
    for name in ("VAULT_DB", "VAULT_DB_PATH"):
        monkeypatch.delenv(name, raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "vault-metadata-db.pickle"
    database.write(path)
    sentinel_names = ("vault-prices-1h.parquet", "reader-state.pickle", "timestamps.duckdb")
    for name in sentinel_names:
        (data_dir / name).write_bytes(b"Preserve all historical state")
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = next(item for item in runner.discover_migrations() if item.name == SCRIPT_NAME)
    (scripts_dir / script.name).write_bytes(script.source)
    original = {p.name: p.read_bytes() for p in data_dir.iterdir()}
    options = {"data_dir": data_dir, "scripts_dir": scripts_dir, "provenance": {"in_docker": False}}
    assert runner.run_pending_migrations(dry_run=True, **options) == [SCRIPT_NAME]
    assert {p.name: p.read_bytes() for p in data_dir.iterdir()} == original
    assert runner.run_pending_migrations(dry_run=False, **options) == [SCRIPT_NAME]
    result = VaultDatabase.read(path)
    row = result.rows[TARGET]
    assert row["Protocol"] == "Avantis"
    assert row["_deposit_permission"] == "permissionless"
    assert row["_deposit_manager"]["can_redeem"] is True
    assert row["Withdraw fee"] == pytest.approx(0.005)
    assert row["_strategy_tags"] == {StrategyTag.liquidity_provider, StrategyTag.market_making, StrategyTag.perpetual_futures}
    for key in ("_notes", "NAV", "_flags", "_description"):
        assert row[key] == database.rows[TARGET][key]
    assert result.rows[OTHER] == database.rows[OTHER]
    assert result.last_scanned_block == database.last_scanned_block
    assert row["_detection_data"].first_seen_at == database.rows[TARGET]["_detection_data"].first_seen_at
    assert row["_detection_data"].first_seen_at_block == 1
    for name in sentinel_names:
        assert (data_dir / name).read_bytes() == original[name]
    assert runner.run_pending_migrations(dry_run=False, **options) == []
    assert avantis.refresh_avantis_metadata(result, provider)["changed_rows"] == 0


def test_avantis_unknown_admission_leaves_database_unchanged(database: VaultDatabase, provider: Mock) -> None:
    """A read failure must abort before staging any row changes.

    :param database: Stale target and control row.
    :param provider: Replace the reviewed implementation with an unknown one.
    :return: None; the original database remains equal to its deep copy.
    """
    original = copy.deepcopy(database.rows)
    provider.eth.get_storage_at.return_value = bytes(32)
    with pytest.raises(ValueError, match="admission could not be established"):
        avantis.refresh_avantis_metadata(database, provider)
    assert database.rows == original


@pytest.mark.parametrize("conflict", ["missing", "protocol", "detection"])
def test_avantis_conflicting_target(database: VaultDatabase, conflict: str) -> None:
    """Refuse absent or conflicting targets before contacting Base.

    :param database: Isolated existing catalogue.
    :param conflict: Required-target invariant to invalidate.
    :return: None; every invalid state raises a hard error.
    """
    if conflict == "missing":
        del database.rows[TARGET]
    elif conflict == "protocol":
        database.rows[TARGET]["Protocol"] = "Other protocol"
    else:
        database.rows[TARGET]["_detection_data"] = dataclasses.replace(database.rows[TARGET]["_detection_data"], features={ERC4626Feature.gains_like})
    with pytest.raises(ValueError):
        avantis.refresh_avantis_metadata(database)
