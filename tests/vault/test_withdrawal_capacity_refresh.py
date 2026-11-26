"""Capacity cadence, persistence and scanner publication boundaries.

RPC stand-ins isolate scheduling, failure and cache behaviour from network
latency; the separate fixed-block fork matrix checks actual protocol readings.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from typing import Any

import pytest

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.vault import scan_all_chains
from eth_defi.vault import withdrawal_capacity_readers as readers
from eth_defi.vault import withdrawal_capacity_refresh as refresh
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase
from eth_defi.vault.withdrawal_capacity import assess_withdrawal_capacity

NOW = datetime(2026, 11, 16, tzinfo=UTC)


@pytest.mark.parametrize("capacity_stage", ["ready", "reader_failed", "invalid_configuration"])
def test_normal_tick_refreshes_configured_chains_before_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capacity_stage: str) -> None:
    """A normal scan tick retains optional refresh and its existing publication."""

    database = VaultDatabase()
    database.rows[VaultSpec(1, "0x" + "a" * 40)] = {"NAV": Decimal(10000), "Denomination": "USDC"}
    database.write(tmp_path / "vault-metadata-db.pickle")
    if capacity_stage == "invalid_configuration":
        monkeypatch.setenv("WITHDRAWAL_CAPACITY_MAX_VAULTS_PER_CHAIN", "abc")
    observed = []
    published = []

    def read(web3: str, path: Path, **kwargs: Any) -> int:
        observed.append(web3)
        if capacity_stage == "reader_failed":
            raise RuntimeError("Capacity stage failed")
        return 1

    monkeypatch.setattr(scan_all_chains, "refresh_withdrawal_capacity", read)
    monkeypatch.setenv("JSON_RPC_CAPACITY_TEST", "unused-test-provider")
    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", lambda _: "configured-chain")
    monkeypatch.setattr(scan_all_chains, "print_dashboard", lambda *args, **kwargs: None)
    monkeypatch.setattr(scan_all_chains, "run_post_processing", lambda **kwargs: published.append(True) or {})
    results = scan_all_chains.run_scan_tick(
        chains=[],
        capacity_chains=[scan_all_chains.ChainConfig("Ethereum", "JSON_RPC_CAPACITY_TEST")],
        active_protocols=[],
        scan_prices=False,
        scan_hypercore=False,
        scan_grvt=False,
        scan_lighter=False,
        scan_hibachi=False,
        scan_apex=False,
        scan_core3=False,
        scan_currency_rates=False,
        max_workers=1,
        core3_max_workers=1,
        currency_api_max_workers=1,
        frequency="1h",
        retry_count=0,
        skip_post_processing=False,
        skip_cleaning=True,
        skip_top_vaults=True,
        skip_sparklines=True,
        skip_metadata=True,
        skip_data=True,
        skip_samples=True,
        vault_db_path=tmp_path / "vault-metadata-db.pickle",
        uncleaned_price_path=tmp_path / "vault-prices-1h.parquet",
        reader_state_path=tmp_path / "vault-reader-state-1h.pickle",
        hyperliquid_db_path=tmp_path / "hyperliquid-vaults.duckdb",
        hyperliquid_hf_db_path=tmp_path / "hyperliquid-vaults-hf.duckdb",
        grvt_db_path=tmp_path / "grvt-vaults.duckdb",
        lighter_db_path=tmp_path / "lighter-pools.duckdb",
        hibachi_db_path=tmp_path / "hibachi-vaults.duckdb",
        apex_db_path=tmp_path / "apex-vaults.duckdb",
        bkp_files=[],
        bkp_dir=tmp_path / "backups",
        core3_db_path=tmp_path / "core3.duckdb",
    )
    assert observed == ([] if capacity_stage == "invalid_configuration" else ["configured-chain"])
    assert published == [True]


def test_idle_loop_refreshes_capacity_without_scan_or_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real loop's idle branch must not enter any scan/publication pipeline."""

    database = VaultDatabase()
    database.rows[VaultSpec(1, "0x" + "a" * 40)] = {"NAV": Decimal(10000), "Denomination": "USDC"}
    database.write(tmp_path / "vault-metadata-db.pickle")
    monkeypatch.chdir(tmp_path)
    settings = {
        "PIPELINE_DATA_DIR": str(tmp_path),
        "LOOP_INTERVAL_SECONDS": "3600",
        "MAX_CYCLES": "1",
        "TEST_CHAINS": "Ethereum",
        "SKIP_CORE3": "true",
        "SKIP_XERBERUS": "true",
        "SKIP_CURRENCY_RATES": "true",
        "SKIP_POST_PROCESSING": "false",
        "FORCE_LEAD_DISCOVERY": "false",
        "JSON_RPC_CAPACITY_TEST": "test-provider",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    for name in ("SCAN_PRICES", "SCAN_HYPERCORE", "SCAN_GRVT", "SCAN_LIGHTER", "SCAN_HIBACHI", "SCAN_APEX", "SCAN_DERIVE_V3", "SCAN_NEUTRAL_TRADE"):
        monkeypatch.setenv(name, "false")
    observed = []

    def read(web3: str, path: Path, **kwargs: Any) -> int:
        observed.append((web3, path))
        return 1

    monkeypatch.setattr(scan_all_chains, "refresh_withdrawal_capacity", read)
    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", lambda _: "configured-chain")
    monkeypatch.setattr(scan_all_chains, "build_chain_configs", lambda: [scan_all_chains.ChainConfig("Ethereum", "JSON_RPC_CAPACITY_TEST"), scan_all_chains.ChainConfig("Hyperliquid", "JSON_RPC_UNUSED")])
    monkeypatch.setattr(scan_all_chains, "get_due_items", lambda *args, **kwargs: ([], []))
    monkeypatch.setattr(scan_all_chains, "refresh_stale_proxy_state", lambda: None)
    monkeypatch.setattr(scan_all_chains, "clear_sqlite_rate_limit_databases", lambda: [])
    monkeypatch.setattr(scan_all_chains, "validate_top_vaults_config", lambda **kwargs: None)
    monkeypatch.setattr(scan_all_chains, "run_scan_tick", lambda **kwargs: pytest.fail("Idle loop entered full scan/publication pipeline"))
    monkeypatch.setattr(scan_all_chains, "run_post_processing", lambda **kwargs: pytest.fail("Idle loop started publication"))
    handlers = set(logging.getLogger().handlers)
    original_level = logging.getLogger().level
    try:
        scan_all_chains._main()
    finally:
        for handler in set(logging.getLogger().handlers) - handlers:
            logging.getLogger().removeHandler(handler)
            handler.close()
        logging.getLogger().setLevel(original_level)
    assert observed == [("configured-chain", tmp_path / "vault-metadata-db.pickle")]


@pytest.mark.parametrize(
    "symbol,tvl,days",
    [("USDC", "9999.999999", 7), ("USDC", "10000", 1), ("WETH", "5", 1), ("WBTC", "0.16", 7), ("UNKNOWN", "100000000", 7), ("USDC", "NaN", 7)],
)
def test_refresh_interval_uses_existing_converter(symbol: str, tvl: str, days: int) -> None:
    """Boundary equality is daily, with unknown or unusable valuations weekly."""
    assert refresh.get_capacity_refresh_interval({"NAV": tvl, "Denomination": symbol}) == timedelta(days=days)


@pytest.mark.parametrize("initial_tvl,new_tvl,days", [("1000", "10000", 1), ("10000", "1000", 7)])
def test_refresh_pins_batch_and_persists_without_rewriting_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial_tvl: str, new_tvl: str, days: int) -> None:
    """The loop persists only its namespace and waits for the selected next deadline."""
    database = VaultDatabase()
    spec = VaultSpec(1, "0x" + "a" * 40)
    row = {"Name": "Retained", "NAV": Decimal(initial_tvl), "Denomination": "USDC", "features": {ERC4626Feature.forty_acres_like}}
    database.rows[spec] = row.copy()
    database.last_scanned_block[1] = 42
    path = tmp_path / "catalogue.pickle"
    database.write(path)
    calls = []
    block_reads = []

    def get_block(number: int | str) -> dict:
        block_reads.append(number)
        return {"number": 126 if number == "latest" else number, "hash": bytes(32), "timestamp": int(NOW.timestamp())}

    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, get_block=get_block))

    def read(number: int) -> dict:
        calls.append(number)
        return {"status": "measured", "current_tvl": new_tvl, "current_capacity": new_tvl, "denomination_symbol": "USDC", "deposits_paused": False, "withdrawals_paused": False}

    def construct(*args: Any, **kwargs: Any) -> SimpleNamespace:
        assert kwargs["features"] == row["features"]
        assert kwargs["default_block_identifier"] == 123
        return SimpleNamespace(fetch_withdrawal_capacity_observation=read)

    monkeypatch.setattr(refresh, "create_vault_instance", construct)
    assert refresh.refresh_withdrawal_capacity(web3, path, now=NOW) == 1
    assert calls == [123] and block_reads == ["latest", 123]
    assert not hasattr(web3, "_withdrawal_capacity_read_context")
    saved_db = VaultDatabase.read(path)
    saved = saved_db.rows[spec]
    assert {key: saved[key] for key in row} == row
    assert saved_db.last_scanned_block == {1: 42}
    observation = saved["_withdrawal_capacity_observation"]
    interval = timedelta(days=days)
    assert observation["refresh_interval_seconds"] == interval.total_seconds()
    assert observation["block_number"] == 123 and observation["observed_at"] == NOW.isoformat()
    assert observation["structural_warning"] is True
    assert observation["status"] == "measured"
    refresh_limit = assess_withdrawal_capacity(observation, now=NOW)
    assert refresh_limit.capacity_warning is True
    assert refresh_limit.current_capacity == Decimal(new_tvl)
    assert refresh.refresh_withdrawal_capacity(web3, path, now=NOW + interval - timedelta(seconds=1)) == 0
    assert block_reads == ["latest", 123]
    assert refresh.refresh_withdrawal_capacity(web3, path, now=NOW + interval) == 1
    assert calls == [123, 123]


