"""Tests for the fixed-scope Rysk production migration."""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.rysk.migration import RYSK_MIGRATION_CHAIN_IDS, RYSK_MIGRATION_POOLS, RyskMigrationPool, iter_rysk_migration_pools, parse_rysk_migration_dry_run
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

RYSK_REVIEWED_POOL_COUNT = 8
TEST_DEPLOYMENT_BLOCK = 123


def load_script(name: str) -> ModuleType:
    """Load one hyphenated Rysk migration script without running its entry point.

    :param name:
        Script filename without the ``.py`` suffix.
    :return:
        Imported script module.
    """

    repository_root = Path(__file__).resolve().parents[3]
    script_path = repository_root / "scripts" / "erc-4626" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rysk_migration_scope_is_fixed_and_excludes_internal_pools() -> None:
    """Keep one deterministic eight-pool scope shared by both migration stages.

    :return:
        None.
    """

    assert RYSK_MIGRATION_CHAIN_IDS == (1, 999)
    assert len(RYSK_MIGRATION_POOLS) == RYSK_REVIEWED_POOL_COUNT
    assert len({(pool.chain_id, pool.address) for pool in RYSK_MIGRATION_POOLS}) == RYSK_REVIEWED_POOL_COUNT
    assert all(pool.deployment_block > 0 for pool in RYSK_MIGRATION_POOLS)
    assert all("377476409d8eb5eac7197cdb906773ce4f4edcf4" not in pool.address for pool in RYSK_MIGRATION_POOLS)
    assert tuple(iter_rysk_migration_pools(1)) == RYSK_MIGRATION_POOLS[:4]
    assert tuple(iter_rysk_migration_pools(999)) == RYSK_MIGRATION_POOLS[4:]


def test_rysk_migration_has_one_strict_operator_choice() -> None:
    """Default to dry-run mode and reject ambiguous values.

    :return:
        None.
    """

    assert parse_rysk_migration_dry_run(None) is True
    assert parse_rysk_migration_dry_run("true") is True
    assert parse_rysk_migration_dry_run("false") is False
    with pytest.raises(ValueError, match="DRY_RUN must be true or false"):
        parse_rysk_migration_dry_run("perhaps")


def test_rysk_backfill_range_uses_reviewed_deployments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start one chain at its earliest reviewed deployment and use a safe head.

    :param monkeypatch:
        Pytest fixture replacing the provider-specific safe-head read.
    :return:
        None.
    """

    module = load_script("backfill-rysk-vault-prices")
    pools = (
        RyskMigrationPool(1, HexAddress("0x0000000000000000000000000000000000000001"), 200),
        RyskMigrationPool(1, HexAddress("0x0000000000000000000000000000000000000002"), 100),
    )
    monkeypatch.setattr(module, "get_almost_latest_block_number", lambda _web3: 1_000)

    assert module.fetch_rysk_full_backfill_range(object(), pools) == (100, 1_000)


def test_rysk_metadata_dry_run_builds_without_mutating(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Exercise every fixed metadata read before applying replacements.

    :param monkeypatch:
        Pytest fixture replacing RPC and common-row construction.
    :param capsys:
        Pytest fixture capturing the migration's operator table.
    :return:
        None.
    """

    module = load_script("migrate-rysk-vault-metadata")
    pool = RyskMigrationPool(1, HexAddress("0x0000000000000000000000000000000000000001"), TEST_DEPLOYMENT_BLOCK)
    fake_web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, block_number=456, get_block=lambda block: {"timestamp": 1_700_000_000} if block == TEST_DEPLOYMENT_BLOCK else None))
    monkeypatch.setattr(module, "RYSK_MIGRATION_CHAIN_IDS", (1,))
    monkeypatch.setattr(module, "iter_rysk_migration_pools", lambda chain_id: iter((pool,)) if chain_id == 1 else iter(()))
    monkeypatch.setattr(module, "read_json_rpc_url", lambda chain_id: "https://example.invalid" if chain_id == 1 else None)
    monkeypatch.setattr(module, "create_multi_provider_web3", lambda _url: fake_web3)
    monkeypatch.setattr(
        module,
        "create_vault_scan_record",
        lambda _web3, detection, _block, _cache: {
            "Name": "Reviewed Rysk pool",
            "Protocol": "Rysk",
            "features": set(detection.features),
            "_detection_data": detection,
        },
    )
    vault_db = VaultDatabase()

    dry_result = module.migrate_rysk_metadata(vault_db, Mock(), dry_run=True)
    assert dry_result.pools == 1
    assert dry_result.inserted == 1
    assert vault_db.rows == {}
    assert "Reviewed Rysk pool" in capsys.readouterr().out

    write_result = module.migrate_rysk_metadata(vault_db, Mock(), dry_run=False)
    assert write_result.pools == 1
    assert VaultSpec(1, pool.address) in vault_db.rows
    assert VaultSpec(1, pool.address) in vault_db.leads
    detection = vault_db.rows[VaultSpec(1, pool.address)]["_detection_data"]
    assert detection.first_seen_at_block == TEST_DEPLOYMENT_BLOCK
    assert detection.features == set(module.RYSK_MIGRATION_FEATURES)
    assert "Reviewed Rysk pool" in capsys.readouterr().out
