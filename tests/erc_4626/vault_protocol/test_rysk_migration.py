"""Tests for the fixed-scope Rysk production migration."""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.rysk.migration import RYSK_MIGRATION_CHAIN_IDS, RYSK_MIGRATION_POOLS, RyskMigrationPool, iter_rysk_migration_pools, parse_rysk_migration_dry_run
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.vaultdb import VaultDatabase

RYSK_REVIEWED_POOL_COUNT = 8
TEST_DEPLOYMENT_BLOCK = 123
TEST_HISTORY_MAX_WORKERS = 7
TEST_ENV_MAX_WORKERS = 6


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

    module = load_script("migrate-rysk-vaults")
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

    module = load_script("migrate-rysk-vaults")
    pool = RyskMigrationPool(1, HexAddress("0x0000000000000000000000000000000000000001"), TEST_DEPLOYMENT_BLOCK)
    fake_web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, block_number=456, get_block=lambda block: {"timestamp": 1_700_000_000} if block == TEST_DEPLOYMENT_BLOCK else None))
    monkeypatch.setattr(module, "RYSK_MIGRATION_CHAIN_IDS", (1,))
    monkeypatch.setattr(module, "iter_rysk_migration_pools", lambda chain_id: iter((pool,)) if chain_id == 1 else iter(()))
    monkeypatch.setattr(module, "read_json_rpc_url", lambda chain_id: "https://example.invalid" if chain_id == 1 else None)
    monkeypatch.setattr(module, "create_multi_provider_web3", lambda _url: fake_web3)
    monkeypatch.setattr(module, "detect_vault_features", lambda _web3, _address, **_kwargs: set(module.RYSK_MIGRATION_FEATURES))
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

    strategy_tags = {StrategyTag.delta_neutral}
    vault_db.rows[VaultSpec(1, pool.address)]["_strategy_tags"] = strategy_tags
    update_result = module.migrate_rysk_metadata(vault_db, Mock(), dry_run=False)
    assert update_result.updated == 1
    assert vault_db.rows[VaultSpec(1, pool.address)]["_strategy_tags"] == strategy_tags
    capsys.readouterr()


def test_rysk_metadata_rejects_address_that_fails_shared_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject a fixed address unless the maintained onchain probe identifies Rysk.

    :param monkeypatch:
        Pytest fixture replacing the expensive onchain classifier.
    :return:
        None.
    """

    module = load_script("migrate-rysk-vaults")
    pool = RyskMigrationPool(1, HexAddress("0x0000000000000000000000000000000000000001"), TEST_DEPLOYMENT_BLOCK)
    fake_web3 = SimpleNamespace(eth=SimpleNamespace(get_block=lambda _block: {"timestamp": 1_700_000_000}))
    monkeypatch.setattr(module, "detect_vault_features", lambda _web3, _address, **_kwargs: {module.ERC4626Feature.broken})

    with pytest.raises(RuntimeError, match="failed the shared onchain classifier"):
        module._build_metadata_replacement(fake_web3, pool, VaultDatabase(), Mock(), 456)


def test_rysk_metadata_rejects_missing_share_token_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Abort cleanly when a classified Rysk pool has no usable token name.

    :param monkeypatch:
        Pytest fixture replacing onchain classification and row construction.
    :return:
        None.
    """

    module = load_script("migrate-rysk-vaults")
    pool = RyskMigrationPool(1, HexAddress("0x0000000000000000000000000000000000000001"), TEST_DEPLOYMENT_BLOCK)
    fake_web3 = SimpleNamespace(eth=SimpleNamespace(get_block=lambda _block: {"timestamp": 1_700_000_000}))
    monkeypatch.setattr(module, "detect_vault_features", lambda _web3, _address, **_kwargs: set(module.RYSK_MIGRATION_FEATURES))
    monkeypatch.setattr(module, "create_vault_scan_record", lambda *_args, **_kwargs: {"Name": None, "Protocol": "Rysk"})

    with pytest.raises(RuntimeError, match="rebuilt as 'Rysk' / None"):
        module._build_metadata_replacement(fake_web3, pool, VaultDatabase(), Mock(), 456)