@pytest.mark.parametrize("days", [1, 7])
def test_snapshot_freshness_matches_refresh_cadence(days: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Daily and weekly snapshots survive their interval plus the scanner grace."""
    monkeypatch.delenv("WITHDRAWAL_CAPACITY_MAX_AGE_SECONDS", raising=False)
    observation = {"status": "measured", "current_capacity": "1000", "current_tvl": "1000", "observed_at": NOW.isoformat(), "refresh_interval_seconds": int(timedelta(days=days).total_seconds())}
    deadline = NOW + timedelta(days=days, hours=2)
    assert assess_withdrawal_capacity(observation, now=deadline).capacity_warning is False
    assert assess_withdrawal_capacity(observation, now=deadline + timedelta(seconds=1)).capacity_warning is None
    monkeypatch.setenv("WITHDRAWAL_CAPACITY_MAX_AGE_SECONDS", "7200")
    assert assess_withdrawal_capacity(observation, now=NOW + timedelta(hours=3)).capacity_warning is None
    assert assess_withdrawal_capacity(observation, now=NOW + timedelta(hours=3), max_age=timedelta(days=1)).capacity_warning is False


@pytest.mark.parametrize("failure", [None, TimeoutError])
def test_batch_coalesces_identical_contract_reads(failure: type[BaseException] | None) -> None:
    """Shared market reads and their failures are cached only within the pinned batch."""
    web3 = SimpleNamespace(_withdrawal_capacity_read_context=({}, Lock()))
    calls = []

    def call(*, block_identifier: int) -> int:
        calls.append(block_identifier)
        if failure:
            raise failure("Provider unavailable")
        return 100

    contract_call = SimpleNamespace(address="0x" + "a" * 40, _encode_transaction_data=lambda: "0x1234", call=call)

    def read(_: int) -> int | None:
        if failure:
            with pytest.raises(failure):
                readers._read(web3, contract_call, 123)
            return None
        return readers._read(web3, contract_call, 123)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(read, range(8)))
    assert calls == [123]
    assert results == [None if failure else 100] * 8


def test_failure_retains_daily_schedule_after_growth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed read cannot demote the last measured large TVL to stale small NAV."""
    spec = VaultSpec(1, "0x" + "a" * 40)
    database = VaultDatabase()
    database.rows[spec] = {"Name": "Grown", "NAV": Decimal(100), "Denomination": "USDC", "_withdrawal_capacity_observation": {"status": "measured", "current_tvl": "10000", "refresh_interval_seconds": 86400, "checked_at": (NOW - timedelta(days=1)).isoformat()}}
    path = tmp_path / "catalogue.pickle"
    database.write(path)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, get_block=lambda number: {"number": 126 if number == "latest" else number, "hash": bytes(32), "timestamp": int(NOW.timestamp())}))
    calls = []

    def read(number: int) -> dict:
        calls.append(number)
        raise TimeoutError("Unavailable")

    monkeypatch.setattr(refresh, "create_vault_instance", lambda *args, **kwargs: SimpleNamespace(fetch_withdrawal_capacity_observation=read))
    assert refresh.refresh_withdrawal_capacity(web3, path, now=NOW) == 1
    observation = VaultDatabase.read(path).rows[spec]["_withdrawal_capacity_observation"]
    assert observation["status"] == "failed" and observation["refresh_interval_seconds"] == 86400
    assert "TimeoutError" in observation["reason"]
    assert "Unavailable" not in observation["reason"]
    assert refresh.refresh_withdrawal_capacity(web3, path, now=NOW + timedelta(hours=23)) == 0
    assert refresh.refresh_withdrawal_capacity(web3, path, now=NOW + timedelta(days=1)) == 1
    assert calls == [123, 123]
    assert VaultDatabase.read(path).rows[spec]["_withdrawal_capacity_observation"]["refresh_interval_seconds"] == 86400


