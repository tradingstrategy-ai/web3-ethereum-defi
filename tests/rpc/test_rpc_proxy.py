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
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import requests

from eth_defi.provider.anvil import AnvilLaunch, _create_default_anvil_proxy_config, launch_anvil
from eth_defi.provider.rpc_proxy import (
    TRANSACTION_LOOKUP_METHODS,
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


#: Delay of the slow upstreams started by :func:`fake_upstream`, in seconds.
SLOW_UPSTREAM_DELAY = 3.0

#: Proxy configuration that hedges transaction lookups, as Anvil launches do.
HEDGED_CONFIG = RPCProxyConfig(hedged_methods=TRANSACTION_LOOKUP_METHODS)

#: Unknown transaction hash used for lookups.
UNKNOWN_HASH = "0x" + "ab" * 32


@pytest.fixture()
def fake_upstream() -> Iterator[Callable[[float, bytes], str]]:
    """Start local upstreams that answer every JSON-RPC call with a fixed reply after a delay.

    :return: Factory taking ``(delay_seconds, response_body)`` and returning the upstream URL.
    """
    servers: list[ThreadingHTTPServer] = []

    def start(delay: float, response_body: bytes) -> str:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                time.sleep(delay)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)

            def log_message(self, format: str, *args) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    try:
        yield start
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def _timed_call(proxy_url: str, method: str, params: list | None = None) -> tuple[dict, float]:
    """Make a raw JSON-RPC call and measure its duration.

    :return: ``(response, seconds)``.
    """
    started = time.time()
    return _rpc_call(proxy_url, method, params), time.time() - started


def test_proxy_hedges_transaction_hash_lookups(two_anvil_upstreams: tuple[AnvilLaunch, AnvilLaunch], fake_upstream: Callable[[float, bytes], str]):
    """A hedged hash lookup is answered by the fastest upstream, not the current one.

    1. Start a hedging proxy whose current upstream answers after a delay
    2. Look up an unknown transaction receipt through the proxy
    3. Verify the fast upstream's reply arrives before the slow one could answer
    4. Verify ordinary methods still use the slow current upstream
    """
    anvil_a, _ = two_anvil_upstreams

    # 1. Start a hedging proxy whose current upstream answers after a delay
    slow_url = fake_upstream(SLOW_UPSTREAM_DELAY, b'{"jsonrpc":"2.0","id":1,"result":null}')
    proxy = start_rpc_proxy([slow_url, anvil_a.json_rpc_url], config=HEDGED_CONFIG)
    try:
        # 2. Look up an unknown transaction receipt through the proxy
        result, duration = _timed_call(proxy.url, "eth_getTransactionReceipt", [UNKNOWN_HASH])

        # 3. Verify the fast upstream's reply arrives before the slow one could answer
        assert result["result"] is None
        assert duration < SLOW_UPSTREAM_DELAY - 1
        assert all(stats.method_counts["eth_getTransactionReceipt"] == 1 for stats in proxy.get_stats().values())

        # 4. Verify ordinary methods still use the slow current upstream
        result, duration = _timed_call(proxy.url, "eth_chainId")
        assert result["result"] is None
        assert duration >= SLOW_UPSTREAM_DELAY
    finally:
        proxy.close()


def test_proxy_does_not_hedge_by_default(two_anvil_upstreams: tuple[AnvilLaunch, AnvilLaunch], fake_upstream: Callable[[float, bytes], str]):
    """Without ``hedged_methods`` a hash lookup uses the current upstream only."""
    anvil_a, _ = two_anvil_upstreams
    slow_url = fake_upstream(SLOW_UPSTREAM_DELAY, b'{"jsonrpc":"2.0","id":1,"result":null}')
    proxy = start_rpc_proxy([slow_url, anvil_a.json_rpc_url])
    try:
        _, duration = _timed_call(proxy.url, "eth_getTransactionReceipt", [UNKNOWN_HASH])
        assert duration >= SLOW_UPSTREAM_DELAY
        assert [stats.method_counts.get("eth_getTransactionReceipt", 0) for stats in proxy.get_stats().values()] == [1, 0]
    finally:
        proxy.close()


