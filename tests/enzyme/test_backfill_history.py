"""Regression tests for the Enzyme historical migration script."""

import asyncio
import datetime
import importlib.util
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from eth_typing import HexAddress

from eth_defi.enzyme.blue_discovery import ENZYME_BLUE_DEPLOYMENTS, EnzymeBlueVaultFactoryCandidate, fetch_enzyme_blue_vault_deployed_event_topic
from eth_defi.enzyme.onyx_discovery import ENZYME_BASE_SHARES_FACTORY, EnzymeVaultFactoryCandidate
from eth_defi.vault.base import VaultSpec

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "enzyme" / "backfill-history.py"
TEST_MAX_WORKERS = 4
BASE_ENZYME_DISCOVERY_SELECTION_COUNT = 3
EXPECTED_LINK_UPDATES = 2
METADATA_END_BLOCK = 50_000_000
RETRY_ATTEMPTS = 2


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


def test_enzyme_backfill_query_combines_factory_and_handler_discovery() -> None:
    """Collect Onyx handler changes without a second Base history scan."""

    module = load_backfill_module()
    query = module.create_factory_query(8453, 35_306_000, 35_306_100)

    assert len(query.logs) == BASE_ENZYME_DISCOVERY_SELECTION_COUNT
    assert [address.lower() for address in query.logs[0].address] == [ENZYME_BASE_SHARES_FACTORY.lower()]
    assert query.logs[1].topics == [list(module.fetch_enzyme_deposit_handler_event_topics())]
    assert query.logs[2].address == [ENZYME_BLUE_DEPLOYMENTS[8453].dispatcher]


def test_enzyme_backfill_uses_one_blue_dispatcher_query_per_chain() -> None:
    """Blue discovery needs one persistent Dispatcher stream, not per-release scans."""

    module = load_backfill_module()
    query = module.create_factory_query(1, 11_632_494, 11_632_500)

    assert len(query.logs) == 1
    assert query.logs[0].address == [ENZYME_BLUE_DEPLOYMENTS[1].dispatcher]
    assert query.logs[0].topics == [[fetch_enzyme_blue_vault_deployed_event_topic()]]
    assert set(module.ENZYME_MIGRATION_CHAINS) == {1, 137, 8453, 42161}


def test_enzyme_backfill_retries_hypersync_rate_limit(monkeypatch) -> None:
    """Restart one factory stream after a temporary server rate limit."""

    module = load_backfill_module()
    web3 = MagicMock()
    web3.eth.chain_id = 137
    candidates = [create_candidate("0x000000000000000000000000000000000000bEEF", 35_306_010)]
    rate_limit = RuntimeError("inner receiver\nCaused by:\nrate limited by server (remaining=0/30 reqs, resets_in=3s)")
    calls = []

    async def fake_fetch(*_args):
        await asyncio.sleep(0)
        calls.append(True)
        if len(calls) == 1:
            raise rate_limit
        return module.EnzymeChainDiscovery(candidates, {})

    sleeps = []
    monkeypatch.setattr(module, "fetch_enzyme_chain_discovery_async", fake_fetch)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)

    discovery = module.fetch_enzyme_chain_discovery(web3, 1, 2, attempts=RETRY_ATTEMPTS, retry_sleep=0.25)
    assert discovery.candidates == candidates
    assert len(calls) == RETRY_ATTEMPTS
    assert sleeps == [0.25]


def test_enzyme_backfill_does_not_retry_nonrecoverable_hypersync_error(monkeypatch) -> None:
    """Surface malformed Hypersync data without hiding it behind retries."""

    module = load_backfill_module()
    web3 = MagicMock()
    web3.eth.chain_id = 1
    calls = []

    async def fake_fetch(*_args):
        await asyncio.sleep(0)
        calls.append(True)
        message = "HyperSync response omitted timestamp"
        raise RuntimeError(message)

    monkeypatch.setattr(module, "fetch_enzyme_chain_discovery_async", fake_fetch)
    monkeypatch.setattr(module.time, "sleep", pytest.fail)

    with pytest.raises(RuntimeError, match="omitted timestamp"):
        module.fetch_enzyme_chain_discovery(web3, 1, 2, attempts=3, retry_sleep=0.25)

    assert len(calls) == 1


