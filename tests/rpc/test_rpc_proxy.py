"""Test JSON-RPC failover proxy.

Tests the :py:mod:`eth_defi.provider.rpc_proxy` module using two
non-fork Anvil instances as upstream providers.

To run::

    pytest tests/rpc/test_rpc_proxy.py -v
"""

import logging
import shutil

import pytest
import requests

from eth_defi.provider.anvil import launch_anvil, AnvilLaunch
from eth_defi.provider.rpc_proxy import (
    RPCProxy,
    default_failure_handler,
    start_rpc_proxy,
)


pytestmark = pytest.mark.skipif(
    shutil.which("anvil") is None,
    reason="Install anvil to run these tests",
)


@pytest.fixture()
def two_anvil_upstreams() -> tuple[AnvilLaunch, AnvilLaunch]:
    """Start two independent non-fork Anvil instances as upstream RPCs."""
    anvil_a = launch_anvil()
    anvil_b = launch_anvil()
    try:
        yield anvil_a, anvil_b
    finally:
        anvil_a.close()
        anvil_b.close()


def _rpc_call(proxy_url: str, method: str, params: list | None = None) -> dict:
    """Make a raw JSON-RPC call to a URL."""
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or [],
        "id": 1,
    }
    resp = requests.post(proxy_url, json=payload, timeout=10)
    return resp.json()


def test_proxy_forwards_request(two_anvil_upstreams: tuple[AnvilLaunch, AnvilLaunch]):
    """Proxy forwards a simple eth_blockNumber call and returns a valid response.

    1. Start proxy with two upstream Anvil instances
    2. Make an eth_blockNumber call through the proxy
    3. Verify a valid hex block number is returned
    """
    anvil_a, anvil_b = two_anvil_upstreams

    # 1. Start proxy
    proxy = start_rpc_proxy([anvil_a.json_rpc_url, anvil_b.json_rpc_url])
    try:
        # 2. Make a call
        result = _rpc_call(proxy.url, "eth_blockNumber")

        # 3. Verify valid response
        assert "result" in result
        assert result["result"].startswith("0x")
        assert int(result["result"], 16) >= 0
    finally:
        proxy.close()


def test_proxy_failover_on_dead_upstream(two_anvil_upstreams: tuple[AnvilLaunch, AnvilLaunch]):
    """When one upstream is dead, the proxy fails over to the other.

    1. Start proxy with two upstream Anvil instances
    2. Kill the first Anvil
    3. Make a call and verify it succeeds via the second Anvil
    4. Check statistics show failure on the dead provider
    """
    anvil_a, anvil_b = two_anvil_upstreams

    # 1. Start proxy
    proxy = start_rpc_proxy(
        [anvil_a.json_rpc_url, anvil_b.json_rpc_url],
        timeout=3.0,
    )
    try:
        # 2. Kill first Anvil
        anvil_a.close()

        # 3. Make a call — should succeed via second Anvil
        result = _rpc_call(proxy.url, "eth_blockNumber")
        assert "result" in result
        assert result["result"].startswith("0x")

        # 4. Check statistics
        stats = proxy.get_stats()
        stat_values = list(stats.values())
        total_failures = sum(s.failure_count for s in stat_values)
        total_requests = sum(s.request_count for s in stat_values)
        assert total_failures >= 1, f"Expected at least 1 failure, got stats: {stats}"
        assert total_requests >= 2, f"Expected at least 2 requests (1 failed + 1 success), got stats: {stats}"
    finally:
        proxy.close()


def test_proxy_all_upstreams_fail(two_anvil_upstreams: tuple[AnvilLaunch, AnvilLaunch]):
    """When all upstreams are dead, proxy returns a 502 JSON-RPC error.

    1. Start proxy with two upstream Anvil instances
    2. Kill both Anvils
    3. Make a call and verify a JSON-RPC error response
    """
    anvil_a, anvil_b = two_anvil_upstreams

    # 1. Start proxy
    proxy = start_rpc_proxy(
        [anvil_a.json_rpc_url, anvil_b.json_rpc_url],
        timeout=2.0,
        retries=2,
        backoff=0.1,
    )
    try:
        # 2. Kill both Anvils
        anvil_a.close()
        anvil_b.close()

        # 3. Make a call — should get error
        result = _rpc_call(proxy.url, "eth_blockNumber")
        assert "error" in result
        assert result["error"]["code"] == -32603
        assert "All upstream providers failed" in result["error"]["message"]
    finally:
        proxy.close()


