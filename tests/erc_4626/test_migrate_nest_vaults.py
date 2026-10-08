"""Safety checks for Nest metadata migration and historical backfill."""

import datetime
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault import scan_all_chains
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

ARC_VAULT_ADDRESS = "0xd258029cf5a177e3306e09fbea63424543a505c0"
WORLDCHAIN_CHAIN_ID = 480
FIRST_SEEN = datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC).replace(tzinfo=None)
UPDATED_AT = datetime.datetime(2026, 10, 6, tzinfo=datetime.UTC).replace(tzinfo=None)
DEPOSIT_COUNT = 19
REDEEM_COUNT = 4
HISTORY_WORKERS = 3
EXPECTED_CLEANED_ROWS = 2
TIMESTAMP_CHUNK_SIZE = 500_000


@pytest.fixture
def migration_module() -> ModuleType:
    """Load the hyphenated migration script for focused unit tests.

    :return:
        Imported Nest migration module.
    """
    path = Path(__file__).resolve().parents[2] / "scripts" / "nest" / "migrate-vaults.py"
    spec = importlib.util.spec_from_file_location("migrate_nest_vaults", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def arc_route() -> dict:
    """Give tests one active Arc route with a published history lower bound.

    :return:
        First-party metadata fields consumed by the migration.
    """
    return {
        "chain_id": 5042,
        "vault_address": ARC_VAULT_ADDRESS,
        "slug": "nest-opal-vault",
        "status": "active",
        "start_block": 20_362_264,
        "display_name": "BlackOpal LiquidStone II",
        "short_description": "Short-term payment financing.",
        "description": "Brazilian card receivables.",
        "yield_origin": "Payment financing.",
        "risk_summary": "Borrower default risk.",
        "underlying_risks": "Servicing risk.",
    }


def test_select_active_routes_requires_start_block(migration_module: ModuleType, arc_route: dict) -> None:
    """Do not seed a vault from an unknown starting block or disabled product."""
    disabled = arc_route | {"status": "disabled", "vault_address": "0x0000000000000000000000000000000000000001"}
    selected = migration_module.select_active_routes({"active": arc_route, "disabled": disabled}, frozenset({5042}))
    assert selected == {5042: [arc_route]}

    with pytest.raises(ValueError, match="start block"):
        migration_module.select_active_routes({"active": arc_route | {"start_block": None}}, frozenset({5042}))

    assert migration_module.parse_networks("arc,base") == frozenset({5042, 8453})
    with pytest.raises(ValueError, match="unsupported Nest chain"):
        migration_module.parse_networks("morph")
    assert WORLDCHAIN_CHAIN_ID in migration_module.MIGRATION_CHAIN_IDS
    assert migration_module.parse_networks(None) is None
    assert migration_module.select_active_routes({"active": arc_route}, None) == {5042: [arc_route]}
    with pytest.raises(ValueError, match="no active routes"):
        migration_module.select_active_routes({"active": arc_route}, frozenset({5042, WORLDCHAIN_CHAIN_ID}))


def test_prepare_migration_preserves_existing_history_and_cursor(migration_module: ModuleType, arc_route: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refresh Nest fields while retaining observed event counts and scan state."""
    spec = VaultSpec(5042, ARC_VAULT_ADDRESS)
    detection = ERC4262VaultDetection(
        chain=5042,
        address=ARC_VAULT_ADDRESS,
        first_seen_at_block=20_362_100,
        first_seen_at=FIRST_SEEN,
        features={ERC4626Feature.erc_7540_like},
        updated_at=FIRST_SEEN,
        deposit_count=DEPOSIT_COUNT,
        redeem_count=REDEEM_COUNT,
    )
    original_row = {"Name": "Generic vault", "Protocol": "<unknown ERC-7540>", "_detection_data": detection}
    vault_db = VaultDatabase(rows={spec: original_row}, last_scanned_block={5042: 22_000_000})
    features = {ERC4626Feature.nest_like, ERC4626Feature.erc_7540_like, ERC4626Feature.erc_7575_like}
    monkeypatch.setattr(migration_module, "detect_vault_features", lambda *_args, **_kwargs: features)
    monkeypatch.setattr(
        migration_module,
        "create_vault_scan_record",
        lambda _web3, refreshed, _block, _cache: {"Protocol": "Nest", "_detection_data": refreshed, "_nest_offchain_data": {"slug": arc_route["slug"]}},
    )
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=5042, block_number=22_000_010))

    rows, leads, result = migration_module.prepare_chain_migration(web3, vault_db, [arc_route], object(), UPDATED_AT)

    refreshed = rows[spec]["_detection_data"]
    assert refreshed.first_seen_at_block == detection.first_seen_at_block
    assert refreshed.first_seen_at == FIRST_SEEN
    assert refreshed.deposit_count == DEPOSIT_COUNT
    assert refreshed.redeem_count == REDEEM_COUNT
    assert refreshed.features == features
    assert leads[spec].deposit_count == DEPOSIT_COUNT
    assert leads[spec].withdrawal_count == REDEEM_COUNT
    assert result.rows == 1 and result.leads == 1
    assert vault_db.rows[spec] is original_row
    assert vault_db.leads == {}
    assert vault_db.last_scanned_block == {5042: 22_000_000}
    assert rows[spec]["_nest_offchain_data"] == arc_route
    assert rows[spec]["_description"] == arc_route["description"]
    assert rows[spec]["Name"] == arc_route["display_name"]
    assert rows[spec]["_curator_slug"] == "nest-dao"


def test_metadata_refresh_ignores_live_statistics(migration_module: ModuleType, arc_route: dict) -> None:
    """Refresh CMS changes without rebuilding a row for every live TVL update."""
    spec = VaultSpec(5042, ARC_VAULT_ADDRESS)
    features = {ERC4626Feature.nest_like}
    detection = ERC4262VaultDetection(chain=5042, address=ARC_VAULT_ADDRESS, first_seen_at_block=arc_route["start_block"], first_seen_at=FIRST_SEEN, features=features, updated_at=UPDATED_AT, deposit_count=0, redeem_count=0)
    row = migration_module.get_metadata_fields(arc_route, spec) | {"Protocol": "Nest", "features": features, "_detection_data": detection}

    live_update = arc_route | {"tvl_usd": 123, "reported_apy": 0.05}
    assert not migration_module.needs_refresh(row, live_update, features)
    assert migration_module.needs_refresh(row, live_update | {"redemption_time_days": 7}, features)


def test_prepare_migration_rejects_wrong_provider_without_writes(migration_module: ModuleType, arc_route: dict) -> None:
    """A misconfigured RPC must fail before any metadata mutation."""
    vault_db = VaultDatabase()
    wrong_web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))

    with pytest.raises(ValueError, match="does not match"):
        migration_module.prepare_chain_migration(wrong_web3, vault_db, [arc_route], object(), UPDATED_AT)

    assert vault_db.rows == {}
    assert vault_db.leads == {}


@pytest.mark.parametrize("chain_id", [5042, 480, 98866])
def test_seeded_nest_history_start_survives_routine_admission(chain_id: int, arc_route: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the imported lower bound when a routine scan has no reader state.

    This exercises the real all-chain selector with zero observed deposits and
    a source horizon more than 14 days after the published start. The historical
    writer receives the complete lower bound rather than a recent bootstrap.

    :param chain_id: Arc, Worldchain or Plume chain identifier.
    :param arc_route: Example Nest metadata with a published start block.
    :param monkeypatch: Replace network reads and capture writer selection.
    :param tmp_path: Isolated scanner metadata and state paths.
    """
    spec = VaultSpec(chain_id, ARC_VAULT_ADDRESS)
    detection = ERC4262VaultDetection(chain=chain_id, address=ARC_VAULT_ADDRESS, first_seen_at_block=arc_route["start_block"], first_seen_at=FIRST_SEEN, features={ERC4626Feature.nest_like}, updated_at=UPDATED_AT, deposit_count=0, redeem_count=0)
    path = tmp_path / "metadata.pickle"
    VaultDatabase(rows={spec: {"_detection_data": detection}}).write(path)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=chain_id, block_number=27_000_000))
    vault = SimpleNamespace(address=ARC_VAULT_ADDRESS, first_seen_at_block=None, get_spec=lambda: spec)
    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", lambda *_args, **_kwargs: web3)
    monkeypatch.setattr(scan_all_chains, "TokenDiskCache", SimpleNamespace)
    monkeypatch.setattr(scan_all_chains, "configure_hypersync_from_env", lambda *_args, **_kwargs: SimpleNamespace(hypersync_client=None))
    monkeypatch.setattr(scan_all_chains, "create_vault_instance", lambda *_args, **_kwargs: vault)

    def write_prices(**kwargs: object) -> dict:
        """Check that admission preserves the imported history boundary.

        :param kwargs: Options from the real recurring price selector.
        :return: Minimal successful historical scan summary.
        """
        assert kwargs["vaults"] == [vault]
        assert vault.first_seen_at_block == arc_route["start_block"]
        assert kwargs["reader_states"] == {}
        assert kwargs["vault_addresses"] == {ARC_VAULT_ADDRESS}
        return {"reader_states": {}, "rows_written": 1, "freshness_rows_written": 0, "freshness_eligible_vaults": 0, "overdue_vaults": {}, "unknown_conversion_vaults": [], "start_block": arc_route["start_block"], "end_block": 27_000_000}

    monkeypatch.setattr(scan_all_chains, "scan_historical_prices_to_parquet", write_prices)
    success, metrics = scan_all_chains.scan_prices_for_chain("https://rpc.example", 1, "1h", vault_db_path=path, reader_state_path=tmp_path / "readers.pickle", uncleaned_price_path=tmp_path / "prices.parquet")
    assert success
    assert metrics["rows_written"] == 1
    assert metrics["tvl_probe_candidates"] == 0


