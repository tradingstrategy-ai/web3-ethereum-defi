"""Nest chain and deposit route selection at the public export boundary."""

import copy
import datetime
import importlib.util
import itertools
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest
from filelock import Timeout

from eth_defi.vault import top_vaults_json
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


@pytest.fixture
def nest_routes() -> list[dict]:
    """Create three eligible deposit entrypoints for one Nest pool.

    Different returns make it possible to check that selection preserves the
    preferred entrypoint's own metrics instead of combining route histories.

    :return: USDC, USDT and pUSD records for one Ethereum share token.
    """
    return [
        {
            "id": f"1-0x{index:040x}",
            "chain_id": 1,
            "address": f"0x{index:040x}",
            "name": "Nest pool",
            "protocol_slug": "nest",
            "share_token_address": "0xabcdef0000000000000000000000000000000001",
            "denomination": denomination,
            "current_nav": 6000.0,
            "peak_nav": 6000.0,
            "one_month_cagr": index / 10,
            "strategy_tags": ["lending"],
        }
        for index, denomination in enumerate(("USDC", "USDT", "pUSD"), start=1)
    ]


@pytest.mark.parametrize(
    "assets,expected",
    [
        (("USDC", "USDT", "pUSD"), "USDC"),
        (("USDC", "USDT"), "USDC"),
        (("USDC", "pUSD"), "USDC"),
        (("USDT", "pUSD"), "USDT"),
        (("USDC",), "USDC"),
        (("USDT",), "USDT"),
        (("pUSD",), "pUSD"),
    ],
)
def test_nest_route_preference(nest_routes: list[dict], assets: tuple[str, ...], expected: str) -> None:
    """Choose the preferred available asset independently of input order.

    The chosen object retains its own TVL and returns, and the source records
    remain unchanged for metrics and sticky-state persistence.

    :param nest_routes: Three entrypoints sharing a pool.
    :param assets: Available denominations.
    :param expected: Preferred available denomination.
    :return: ``None`` after checking every input permutation.
    """
    available = [record for record in nest_routes if record["denomination"] in assets]
    preferred = next(record for record in available if record["denomination"] == expected)
    before = copy.deepcopy(available)
    for permutation in itertools.permutations(available):
        selected = list(top_vaults_json.select_preferred_nest_routes(list(permutation)))
        assert len(selected) == 1
        assert selected[0] is preferred
    assert available == before


def test_nest_route_selection_preserves_distinct_pools(nest_routes: list[dict]) -> None:
    """Group a Nest share token across chains without merging other products.

    Unknown identities are retained independently, as are other protocols.
    Token-address casing and legacy string chain IDs do not split one pool.

    :param nest_routes: Three entrypoints sharing a pool.
    :return: ``None`` after checking pool boundaries and unaffected records.
    """
    usdc, usdt, pusd = nest_routes
    same_pool = {**usdt, "chain_id": "1", "share_token_address": usdt["share_token_address"].upper()}
    other_chain = {**usdt, "chain_id": 98866}
    other_pool = {**pusd, "share_token_address": "0x0000000000000000000000000000000000000002"}
    other_protocol = {**usdt, "protocol_slug": "morpho"}
    missing_share = {**pusd, "share_token_address": None}
    missing_chain = {**pusd, "chain_id": None}
    records = [same_pool, other_protocol, usdc, other_chain, other_pool, missing_share, missing_chain]

    assert list(top_vaults_json.select_preferred_nest_routes(records)) == [other_chain, other_protocol, other_pool, missing_share, missing_chain]