def test_proxy_auto_switch(two_anvil_upstreams: tuple[AnvilLaunch, AnvilLaunch]):
    """Auto-switch distributes requests across providers.

    1. Start proxy with auto_switch_request_count=3
    2. Make 6+ requests
    3. Verify both providers received requests via statistics
    """
    anvil_a, anvil_b = two_anvil_upstreams

    # 1. Start proxy with auto-switch after 3 requests
    proxy = start_rpc_proxy(
        [anvil_a.json_rpc_url, anvil_b.json_rpc_url],
        auto_switch_request_count=3,
    )
    try:
        # 2. Make 7 requests
        for _ in range(7):
            result = _rpc_call(proxy.url, "eth_blockNumber")
            assert "result" in result

        # 3. Check both providers got requests
        stats = proxy.get_stats()
        stat_values = list(stats.values())
        assert len(stat_values) == 2
        # Both should have at least 1 request
        assert stat_values[0].request_count >= 1, f"Provider 0 got no requests: {stat_values[0]}"
        assert stat_values[1].request_count >= 1, f"Provider 1 got no requests: {stat_values[1]}"
        # Total should be 7
        assert sum(s.request_count for s in stat_values) == 7
    finally:
        proxy.close()


def test_proxy_statistics_tracking(two_anvil_upstreams: tuple[AnvilLaunch, AnvilLaunch]):
    """Statistics track request counts and method breakdowns correctly.

    1. Start proxy
    2. Make several calls with different methods
    3. Verify request_count, method_counts via get_stats()
    """
    anvil_a, anvil_b = two_anvil_upstreams

    # 1. Start proxy
    proxy = start_rpc_proxy([anvil_a.json_rpc_url, anvil_b.json_rpc_url])
    try:
        # 2. Make calls
        _rpc_call(proxy.url, "eth_blockNumber")
        _rpc_call(proxy.url, "eth_blockNumber")
        _rpc_call(proxy.url, "eth_chainId")

        # 3. Verify statistics
        stats = proxy.get_stats()
        total_requests = sum(s.request_count for s in stats.values())
        assert total_requests == 3

        # All method_counts across providers should add up
        all_methods: dict[str, int] = {}
        for s in stats.values():
            for m, c in s.method_counts.items():
                all_methods[m] = all_methods.get(m, 0) + c
        assert all_methods.get("eth_blockNumber", 0) == 2
        assert all_methods.get("eth_chainId", 0) == 1
    finally:
        proxy.close()


def test_proxy_close_logs_stats(two_anvil_upstreams: tuple[AnvilLaunch, AnvilLaunch], caplog):
    """close() logs per-provider statistics summary.

    1. Start proxy and make a request
    2. Close the proxy
    3. Verify statistics were logged
    """
    anvil_a, anvil_b = two_anvil_upstreams

    # 1. Start proxy and make a request
    proxy = start_rpc_proxy([anvil_a.json_rpc_url, anvil_b.json_rpc_url])
    _rpc_call(proxy.url, "eth_blockNumber")

    # 2. Close and capture logs
    with caplog.at_level(logging.INFO, logger="eth_defi.provider.rpc_proxy"):
        proxy.close()

    # 3. Verify log output
    assert "shutting down" in caplog.text
    assert "requests" in caplog.text


def test_default_failure_handler():
    """default_failure_handler correctly classifies responses.

    1. Test HTTP 429 (retryable) returns True
    2. Test HTTP 200 with valid result returns False
    3. Test HTTP 200 with JSON-RPC error code -32603 returns True
    4. Test HTTP 200 with retryable error message returns True
    """
    # 1. Retryable HTTP status
    assert default_failure_handler(429, None) is True
    assert default_failure_handler(502, None) is True

    # 2. Success
    assert default_failure_handler(200, {"jsonrpc": "2.0", "result": "0x1", "id": 1}) is False

    # 3. Retryable JSON-RPC error code
    assert default_failure_handler(200, {"jsonrpc": "2.0", "error": {"code": -32603, "message": "internal"}, "id": 1}) is True

    # 4. Retryable error message
    assert default_failure_handler(200, {"jsonrpc": "2.0", "error": {"code": -99999, "message": "nonce too low"}, "id": 1}) is True

    # Non-retryable error
    assert default_failure_handler(200, {"jsonrpc": "2.0", "error": {"code": -32600, "message": "invalid request"}, "id": 1}) is False