@pytest.mark.parametrize("checked_at", [None, "2026-12-01T00:00:00"])
def test_missing_invalid_or_future_schedule_is_due(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checked_at: str | None) -> None:
    """Legacy or unusable scheduling timestamps never withhold the first check."""
    spec = VaultSpec(1, "0x" + "a" * 40)
    database = VaultDatabase()
    database.rows[spec] = {"NAV": Decimal(100), "Denomination": "USDC", "_withdrawal_capacity_observation": {"checked_at": checked_at}}
    path = tmp_path / "catalogue.pickle"
    database.write(path)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, get_block=lambda number: {"number": 126 if number == "latest" else number, "hash": bytes(32), "timestamp": int(NOW.timestamp())}))
    monkeypatch.setattr(refresh, "create_vault_instance", lambda *args, **kwargs: None)
    assert refresh.refresh_withdrawal_capacity(web3, path, now=NOW) == 1
    saved = VaultDatabase.read(path).rows[spec]["_withdrawal_capacity_observation"]
    assert saved["status"] == "unsupported" and saved["refresh_interval_seconds"] == 604800


@pytest.mark.parametrize("days", [1, 7])
def test_due_selection_uses_recorded_interval_until_refresh(days: int) -> None:
    """A metadata-only NAV change cannot desynchronise expiry and the due deadline."""
    database = VaultDatabase()
    spec = VaultSpec(1, "0x" + "a" * 40)
    database.rows[spec] = {"NAV": Decimal(100 if days == 1 else 10000), "Denomination": "USDC", "_withdrawal_capacity_observation": {"status": "unsupported", "checked_at": NOW.isoformat(), "refresh_interval_seconds": days * 86400}}
    assert refresh.select_due_capacity_vaults(database, now=NOW + timedelta(days=days) - timedelta(seconds=1)) == {}
    assert refresh.select_due_capacity_vaults(database, now=NOW + timedelta(days=days)) == {1: [spec]}


