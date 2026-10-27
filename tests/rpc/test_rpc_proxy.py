"""Test JSON-RPC failover proxy.

Tests the :py:mod:`eth_defi.provider.rpc_proxy` module using two
non-fork Anvil instances as upstream providers.

To run::

    pytest tests/rpc/test_rpc_proxy.py -v
"""

import logging
import shutil
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import requests

from eth_defi.provider.anvil import AnvilLaunch, launch_anvil
from eth_defi.provider.rpc_proxy import (
    RPCProxyConfig,
    _ProxyRequestHandler,  # noqa: PLC2701
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


def _make_disconnecting_handler(*, suppress_client_disconnect_errors: bool) -> _ProxyRequestHandler:
    """Create a request handler that raises when writing a response."""

    handler = object.__new__(_ProxyRequestHandler)
    handler.server = SimpleNamespace(
        config=RPCProxyConfig(suppress_client_disconnect_errors=suppress_client_disconnect_errors),
        proxy_name="test-rpc-proxy",
    )
    handler.close_connection = False

    def raise_broken_pipe(*_args: object, **_kwargs: object) -> None:
        """Simulate an already-closed downstream client socket."""

        message = "client disconnected"
        raise BrokenPipeError(message)

    handler.send_response = raise_broken_pipe
    return handler


def test_proxy_response_write_reraises_client_disconnect_by_default() -> None:
    """Direct proxy users see downstream client disconnects by default.

    1. Create a handler with disconnect suppression disabled
    2. Simulate a downstream client disconnect while sending a response
    3. Assert the original ``BrokenPipeError`` is still raised
    """

    handler = _make_disconnecting_handler(suppress_client_disconnect_errors=False)

    with pytest.raises(BrokenPipeError):
        handler._send_json_response(200, b"{}")


def test_proxy_response_write_can_suppress_client_disconnect() -> None:
    """Anvil-managed proxy mode can suppress normal teardown disconnects.

    1. Create a handler with disconnect suppression enabled
    2. Simulate a downstream client disconnect while sending a response
    3. Assert the response is reported as unwritten without a traceback
    """

    handler = _make_disconnecting_handler(suppress_client_disconnect_errors=True)

    assert handler._send_json_response(200, b"{}") is False
    assert handler.close_connection is True


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


def test_proxy_answers_anvil_probe_methods_locally(two_anvil_upstreams: tuple[AnvilLaunch, AnvilLaunch]):
    """Anvil 1.8 fork probes get a local -32601 and never reach an upstream.

    1. Start a proxy with two upstream Anvil instances
    2. Call eth_getAccountInfo and anvil_nodeInfo through the proxy
    3. Verify both answer -32601 and the upstream statistics recorded no such request
    """
    anvil_a, anvil_b = two_anvil_upstreams

    # 1. Start a proxy with two upstream Anvil instances
    proxy = start_rpc_proxy([anvil_a.json_rpc_url, anvil_b.json_rpc_url])
    try:
        # 2. Call eth_getAccountInfo and anvil_nodeInfo through the proxy
        for method in ("eth_getAccountInfo", "anvil_nodeInfo", "eth_getBlockAccessList"):
            result = _rpc_call(proxy.url, method, ["0x0000000000000000000000000000000000000001", "latest"])

            # 3. Verify both answer -32601 and the upstream statistics recorded no such request
            assert result["error"]["code"] == -32601
            assert method in result["error"]["message"]
            assert all(stats.method_counts.get(method, 0) == 0 for stats in proxy.get_stats().values())

        # Ordinary methods still reach the upstreams
        assert _rpc_call(proxy.url, "eth_blockNumber")["result"].startswith("0x")
    finally:
        proxy.close()


#: Delay of the slow upstream in :func:`slow_upstream_url`, in seconds.
SLOW_UPSTREAM_DELAY = 3.0


class _SlowNullHandler(BaseHTTPRequestHandler):
    """Upstream that answers every JSON-RPC call with ``null`` after a delay."""

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        time.sleep(SLOW_UPSTREAM_DELAY)
        body = b'{"jsonrpc":"2.0","id":1,"result":null}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        pass


@pytest.fixture()
def slow_upstream_url() -> Iterator[str]:
    """Start a local upstream that answers ``null`` after :data:`SLOW_UPSTREAM_DELAY`.

    :return: Upstream URL.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowNullHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_proxy_hedges_transaction_hash_lookups(two_anvil_upstreams: tuple[AnvilLaunch, AnvilLaunch], slow_upstream_url: str):
    """A hash lookup is answered by the fastest upstream, not the current one.

    1. Start a proxy whose current upstream answers after a delay
    2. Look up an unknown transaction receipt through the proxy
    3. Verify the fast upstream's reply arrives before the slow one could answer
    4. Verify ordinary methods still use the slow current upstream
    """
    anvil_a, _ = two_anvil_upstreams

    # 1. Start a proxy whose current upstream answers after a delay
    proxy = start_rpc_proxy([slow_upstream_url, anvil_a.json_rpc_url])
    try:
        # 2. Look up an unknown transaction receipt through the proxy
        started = time.time()
        result = _rpc_call(proxy.url, "eth_getTransactionReceipt", ["0x" + "ab" * 32])

        # 3. Verify the fast upstream's reply arrives before the slow one could answer
        assert result["result"] is None
        assert time.time() - started < SLOW_UPSTREAM_DELAY - 1
        assert all(stats.method_counts["eth_getTransactionReceipt"] == 1 for stats in proxy.get_stats().values())

        # 4. Verify ordinary methods still use the slow current upstream
        started = time.time()
        assert _rpc_call(proxy.url, "eth_chainId")["result"] is None
        assert time.time() - started >= SLOW_UPSTREAM_DELAY
    finally:
        proxy.close()
