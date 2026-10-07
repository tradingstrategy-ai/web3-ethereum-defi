"""Offline JSON-RPC accounting tests for block timestamp readers."""

import datetime
import threading
from pathlib import Path

import pandas as pd
import pytest
from web3 import HTTPProvider

from eth_defi.event_reader import multicall_timestamp
from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase
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
    expected_timestamp = 1_767_225_600
    web3 = CountingWeb3()
    monkeypatch.setattr(multicall_timestamp, "_timestamp_instance", threading.local())

    def fetch_timestamp(counting_web3: CountingWeb3, _block_number: int, raw: object) -> int:
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


def test_rpc_timestamp_cache_fills_old_gaps_on_the_requested_grid(tmp_path: Path) -> None:
    """Sparse cache endpoints and nearby blocks do not establish exact coverage.

    Use real Anvil block headers and a file-backed cache containing future and
    off-grid entries. The first scan fills historical gaps, a repeat reads no
    headers, and a shifted sampling grid fetches only its own missing blocks.

    :param tmp_path: Isolated persistent timestamp-cache directory.
    :return: None; checks exact stored headers, cache reuse and physical RPC counts.
    """
    anvil = launch_anvil()
    timestamps = None
    cache = None
    try:
        provider = HTTPProvider(anvil.json_rpc_url)
        provider.make_request("anvil_mine", ["0x40"])
        exact_21 = int(provider.make_request("eth_getBlockByNumber", ["0x15", False])["result"]["timestamp"], 16)
        cache = BlockTimestampDatabase.create(31337, tmp_path)
        seeded = {0: 1, 2: 2, 12: 12, 21: exact_21, 64: 64}
        cache.import_chain_data(31337, pd.Series(seeded))
        cache.close()
        cache = None
        stats = RPCRequestStats()
        factory = MultiProviderWeb3Factory(anvil.json_rpc_url, retries=0, skip_verification=True, expected_chain_id=31337, rpc_request_stats=stats)
        domain = get_url_domain(anvil.json_rpc_url)
        for start, end, expected_calls in [(1, 31, 3), (1, 31, 3), (2, 32, 5)]:
            timestamps = multicall_timestamp.fetch_block_timestamps_multiprocess_auto_backend(chain_id=31337, web3factory=factory, start_block=start, end_block=end, step=10, display_progress=False, max_workers=2, cache_path=tmp_path, rpc_request_stats=stats)
            exact_blocks = timestamps.timestamp_db.query(start, end)
            assert set(range(start, end + 1, 10)).issubset(exact_blocks.index)
            assert timestamps.timestamp_db.query(0, 64).loc[list(seeded)].to_dict() == {block: datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).replace(tzinfo=None) for block, epoch in seeded.items()}
            calls, errors = stats.export()
            assert calls[domain, "eth_getBlockByNumber"] == expected_calls
            assert not errors
            timestamps.close()
            timestamps = None
    finally:
        if timestamps is not None:
            timestamps.close()
        if cache is not None:
            cache.close()
        anvil.close()