@pytest.mark.parametrize("preloaded", [False, True])
def test_capacity_coordinator_loads_once_and_skips_fresh_chains(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, preloaded: bool) -> None:
    """Only due chains get providers; a supplied catalogue bypasses disk entirely."""
    path = tmp_path / "catalogue.pickle"
    database = VaultDatabase()
    for chain in (1, 8453, 42161):
        database.rows[VaultSpec(chain, "0x" + "a" * 40)] = {"NAV": Decimal(10000), "Denomination": "USDC"}
    database.rows[VaultSpec(8453, "0x" + "a" * 40)]["_withdrawal_capacity_observation"] = {"checked_at": NOW.isoformat(), "refresh_interval_seconds": 86400}
    database.write(path)
    if preloaded:
        path.unlink()
    loaded = []
    providers = []
    updated = []
    original_read = VaultDatabase.read

    def read_catalogue(target: Path) -> VaultDatabase:
        loaded.append(target)
        return original_read(target)

    def make_provider(rpc: str) -> str:
        providers.append(rpc)
        return rpc

    def refresh_chain(web3: str, target: Path, **kwargs: Any) -> int:
        assert target == path and kwargs["max_workers"] == 4
        assert kwargs["database"].rows.keys() == database.rows.keys()
        if preloaded:
            assert kwargs["database"] is database
        updated.extend(kwargs["targets"])
        return len(kwargs["targets"])

    # Replace transport only: selection and persisted catalogue loading stay real.
    monkeypatch.setattr(VaultDatabase, "read", read_catalogue)
    monkeypatch.setattr(scan_all_chains, "native_datetime_utc_now", lambda: NOW.replace(tzinfo=None))
    monkeypatch.setattr(scan_all_chains, "create_multi_provider_web3", make_provider)
    monkeypatch.setattr(scan_all_chains, "refresh_withdrawal_capacity", refresh_chain)
    monkeypatch.setenv("JSON_RPC_CAPACITY_TEST", "ethereum")
    monkeypatch.setenv("JSON_RPC_CAPACITY_FRESH", "base")
    chains = [scan_all_chains.ChainConfig("Ethereum", "JSON_RPC_CAPACITY_TEST"), scan_all_chains.ChainConfig("Base", "JSON_RPC_CAPACITY_FRESH")]
    assert scan_all_chains.refresh_withdrawal_capacity_for_chains(chains, path, max_workers=50, database=database if preloaded else None) == 1
    assert loaded == ([] if preloaded else [path]) and providers == ["ethereum"]
    assert updated == [VaultSpec(1, "0x" + "a" * 40)]