@pytest.mark.parametrize(
    "chain_ids,expected_chain",
    [
        ((98866, 1, 5042), 98866),
        ((1, 5042, 42161), 1),
        ((5042, 43114, 8453, 56), 5042),
        ((43114, 8453, 56), 43114),
        ((8453, 56), 8453),
        ((98866,), 98866),
        ((1,), 1),
    ],
)
def test_nest_chain_preference(nest_routes: list[dict], chain_ids: tuple[int, ...], expected_chain: int) -> None:
    """Prefer Plume, Ethereum, then canonical chain names alphabetically.

    Chain selection takes precedence over deposit denomination, freshness and
    advertised yield. The selected route retains its own TVL and metrics even
    when other chains have larger balances or more recent observations.

    :param nest_routes: Different asset records for one share token.
    :param chain_ids: Chains offering the same product.
    :param expected_chain: Preferred available chain.
    :return: ``None`` after checking every input ordering and source records.
    """
    records = [
        {
            **nest_routes[index % len(nest_routes)],
            "chain_id": str(chain_id),
            "chain": "misleading cached name",
            "current_nav": 1000.0 * (index + 1),
            "last_updated_at": f"2026-10-0{index + 1}T12:00:00",
            "flags": ["stale"] if chain_id == expected_chain else [],
        }
        for index, chain_id in enumerate(chain_ids)
    ]
    preferred = next(record for record in records if int(record["chain_id"]) == expected_chain)
    before = copy.deepcopy(records)
    for permutation in itertools.permutations(records):
        assert list(top_vaults_json.select_preferred_nest_routes(list(permutation))) == [preferred]
    assert records == before


def test_nest_chain_preference_before_asset_preference(nest_routes: list[dict]) -> None:
    """Prefer Plume's best asset even when Ethereum offers USDC.

    Reversing the input preserves both chain priority and USDT over pUSD on
    the preferred chain, including bridged token-symbol normalisation.

    :param nest_routes: USDC, USDT and pUSD routes sharing one token.
    :return: ``None`` after checking the selected original object.
    """
    ethereum_usdc, usdt, pusd = nest_routes
    plume_usdt = {**usdt, "chain_id": 98866, "denomination": "USDT0"}
    plume_pusd = {**pusd, "chain_id": 98866}
    records = [ethereum_usdc, plume_usdt, plume_pusd]
    for permutation in itertools.permutations(records):
        [selected] = top_vaults_json.select_preferred_nest_routes(list(permutation))
        assert selected is plume_usdt


def test_nest_route_same_asset_tie_is_deterministic(nest_routes: list[dict]) -> None:
    """Choose the lowest entrypoint address when deposit asset ranks tie.

    Input order and advertised yield do not choose between otherwise equal
    routes, preventing repeated exports from switching their canonical route.

    :param nest_routes: Three entrypoints sharing a pool.
    :return: ``None`` after checking equal-rank permutations.
    """
    first = nest_routes[0]
    second = {**first, "address": "0xabcdef0000000000000000000000000000000002", "one_month_cagr": 0.9}
    for records in ([first, second], [second, first]):
        assert list(top_vaults_json.select_preferred_nest_routes(records)) == [first]


@pytest.mark.parametrize("symbol,expected_asset", [("USDC.e", "USDC.e"), ("USDT0", "USDT0"), ("USD₮0", "USD₮0")])
def test_nest_route_preference_normalises_bridge_symbols(nest_routes: list[dict], symbol: str, expected_asset: str) -> None:
    """Prefer bridged USDC and USDT entrypoints over pUSD.

    Normalisation affects selection only; the selected entrypoint retains its
    actual onchain denomination symbol in the export.

    :param nest_routes: Entry-point records for the same pool.
    :param symbol: Bridged denomination to compare with pUSD.
    :param expected_asset: Symbol expected on the selected record.
    :return: ``None`` after checking the preferred route's unchanged symbol.
    """
    bridged = {**nest_routes[0], "denomination": symbol}
    selected = list(top_vaults_json.select_preferred_nest_routes([nest_routes[2], bridged]))
    assert selected == [bridged]
    assert selected[0]["denomination"] == expected_asset


