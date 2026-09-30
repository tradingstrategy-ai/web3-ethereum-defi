"""Transport-level parity and physical request budgets for current batches."""

import datetime
import threading
from decimal import Decimal
from types import SimpleNamespace

import eth_abi
import pytest
from requests.exceptions import ConnectionError
from web3 import HTTPProvider, Web3

from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.erc_4626.vault import ERC4626Vault, VaultReaderState
from eth_defi.event_reader import fast_json_rpc, multicall_batcher
from eth_defi.event_reader.multicall_batcher import MULTICALL_DEPLOY_ADDRESS
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory
from eth_defi.provider.rpcdb import RPCRequestStats
from eth_defi.vault import rpc_batch
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.rpc_batch import fetch_batched_tvl_probes, fetch_metadata_snapshots


@pytest.fixture()
def recording_factory(monkeypatch: pytest.MonkeyPatch):
    """Use real Web3/Multicall encoding with an observable fake transport."""
    methods = []
    asset = "0x" + "a" * 40

    def contract_result(address: str, data: bytes) -> tuple[bool, bytes]:
        selector = data[:4]
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
    return factory, stats, methods


def test_tvl_batch_matches_individual_reads_and_reduces_requests(recording_factory) -> None:
    """Forty ordinary TVLs have identical values with one physical eth_call."""
    factory, stats, methods = recording_factory
    web3 = factory()
    token = SimpleNamespace(symbol="USDC", convert_to_decimals=lambda raw: Decimal(raw) / Decimal(1_000_000))
    vaults = [ERC4626Vault(web3, VaultSpec(1, f"0x{number:040x}"), default_block_identifier=20_000_000) for number in range(1, 41)]
    for vault in vaults:
        vault.__dict__["denomination_token"] = token
        vault.get_historical_reader = lambda stateful, vault=vault: SimpleNamespace(reader_state=VaultReaderState(vault))
    individual = {vault.address: vault.fetch_nav() for vault in vaults}
    before = methods.count("eth_call")
    results = list(fetch_batched_tvl_probes(vaults, factory, 20_000_000, max_workers=1))
    assert {vault.address: amount for vault, amount, unknown in results if not unknown} == individual
    assert before == 40
    assert methods.count("eth_call") - before == 1
    assert "eth_getBlockByNumber" not in methods
    assert stats.calls["rpc.example", "eth_call"] == 41


def test_metadata_batches_use_raw_shared_inputs(recording_factory) -> None:
    """Ordinary inputs share batches while specialised chains are excluded."""
    factory, stats, methods = recording_factory
    detections = [ERC4262VaultDetection(chain=1, address=f"0x{number:040x}", first_seen_at_block=1, first_seen_at=datetime.datetime(2026, 1, 1), features=set(), updated_at=datetime.datetime(2026, 9, 30), deposit_count=100, redeem_count=0) for number in range(1, 41)]
    snapshots = fetch_metadata_snapshots(detections, factory, 20_000_000, max_workers=1)
    assert len(snapshots) == 40
    assert all(snapshot["share_reverted"] for snapshot in snapshots.values())
    assert all(snapshot["block"] == 20_000_000 for snapshot in snapshots.values())
    assert methods.count("eth_call") == 4
    assert "eth_getBlockByNumber" not in methods


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