def test_enzyme_backfill_creates_factory_qualified_lead_and_detection() -> None:
    """Persist the factory provenance and non-ERC-4626 protocol marker."""

    module = load_backfill_module()
    candidate = create_candidate("0x000000000000000000000000000000000000bEEF", 35_306_010)

    lead = module.create_enzyme_lead(candidate)
    detection = module.create_enzyme_detection(candidate, {candidate.address.lower(): module.VaultDepositPermission.whitelisted})

    assert lead.enzyme_factory_candidate == candidate
    assert detection.features == {module.ERC4626Feature.enzyme_onyx_like}
    assert detection.current_deposit_permission == "whitelisted"


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
    checkpoint = {
        "version": module.CHECKPOINT_VERSION,
        "chains": {
            "8453": {
                "end_block": 50_000_000,
                "candidates": [module.serialise_candidate(onyx), module.serialise_candidate(blue)],
                "active_onyx_deposit_handlers": {onyx.address: ["0x0000000000000000000000000000000000000001"]},
                "prices_complete": False,
            }
        },
        "cleaned": False,
    }

    module.write_checkpoint(checkpoint_path, checkpoint)
    loaded = module.read_checkpoint(checkpoint_path)
    candidates = [module.deserialise_candidate(item) for item in loaded["chains"]["8453"]["candidates"]]

    assert candidates == [onyx, blue]
    assert loaded["chains"]["8453"]["active_onyx_deposit_handlers"] == {onyx.address: ["0x0000000000000000000000000000000000000001"]}


def test_enzyme_backfill_retains_checkpoint_before_handler_index(tmp_path: Path) -> None:
    """Preserve completed price state when an old Base checkpoint lacks handlers."""

    module = load_backfill_module()
    onyx = create_candidate("0x000000000000000000000000000000000000bEEF", 35_306_010)
    checkpoint_path = tmp_path / "enzyme-state.json"
    checkpoint = {
        "version": module.CHECKPOINT_VERSION,
        "chains": {
            "8453": {
                "end_block": 50_000_000,
                "candidates": [module.serialise_candidate(onyx)],
                "prices_complete": True,
            }
        },
        "cleaned": False,
    }

    module.write_checkpoint(checkpoint_path, checkpoint)
    loaded = module.read_checkpoint(checkpoint_path)

    assert loaded == checkpoint


def test_enzyme_backfill_holds_shared_pipeline_lock(monkeypatch, tmp_path: Path) -> None:
    """Protect the whole metadata pickle from concurrent scanner replacement."""

    module = load_backfill_module()
    vault_db_path = tmp_path / "vault-metadata-db.pickle"
    lock_calls = []
    migration_calls = []
    monkeypatch.setenv("VAULT_DB_PATH", str(vault_db_path))
    monkeypatch.setenv("PIPELINE_LOCK_TIMEOUT", "12")
    monkeypatch.setattr(module, "_run_migration", lambda: migration_calls.append(True))

    @contextmanager
    def fake_wait_other_writers(path: Path, timeout: float) -> Iterator[None]:
        """Capture the lock target around the mocked migration.

        :param path: Requested shared lock path.
        :param timeout: Requested acquisition timeout.
        :yield: Control to the protected migration body.
        """

        lock_calls.append((path, timeout))
        yield

    monkeypatch.setattr(module, "wait_other_writers", fake_wait_other_writers)

    module.main()

    assert lock_calls == [(tmp_path / "scan-pipeline", 12.0)]
    assert migration_calls == [True]