@pytest.mark.parametrize("cross_chain", [False, True])
def test_nest_public_export_selects_after_sticky_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nest_routes: list[dict], *, cross_chain: bool) -> None:
    """Publish one pool and retain every route's history and sticky record.

    Exercise the real exporter with isolated metadata, Parquet and persisted
    state. Only metric calculation is replaced: first USDT qualifies, then
    USDC and optionally Plume qualify, then missing preferred metrics replay
    stored routes without restoring duplicate listings. Category aggregates
    use the selected route's TVL and monthly return.

    :param tmp_path: Private export and scanner files.
    :param monkeypatch: Isolate environment, clock and metric calculation.
    :param nest_routes: Three entrypoints sharing a pool.
    :param cross_chain: Add a preferred Plume route after the first export.
    :return: ``None`` after checking public output and preserved scanner files.
    """
    monkeypatch.delenv("VAULT_EXPORT_STATE_PATH", raising=False)
    monkeypatch.delenv("VAULT_METRICS_STATE_PATH", raising=False)
    monkeypatch.setattr(top_vaults_json, "THRESHOLD_TVL", 5000.0)
    now = datetime.datetime(2026, 10, 7, 12)  # noqa: DTZ001 - Scanner timestamps use naive UTC.
    monkeypatch.setattr(top_vaults_json, "native_datetime_utc_now", lambda: now)
    monkeypatch.setattr(top_vaults_json, "run_vault_export_post_processors", lambda _vault_db: set())
    plume = {**nest_routes[1], "id": f"98866-{nest_routes[1]['address']}", "chain_id": 98866, "current_nav": 7000.0}
    if cross_chain:
        nest_routes = [*nest_routes, plume]

    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    VaultDatabase(
        rows={
            VaultSpec(record["chain_id"], record["address"]): {
                "Denomination": record["denomination"],
                "Protocol": "Nest",
                "_detection_data": SimpleNamespace(chain=record["chain_id"], address=record["address"]),
                "_denomination_token": {"address": record["address"], "decimals": 6},
            }
            for record in nest_routes
        }
    ).write(vault_db_path)
    prices = pd.DataFrame(
        [
            {
                "id": record["id"],
                "chain": record["chain_id"],
                "address": record["address"],
                "share_price": 1.0,
                "total_assets": record["current_nav"],
                "timestamp": timestamp,
            }
            for record in nest_routes
            for timestamp in (now - datetime.timedelta(days=30), now)
        ]
    ).set_index("timestamp")
    prices["block_number"] = range(1, len(prices) + 1)
    parquet_path = tmp_path / "prices.parquet"
    prices.to_parquet(parquet_path)
    original_files = {path: path.read_bytes() for path in (vault_db_path, parquet_path)}

    calculated_routes = nest_routes[1:3]

    def calculate_metrics(returns_df: pd.DataFrame, vault_db: VaultDatabase, **_kwargs: object) -> pd.DataFrame:
        """Supply fixed metrics while checking all entrypoints are calculated.

        This avoids repeating return mathematics while preserving the real
        export eligibility, sticky replay, aggregation and file writes.

        :param returns_df: Daily returns for every admitted scanner entrypoint.
        :param vault_db: Loaded metadata for every entrypoint.
        :param _kwargs: Unused additional metric inputs.
        :return: Selected current records with complete one-month coverage.
        """
        assert set(returns_df["id"]) == {record["id"] for record in nest_routes}
        assert len(vault_db) == len(nest_routes)
        return pd.DataFrame(
            [
                {
                    **record,
                    "last_updated_at": now,
                    "one_month_start": now - datetime.timedelta(days=30),
                    "one_month_end": now,
                    "one_month_samples": 31,
                }
                for record in calculated_routes
            ]
        )

    monkeypatch.setattr(top_vaults_json, "calculate_lifetime_metrics", calculate_metrics)
    output_path = tmp_path / "public.json"
    preferred = plume if cross_chain else nest_routes[0]
    for current, expected_record, sticky_count in (
        (nest_routes[1:3], nest_routes[1], 2),
        (nest_routes, preferred, len(nest_routes)),
        (nest_routes[1:3], preferred, len(nest_routes)),
        ([], preferred, len(nest_routes)),
    ):
        calculated_routes = current
        output = top_vaults_json.main(
            data_dir=tmp_path,
            vault_db_path=vault_db_path,
            parquet_path=parquet_path,
            output_path=output_path,
            core3_db_path=tmp_path / "absent-core3.duckdb",
            xerberus_db_path=tmp_path / "absent-xerberus.duckdb",
            feed_db_path=tmp_path / "absent-feed.duckdb",
        )
        assert len(output["vaults"]) == 1
        selected = output["vaults"][0]
        assert selected["id"] == expected_record["id"]
        assert selected["chain_id"] == expected_record["chain_id"]
        assert selected["denomination"] == expected_record["denomination"]
        assert selected["one_month_cagr"] == expected_record["one_month_cagr"]
        assert selected["current_nav"] == expected_record["current_nav"]
        assert json.loads(output_path.read_text())["vaults"] == output["vaults"]
        state = json.loads((tmp_path / "vault-export-state.json").read_text())
        assert len(state["vaults"]) == sticky_count
        if expected_record in current:
            category = output["categories"]["lending"]
            assert category["vault_count"] == 1
            assert category["tvl_usd"] == expected_record["current_nav"]
            assert category["one_month_apy"] == pytest.approx(expected_record["one_month_cagr"])
    assert all(path.read_bytes() == original for path, original in original_files.items())