def test_backfill_history_preserves_reader_state_and_unrelated_prices(migration_module: ModuleType, arc_route: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Warm HyperSync timestamps, then replace only the selected Nest address."""
    spec = VaultSpec(5042, ARC_VAULT_ADDRESS)
    features = {ERC4626Feature.nest_like, ERC4626Feature.erc_7540_like}
    detection = ERC4262VaultDetection(chain=5042, address=ARC_VAULT_ADDRESS, first_seen_at_block=arc_route["start_block"], first_seen_at=FIRST_SEEN, features=features, updated_at=UPDATED_AT, deposit_count=0, redeem_count=0)
    vault_db = VaultDatabase(rows={spec: {"_detection_data": detection, "Denomination": "USDC"}})
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=5042, block_number=22_000_010))
    vault = SimpleNamespace(features=features, first_seen_at_block=None)
    calls: list[str] = []
    scanned: dict = {}
    cleaned: dict = {}

    def warm_timestamps(*args: object, **kwargs: object) -> SimpleNamespace:
        """Record the cache fill before the price scan."""
        calls.append("warm")
        assert args[1:] == (5042, arc_route["start_block"], 22_000_009)
        assert kwargs["cache_path"] == tmp_path
        assert kwargs["attempts"] == 1
        assert kwargs["chunk_size"] == TIMESTAMP_CHUNK_SIZE
        return SimpleNamespace(close=lambda: calls.append("close"))

    def scan_prices(**kwargs: object) -> dict:
        """Capture the shared historical writer options."""
        calls.append("scan")
        scanned.update(kwargs)
        return {"rows_written": 2}

    def clean_prices(vault_ids: set[str], **kwargs: object) -> int:
        """Capture the selected cleaned-history replacement."""
        calls.append("clean")
        cleaned.update({"vault_ids": vault_ids, **kwargs})
        return 2

    monkeypatch.setattr(migration_module, "configure_hypersync_from_env", lambda _: SimpleNamespace(hypersync_client=SimpleNamespace()))
    monkeypatch.setattr(migration_module, "fetch_block_timestamps_using_hypersync_cached", warm_timestamps)
    monkeypatch.setattr(migration_module, "create_vault_instance", lambda *_args, **_kwargs: vault)
    monkeypatch.setattr(migration_module, "scan_historical_prices_to_parquet", scan_prices)
    monkeypatch.setattr(migration_module, "replace_cleaned_vault_histories", clean_prices)
    monkeypatch.setattr(migration_module, "pformat_scan_result", lambda _: "2 raw rows")
    monkeypatch.setattr(migration_module, "read_json_rpc_url", lambda _: "https://rpc.example")
    monkeypatch.setattr(migration_module, "MultiProviderWeb3Factory", lambda *_args, **_kwargs: object())

    result = migration_module.backfill_chain_history(
        web3,
        [arc_route],
        vault_db,
        object(),
        vault_db_path=tmp_path / "vault-metadata-db.pickle",
        raw_price_path=tmp_path / "vault-prices-1h.parquet",
        cleaned_price_path=tmp_path / "cleaned-vault-prices-1h.parquet",
        timestamp_cache_dir=tmp_path,
        max_workers=HISTORY_WORKERS,
    )

    assert calls == ["warm", "close", "scan", "clean"]
    assert vault.first_seen_at_block == arc_route["start_block"]
    assert scanned["reader_states"] is None
    assert scanned["vault_addresses"] == {ARC_VAULT_ADDRESS}
    assert scanned["frequency"] == "1h"
    assert scanned["write_all_samples"] is True
    assert scanned["max_workers"] == HISTORY_WORKERS
    assert scanned["timestamp_cache_file"] == tmp_path
    assert cleaned["vault_ids"] == {spec.as_string_id()}
    assert result["cleaned_rows"] == EXPECTED_CLEANED_ROWS

    calls.clear()
    vault_db.rows[spec]["Denomination"] = "PRIME"
    result = migration_module.backfill_chain_history(
        web3,
        [arc_route],
        vault_db,
        object(),
        vault_db_path=tmp_path / "vault-metadata-db.pickle",
        raw_price_path=tmp_path / "vault-prices-1h.parquet",
        cleaned_price_path=tmp_path / "cleaned-vault-prices-1h.parquet",
        timestamp_cache_dir=tmp_path,
        max_workers=HISTORY_WORKERS,
    )
    assert calls == ["warm", "close", "scan"]
    assert result["unsupported_denominations"] == 1


def test_isolated_history_requires_isolated_price_and_cache_paths(migration_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An isolated metadata migration cannot change the shared price files."""
    raw = tmp_path / "vault-prices-1h.parquet"
    cleaned = tmp_path / "cleaned-vault-prices-1h.parquet"
    raw.touch()
    cleaned.touch()
    monkeypatch.setenv("HYPERSYNC_API_KEY", "test-only")
    monkeypatch.delenv("UNCLEANED_PRICE_DATABASE", raising=False)
    monkeypatch.delenv("CLEANED_PRICE_DATABASE", raising=False)
    monkeypatch.delenv("TIMESTAMP_CACHE_DIR", raising=False)

    with pytest.raises(ValueError, match="Isolated VAULT_DB_PATH"):
        migration_module.validate_history_paths(tmp_path / "vault-metadata-db.pickle", raw, cleaned, tmp_path)

    monkeypatch.setenv("UNCLEANED_PRICE_DATABASE", str(raw))
    monkeypatch.setenv("CLEANED_PRICE_DATABASE", str(cleaned))
    monkeypatch.setenv("TIMESTAMP_CACHE_DIR", str(tmp_path))
    migration_module.validate_history_paths(tmp_path / "vault-metadata-db.pickle", raw, cleaned, tmp_path)

    # A different metadata filename beside the shared prices is still isolated.
    monkeypatch.setattr(migration_module, "DEFAULT_UNCLEANED_PRICE_DATABASE", raw)
    with pytest.raises(ValueError, match="private Parquet copies"):
        migration_module.validate_history_paths(tmp_path / "private-metadata.pickle", raw, cleaned, tmp_path)


def test_apply_backfills_when_metadata_is_already_current(migration_module: ModuleType, arc_route: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The history stage runs on apply even if there is no metadata write."""
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase().write(vault_db_path)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=5042))
    history_calls: list[int] = []
    export_calls: list[set[str]] = []
    monkeypatch.setenv("NETWORKS", "arc")
    monkeypatch.setattr(migration_module, "fetch_nest_vaults", lambda **_kwargs: {"arc": arc_route})
    monkeypatch.setattr(migration_module, "read_json_rpc_url", lambda _: "https://rpc.example")
    monkeypatch.setattr(migration_module, "create_multi_provider_web3", lambda _: web3)
    monkeypatch.setattr(migration_module, "prepare_chain_migration", lambda *_args: ({}, {}, migration_module.NestMigrationResult(5042, 1, 0, 0)))
    monkeypatch.setattr(migration_module, "plan_existing_metadata_updates", lambda *_args: {})
    monkeypatch.setattr(migration_module, "validate_history_paths", lambda *_args: None)
    monkeypatch.setattr(migration_module, "backfill_chain_history", lambda reader_web3, *_args, **_kwargs: history_calls.append(reader_web3.eth.chain_id) or {"chain": "arc", "cleaned_rows": 1})
    monkeypatch.setattr(migration_module, "rebuild_nest_export", lambda _metadata, _prices, vault_ids: export_calls.append(vault_ids))

    migration_module.run_migration(dry_run=True, scan_prices=True, vault_db_path=vault_db_path)
    assert history_calls == []
    assert export_calls == []

    migration_module.run_migration(dry_run=False, scan_prices=True, vault_db_path=vault_db_path)
    assert history_calls == [5042]
    assert export_calls == [{f"5042-{ARC_VAULT_ADDRESS}"}]

    # A focused Arc repair regenerates its public classification without backfill.
    migration_module.run_migration(dry_run=False, scan_prices=False, vault_db_path=vault_db_path)
    assert history_calls == [5042]
    assert export_calls == [{f"5042-{ARC_VAULT_ADDRESS}"}] * 2


def test_nest_export_backup_and_pipeline_isolation(migration_module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Back up export state and refuse overrides outside the private pipeline.

    A migration using a metadata copy must not overwrite the shared metrics
    or retained qualification state through an inherited environment setting.

    :param migration_module: Operator migration module.
    :param tmp_path: Private scanner directory.
    :param monkeypatch: Replace expensive metric generation with a call capture.
    :return: ``None`` after checking backups and isolated export arguments.
    """
    for name in ("VAULT_EXPORT_STATE_PATH", "VAULT_METRICS_STATE_PATH"):
        monkeypatch.delenv(name, raising=False)
    metadata = tmp_path / "vault-metadata-db.pickle"
    prices = tmp_path / "cleaned-vault-prices-1h.parquet"
    prices.touch()
    originals = {name: name.encode() for name in ("top_vaults_by_chain.json", "vault-export-state.json", "vault-metrics-state.json")}
    for name, content in originals.items():
        (tmp_path / name).write_bytes(content)
    calls: list[dict] = []
    monkeypatch.setattr(migration_module, "export_vaults", lambda **kwargs: calls.append(kwargs))
    vault_ids = {f"5042-{ARC_VAULT_ADDRESS}"}
    migration_module.rebuild_nest_export(metadata, prices, vault_ids)
    assert calls == [{"data_dir": tmp_path, "vault_db_path": metadata, "parquet_path": prices, "output_path": tmp_path / "top_vaults_by_chain.json", "core3_db_path": tmp_path / "core3/core3.duckdb", "xerberus_db_path": tmp_path / "xerberus/xerberus.duckdb", "feed_db_path": tmp_path / "vault-post-database.duckdb", "force_vault_ids": vault_ids}]
    for name, content in originals.items():
        assert (tmp_path / name).read_bytes() == content
        assert (tmp_path / f"{name}.bak-nest-vaults").read_bytes() == content
    monkeypatch.setenv("VAULT_EXPORT_STATE_PATH", str(tmp_path.parent / "shared-state.json"))
    with pytest.raises(ValueError, match="state overrides"):
        migration_module.rebuild_nest_export(metadata, prices, vault_ids)
    assert len(calls) == 1
