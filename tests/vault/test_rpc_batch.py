"""Transport-level parity and physical request budgets for current batches."""

import datetime
import threading
from decimal import Decimal
from types import SimpleNamespace

import eth_abi
import pytest
from requests.exceptions import ConnectionError
from web3 import HTTPProvider, Web3

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.scan import ACTIVITY_STATUS_MIN_NAV
from eth_defi.erc_4626.vault import ERC4626Vault, VaultReaderState
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.event_reader import fast_json_rpc, multicall_batcher
from eth_defi.event_reader.multicall_batcher import MULTICALL_DEPLOY_ADDRESS
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory
from eth_defi.provider.rpcdb import RPCRequestStats
from eth_defi.token import TokenDetails, TokenDiskCache
from eth_defi.vault import rpc_batch
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.rpc_batch import fetch_batched_tvl_probes, fetch_metadata_snapshots, prepare_metadata_batch


@pytest.fixture()
def recording_factory(monkeypatch: pytest.MonkeyPatch):
    """Use real Web3/Multicall encoding with an observable fake transport."""
    methods = []
    payload_sizes = []
    asset = "0x" + "a" * 40

    def contract_result(address: str, data: bytes) -> tuple[bool, bytes]:
        selector = data[:4]
        if selector in (Web3.keccak(text="symbol()")[:4], Web3.keccak(text="name()")[:4]):
            return True, eth_abi.encode(["string"], ["USDC"])
        if selector == Web3.keccak(text="decimals()")[:4]:
            return True, eth_abi.encode(["uint256"], [6])
        if selector == Web3.keccak(text="managementFee()")[:4]:
            return True, eth_abi.encode(["uint256"], [2 * 10**16])
        if selector in (Web3.keccak(text="performanceFee()")[:4], Web3.keccak(text="fee()")[:4]):
            return True, eth_abi.encode(["uint256"], [3 * 10**16])
        if selector == Web3.keccak(text="asset()")[:4]:
            return True, eth_abi.encode(["address"], [asset])
        if selector == Web3.keccak(text="share()")[:4]:
            return False, b""
        raw = (int(address, 16) % 40 + 1) * 2_000_000_000
        return True, eth_abi.encode(["uint256"], [raw])

    def request(self, method, params):
        methods.append(method)
        if method == "eth_chainId":
            result = "0x1"
        elif method == "web3_clientVersion":
            result = "geth/test"
        elif method == "eth_blockNumber":
            result = hex(20_000_000)
        elif method == "eth_call":
            transaction = params[0]
            data = bytes.fromhex(transaction["data"][2:])
            if transaction["to"].lower() == MULTICALL_DEPLOY_ADDRESS.lower():
                _, calls = eth_abi.decode(["bool", "(address,bytes)[]"], data[4:])
                payload_sizes.append(len(calls))
                outputs = [contract_result(address, payload) for address, payload in calls]
                result = "0x" + eth_abi.encode(["uint256", "bytes32", "(bool,bytes)[]"], [20_000_000, bytes(32), outputs]).hex()
            else:
                success, payload = contract_result(transaction["to"], data)
                if not success:
                    return {"jsonrpc": "2.0", "id": 1, "error": {"code": 3, "message": "execution reverted"}}
                result = "0x" + payload.hex()
        else:
            raise AssertionError(f"Unexpected physical RPC method: {method}")
        return {"jsonrpc": "2.0", "id": 1, "result": result}

    monkeypatch.setattr(fast_json_rpc, "_make_request", request)
    monkeypatch.setattr(HTTPProvider, "make_request", request)
    monkeypatch.setattr(multicall_batcher, "_reader_instance", threading.local())
    stats = RPCRequestStats(operation="tvl_admission")
    factory = MultiProviderWeb3Factory("https://rpc.example", retries=0, skip_verification=True, expected_chain_id=1, rpc_request_stats=stats)
    factory.recorded_payload_sizes = payload_sizes
    return factory, stats, methods