@pytest.fixture
def migration() -> ModuleType:
    """Load the operator script without invoking its entry point.

    Importing the script exercises the implementation operators use for an
    offline repair, while keeping every test's destination isolated.

    :return: Nest export migration module.
    """
    path = Path(__file__).resolve().parents[2] / "scripts" / "nest" / "migrate-export-routes.py"
    spec = importlib.util.spec_from_file_location("migrate_nest_export_routes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("cross_chain", [False, True])
def test_nest_export_migration_preserves_source_and_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, migration: ModuleType, nest_routes: list[dict], *, cross_chain: bool) -> None:
    """Preview and repair old listings with a recoverable original backup.

    The repair updates aggregate TVL and coverage counts, retains unrelated
    records and provenance, and does not rewrite an already repaired export.

    :param tmp_path: Isolated pipeline directory.
    :param monkeypatch: Verify the writer lock at the publication boundary.
    :param migration: Operator migration script.
    :param nest_routes: Duplicate deposit entrypoints.
    :param cross_chain: Include a preferred chain with a different asset and TVL.
    :return: ``None`` after checking preview, applied and repeated runs.
    """
    nest_routes[0]["curator_slug"] = "nest-dao"
    nest_routes[1]["curator_slug"] = "old-curator"
    unrelated = {**nest_routes[0], "protocol_slug": "morpho", "current_nav": 100.0}
    preferred = nest_routes[0]
    if cross_chain:
        preferred = {**preferred, "id": f"98866-{preferred['address']}", "chain_id": 98866, "denomination": "USDT", "current_nav": 7000.0}
        nest_routes = [*nest_routes, preferred]
    source = {
        "generated_at": "2026-10-07T12:00:00Z",
        "metadata": {"version": {"commit_hash": "original-build"}},
        "vaults": [*nest_routes, unrelated],
        "categories": {},
        "xerberus_stats": {},
        "core3_protocols": {"nest": {"risk": "original"}, "absent": {}},
        "xerberus_protocols": {"nest": {"name": "Nest"}, "absent": {}},
        "curators": {"nest-dao": {"name": "Nest DAO"}, "old-curator": {}},
    }
    export_path = tmp_path / "top_vaults_by_chain.json"
    export_path.write_text(json.dumps(source))
    original = export_path.read_bytes()
    removed_count = len(nest_routes) - 1

    assert migration.migrate_nest_export(tmp_path, dry_run=True) == removed_count
    assert export_path.read_bytes() == original
    assert not list(tmp_path.glob("*.before-nest-route-migration-*"))

    original_writer = migration.write_strict_json

    def write_with_lock_check(path: Path, payload: dict, *, validated: bool = False) -> None:
        """Verify that publication is protected by the shared scanner lock.

        A separate lock object cannot acquire the same file while migration
        writes its replacement.

        :param path: Public JSON destination.
        :param payload: Repaired export.
        :param validated: Whether strict validation has already passed.
        :return: ``None`` after the normal atomic write.
        """
        with pytest.raises(Timeout), migration.wait_other_writers(tmp_path / "scan-pipeline", timeout=0):
            pytest.fail("Migration did not hold the scanner writer lock")
        original_writer(path, payload, validated=validated)

    monkeypatch.setattr(migration, "write_strict_json", write_with_lock_check)
    assert migration.migrate_nest_export(tmp_path, dry_run=False) == removed_count
    migrated = json.loads(export_path.read_bytes())
    assert migrated["vaults"] == [preferred, unrelated]
    assert migrated["metadata"] == source["metadata"]
    assert migrated["generated_at"] == source["generated_at"]
    expected_tvl = preferred["current_nav"] + unrelated["current_nav"]
    assert migrated["categories"]["lending"]["tvl_usd"] == expected_tvl
    assert migrated["xerberus_stats"]["total_vaults"] == len(migrated["vaults"])
    assert migrated["core3_protocols"] == {"nest": {"risk": "original"}}
    assert migrated["xerberus_protocols"] == {"nest": {"name": "Nest"}}
    assert migrated["curators"] == {"nest-dao": {"name": "Nest DAO"}}
    backups = list(tmp_path.glob("*.before-nest-route-migration-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original

    repaired = export_path.read_bytes()
    assert migration.migrate_nest_export(tmp_path, dry_run=False) == 0
    assert export_path.read_bytes() == repaired
    assert list(tmp_path.glob("*.before-nest-route-migration-*")) == backups


def test_nest_export_migration_aggregate_failure_preserves_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, migration: ModuleType, nest_routes: list[dict]) -> None:
    """Abort an offline repair when aggregate calculation fails.

    Existing JSON remains byte-for-byte intact and no backup is created before
    the prospective replacement has passed calculation and validation.

    :param tmp_path: Isolated pipeline directory.
    :param monkeypatch: Replace aggregate calculation with a failure.
    :param migration: Operator migration script.
    :param nest_routes: Duplicate deposit entrypoints.
    :return: ``None`` after checking that the original export is preserved.
    """
    export_path = tmp_path / "top_vaults_by_chain.json"
    export_path.write_text(json.dumps({"vaults": nest_routes, "categories": {}}))
    original = export_path.read_bytes()

    def fail_aggregate(_vaults: list[dict]) -> dict:
        """Simulate an unavailable aggregate calculation.

        Raising before publication checks that a repair cannot leave stale
        category totals beside a newly selected vault list.

        :param _vaults: Unused selected export records.
        :return: Never returns because calculation fails.
        """
        message = "aggregate calculation failed"
        raise ValueError(message)

    monkeypatch.setattr(migration, "build_strategy_categories_for_export", fail_aggregate)
    with pytest.raises(ValueError, match="aggregate calculation failed"):
        migration.migrate_nest_export(tmp_path, dry_run=False)
    assert export_path.read_bytes() == original
    assert not list(tmp_path.glob("*.before-nest-route-migration-*"))


def test_nest_export_migration_preserves_absent_categories(tmp_path: Path, migration: ModuleType, nest_routes: list[dict]) -> None:
    """Keep an export's optional category field absent during offline repair.

    Operators can override the public export location while the migration
    still uses the configured pipeline directory for its scanner lock.

    :param tmp_path: Isolated pipeline directory.
    :param migration: Operator migration script.
    :param nest_routes: Duplicate entrypoint records.
    :return: ``None`` after checking selection without schema expansion.
    """
    export_path = tmp_path / "alternate.json"
    export_path.write_text(json.dumps({"vaults": nest_routes}))
    assert migration.migrate_nest_export(tmp_path, dry_run=False, export_path=export_path) == len(nest_routes) - 1
    assert json.loads(export_path.read_bytes()) == {"vaults": [nest_routes[0]]}


def test_nest_export_migration_rejects_invalid_dry_run(monkeypatch: pytest.MonkeyPatch, migration: ModuleType) -> None:
    """Reject ambiguous write settings before touching scanner files.

    An invalid boolean must fail at configuration validation, even when the
    default export is absent, rather than silently applying a migration.

    :param monkeypatch: Configure an invalid write switch.
    :param migration: Operator migration script.
    :return: ``None`` after checking the configuration error.
    """
    monkeypatch.setenv("DRY_RUN", "invalid")
    with pytest.raises(ValueError, match="DRY_RUN must be true or false"):
        migration.main()