def test_rysk_history_threads_fixed_scope_and_workers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Pass the fixed chain order, caches and worker count to each history stage.

    :param monkeypatch:
        Pytest fixture replacing per-chain network work.
    :param tmp_path:
        Pytest temporary output directory.
    :return:
        None.
    """

    module = load_script("migrate-rysk-vaults")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(module, "_backfill_rysk_history_chain", lambda **kwargs: calls.append(kwargs))

    module.backfill_rysk_history(
        price_database=tmp_path / "prices.parquet",
        context_database=tmp_path / "context.duckdb",
        token_cache_path=tmp_path / "tokens.sqlite",
        timestamp_cache_path=tmp_path / "timestamps",
        max_workers=TEST_HISTORY_MAX_WORKERS,
    )

    assert [call["chain_id"] for call in calls] == [1, 999]
    assert all(call["max_workers"] == TEST_HISTORY_MAX_WORKERS for call in calls)
    assert all(call["timestamp_cache_path"] == tmp_path / "timestamps" for call in calls)


def test_rysk_history_passes_isolated_timestamp_cache_to_writer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Thread the selected timestamp cache into the common Parquet writer.

    :param monkeypatch:
        Pytest fixture replacing network and writer dependencies.
    :param tmp_path:
        Pytest temporary output directory.
    :param capsys:
        Pytest fixture capturing the per-chain migration summary.
    :return:
        None.
    """

    module = load_script("migrate-rysk-vaults")
    pool = RyskMigrationPool(1, HexAddress("0x0000000000000000000000000000000000000001"), TEST_DEPLOYMENT_BLOCK)
    fake_web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))
    token_cache = Mock()
    captured: dict[str, object] = {}
    timestamp_cache_path = tmp_path / "timestamps"

    monkeypatch.setattr(module, "read_json_rpc_url", lambda _chain_id: "https://example.invalid")
    monkeypatch.setattr(module, "create_multi_provider_web3", lambda _url: fake_web3)
    monkeypatch.setattr(module, "iter_rysk_migration_pools", lambda _chain_id: iter((pool,)))
    monkeypatch.setattr(module, "fetch_rysk_full_backfill_range", lambda _web3, _pools: (TEST_DEPLOYMENT_BLOCK, 456))
    monkeypatch.setattr(module, "configure_hypersync_from_env", lambda _web3: SimpleNamespace(hypersync_client=object()))
    monkeypatch.setattr(module, "fetch_and_store_rysk_premium_history", lambda **_kwargs: SimpleNamespace(observations_fetched=0, observations_inserted=0))
    monkeypatch.setattr(module, "TokenDiskCache", lambda _path: token_cache)
    monkeypatch.setattr(module, "RyskVault", lambda *_args, **_kwargs: SimpleNamespace(address=pool.address))
    monkeypatch.setattr(module, "MultiProviderWeb3Factory", lambda _url: object())

    def capture_scan(**kwargs: object) -> object:
        """Capture common-writer arguments for assertions."""

        captured.update(kwargs)
        return object()

    monkeypatch.setattr(module, "scan_historical_prices_to_parquet", capture_scan)
    monkeypatch.setattr(module, "pformat_scan_result", lambda _result: "scan result")

    module._backfill_rysk_history_chain(
        chain_id=1,
        price_database=tmp_path / "prices.parquet",
        context_database=tmp_path / "context.duckdb",
        token_cache_path=tmp_path / "tokens.sqlite",
        timestamp_cache_path=timestamp_cache_path,
        max_workers=TEST_HISTORY_MAX_WORKERS,
    )

    assert captured["timestamp_cache_file"] == timestamp_cache_path
    assert captured["max_workers"] == TEST_HISTORY_MAX_WORKERS
    token_cache.commit.assert_called_once()
    token_cache.close.assert_called_once()
    capsys.readouterr()


def test_rysk_main_dry_run_rehearses_production_parquet_without_mutation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Copy production Parquet into temporary storage before dry-run history.

    :param monkeypatch:
        Pytest fixture replacing network and historical work.
    :param tmp_path:
        Pytest temporary production data directory.
    :param capsys:
        Pytest fixture capturing the migration summary.
    :return:
        None.
    """

    module = load_script("migrate-rysk-vaults")
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    price_database = tmp_path / "vault-prices-1h.parquet"
    context_database = tmp_path / "vault-historical-context.duckdb"
    VaultDatabase().write(vault_db_path)
    price_database.write_bytes(b"production parquet sentinel")
    context_database.write_bytes(b"production context sentinel")
    token_cache = Mock()
    observed: dict[str, object] = {}

    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setenv("MAX_WORKERS", str(TEST_ENV_MAX_WORKERS))
    monkeypatch.setattr(module, "setup_console_logging", lambda **_kwargs: None)
    monkeypatch.setattr(module, "get_pipeline_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "TokenDiskCache", lambda _path: token_cache)
    monkeypatch.setattr(module, "migrate_rysk_metadata", lambda _db, _cache, *, dry_run: module.RyskMetadataMigrationResult(8, 8, 0) if dry_run else None)

    def capture_backfill(**kwargs: object) -> None:
        """Validate temporary paths while the temporary directory exists."""

        dry_run_prices = kwargs["price_database"]
        assert isinstance(dry_run_prices, Path)
        assert dry_run_prices != price_database
        assert dry_run_prices.read_bytes() == b"production parquet sentinel"
        observed.update(kwargs)

    monkeypatch.setattr(module, "backfill_rysk_history", capture_backfill)

    module.main()

    assert observed["max_workers"] == TEST_ENV_MAX_WORKERS
    assert price_database.read_bytes() == b"production parquet sentinel"
    assert context_database.read_bytes() == b"production context sentinel"
    token_cache.commit.assert_not_called()
    token_cache.close.assert_called_once()
    assert "Dry run complete" in capsys.readouterr().out