@pytest.mark.parametrize("batch_size", [None, 7], ids=["default", "explicit-partial-chunk"])
def test_tvl_batch_matches_individual_reads_and_reduces_requests(recording_factory, batch_size: int | None) -> None:
    """TVLs match direct reads using the default or an explicit chunk limit.

    An uneven explicit limit exercises the final partial chunk through real
    Multicall encoding. Request counts prove that the argument controls batching
    without dropping candidates or changing their values.
    """
    factory, stats, methods = recording_factory
    web3 = factory()
    token = SimpleNamespace(symbol="USDC", convert_to_decimals=lambda raw: Decimal(raw) / Decimal(1_000_000))
    vaults = [ERC4626Vault(web3, VaultSpec(1, f"0x{number:040x}"), default_block_identifier=20_000_000) for number in range(1, 41)]
    for vault in vaults:
        vault.__dict__["denomination_token"] = token
        vault.get_historical_reader = lambda stateful, vault=vault: SimpleNamespace(reader_state=VaultReaderState(vault))
    individual = {vault.address: vault.fetch_nav() for vault in vaults}
    before = methods.count("eth_call")
    batch_options = {} if batch_size is None else {"batch_size": batch_size}
    results = list(fetch_batched_tvl_probes(vaults, factory, 20_000_000, max_workers=1, **batch_options))
    expected_requests = 1 if batch_size is None else (len(vaults) + batch_size - 1) // batch_size
    assert {vault.address: amount for vault, amount, unknown in results if not unknown} == individual
    assert before == 40
    assert methods.count("eth_call") - before == expected_requests
    assert "eth_getBlockByNumber" not in methods
    assert stats.calls["rpc.example", "eth_call"] == before + expected_requests


@pytest.mark.parametrize("batch_size", [None, 13], ids=["default", "explicit-split-vault"])
def test_metadata_batches_use_raw_shared_inputs(recording_factory, batch_size: int | None) -> None:
    """Metadata snapshots survive chunk boundaries within a vault's inputs.

    Four inputs per vault cannot align with the explicit 13-subcall limit.
    Exercising real encoding checks that snapshots are assembled correctly
    across chunks and the documented limit counts subcalls rather than vaults.
    """
    factory, stats, methods = recording_factory
    detections = [ERC4262VaultDetection(chain=1, address=f"0x{number:040x}", first_seen_at_block=1, first_seen_at=datetime.datetime(2026, 1, 1), features=set(), updated_at=datetime.datetime(2026, 9, 30), deposit_count=100, redeem_count=0) for number in range(1, 41)]
    batch_options = {} if batch_size is None else {"batch_size": batch_size}
    snapshots = fetch_metadata_snapshots(detections, factory, 20_000_000, max_workers=1, **batch_options)
    assert len(snapshots) == 40
    assert all(snapshot["share_reverted"] for snapshot in snapshots.values())
    assert all(snapshot["block"] == 20_000_000 for snapshot in snapshots.values())
    assert all({"asset", "totalAssets", "totalSupply", "share_reverted", "block"} == snapshot.keys() for snapshot in snapshots.values())
    subcall_count = len(detections) * 4
    expected_requests = 4 if batch_size is None else (subcall_count + batch_size - 1) // batch_size
    assert methods.count("eth_call") == expected_requests
    assert "eth_getBlockByNumber" not in methods


@pytest.mark.parametrize("snapshot_reverted", [False, True])
def test_share_snapshot_does_not_override_an_explicit_historical_block(recording_factory, snapshot_reverted: bool) -> None:
    """A metadata default must not change an explicit historical relationship.

    The transport's ordinary vault reverts share(), while a snapshot may hold
    either a later ERC-7575 token or that revert. Matching reads need no RPC;
    a different source block must execute the contract regardless of the cached
    outcome, because a proxy upgrade can change the share-token relationship.
    """
    factory, _, methods = recording_factory
    vault = ERC4626Vault(factory(), VaultSpec(1, "0x" + "1" * 40), default_block_identifier=20_000_000)
    share_token = "0x" + "b" * 40
    vault._rpc_metadata_snapshot = {"block": 20_000_000, **({"share_reverted": True} if snapshot_reverted else {"share": eth_abi.encode(["address"], [share_token])})}
    expected = vault.vault_address if snapshot_reverted else Web3.to_checksum_address(share_token)
    before = methods.count("eth_call")
    assert vault.fetch_share_token_address() == expected
    assert vault.fetch_share_token_address(20_000_000) == expected
    assert methods.count("eth_call") == before
    assert vault.fetch_share_token_address(19_999_999) == vault.vault_address
    assert methods.count("eth_call") == before + 1