def test_capacity_batches_are_bounded_and_resume_oldest_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A capped tick leaves other chains intact and the next tick drains its backlog."""
    database = VaultDatabase()
    specs = [VaultSpec(1, "0x" + str(i) * 40) for i in (1, 2, 3)]
    for i, spec in enumerate(specs):
        database.rows[spec] = {"NAV": Decimal(10000), "Denomination": "USDC", "_withdrawal_capacity_observation": {"checked_at": (NOW - timedelta(days=1, hours=3 - i)).isoformat(), "refresh_interval_seconds": 86400}}
    other = VaultSpec(8453, "0x" + "a" * 40)
    other_row = {"Name": "Other chain", "NAV": Decimal(10000), "Denomination": "USDC"}
    database.rows[other] = other_row.copy()
    path = tmp_path / "catalogue.pickle"
    database.write(path)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, get_block=lambda number: {"number": 126 if number == "latest" else number, "hash": bytes(32), "timestamp": int(NOW.timestamp())}))
    constructed = []

    def construct(web3: SimpleNamespace, address: str, **kwargs: Any) -> None:
        constructed.append(address)
        return None

    # Unsupported adapters need no network; the real scheduler and writer run.
    monkeypatch.setattr(refresh, "create_vault_instance", construct)
    assert refresh.refresh_withdrawal_capacity(web3, path, now=NOW, max_vaults_per_chain=2) == 2
    assert constructed == [spec.vault_address for spec in specs[:2]]
    assert VaultDatabase.read(path).rows[other] == other_row
    assert refresh.refresh_withdrawal_capacity(web3, path, now=NOW, max_vaults_per_chain=2) == 1
    assert constructed == [spec.vault_address for spec in specs]
    assert VaultDatabase.read(path).rows[other] == other_row
