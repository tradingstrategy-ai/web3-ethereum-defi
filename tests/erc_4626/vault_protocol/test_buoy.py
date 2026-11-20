"""Buoy published accounting, optional enrichment and onchain integration."""

import datetime
import json
import os
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import requests
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import create_vault_scan_record, fetch_deposit_permission
from eth_defi.erc_4626.hypersync_discovery import HypersyncVaultDiscover
from eth_defi.erc_4626.discovery_base import LeadScanReport, PotentialVaultMatch, _prepare_probe_leads
from eth_defi.erc_4626.vault_protocol.buoy.constants import BUOY_FACTORY, BUOY_CREATED_TOPICS
from eth_defi.erc_4626.vault_protocol.buoy import offchain_metadata
from eth_defi.erc_4626.vault_protocol.buoy.discovery import fetch_buoy_implementation, fetch_buoy_vaults
from eth_defi.erc_4626.vault_protocol.buoy.vault import SNAPSHOT_FIELDS, BuoyAccounting, BuoyVault
from eth_defi.event_reader.multicall_batcher import EncodedCall, EncodedCallResult
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.historical import VaultHistoricalReadMulticaller
from eth_defi.vault.base import VaultHistoricalRead, VaultSpec
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.fee import VaultFeeMode
from eth_defi.research import vault_metrics
from eth_defi.research.vault_metrics import calculate_lifetime_metrics, export_lifetime_row

ADDRESS = HexAddress("0x87f76a7e6c346904192c884d48c02d3ac640de1e")
BLOCK = 51_248_351


@pytest.fixture(autouse=True)
def clear_metadata_memory() -> None:
    """Keep clock and failure-cooldown tests independent.

    Disk caches are isolated with pytest temporary directories.
    """
    offchain_metadata._memory.clear()


def test_buoy_accounting_subtracts_obligations_once() -> None:
    """Pending deposits do not enter cached NAV or unburnt-share equity.

    The snapshot also verifies exact 1e18 price scaling.
    """
    snapshot = BuoyAccounting.from_values([2 * 10**18, 200, 100, 250, 1000, 50, 80, 1000])
    assert snapshot.assets == 200
    with pytest.raises(ValueError, match="Inconsistent"):
        BuoyAccounting.from_values([2 * 10**18, 150, 100, 250, 1000, 50, 80, 1000])


def test_buoy_zero_is_not_a_failed_read() -> None:
    """A published total loss is valid with non-zero share supply.

    Bootstrap prices are separately recognised when supply is zero.
    """
    assert BuoyAccounting.from_values([0, 0, 100, 0, 1000, 0, 0, 0]).price == 0
    assert BuoyAccounting.from_values([10**18, 0, 0, 0, 0, 0, 0, 0]).supply == 0


def test_metadata_failure_preserves_cache_and_original_fetch_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed and incomplete refreshes never replace known-good descriptions.

    The retry cooldown expires independently of the last successful fetch.
    """
    now = datetime.datetime(2026, 11, 1)
    raw = {"address": ADDRESS, "chainId": 999, "description": "First sentence. Second sentence.", "tvl": "999999999", "performanceFeeBps": 9999}
    response = Mock()
    response.json.return_value = {"items": [raw], "page": {"offset": 0, "hasMore": False}}
    get = Mock(return_value=response)
    monkeypatch.setattr(offchain_metadata.requests, "get", get)
    result = offchain_metadata.fetch_buoy_vaults_for_chain(999, cache_path=tmp_path, now_=now)
    assert result[ADDRESS]["short_description"] == "First sentence."
    assert "tvl" not in result[ADDRESS] and "performanceFeeBps" not in result[ADDRESS]
    file = next(tmp_path.glob("*.json"))
    saved = file.read_bytes()
    get.side_effect = requests.Timeout("provider timeout")
    assert offchain_metadata.fetch_buoy_vaults_for_chain(999, cache_path=tmp_path, now_=now + datetime.timedelta(days=1)) == result
    assert file.read_bytes() == saved
    assert offchain_metadata.fetch_buoy_vaults_for_chain(999, cache_path=tmp_path, now_=now + datetime.timedelta(days=1, minutes=5)) == result
    assert get.call_count == 2
    offchain_metadata.fetch_buoy_vaults_for_chain(999, cache_path=tmp_path, now_=now + datetime.timedelta(days=1, hours=1))
    assert get.call_count == 3


@pytest.mark.parametrize(
    "page",
    [
        {"items": [], "page": {"offset": 0, "hasMore": False}},
        {"items": [{"address": ADDRESS, "chainId": 1}], "page": {"offset": 0, "hasMore": False}},
        {"items": [{"address": ADDRESS, "chainId": 999}], "page": {"offset": 1, "hasMore": False}},
    ],
)
def test_metadata_invalid_page_does_not_poison_cache(page: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty, cross-chain and malformed pagination responses remain unavailable.

    Cold-start failures cannot create a successful empty cache.

    :param page: Invalid provider response.
    :param tmp_path: Isolated cache directory.
    :param monkeypatch: HTTP replacement.
    """
    response = Mock()
    response.json.return_value = page
    monkeypatch.setattr(offchain_metadata.requests, "get", Mock(return_value=response))
    assert offchain_metadata.fetch_buoy_vaults_for_chain(999, cache_path=tmp_path) == {}
    assert not tuple(tmp_path.glob("*.json"))