def test_enzyme_backfill_repairs_unknown_permissions_for_both_architectures(monkeypatch) -> None:
    """Require conclusive current permissions for both Blue and Onyx."""

    module = load_backfill_module()
    monkeypatch.delenv("ENZYME_REFRESH_EXISTING_METADATA", raising=False)
    onyx = create_candidate("0x000000000000000000000000000000000000bEEF", 35_306_010)
    blue = EnzymeBlueVaultFactoryCandidate(
        chain=8453,
        address=HexAddress("0x000000000000000000000000000000000000cafE"),
        dispatcher_address=ENZYME_BLUE_DEPLOYMENTS[8453].dispatcher,
        fund_deployer=HexAddress("0x000000000000000000000000000000000000a11c"),
        vault_accessor=HexAddress("0x000000000000000000000000000000000000acce"),
        fund_name="Permission repair",
        created_block=23_200_000,
        created_at=onyx.created_at,
        transaction_hash="0xbead",
        log_index=3,
    )
    database = module.VaultDatabase(
        rows={
            VaultSpec(blue.chain, blue.address): {
                "Name": "Blue",
                "Symbol": "BLUE",
                "Link": f"https://app.enzyme.finance/vault/{blue.address}?network=base",
                "NAV": 0,
                "Shares": 0,
                "_denomination_token": {"symbol": "USDC"},
                "_fees": SimpleNamespace(fee_mode="internalised"),
                "_deposit_permission": "unknown",
                "_short_description": "Blue summary",
                "_description": "Blue description",
                "_enzyme_metadata_version": module.ENZYME_CURRENT_METADATA_VERSION,
            },
            VaultSpec(onyx.chain, onyx.address): {
                "Name": "Onyx",
                "Symbol": "ONYX",
                "Link": f"https://app.enzyme.finance/vault/{onyx.address}?network=base",
                "NAV": 0,
                "Shares": 0,
                "_denomination_token": {"symbol": "USDC"},
                "_fees": SimpleNamespace(fee_mode="internalised"),
                "_deposit_permission": "unknown",
                "_short_description": "Onyx summary",
                "_description": "Onyx description",
                "_enzyme_metadata_version": module.ENZYME_CURRENT_METADATA_VERSION,
            },
        }
    )

    assert module.should_refresh_metadata(database, blue, METADATA_END_BLOCK) is True
    assert module.should_refresh_metadata(database, onyx, METADATA_END_BLOCK) is True

    database.rows[VaultSpec(blue.chain, blue.address)]["_deposit_permission"] = "permissionless"
    database.rows[VaultSpec(onyx.chain, onyx.address)]["_deposit_permission"] = "whitelisted"
    database.rows[VaultSpec(onyx.chain, onyx.address)]["_enzyme_onyx_permission_version"] = module.ENZYME_ONYX_PERMISSION_VERSION
    assert module.should_refresh_metadata(database, blue, METADATA_END_BLOCK) is False
    assert module.should_refresh_metadata(database, onyx, METADATA_END_BLOCK) is False


def test_enzyme_backfill_repairs_missing_descriptions(monkeypatch) -> None:
    """Use complete descriptions as the resumable current-metadata marker."""

    module = load_backfill_module()
    monkeypatch.delenv("ENZYME_REFRESH_EXISTING_METADATA", raising=False)
    onyx = create_candidate("0x000000000000000000000000000000000000bEEF", 35_306_010)
    spec = VaultSpec(onyx.chain, onyx.address)
    database = module.VaultDatabase(
        rows={
            spec: {
                "Name": "Onyx",
                "Symbol": "ONYX",
                "Link": module.create_enzyme_vault_link(onyx.chain, onyx.address),
                "NAV": 0,
                "Shares": 0,
                "_denomination_token": {"symbol": "USDC"},
                "_fees": SimpleNamespace(fee_mode="internalised"),
                "_deposit_permission": "whitelisted",
                "_enzyme_metadata_version": module.ENZYME_CURRENT_METADATA_VERSION,
                "_enzyme_onyx_permission_version": module.ENZYME_ONYX_PERMISSION_VERSION,
            }
        }
    )

    assert module.should_refresh_metadata(database, onyx, METADATA_END_BLOCK) is True

    database.rows[spec]["_short_description"] = "Onyx summary"
    database.rows[spec]["_description"] = "Onyx description"
    assert module.should_refresh_metadata(database, onyx, METADATA_END_BLOCK) is False


def test_enzyme_backfill_refreshes_only_onyx_permission_semantics(monkeypatch) -> None:
    """Use a protocol-specific marker instead of rereading unaffected Blue rows."""

    module = load_backfill_module()
    monkeypatch.delenv("ENZYME_REFRESH_EXISTING_METADATA", raising=False)
    onyx = create_candidate("0x000000000000000000000000000000000000bEEF", 35_306_010)
    row = {
        "Name": "Onyx",
        "Symbol": "ONYX",
        "_denomination_token": {"symbol": "USDC"},
        "_deposit_permission": "whitelisted",
        "_short_description": "Onyx summary",
        "_description": "Onyx description",
        "_enzyme_metadata_version": module.ENZYME_CURRENT_METADATA_VERSION,
    }
    database = module.VaultDatabase(rows={VaultSpec(onyx.chain, onyx.address): row})

    assert module.should_refresh_metadata(database, onyx, METADATA_END_BLOCK) is True

    row["_enzyme_onyx_permission_version"] = module.ENZYME_ONYX_PERMISSION_VERSION
    assert module.should_refresh_metadata(database, onyx, METADATA_END_BLOCK) is False


