"""Offline JSON-RPC accounting tests for block timestamp readers."""

import datetime
import threading
from pathlib import Path

import pytest
from web3 import HTTPProvider

from eth_defi.event_reader import multicall_timestamp
from eth_defi.provider.anvil import launch_anvil
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory
from eth_defi.provider.rpcdb import RPCRequestStats
from eth_defi.utils import get_url_domain


class CountingEth:
    """Minimal Web3 ``eth`` facade that records its chain-id read."""

    def __init__(self, web3: "CountingWeb3") -> None:
        self.web3 = web3

    @property
    def chain_id(self) -> int:
        """Return the test chain and count the physical request."""

        self.web3.rpc_request_stats.record_call("rpc.example", "eth_chainId")
        return 1


class CountingWeb3:
    """Minimal cached worker Web3 supporting accumulator attachment."""

    def __init__(self) -> None:
        self.rpc_request_stats: RPCRequestStats | None = None
        self.eth = CountingEth(self)

    def set_rpc_request_stats(self, stats: RPCRequestStats | None) -> None:
        """Attach the current subprocess task's accumulator."""

        self.rpc_request_stats = stats


def test_timestamp_worker_returns_and_detaches_rpc_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful timestamp task returns calls and detaches cached Web3."""

    tested_block = 100
    expected_timestamp = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc).replace(tzinfo=None)
    web3 = CountingWeb3()
    monkeypatch.setattr(multicall_timestamp, "_timestamp_instance", threading.local())

    def fetch_timestamp(counting_web3: CountingWeb3, _block_number: int, raw: object) -> datetime.datetime:
        """Stand in for the physical block request."""

        assert raw is True
        counting_web3.rpc_request_stats.record_call("rpc.example", "eth_getBlockByNumber")
        return expected_timestamp

    monkeypatch.setattr(multicall_timestamp, "get_block_timestamp", fetch_timestamp)

    block_number, timestamp, stats = multicall_timestamp._read_timestamp_subprocess(
        web3factory=lambda: web3,
        chain_id=1,
        block_number=tested_block,
        collect_rpc_request_stats=True,
    )

    calls, errors = stats.export()
    assert block_number == tested_block
    assert timestamp == expected_timestamp
    assert calls == {
        ("rpc.example", "eth_chainId"): 1,
        ("rpc.example", "eth_getBlockByNumber"): 1,
    }
    assert errors == {}
    assert web3.rpc_request_stats is None


def test_hypersync_timestamp_path_does_not_receive_rpc_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    """The HyperSync timestamp backend stays outside JSON-RPC accounting."""

    expected = object()

    def fetch_hypersync_timestamps(**kwargs: object) -> object:
        """Verify the optimised backend receives no RPC accumulator."""

        assert "rpc_request_stats" not in kwargs
        return expected

    monkeypatch.setattr(
        "eth_defi.hypersync.hypersync_timestamp.fetch_block_timestamps_using_hypersync_cached",
        fetch_hypersync_timestamps,
    )

    result = multicall_timestamp.fetch_block_timestamps_multiprocess_auto_backend(
        chain_id=1,
        web3factory=lambda: None,
        start_block=100,
        end_block=101,
        step=1,
        display_progress=False,
        hypersync_client=object(),
        rpc_request_stats=RPCRequestStats(),
    )

    assert result is expected


def test_timestamp_loky_tasks_merge_exact_physical_calls(tmp_path: Path) -> None:
    """Several real process tasks return one block call each to the parent."""

    expected_block_calls = 3
    anvil = launch_anvil()
    timestamps = None
    try:
        HTTPProvider(anvil.json_rpc_url).make_request("anvil_mine", ["0x3"])
        provider_domain = get_url_domain(anvil.json_rpc_url)
        stats = RPCRequestStats()
        web3factory = MultiProviderWeb3Factory(
            anvil.json_rpc_url,
            retries=0,
            skip_verification=True,
            expected_chain_id=31337,
            rpc_request_stats=stats,
        )

        timestamps = multicall_timestamp.fetch_block_timestamps_multiprocess(
            chain_id=31337,
            web3factory=web3factory,
            start_block=0,
            end_block=2,
            step=1,
            display_progress=False,
            max_workers=2,
            cache_path=tmp_path,
            rpc_request_stats=stats,
        )

        calls, errors = stats.export()
        assert calls[provider_domain, "eth_getBlockByNumber"] == expected_block_calls
        assert errors == {}
    finally:
        if timestamps is not None:
            timestamps.close()
        anvil.close()