def test_nav_schema_addition_preserves_old_rows() -> None:
    """Schema migration adds nullable provenance without discarding old values.

    An unchanged legacy row has no implied NAV publication time.
    """
    table = pa.table({"chain": pa.array([999], type=pa.uint32()), "address": [ADDRESS], "block_number": pa.array([BLOCK], type=pa.uint64()), "share_price": [1.25]})
    migrated = VaultHistoricalRead.migrate_parquet_schema(table)
    assert migrated["nav_timestamp"].to_pylist() == [None]
    assert migrated["share_price"].to_pylist() == [1.25]
    assert len(migrated) == len(table)


@pytest.fixture
def live_web3() -> Web3:
    """Use the supplied provider for genuine block-pinned contract reads.

    No fork or transactions are needed for the storage-only read surface.

    :return: Configured HyperEVM connection, or skip without credentials.
    """
    rpc = os.environ.get("JSON_RPC_HYPERLIQUID")
    if not rpc:
        pytest.skip("JSON_RPC_HYPERLIQUID is required")
    return create_multi_provider_web3(rpc)


def test_buoy_real_registry_and_published_accounting(live_web3: Web3, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Discover without the API and read a fixed historical published valuation.

    The known contract NAV is substantially older than the live website quote;
    the latter must never replace it. This also exercises the shared scanner.

    :param live_web3: Real supplied archive provider.
    :param tmp_path: Isolated metadata cache.
    :param monkeypatch: Disable optional API enrichment.
    """
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.buoy.vault.fetch_buoy_vault_metadata", lambda *args, **kwargs: None)
    addresses = tuple(fetch_buoy_vaults(live_web3, BLOCK))
    assert len(addresses) == 60
    assert ADDRESS in addresses
    vault = create_vault_instance(live_web3, ADDRESS, features={ERC4626Feature.buoy_like}, default_block_identifier=BLOCK)
    assert isinstance(vault, BuoyVault)
    assert vault.implementation == "0x27cd383395f366be39b5a38a08dc98775d665bb0"
    accounting = vault.fetch_accounting(BLOCK)
    assert accounting == BuoyAccounting(1000000000689655172, 1450000001, 1450000000, 1450000001, 1786965119, 0, 0, 1000)
    assert vault.get_deposit_manager_capability() is None
    assert vault.get_deposit_manager() is None
    assert not vault.is_whitelisted_deposit()
    reader = vault.get_historical_reader(stateful=False)
    timestamp = datetime.datetime.fromtimestamp(live_web3.eth.get_block(BLOCK)["timestamp"], datetime.UTC).replace(tzinfo=None)
    calls = list(reader.construct_multicalls())
    results = [EncodedCallResult(call=call, success=True, result=bytes(live_web3.eth.call({"to": call.address, "data": call.data}, block_identifier=BLOCK)), block_identifier=BLOCK, timestamp=timestamp) for call in calls]
    row = reader.process_result(BLOCK, timestamp, results)
    assert row.share_price == Decimal("1.000000000689655172")
    assert row.total_assets == Decimal("1450.000001")
    assert row.nav_timestamp == datetime.datetime(2026, 8, 17, 11, 11, 59)
    assert row.export()["nav_timestamp"] == row.nav_timestamp
    detection = ERC4262VaultDetection(chain=999, address=ADDRESS, features={ERC4626Feature.buoy_like}, first_seen_at_block=1, first_seen_at=row.nav_timestamp, updated_at=timestamp, deposit_count=0, redeem_count=0)
    record = create_vault_scan_record(live_web3, detection, BLOCK, token_cache={})
    assert record["Protocol"] == "Buoy"
    assert record["_nav_timestamp"] == row.nav_timestamp
    assert record["_deposit_manager"] is None
    assert record["_description"] is None
    assert record["_deposit_permission"] == "permissionless"
    assert record["_fees"].fee_mode == VaultFeeMode.internalised_minting
    assert record["_fees"].protocol == 0.0

    # Use the real onchain scanner fee record through the actual metrics/JSON
    # path. The fixture supplies synthetic price history, not Buoy's returns.
    prices = pd.read_parquet(Path(__file__).parents[2] / "research" / "chain-hemi-prices-1h.parquet")
    prices = prices.loc[prices["id"] == "43111-0x614eb485de3c6c49701b40806ac1b985ad6f0a2f"].copy()
    prices.index = prices.index + (pd.Timestamp(timestamp) - prices.index.max())
    prices["id"] = vault.get_spec().as_string_id()
    prices["chain"] = 999
    prices["address"] = ADDRESS
    prices["nav_timestamp"] = prices.index
    monkeypatch.setattr(vault_metrics, "native_datetime_utc_now", lambda: timestamp)
    metrics = calculate_lifetime_metrics(prices, {vault.get_spec(): record}).iloc[0]
    exported = export_lifetime_row(metrics)
    expected_gross = {"fee_mode": "internalised_minting", "management": 0.0, "performance": 0.1, "deposit": 0.0, "withdraw": 0.0, "protocol": 0.0}
    assert exported["gross_fees"] == expected_gross
    assert exported["net_fees"] == expected_gross | {"performance": 0.0}
    assert exported["mgmt_fee"] == exported["management_fee"] == 0.0
    assert exported["perf_fee"] == exported["performance_fee"] == 0.1
    assert exported["deposit_fee"] == exported["withdraw_fee"] == 0.0
    assert exported["fee_mode"] == "internalised_minting"
    assert exported["fee_internalised"] is True
    assert exported["fee_label"]
    valid_periods = [period for period in metrics["period_results"] if period.returns_gross is not None]
    assert valid_periods
    assert all(period.returns_net == period.returns_gross for period in valid_periods)
    json.dumps(exported, allow_nan=False)


def test_buoy_real_public_metadata(tmp_path: Path) -> None:
    """Read the actual public metadata API without credentials.

    This focused real-provider test complements cache fixtures and does not
    compare live economic values or assert a changing vault count.

    :param tmp_path: Isolated cache directory.
    """
    metadata = offchain_metadata.fetch_buoy_vaults_for_chain(999, cache_path=tmp_path)
    assert ADDRESS in metadata
    assert metadata[ADDRESS]["description"]
    assert "\n" not in metadata[ADDRESS]["short_description"]


@pytest.mark.skipif(not os.environ.get("BUOY_PRODUCTION_PARQUET"), reason="BUOY_PRODUCTION_PARQUET must name a downloaded production copy")
def test_buoy_schema_migration_production_copy() -> None:
    """Validate every production row while adding nullable NAV provenance.

    This manual full-data check streams a downloaded copy, never opens the
    production writer, and treats NaN values as equal. It verifies all original
    columns, not just the count of synthetic rows.
    """
    path = Path(os.environ["BUOY_PRODUCTION_PARQUET"])
    parquet = pq.ParquetFile(path)
    count = 0
    for batch in parquet.iter_batches(batch_size=250_000):
        original = pa.Table.from_batches([batch])
        migrated = VaultHistoricalRead.migrate_parquet_schema(original)
        assert len(migrated) == len(original)
        assert migrated["nav_timestamp"].null_count == len(migrated)
        for name in original.schema.names:
            assert original[name].to_pandas().equals(migrated[name].to_pandas()), name
        count += len(migrated)
    assert count == parquet.metadata.num_rows


@pytest.mark.parametrize(
    "address, implementation",
    [
        ("0x8e6d89c4e5c8d32532efb10d133a1f5edc9010b4", "0x3fe85a627dcc8ff6896b1376beda7b1493f2c538"),
        ("0x754eaf74dee8daf61e75fe778007202f8fd8f9f6", "0xe412625761b8256832a018765dcf2ef8e7b0397b"),
        ("0xa6854fcf2e69e4e73ce377bc9d02a26e4d44e839", "0x47d0e7aca4246da0334f97d6840cf8559d771663"),
    ],
)
def test_buoy_real_additional_generations(live_web3: Web3, address: str, implementation: str) -> None:
    """Exercise each additional verified accounting generation at one block.

    Immutable clone targets and source-backed integer identities are fixed;
    optional v3 admission is read rather than inferred from the API.

    :param live_web3: Real supplied provider.
    :param address: Reviewed vault clone.
    :param implementation: Exact expected implementation.
    """
    vault = BuoyVault(live_web3, VaultSpec(999, address), features={ERC4626Feature.buoy_like}, default_block_identifier=BLOCK)
    assert vault.implementation == implementation
    snapshot = vault.fetch_accounting(BLOCK)
    assert snapshot.assets == max(snapshot.gross_nav - snapshot.owed, 0)
    assert vault.share_token.decimals == vault.denomination_token.decimals == 6
    assert vault.is_whitelisted_deposit() is False
    fees = vault.get_fee_data(BLOCK)
    expected = {
        "0x3fe85a627dcc8ff6896b1376beda7b1493f2c538": (0.05, 0.0),
        "0xe412625761b8256832a018765dcf2ef8e7b0397b": (0.01, 0.001),
        "0x47d0e7aca4246da0334f97d6840cf8559d771663": (0.1, 0.01),
    }
    assert (fees.performance, fees.protocol) == expected[implementation]
    assert fees.management == fees.deposit == fees.withdraw == 0.0
    assert fees.fee_mode == VaultFeeMode.internalised_minting


def test_buoy_rejects_unreviewed_or_non_clone_code() -> None:
    """Reject contracts that only imitate the public snapshot selector.

    A minimal clone with an unknown implementation is also unsupported.
    """
    web3 = Mock()
    web3.eth.get_code.return_value = b"\x60\x00"
    with pytest.raises(ValueError, match="minimal proxy"):
        fetch_buoy_implementation(web3, ADDRESS)
    web3.eth.get_code.return_value = bytes.fromhex("363d3d373d3d3d363d73" + "11" * 20 + "5af43d82803e903d91602b57fd5bf3")
    with pytest.raises(ValueError, match="Unreviewed"):
        fetch_buoy_implementation(web3, ADDRESS)


def test_buoy_policy_failure_is_not_permissionless(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed v3 whitelist read is exported as unknown admission.

    The fixed policy pointer and policy response are separate reads; caps do
    not determine whether identity approval is required.

    :param monkeypatch: Replace only the policy contract binding.
    """
    web3 = Mock()
    vault = BuoyVault(web3, VaultSpec(999, ADDRESS), features={ERC4626Feature.buoy_like}, default_block_identifier=BLOCK)
    vault.__dict__["implementation"] = "0x47d0e7aca4246da0334f97d6840cf8559d771663"
    contract = Mock()
    contract.functions.policy.return_value.call.return_value = "0x" + "11" * 20
    vault.__dict__["vault_contract"] = contract
    policy = Mock()
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.buoy.vault.get_deployed_contract", lambda *args: policy)
    policy.functions.configOf.return_value.call.return_value = (0, True, 0)
    assert fetch_deposit_permission(vault).value == "whitelisted"
    policy.functions.configOf.return_value.call.return_value = (5000, False, 100)
    assert fetch_deposit_permission(vault).value == "permissionless"
    policy.functions.configOf.return_value.call.side_effect = requests.Timeout("unavailable policy")
    assert fetch_deposit_permission(vault).value == "unknown"


def test_buoy_factory_events_both_versions_and_spoof_rejection() -> None:
    """Both official creation signatures seed leads without ERC-4626 flows.

    A matching topic emitted outside the registry cannot create a Buoy lead.
    """
    scanner = object.__new__(HypersyncVaultDiscover)
    scanner.buoy_factories = (BUOY_FACTORY,)
    scanner.buoy_vaults = {ADDRESS}
    report = LeadScanReport(backend=scanner)
    leads = {}
    for topic in BUOY_CREATED_TOPICS:
        log = SimpleNamespace(address=BUOY_FACTORY, topics=[topic, "0x" + "00" * 12 + ADDRESS[2:], "0x" + "00" * 32], block_number=100)
        scanner.process_log(report, leads, {}, 999, log, datetime.datetime(2026, 8, 1), set())
    assert len(leads) == 1
    assert leads[ADDRESS].buoy_factory == BUOY_FACTORY
    assert leads[ADDRESS].is_candidate()
    addresses, by_address, count = _prepare_probe_leads(leads)
    assert addresses == []
    assert count == 1
    assert ADDRESS in by_address
    scanner.buoy_vaults.clear()
    with pytest.raises(ValueError, match="not registered"):
        scanner.process_log(report, {}, {}, 999, log, datetime.datetime(2026, 8, 1), set())


def test_buoy_metadata_incomplete_refresh_keeps_whole_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A later failed page cannot publish a truncated catalogue.

    Even a valid first page does not advance the successful-cache timestamp
    until all pages have been validated.

    :param tmp_path: Isolated disk cache.
    :param monkeypatch: Replace HTTP responses only.
    """
    now = datetime.datetime(2026, 11, 1)
    response = Mock()
    response.json.return_value = {"items": [{"address": ADDRESS, "chainId": 999, "description": "Original description."}], "page": {"offset": 0, "hasMore": False}}
    get = Mock(return_value=response)
    monkeypatch.setattr(offchain_metadata.requests, "get", get)
    original = offchain_metadata.fetch_buoy_vaults_for_chain(999, cache_path=tmp_path, now_=now)
    saved = next(tmp_path.glob("*.json")).read_bytes()
    response.json.return_value["page"]["hasMore"] = True
    get.side_effect = [response, requests.Timeout()]
    assert offchain_metadata.fetch_buoy_vaults_for_chain(999, cache_path=tmp_path, now_=now + datetime.timedelta(days=2)) == original
    assert next(tmp_path.glob("*.json")).read_bytes() == saved


def test_buoy_spot_strategy_does_not_inherit_perpetual_tag() -> None:
    """Spot accumulation has address-specific tags, without a venue default.

    Unknown registrations retain the explicit missing-information result.
    """
    web3 = Mock()
    spot = BuoyVault(web3, VaultSpec(999, "0x6e8075cf5f840067a3d82453677db2c0d88db51e"))
    assert StrategyTag.directional_trading in spot.get_strategy_tags()
    assert StrategyTag.perpetual_futures not in spot.get_strategy_tags()
    unknown = BuoyVault(web3, VaultSpec(999, "0x" + "11" * 20))
    assert unknown.get_strategy_tags() is None


def test_buoy_handwritten_copy_precedes_optional_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reviewed manual descriptions do not disappear during an API outage.

    This uses the same maintained-copy precedence as IPOR and other adapters.

    :param monkeypatch: Supply a handwritten record and forbid HTTP enrichment.
    """
    web3 = Mock()
    vault = BuoyVault(web3, VaultSpec(999, ADDRESS))
    copy = SimpleNamespace(description="Manually reviewed strategy.", short_description="Reviewed strategy.")
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.buoy.vault.get_handwritten_vault_metadata", lambda *args: copy)
    api = Mock(side_effect=AssertionError("Manual metadata must not require the API"))
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.buoy.vault.fetch_buoy_vault_metadata", api)
    assert vault.description == copy.description
    assert vault.short_description == copy.short_description
    api.assert_not_called()


def test_buoy_valid_json_but_corrupt_cache_is_not_silently_refreshed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cached records cannot inject economics or bypass identity validation.

    Corrupt disk state is surfaced before HTTP replacement, preserving the
    original file for diagnosis rather than erasing persisted descriptions.

    :param tmp_path: Isolated metadata cache.
    :param monkeypatch: Replace HTTP with a known complete listing.
    """
    response = Mock()
    response.json.return_value = {"items": [{"address": ADDRESS, "chainId": 999}], "page": {"offset": 0, "hasMore": False}}
    get = Mock(return_value=response)
    monkeypatch.setattr(offchain_metadata.requests, "get", get)
    offchain_metadata.fetch_buoy_vaults_for_chain(999, cache_path=tmp_path)
    path = next(tmp_path.glob("*.json"))
    saved = json.loads(path.read_text())
    saved["vaults"][ADDRESS]["tvl"] = "123456"
    path.write_text(json.dumps(saved))
    before = path.read_bytes()
    offchain_metadata._memory.clear()
    with pytest.raises(ValueError, match="cached record"):
        offchain_metadata.fetch_buoy_vaults_for_chain(999, cache_path=tmp_path)
    assert path.read_bytes() == before
    assert get.call_count == 1


@pytest.mark.parametrize("fault", ["missing", "revert", "mixed_block", "future_nav", "identity", "fee"])
def test_invalid_buoy_observation_does_not_advance_chain_reader(fault: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Defer one invalid observation while continuing with a healthy neighbour.

    Exercise the real decoder and shared consumer, without network reads. A
    later successful observation must remain eligible for the unchanged state.

    :param fault: Required-call or published-accounting failure.
    :param monkeypatch: Replace reader preparation only.
    :return: None after checking source history and state remain unclaimed.
    """
    timestamp = datetime.datetime(2026, 11, 14)
    vault = BuoyVault(Mock(), VaultSpec(999, ADDRESS), features={ERC4626Feature.buoy_like})
    vault.__dict__["denomination_token"] = Mock(symbol="USDC", convert_to_decimals=lambda value: Decimal(value) / 10**6)
    vault.__dict__["share_token"] = Mock(symbol="USDC", convert_to_decimals=lambda value: Decimal(value) / 10**6)
    reader = vault.get_historical_reader(stateful=True)
    neighbour = BuoyVault(Mock(), VaultSpec(999, "0x" + "33" * 20), features={ERC4626Feature.buoy_like})
    neighbour.__dict__["denomination_token"] = vault.denomination_token
    neighbour.__dict__["share_token"] = vault.share_token
    healthy_reader = neighbour.get_historical_reader(stateful=True)
    values = [10**18, 100, 100, 100, int(timestamp.replace(tzinfo=datetime.UTC).timestamp()), 0, 0, 1000]
    healthy_values = values.copy()
    if fault == "future_nav":
        values[4] += 100
    elif fault == "identity":
        values[1] = 99
    elif fault == "fee":
        values[-1] = 5001
    results = [EncodedCallResult(EncodedCall(name, ADDRESS, b"", extra_data={"vault": ADDRESS, "function": name}), True, value.to_bytes(32, "big"), BLOCK, timestamp) for name, value in zip(SNAPSHOT_FIELDS, values, strict=True)]
    if fault == "missing":
        results.pop()
    elif fault == "revert":
        results[0].success = False
    elif fault == "mixed_block":
        results[0].block_identifier += 1
    if fault == "mixed_block":
        # The shared transport rejects mislabelled blocks before dispatch. Test
        # the adapter's own validation without weakening that global invariant.
        row = reader.process_result(BLOCK, timestamp, results)
        assert row.share_price is None and row.errors
        assert reader.reader_state.last_block is None
        return
    healthy_results = [EncodedCallResult(EncodedCall(name, neighbour.address, b"", extra_data={"vault": neighbour.address, "function": name}), True, value.to_bytes(32, "big"), BLOCK, timestamp) for name, value in zip(SNAPSHOT_FIELDS, healthy_values, strict=True)]
    scanner = VaultHistoricalReadMulticaller(web3factory=None, supported_quote_tokens=None)
    monkeypatch.setattr(scanner, "prepare_readers", lambda *args, **kwargs: {ADDRESS: reader, neighbour.address: healthy_reader})
    monkeypatch.setattr(scanner, "generate_vault_historical_calls", lambda *args: ())

    def source(**kwargs: object) -> Iterator[SimpleNamespace]:
        """Supply the malformed observation through the normal consumer."""
        yield SimpleNamespace(block_number=BLOCK, timestamp=timestamp, results=results + healthy_results)

    before = reader.reader_state.last_block
    observed = list(scanner.read_historical([vault, neighbour], BLOCK, BLOCK + 1, 1, reader_func=source))
    assert [row.vault for row in observed] == [neighbour]
    assert healthy_reader.reader_state.last_block == BLOCK
    assert reader.reader_state.last_block == before
    assert reader.reader_state.rpc_error_count == 1
    assert scanner.unavailable_observations == 1
    assert ADDRESS not in scanner.latest_observed_at


def test_buoy_factory_lead_reaches_detection_without_generic_probing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Carry an official factory lead through the full classification boundary.

    Buoy's non-standard execution surface must never depend on a successful
    generic ERC-4626 probe or invented deposit counts.

    :param monkeypatch: Supply indexed leads and inspect probe inputs.
    :return: None after checking the canonical detection and creation boundary.
    """
    scanner = HypersyncVaultDiscover(Mock(), None, None)
    scanner.web3.eth.chain_id = 999
    created_at = datetime.datetime(2026, 8, 1)
    lead = PotentialVaultMatch(chain=999, address=ADDRESS, first_seen_at_block=100, first_seen_at=created_at, deposit_count=0, withdrawal_count=0, buoy_factory=BUOY_FACTORY)
    report = LeadScanReport(backend=scanner, leads={ADDRESS: lead})
    monkeypatch.setattr(scanner, "clip_end_block_to_available_height", lambda start, end: end)
    monkeypatch.setattr(scanner, "fetch_leads", lambda *args: report)
    probe = Mock(return_value=iter(()))
    monkeypatch.setattr("eth_defi.erc_4626.discovery_base.probe_vaults", probe)
    result = scanner.scan_vaults(100, 200, display_progress=False, hardcoded_lead_sources=())
    assert probe.call_args.args[2] == []
    detection = result.detections[ADDRESS]
    assert detection.features == {ERC4626Feature.buoy_like}
    assert detection.first_seen_at_block == 100
    assert detection.first_seen_at == created_at
    assert detection.deposit_count == detection.redeem_count == 0


def test_buoy_registry_keeps_retired_factories_and_deduplicates_vaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retain reviewed factories after Router rotation without API discovery.

    Current and legacy registry lists can overlap. Every unique official
    factory is queried at the same supplied block and duplicate vault entries
    across those factories produce one identity.

    :param monkeypatch: Replace onchain contract bindings with fixed registries.
    :return: None after checking the independent onchain inventory.
    """
    web3 = Mock()
    web3.eth.chain_id = 999
    current = "0x" + "44" * 20
    legacy = "0x" + "55" * 20
    router = Mock()
    router.functions.factory.return_value.call.return_value = current
    router.functions.legacyFactoriesCount.return_value.call.return_value = 2
    router.functions.legacyFactories.side_effect = lambda index: SimpleNamespace(call=lambda **kwargs: (legacy, current)[index])
    factory = Mock()
    factory.functions.vaultsCount.return_value.call.return_value = 1
    factory.functions.allVaults.return_value.call.return_value = [ADDRESS]
    bound = []

    def bind(connection: Web3, abi: str, address: HexAddress) -> Mock:
        """Retain the binding order for current, legacy and retired factories."""
        if abi.endswith("Router.json"):
            return router
        bound.append(address)
        return factory

    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.buoy.discovery.get_deployed_contract", bind)
    assert tuple(fetch_buoy_vaults(web3, BLOCK)) == (ADDRESS,)
    assert bound == [BUOY_FACTORY, current, legacy]
    assert factory.functions.vaultsCount.return_value.call.call_args.kwargs == {"block_identifier": BLOCK}
    assert factory.functions.allVaults.return_value.call.call_args.kwargs == {"block_identifier": BLOCK}


@pytest.mark.parametrize("tvl, expected_frequency", [(500, "small_tvl"), (499, "faded")])
def test_buoy_small_vault_polling_threshold(tvl: int, expected_frequency: str) -> None:
    """Keep a $500 prop trading vault active after its traction window.

    Actual reader state receives two genuine observations; sub-threshold
    vaults still fade rather than becoming permanently active.

    :param tvl: Observed USDC NAV throughout the traction period.
    :param expected_frequency: Normal daily or faded polling classification.
    :return: None after checking the adaptive reader's policy.
    """
    vault = BuoyVault(Mock(), VaultSpec(999, ADDRESS), features={ERC4626Feature.buoy_like})
    vault.__dict__["denomination_token"] = Mock(symbol="USDC")
    reader = vault.get_historical_reader(stateful=True)
    state = reader.reader_state
    origin = datetime.datetime(2026, 8, 1)
    for block, timestamp in ((1, origin), (2, origin + state.traction_period + datetime.timedelta(days=1))):
        state.on_called(SimpleNamespace(block_identifier=block, timestamp=timestamp), total_assets=Decimal(tvl), share_price=Decimal(1))
    assert state.get_frequency()[0] == expected_frequency
    assert state.min_tvl_threshold == state.tiny_tvl_threshold_rare_read == Decimal(500)


@pytest.mark.parametrize("implementation, expected_protocol", [("0x27cd383395f366be39b5a38a08dc98775d665bb0", 0.0), ("0x3fe85a627dcc8ff6896b1376beda7b1493f2c538", 0.0), ("0xe412625761b8256832a018765dcf2ef8e7b0397b", 0.01), ("0x47d0e7aca4246da0334f97d6840cf8559d771663", 0.01)])
def test_buoy_fee_breakdown_is_block_aligned_and_included(implementation: str, expected_protocol: float) -> None:
    """Expose complete fees with the referral cut inside the total performance fee.

    Direct adapter construction without discovery flags must still classify
    the fee mode. The aggregate rate remains 10%, never 11% after the split.

    :param implementation: Reviewed generation determining split availability.
    :param expected_protocol: Protocol cut on the new-profit basis.
    :return: None after checking canonical fees and source-block alignment.
    """
    web3 = Mock()
    web3.eth.get_block.return_value = {"number": BLOCK}
    vault = BuoyVault(web3, VaultSpec(999, ADDRESS), default_block_identifier=BLOCK)
    vault.__dict__["implementation"] = implementation
    contract = Mock()
    contract.functions.performanceFeeBps.return_value.call.return_value = 1000
    contract.functions.protocolFeeShareBps.return_value.call.return_value = 1000
    contract.functions.protocolFeeRecipient.return_value.call.return_value = "0x" + "11" * 20
    vault.__dict__["vault_contract"] = contract
    fees = vault.get_fee_data("latest")
    assert fees.fee_mode == VaultFeeMode.internalised_minting
    assert fees.management == fees.deposit == fees.withdraw == 0.0
    assert fees.performance == 0.1
    assert fees.protocol == expected_protocol
    assert fees.get_net_fees().performance == fees.get_net_fees().protocol == 0.0
    assert contract.functions.performanceFeeBps.return_value.call.call_args.kwargs == {"block_identifier": BLOCK}
    if expected_protocol:
        assert contract.functions.protocolFeeShareBps.return_value.call.call_args.kwargs == {"block_identifier": BLOCK}
        assert contract.functions.protocolFeeRecipient.return_value.call.call_args.kwargs == {"block_identifier": BLOCK}
    else:
        contract.functions.protocolFeeShareBps.assert_not_called()


def test_buoy_referral_read_failure_cannot_become_zero() -> None:
    """Keep a failed onchain split read unavailable instead of inventing a fee.

    The aggregate performance read can succeed while the required split fails;
    a partial schedule cannot be presented as complete data.

    :return: None after checking propagation of the provider failure.
    """
    vault = BuoyVault(Mock(), VaultSpec(999, ADDRESS), default_block_identifier=BLOCK)
    vault.__dict__["implementation"] = "0xe412625761b8256832a018765dcf2ef8e7b0397b"
    contract = Mock()
    contract.functions.performanceFeeBps.return_value.call.return_value = 1000
    contract.functions.protocolFeeShareBps.return_value.call.side_effect = requests.Timeout("source unavailable")
    vault.__dict__["vault_contract"] = contract
    with pytest.raises(requests.Timeout):
        vault.get_fee_data(BLOCK)