def test_enzyme_backfill_refreshes_old_metadata_semantics(monkeypatch) -> None:
    """Refresh otherwise complete rows after fee or permission semantics change."""

    module = load_backfill_module()
    monkeypatch.delenv("ENZYME_REFRESH_EXISTING_METADATA", raising=False)
    onyx = create_candidate("0x000000000000000000000000000000000000bEEF", 35_306_010)
    database = module.VaultDatabase(
        rows={
            VaultSpec(onyx.chain, onyx.address): {
                "Name": "Onyx",
                "Symbol": "ONYX",
                "_denomination_token": {"symbol": "USDC"},
                "_deposit_permission": "whitelisted",
                "_short_description": "Onyx summary",
                "_description": "Onyx description",
                "_enzyme_metadata_version": module.ENZYME_CURRENT_METADATA_VERSION - 1,
                "_enzyme_onyx_permission_version": module.ENZYME_ONYX_PERMISSION_VERSION,
                "_enzyme_metadata_checked_block": METADATA_END_BLOCK,
            }
        }
    )

    assert module.should_refresh_metadata(database, onyx, METADATA_END_BLOCK) is True


def test_enzyme_backfill_does_not_repeat_failed_metadata_at_same_checkpoint(monkeypatch) -> None:
    """Retry an inconclusive row only after a later run advances its fixed head."""

    module = load_backfill_module()
    monkeypatch.delenv("ENZYME_REFRESH_EXISTING_METADATA", raising=False)
    onyx = create_candidate("0x000000000000000000000000000000000000bEEF", 35_306_010)
    spec = VaultSpec(onyx.chain, onyx.address)
    database = module.VaultDatabase(
        rows={
            spec: {
                "Name": "<broken: ContractLogicError>",
                "_enzyme_metadata_checked_block": METADATA_END_BLOCK,
                "_enzyme_metadata_version": module.ENZYME_CURRENT_METADATA_VERSION,
                "_enzyme_onyx_permission_version": module.ENZYME_ONYX_PERMISSION_VERSION,
            }
        }
    )

    assert module.should_refresh_metadata(database, onyx, METADATA_END_BLOCK) is False
    assert module.should_refresh_metadata(database, onyx, METADATA_END_BLOCK + 1) is True


def test_enzyme_backfill_accepts_unavailable_optional_current_values(monkeypatch) -> None:
    """Do not retry deprecated vaults whose current NAV or fees cannot execute."""

    module = load_backfill_module()
    monkeypatch.delenv("ENZYME_REFRESH_EXISTING_METADATA", raising=False)
    onyx = create_candidate("0x000000000000000000000000000000000000bEEF", 35_306_010)
    spec = VaultSpec(onyx.chain, onyx.address)
    database = module.VaultDatabase(
        rows={
            spec: {
                "Name": "Inactive Onyx",
                "Symbol": "ONYX",
                "NAV": None,
                "Shares": 0,
                "_denomination_token": {"symbol": "USDC"},
                "_fees": SimpleNamespace(fee_mode=None),
                "_deposit_permission": "whitelisted",
                "_short_description": "Onyx summary",
                "_description": "Onyx description",
            }
        }
    )

    assert module.has_complete_current_metadata(database, onyx) is True


def test_enzyme_backfill_repairs_generic_listing_links_without_metadata_reads() -> None:
    """Rewrite both architectures locally and keep the operation idempotent."""

    module = load_backfill_module()
    onyx = create_candidate("0x000000000000000000000000000000000000bEEF", 35_306_010)
    blue = EnzymeBlueVaultFactoryCandidate(
        chain=8453,
        address=HexAddress("0x000000000000000000000000000000000000cafE"),
        dispatcher_address=ENZYME_BLUE_DEPLOYMENTS[8453].dispatcher,
        fund_deployer=HexAddress("0x000000000000000000000000000000000000a11c"),
        vault_accessor=HexAddress("0x000000000000000000000000000000000000acce"),
        fund_name="Link repair",
        created_block=23_200_000,
        created_at=onyx.created_at,
        transaction_hash="0xbead",
        log_index=3,
    )
    database = module.VaultDatabase(
        rows={
            VaultSpec(onyx.chain, onyx.address): {
                "Name": "Onyx",
                "Link": "https://app.enzyme.finance/discover/vaults?network=base",
                "_deposit_permission": "unknown",
            },
            VaultSpec(blue.chain, blue.address): {
                "Name": "Blue",
                "Link": "https://app.enzyme.finance/discover/vaults",
                "_deposit_permission": "permissionless",
            },
        }
    )

    assert module.update_enzyme_vault_links(database, [onyx, blue]) == EXPECTED_LINK_UPDATES
    assert database.rows[VaultSpec(onyx.chain, onyx.address)]["Link"] == module.create_enzyme_vault_link(onyx.chain, onyx.address)
    assert database.rows[VaultSpec(blue.chain, blue.address)]["Link"] == module.create_enzyme_vault_link(blue.chain, blue.address)
    assert module.update_enzyme_vault_links(database, [onyx, blue]) == 0


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