def test_current_probe_refreshes_numeric_hyperevm_block(recording_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    """A delayed metadata batch refreshes its numeric block before execution."""
    factory, stats, methods = recording_factory
    call = multicall_batcher.EncodedCall.from_keccak_signature(address="0x" + "1" * 40, function="totalAssets", signature=Web3.keccak(text="totalAssets()")[:4], data=b"", extra_data={})
    result = list(multicall_batcher.read_multicall_chunked(1, factory, [call], block_identifier=19_000_000, max_workers=1, timestamped_results=False, backend="threading", refresh_current_block=True))
    assert result[0].block_identifier == 19_999_990
    assert methods.count("eth_blockNumber") == 1
    assert "eth_getBlockByNumber" not in methods


def test_idle_lending_snapshot_reuses_assets_and_balance(recording_factory) -> None:
    """Morpho/IPOR economics match legacy reads using one idle balance call."""
    factory, _, methods = recording_factory
    web3 = factory()
    vault = ERC4626Vault(web3, VaultSpec(1, "0x" + "2" * 40), default_block_identifier=20_000_000)
    denomination = Web3.to_checksum_address("0x" + "a" * 40)
    token = SimpleNamespace(convert_to_raw=lambda amount: int(amount * Decimal(1_000_000)), convert_to_decimals=lambda raw: Decimal(raw) / Decimal(1_000_000), contract=web3.eth.contract(address=denomination, abi=[{"name": "balanceOf", "type": "function", "inputs": [{"name": "owner", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"}]))
    vault.__dict__["denomination_token"] = token
    before = methods.count("eth_call")
    total_assets = Decimal(100_000)
    liquidity, utilisation = vault.fetch_idle_lending_snapshot(total_assets, 20_000_000)
    expected_idle = (int(denomination, 16) % 40 + 1) * 2_000_000_000
    assert liquidity == token.convert_to_decimals(expected_idle)
    assert utilisation == (token.convert_to_raw(total_assets) - expected_idle) / token.convert_to_raw(total_assets)
    assert methods.count("eth_call") - before == 1


@pytest.mark.parametrize("failure", [ConnectionError("provider unavailable"), ExtraValueError({"code": -32090, "message": "request rejected"})])
def test_admission_batch_transport_failure_preserves_candidates(recording_factory, monkeypatch: pytest.MonkeyPatch, failure: BaseException) -> None:
    """A failed shared admission read defers candidates without aborting the chain."""

    factory, _, _ = recording_factory
    vault = ERC4626Vault(factory(), VaultSpec(1, "0x" + "1" * 40))
    vault.__dict__["denomination_token"] = SimpleNamespace(symbol="USDC")
    vault.get_historical_reader = lambda stateful: SimpleNamespace(reader_state=VaultReaderState(vault))

    def fail_batch(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(rpc_batch, "read_multicall_chunked", fail_batch)
    assert list(fetch_batched_tvl_probes([vault], factory, 20_000_000, max_workers=1)) == [(vault, None, False)]


def test_specialised_admission_failure_keeps_other_candidates(recording_factory) -> None:
    """A HyperEVM candidate outage does not discard another successful probe."""

    factory, _, _ = recording_factory
    vaults = [ERC4626Vault(factory(), VaultSpec(999, "0x" + str(number) * 40)) for number in (1, 2)]
    for vault in vaults:
        vault.__dict__["denomination_token"] = SimpleNamespace(symbol="USDC")
        vault.get_historical_reader = lambda stateful, vault=vault: SimpleNamespace(reader_state=VaultReaderState(vault))

    def unavailable_nav() -> Decimal:
        raise ConnectionError("provider unavailable")

    vaults[0].fetch_nav = unavailable_nav
    vaults[1].fetch_nav = lambda: Decimal(2000)
    results = list(fetch_batched_tvl_probes(vaults, factory, 20_000_000, max_workers=2))
    assert results == [(vaults[0], None, False), (vaults[1], Decimal(2000), False)]


def test_process_task_retains_explicit_operation_label(recording_factory) -> None:
    """A process-style task uses probe provenance instead of its factory label."""
    factory, parent, _ = recording_factory
    parent.operation = "discovery_preparation"
    probes = RPCRequestStats(operation="feature_probe")
    call = multicall_batcher.EncodedCall.from_keccak_signature(address="0x" + "1" * 40, function="totalAssets", signature=Web3.keccak(text="totalAssets()")[:4], data=b"", extra_data={})
    results = list(multicall_batcher.read_multicall_chunked(1, factory, [call], block_identifier=20_000_000, max_workers=1, timestamped_results=False, backend="loky", rpc_request_stats=probes))
    assert len(results) == 1
    assert probes.operation_calls["feature_probe", "rpc.example", "eth_call"] == 1
    assert probes.operation_calls["discovery_preparation", "rpc.example", "eth_call"] == 0


@pytest.mark.parametrize("batch_size", [40, 100])
def test_prepared_metadata_batches_fees_idle_and_warmup(recording_factory, tmp_path, batch_size: int) -> None:
    """Prepared inputs retain provenance and eliminate per-vault lending reads.

    Warm caches isolate the economic-input budget; a separate assertion bounds
    every cold-cache request through the same configured batch size.
    """
    factory, _, methods = recording_factory
    now = datetime.datetime(2026, 1, 1)
    detections = [ERC4262VaultDetection(chain=1, address=f"0x{number:040x}", first_seen_at_block=1, first_seen_at=now, features={ERC4626Feature.morpho_v2_like}, updated_at=now, deposit_count=100, redeem_count=0) for number in range(1, 41)]
    cache = TokenDiskCache(tmp_path / "tokens.sqlite")
    try:
        prepare_metadata_batch(detections, factory, 20_000_000, 1, cache, min_lending_nav=ACTIVITY_STATUS_MIN_NAV, batch_size=batch_size)
        assert max(factory.recorded_payload_sizes) <= batch_size
        before = methods.count("eth_call")
        snapshots = prepare_metadata_batch(detections, factory, 20_000_000, 1, cache, min_lending_nav=ACTIVITY_STATUS_MIN_NAV, batch_size=batch_size)
        assert methods.count("eth_call") - before == (280 + batch_size - 1) // batch_size + 1
        assert sum("idle_balance" in snapshot for snapshot in snapshots.values()) == 38
        detection = detections[3]
        vault = MorphoV2Vault(factory(), VaultSpec(1, detection.address), default_block_identifier=20_000_000, token_cache=cache)
        snapshot = snapshots[detection.address.lower()]
        vault._rpc_metadata_snapshot = snapshot
        before = methods.count("eth_call")
        assets = vault.fetch_total_assets(20_000_000)
        assert methods.count("eth_call") == before, "assets triggered direct read"
        idle, utilisation = vault.fetch_lending_snapshot(assets, 20_000_000)
        assert methods.count("eth_call") == before, "idle triggered direct read"
        fees = vault.get_fee_data(20_000_000)
        assert idle is not None and utilisation is not None
        assert fees.management == pytest.approx(0.02)
        assert fees.performance == pytest.approx(0.03)
        assert methods.count("eth_call") == before
        assert vault._get_metadata_input("performanceFee", 19_999_999) is None
        assert vault._get_metadata_input("performanceFee", "latest") is None
        vault._rpc_metadata_snapshot = {**snapshot, "chain": 42161}
        assert vault._get_metadata_input("performanceFee", 20_000_000) is None
    finally:
        cache.close()


@pytest.mark.parametrize("decimals", [0, 6])
def test_preparation_reuses_zero_decimals_as_unavailable(recording_factory, tmp_path, decimals: int) -> None:
    """Cached zero decimals skip new enrichment without a cache schema change."""
    factory, _, _ = recording_factory
    now = datetime.datetime(2026, 1, 1)
    detection = ERC4262VaultDetection(chain=1, address="0x" + "4" * 40, first_seen_at_block=1, first_seen_at=now, features={ERC4626Feature.morpho_like}, updated_at=now, deposit_count=100, redeem_count=0)
    cache = TokenDiskCache(tmp_path / "tokens.sqlite")
    asset = "0x" + "a" * 40
    try:
        cache[TokenDetails.generate_cache_key(1, asset)] = {"address": asset, "symbol": "USDC", "name": "USDC", "decimals": decimals, "supply": 0}
        snapshot = prepare_metadata_batch([detection], factory, 20_000_000, 1, cache, min_lending_nav=ACTIVITY_STATUS_MIN_NAV)[detection.address.lower()]
        assert ("idle_balance" in snapshot) == (decimals > 0)
    finally:
        cache.close()


@pytest.mark.parametrize("batch_size,expected", [(40, 25), (100, 10)])
def test_regular_batch_physical_budget_and_worker_reuse(recording_factory, batch_size: int, expected: int) -> None:
    """One thousand real encoded subcalls use the configured physical budget.

    Reusing the same factory at another size must not retain the old reader
    limit, including a rollback from 100 to 40.
    """
    factory, _, methods = recording_factory
    calls = [multicall_batcher.EncodedCall.from_keccak_signature(address=f"0x{number:040x}", function="totalAssets", signature=Web3.keccak(text="totalAssets()")[:4], data=b"", extra_data={}) for number in range(1, 1001)]
    for size in (100, batch_size, 40):
        before = methods.count("eth_call")
        factory.recorded_payload_sizes.clear()
        results = list(multicall_batcher.read_multicall_chunked(1, factory, calls, 20_000_000, max_workers=1, chunk_size=size, batch_size=size, backend="threading", timestamped_results=False))
        assert len(results) == len(calls)
        assert [r.call.address for r in results] == [c.address for c in calls]
        assert methods.count("eth_call") - before == (len(calls) + size - 1) // size
        assert max(factory.recorded_payload_sizes) == size
        if size == batch_size:
            assert methods.count("eth_call") - before == expected


@pytest.mark.parametrize("chain_id,requested,expected", [(1, 100, 100), (5000, 100, 16), (100, 100, 16), (999, 100, 40), (5000, 7, 7), (999, 7, 7)])
def test_regular_chain_caps_honour_smaller_overrides(chain_id: int, requested: int, expected: int) -> None:
    """Apply only known constraints without overriding a smaller caller limit."""
    reader = object.__new__(multicall_batcher.MultiprocessMulticallReader)
    reader.batch_size = requested
    assert reader.get_batch_size(chain_id) == expected