def test_proxy_hedge_prefers_slow_success_over_fast_error(fake_upstream: Callable[[float, bytes], str]):
    """A fast non-retryable error does not beat a slower successful reply."""
    receipt = b'{"jsonrpc":"2.0","id":1,"result":{"status":"0x1"}}'
    slow_success_url = fake_upstream(1.0, receipt)
    fast_error_url = fake_upstream(0.0, b'{"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"invalid argument"}}')
    proxy = start_rpc_proxy([fast_error_url, slow_success_url], config=HEDGED_CONFIG)
    try:
        result, _ = _timed_call(proxy.url, "eth_getTransactionReceipt", [UNKNOWN_HASH])
        assert result["result"] == {"status": "0x1"}
    finally:
        proxy.close()


def test_proxy_hedging_is_bounded(fake_upstream: Callable[[float, bytes], str]):
    """While the hedging limit is used up, lookups fall back to the normal failover path.

    1. Start a proxy that allows one outstanding hedged request, with a fast and a slow upstream
    2. The first lookup returns fast but leaves its slow attempt running
    3. The second lookup cannot hedge and waits for the current, slow upstream
    """
    null_reply = b'{"jsonrpc":"2.0","id":1,"result":null}'

    # 1. Start a proxy that allows one outstanding hedged request, with a fast and a slow upstream
    config = RPCProxyConfig(hedged_methods=TRANSACTION_LOOKUP_METHODS, max_hedged_requests=1)
    proxy = start_rpc_proxy([fake_upstream(SLOW_UPSTREAM_DELAY, null_reply), fake_upstream(0.0, null_reply)], config=config)
    try:
        # 2. The first lookup returns fast but leaves its slow attempt running
        _, duration = _timed_call(proxy.url, "eth_getTransactionReceipt", [UNKNOWN_HASH])
        assert duration < SLOW_UPSTREAM_DELAY - 1

        # 3. The second lookup cannot hedge and waits for the current, slow upstream
        _, duration = _timed_call(proxy.url, "eth_getTransactionReceipt", [UNKNOWN_HASH])
        assert duration >= SLOW_UPSTREAM_DELAY - 1
    finally:
        proxy.close()


def test_proxy_answers_transaction_lookups_locally(fake_upstream: Callable[[float, bytes], str]):
    """Anvil's fork proxy answers unknown-hash transaction lookups itself, without an upstream round trip.

    Anvil >= 1.8 forwards receipt polls for its own unmined transactions to
    the fork upstream, and a slow provider turned every transaction into a
    multi-second wait. A test's transactions are only mined by the fork, so
    the launch default answers ``null`` locally.

    1. Start a proxy with the Anvil launch default config and a slow upstream
    2. Look up an unknown receipt and transaction through the proxy
    3. Verify both answer ``null`` at once and never reach the upstream
    4. Verify ordinary methods are still forwarded to the upstream
    """
    # 1. Start a proxy with the Anvil launch default config and a slow upstream
    config = _create_default_anvil_proxy_config(1)
    assert config.local_null_methods == TRANSACTION_LOOKUP_METHODS
    assert not config.hedged_methods
    slow_url = fake_upstream(SLOW_UPSTREAM_DELAY, b'{"jsonrpc":"2.0","id":1,"result":"0x1"}')
    proxy = start_rpc_proxy([slow_url], config=config)
    try:
        # 2. Look up an unknown receipt and transaction through the proxy
        receipt, receipt_duration = _timed_call(proxy.url, "eth_getTransactionReceipt", [UNKNOWN_HASH])
        transaction, transaction_duration = _timed_call(proxy.url, "eth_getTransactionByHash", [UNKNOWN_HASH])

        # 3. Verify both answer null at once and never reach the upstream
        assert receipt["result"] is None and transaction["result"] is None
        assert receipt_duration < 1 and transaction_duration < 1
        [stats] = proxy.get_stats().values()
        assert "eth_getTransactionReceipt" not in stats.method_counts
        assert "eth_getTransactionByHash" not in stats.method_counts

        # 4. Verify ordinary methods are still forwarded to the upstream
        result, duration = _timed_call(proxy.url, "eth_chainId")
        assert result["result"] == "0x1"
        assert duration >= SLOW_UPSTREAM_DELAY
        assert stats.method_counts["eth_chainId"] == 1
    finally:
        proxy.close()
