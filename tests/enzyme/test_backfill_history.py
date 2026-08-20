"""Regression tests for the Enzyme historical migration script."""

import datetime
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

from eth_typing import HexAddress

from eth_defi.enzyme.blue_discovery import ENZYME_BLUE_DEPLOYMENTS, EnzymeBlueVaultFactoryCandidate, fetch_enzyme_blue_vault_deployed_event_topic
from eth_defi.enzyme.onyx_discovery import ENZYME_BASE_SHARES_FACTORY, EnzymeVaultFactoryCandidate
from eth_defi.vault.base import VaultSpec

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "enzyme" / "backfill-history.py"
TEST_MAX_WORKERS = 4
BASE_ENZYME_FACTORY_COUNT = 2


def load_backfill_module() -> ModuleType:
    """Load the hyphenated Enzyme migration script as a Python module."""

    spec = importlib.util.spec_from_file_location("enzyme_backfill_history", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_candidate(address: str, block_number: int) -> EnzymeVaultFactoryCandidate:
    """Create a deterministic factory-confirmed Enzyme vault candidate."""

    timestamp = datetime.datetime(2026, 8, 20, 12, 0, 0, tzinfo=datetime.UTC).replace(tzinfo=None)
    return EnzymeVaultFactoryCandidate(
        chain=8453,
        address=HexAddress(address),
        factory_address=ENZYME_BASE_SHARES_FACTORY,
        created_block=block_number,
        created_at=timestamp,
        transaction_hash="0xdead",
        log_index=0,
    )


def test_enzyme_backfill_query_is_limited_to_official_factory() -> None:
    """Do not turn the targeted migration into a generic lead scan."""

    module = load_backfill_module()
    query = module.create_factory_query(8453, 35_306_000, 35_306_100)

    assert len(query.logs) == BASE_ENZYME_FACTORY_COUNT
    assert [address.lower() for address in query.logs[0].address] == [ENZYME_BASE_SHARES_FACTORY.lower()]
    assert query.logs[1].address == [ENZYME_BLUE_DEPLOYMENTS[8453].dispatcher]


def test_enzyme_backfill_uses_one_blue_dispatcher_query_per_chain() -> None:
    """Blue discovery needs one persistent Dispatcher stream, not per-release scans."""

    module = load_backfill_module()
    query = module.create_factory_query(1, 11_632_494, 11_632_500)

    assert len(query.logs) == 1
    assert query.logs[0].address == [ENZYME_BLUE_DEPLOYMENTS[1].dispatcher]
    assert query.logs[0].topics == [[fetch_enzyme_blue_vault_deployed_event_topic()]]
    assert set(module.ENZYME_MIGRATION_CHAINS) == {1, 137, 8453, 42161}


def test_enzyme_backfill_creates_factory_qualified_lead_and_detection() -> None:
    """Persist the factory provenance and non-ERC-4626 protocol marker."""

    module = load_backfill_module()
    candidate = create_candidate("0x000000000000000000000000000000000000bEEF", 35_306_010)

    lead = module.create_enzyme_lead(candidate)
    detection = module.create_enzyme_detection(candidate)

    assert lead.enzyme_factory_candidate == candidate
    assert detection.features == {module.ERC4626Feature.enzyme_onyx_like}


def test_enzyme_backfill_checkpoint_roundtrips_both_protocol_candidates(tmp_path: Path) -> None:
    """Persisted discovery data must avoid a duplicate factory scan after interruption."""

    module = load_backfill_module()
    onyx = create_candidate("0x000000000000000000000000000000000000bEEF", 35_306_010)
    blue = EnzymeBlueVaultFactoryCandidate(
        chain=8453,
        address=HexAddress("0x000000000000000000000000000000000000cafE"),
        dispatcher_address=ENZYME_BLUE_DEPLOYMENTS[8453].dispatcher,
        fund_deployer=HexAddress("0x000000000000000000000000000000000000a11c"),
        vault_accessor=HexAddress("0x000000000000000000000000000000000000acce"),
        fund_name="Checkpoint Blue",
        created_block=23_200_000,
        created_at=onyx.created_at,
        transaction_hash="0xbead",
        log_index=3,
    )
    checkpoint_path = tmp_path / "enzyme-state.json"
    checkpoint = {"version": module.CHECKPOINT_VERSION, "chains": {"8453": {"end_block": 50_000_000, "candidates": [module.serialise_candidate(onyx), module.serialise_candidate(blue)], "prices_complete": False}}, "cleaned": False}

    module.write_checkpoint(checkpoint_path, checkpoint)
    loaded = module.read_checkpoint(checkpoint_path)
    candidates = [module.deserialise_candidate(item) for item in loaded["chains"]["8453"]["candidates"]]

    assert candidates == [onyx, blue]


def test_enzyme_backfill_skips_caught_up_vaults(monkeypatch, tmp_path: Path) -> None:
    """Do not rewrite a caught-up vault because another needs a backfill."""

    module = load_backfill_module()
    first = create_candidate("0x000000000000000000000000000000000000bEEF", 35_306_010)
    caught_up = create_candidate("0x000000000000000000000000000000000000cAFE", 35_306_020)
    calls = []

    monkeypatch.setattr(module, "fetch_latest_existing_price_blocks", lambda *_args: {caught_up.address.lower(): 40_000_000})
    monkeypatch.setattr(module, "configure_hypersync_from_env", lambda *_args: SimpleNamespace(hypersync_client=None))
    monkeypatch.setattr(module, "build_vaults", lambda *_args: [object()])
    monkeypatch.setattr(module, "pformat_scan_result", lambda *_args: "ok")

    def fake_scan_historical_prices_to_parquet(**kwargs):
        calls.append(kwargs)
        return {"reader_states": kwargs["reader_states"]}

    monkeypatch.setattr(module, "scan_historical_prices_to_parquet", fake_scan_historical_prices_to_parquet)
    result = module.scan_price_history(
        web3=SimpleNamespace(eth=SimpleNamespace(chain_id=8453)),
        json_rpc_url="https://example.invalid",
        token_cache=object(),
        reader_states={},
        candidates=[first, caught_up],
        price_path=tmp_path / "prices.parquet",
        end_block=40_000_000,
        frequency="1d",
        max_workers=1,
        rewrite_targeted=False,
    )

    assert result is not None
    assert calls[0]["vault_addresses"] == {first.address.lower()}


def test_enzyme_backfill_uses_reader_state_for_change_compressed_history(monkeypatch, tmp_path: Path) -> None:
    """Do not rescan when a saved state reached the head without a price change."""

    module = load_backfill_module()
    candidate = create_candidate("0x000000000000000000000000000000000000bEEF", 35_306_010)
    calls = []
    reader_states = {VaultSpec(candidate.chain, candidate.address): {"last_block": 40_000_000}}

    monkeypatch.setattr(module, "fetch_latest_existing_price_blocks", lambda *_args: {candidate.address.lower(): 39_000_000})
    monkeypatch.setattr(module, "configure_hypersync_from_env", lambda *_args: SimpleNamespace(hypersync_client=None))
    monkeypatch.setattr(module, "build_vaults", lambda *_args: [object()])

    def fake_scan_historical_prices_to_parquet(**kwargs):
        calls.append(kwargs)
        return {"reader_states": kwargs["reader_states"]}

    monkeypatch.setattr(module, "scan_historical_prices_to_parquet", fake_scan_historical_prices_to_parquet)
    result = module.scan_price_history(
        web3=SimpleNamespace(eth=SimpleNamespace(chain_id=8453)),
        json_rpc_url="https://example.invalid",
        token_cache=object(),
        reader_states=reader_states,
        candidates=[candidate],
        price_path=tmp_path / "prices.parquet",
        end_block=40_000_000,
        frequency="1d",
        max_workers=1,
        rewrite_targeted=False,
    )

    assert result is None
    assert calls == []


def test_enzyme_backfill_scans_all_selected_vaults_in_one_multicall_batch(monkeypatch, tmp_path: Path) -> None:
    """Submit all Base Enzyme vaults to one shared historical scan call."""

    module = load_backfill_module()
    first = create_candidate("0x000000000000000000000000000000000000bEEF", 35_306_010)
    second = create_candidate("0x000000000000000000000000000000000000cAFE", 35_306_020)
    calls = []

    monkeypatch.setattr(module, "fetch_latest_existing_price_blocks", lambda *_args: {})
    monkeypatch.setattr(module, "configure_hypersync_from_env", lambda *_args: SimpleNamespace(hypersync_client=None))
    monkeypatch.setattr(module, "build_vaults", lambda *_args: [object(), object()])
    monkeypatch.setattr(module, "pformat_scan_result", lambda *_args: "ok")

    def fake_scan_historical_prices_to_parquet(**kwargs):
        calls.append(kwargs)
        return {"reader_states": kwargs["reader_states"]}

    monkeypatch.setattr(module, "scan_historical_prices_to_parquet", fake_scan_historical_prices_to_parquet)
    module.scan_price_history(
        web3=SimpleNamespace(eth=SimpleNamespace(chain_id=8453)),
        json_rpc_url="https://example.invalid",
        token_cache=object(),
        reader_states={},
        candidates=[first, second],
        price_path=tmp_path / "prices.parquet",
        end_block=40_000_000,
        frequency="1d",
        max_workers=TEST_MAX_WORKERS,
        rewrite_targeted=False,
    )

    assert len(calls) == 1
    assert calls[0]["vault_addresses"] == {first.address.lower(), second.address.lower()}
    assert calls[0]["max_workers"] == TEST_MAX_WORKERS
