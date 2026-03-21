"""JSON-RPC failover proxy for multiple upstream RPC providers.

A lightweight threaded HTTP proxy that presents a single JSON-RPC endpoint
while internally routing requests across multiple upstream RPC providers
with automatic failover, retry, and per-provider statistics.

The primary use case is Anvil mainnet forks: Anvil accepts only a single
``--fork-url`` and has no internal retry or failover logic. When the upstream
RPC is slow or rate-limited, Anvil hangs indefinitely. This proxy sits
between Anvil and the upstream RPCs, transparently handling failures.

However the proxy is general-purpose and can be used with any software
that needs a single RPC URL backed by multiple upstreams.

Example usage::

    from eth_defi.provider.rpc_proxy import start_rpc_proxy

    proxy = start_rpc_proxy(
        [
            "https://rpc-provider-a.example.com",
            "https://rpc-provider-b.example.com",
        ]
    )
    print(f"Proxy listening at {proxy.url}")

    # Pass proxy.url to Anvil, or any other JSON-RPC client
    # ...

    # When done, shut down and see statistics
    proxy.close()

See :py:func:`eth_defi.provider.anvil.launch_anvil` for automatic integration.
"""

import datetime
import logging
import socketserver
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, TypeAlias

import orjson
import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError, Timeout

from eth_defi.middleware import (
    DEFAULT_RETRYABLE_HTTP_STATUS_CODES,
    DEFAULT_RETRYABLE_RPC_ERROR_CODES,
    DEFAULT_RETRYABLE_RPC_ERROR_MESSAGES,
)
from eth_defi.utils import find_free_port, get_url_domain, is_localhost_port_listening

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Failure detection type
# ---------------------------------------------------------------------------

#: Type alias for the failure detection callback.
#:
#: Receives the HTTP status code and the parsed JSON-RPC response body
#: (or ``None`` if the body could not be parsed as JSON).
#: Returns ``True`` if the response indicates a failure that should
#: trigger a retry on the next provider.
#:
#: See :py:func:`default_failure_handler` for the built-in implementation.
FailureHandler: TypeAlias = Callable[[int, dict | None], bool]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RPCProxyConfig:
    """Configuration for the JSON-RPC failover proxy.

    Collects all tuneable parameters of :py:func:`start_rpc_proxy` into a
    single object with sensible defaults. Every field has a docstring
    explaining its purpose, default value, and interaction with other fields.

    You can construct this directly and pass it to :py:func:`start_rpc_proxy`,
    or pass it as the ``proxy_multiple_upstream`` argument to
    :py:func:`~eth_defi.provider.anvil.launch_anvil`.

    Example — standalone proxy with custom configuration::

        from eth_defi.provider.rpc_proxy import RPCProxyConfig, start_rpc_proxy

        config = RPCProxyConfig(
            timeout=15.0,
            retries=5,
            auto_switch_request_count=50,
        )
        proxy = start_rpc_proxy(
            ["https://rpc-a.example.com", "https://rpc-b.example.com"],
            config=config,
        )

    Example — passing to ``launch_anvil``::

        from eth_defi.provider.anvil import launch_anvil
        from eth_defi.provider.rpc_proxy import RPCProxyConfig

        config = RPCProxyConfig(timeout=10.0, retries=4)
        launch = launch_anvil(
            fork_url="https://rpc-a.example.com https://rpc-b.example.com",
            proxy_multiple_upstream=config,
        )

    See also :py:class:`RPCProxy`, :py:func:`start_rpc_proxy`,
    :py:func:`default_failure_handler`.
    """

    #: Human-readable name for this proxy instance.
    #:
    #: Used in log messages and as the background thread name to help
    #: identify which proxy is reporting when multiple proxies run
    #: concurrently (e.g. one per chain). If ``None``, a default name
    #: is generated from the port number.
    name: str | None = None

    #: Per-upstream-attempt timeout in seconds.
    #:
    #: Each individual request to an upstream RPC provider will be aborted
    #: after this duration. Set lower than the typical caller timeout (90 s)
    #: to leave room for failover attempts. For example, with the default
    #: of 30 s and 3 retries, the worst-case wall-clock time per incoming
    #: request is ~90 s — matching Anvil's typical read timeout.
    timeout: float = 30.0

    #: Maximum number of upstream attempts per incoming request.
    #:
    #: The proxy cycles through available providers up to this many times
    #: before giving up and returning an ``HTTP 502`` with a JSON-RPC error
    #: body to the caller. Each attempt targets the next provider in
    #: round-robin order (or the same provider if only one is configured).
    retries: int = 3

    #: Initial sleep duration in seconds between retry attempts.
    #:
    #: Grows by 1.5× after each retry (e.g. 0.5 → 0.75 → 1.125 …).
    #: Kept short because retries typically switch to a *different* provider,
    #: so there is no benefit in waiting for the same provider to recover.
    backoff: float = 0.5

    #: Number of successful requests to serve from one provider before
    #: automatically switching to the next in round-robin order.
    #:
    #: Set to ``0`` (the default) to disable auto-switching — the proxy
    #: will only switch providers on errors. Setting a positive value
    #: helps distribute load across providers and can detect degraded
    #: providers early by exercising all of them regularly.
    auto_switch_request_count: int = 0

    #: Logging level for upstream failure and switchover events.
    #:
    #: Each time the proxy encounters a retryable error or switches to
    #: another upstream provider, it logs a message at this level.
    #: Defaults to ``logging.INFO`` so failures are visible in normal
    #: operation. Set to ``logging.WARNING`` or ``logging.DEBUG`` to
    #: adjust verbosity.
    switchover_log_level: int = logging.INFO

    #: Logging level for request/response payload dumping.
    #:
    #: When the effective logger level is at or below this threshold,
    #: the proxy logs the full JSON-RPC request body before forwarding
    #: and the full response body after receiving it. This is useful for
    #: debugging but generates significant output.
    #:
    #: When the logger level is *above* this threshold the formatting
    #: is skipped entirely — zero overhead in production.
    #: Defaults to ``logging.DEBUG``.
    request_log_level: int = logging.DEBUG

    #: Maximum byte size for logged request/response payloads.
    #:
    #: Payloads larger than this are truncated with a
    #: ``"… (truncated, total N bytes)"`` suffix. Prevents massive
    #: ``eth_getCode`` or ``debug_traceTransaction`` responses from
    #: flooding log files.
    log_max_size: int = 2048

    #: Maximum number of error replies stored per provider in
    #: :py:attr:`UpstreamRPCProviderStatistics.error_replies`.
    #:
    #: Oldest entries are discarded when this limit is reached,
    #: keeping memory bounded during long-running proxy sessions.
    max_error_replies: int = 100

    #: Custom failure detection callback.
    #:
    #: Called with ``(http_status, parsed_json_body)`` for every upstream
    #: response. Must return ``True`` if the response should be treated
    #: as a retryable failure (triggering a switch to the next provider).
    #:
    #: Connection-level failures (timeouts, refused connections) bypass
    #: this handler and are always retried.
    #:
    #: Defaults to :py:func:`default_failure_handler`, which replicates
    #: the battle-tested error classification from
    #: :py:mod:`eth_defi.middleware`.
    failure_handler: FailureHandler = field(default=None)

    def __post_init__(self):
        if self.failure_handler is None:
            # Avoid circular default — default_failure_handler is defined
            # later in this module, so we resolve it at runtime.
            self.failure_handler = default_failure_handler


# ---------------------------------------------------------------------------
# Failure detection
# ---------------------------------------------------------------------------


def default_failure_handler(http_status: int, json_body: dict | None) -> bool:
    """Default failure detection replicating :py:mod:`eth_defi.middleware` logic.

    Adapted from :py:func:`eth_defi.middleware.is_retryable_http_exception`
    and :py:class:`eth_defi.provider.fallback.FallbackProvider` to work at the
    HTTP proxy level with raw status codes and JSON bodies rather than
    Python exceptions.

    The checks are, in order:

    1. HTTP status code against :py:data:`eth_defi.middleware.DEFAULT_RETRYABLE_HTTP_STATUS_CODES`
    2. JSON-RPC ``error.code`` against :py:data:`eth_defi.middleware.DEFAULT_RETRYABLE_RPC_ERROR_CODES`
    3. JSON-RPC ``error.message`` substring match against
       :py:data:`eth_defi.middleware.DEFAULT_RETRYABLE_RPC_ERROR_MESSAGES`

    Connection-level failures (timeouts, refused connections) are always
    retried and never reach this handler — they are caught earlier in
    :py:meth:`_ProxyRequestHandler._try_upstream`.

    :param http_status:
        HTTP response status code from the upstream provider.

    :param json_body:
        Parsed JSON-RPC response body, or ``None`` if unparseable.

    :return:
        ``True`` if the response should be treated as a retryable failure.
    """
    # 1. Check HTTP status code
    if http_status in DEFAULT_RETRYABLE_HTTP_STATUS_CODES:
        return True

    # 2. Check JSON-RPC error payload
    if json_body is not None and isinstance(json_body, dict):
        error = json_body.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, int) and code in DEFAULT_RETRYABLE_RPC_ERROR_CODES:
                return True

            message = error.get("message", "")
            if isinstance(message, str):
                # Exact match first, then substring (like fallback.py)
                if message in DEFAULT_RETRYABLE_RPC_ERROR_MESSAGES:
                    return True
                for check_msg in DEFAULT_RETRYABLE_RPC_ERROR_MESSAGES:
                    if check_msg in message:
                        return True

    return False


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class UpstreamRPCProviderStatistics:
    """Per-provider statistics collected during proxy operation.

    Tracks request counts, failure counts, and method-level
    breakdowns for each upstream RPC provider. Instances are
    keyed by provider URL in :py:attr:`RPCProxy.provider_stats`.
    """

    #: Upstream RPC URL (with API keys stripped for safe logging)
    url: str

    #: Total number of requests forwarded to this provider
    request_count: int = 0

    #: Total number of failed requests (timeouts, HTTP errors, RPC errors)
    failure_count: int = 0

    #: Timestamp of the last failure (naive UTC), or ``None`` if no failures
    last_failure: datetime.datetime | None = None

    #: Breakdown of requests by JSON-RPC method name.
    #: e.g. ``{"eth_getBlockByNumber": 42, "eth_getBalance": 15}``
    method_counts: dict[str, int] = field(default_factory=dict)

    #: Breakdown of failures by JSON-RPC method name
    method_failure_counts: dict[str, int] = field(default_factory=dict)

    #: List of error replies received from this provider.
    #: Each entry is a dict with keys: ``"timestamp"`` (naive UTC),
    #: ``"method"`` (JSON-RPC method), ``"http_status"`` (int or None),
    #: ``"error"`` (str summary — exception message, HTTP status text,
    #: or JSON-RPC error message).
    #: Capped at :py:data:`DEFAULT_MAX_ERROR_REPLIES` to avoid unbounded growth.
    error_replies: list[dict] = field(default_factory=list)

    #: Lock protecting concurrent updates from handler threads
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_request(self, method: str) -> None:
        """Record a request being sent to this provider."""
        with self._lock:
            self.request_count += 1
            self.method_counts[method] = self.method_counts.get(method, 0) + 1

    def record_failure(self, method: str, error_summary: str, http_status: int | None = None, max_error_replies: int = 100) -> None:
        """Record a failed request to this provider."""
        now = datetime.datetime.utcnow()
        with self._lock:
            self.failure_count += 1
            self.last_failure = now
            self.method_failure_counts[method] = self.method_failure_counts.get(method, 0) + 1
            entry = {
                "timestamp": now,
                "method": method,
                "http_status": http_status,
                "error": error_summary,
            }
            self.error_replies.append(entry)
            # Trim oldest entries if over limit
            if len(self.error_replies) > max_error_replies:
                self.error_replies = self.error_replies[-max_error_replies:]


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """Threaded HTTP server that handles each request in a new thread."""

    #: Allow rapid port reuse during tests
    allow_reuse_address = True

    #: Daemon threads so they don't block process exit
    daemon_threads = True


class _ProxyRequestHandler(BaseHTTPRequestHandler):
    """JSON-RPC proxy request handler.

    Forwards POST requests to upstream RPC providers with failover.
    Shared state is accessed via ``self.server`` attributes set up by
    :py:func:`start_rpc_proxy`.
    """

    def do_POST(self) -> None:
        """Handle a JSON-RPC POST request."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # Parse JSON to extract method name for logging/stats
        method = "unknown"
        request_id = None
        try:
            parsed = orjson.loads(body)
            if isinstance(parsed, dict):
                method = parsed.get("method", "unknown")
                request_id = parsed.get("id")
        except (orjson.JSONDecodeError, ValueError):
            pass

        # Optional request payload logging
        if logger.isEnabledFor(self.server.config.request_log_level):
            payload_str = _truncate_payload(body, self.server.config.log_max_size)
            logger.log(self.server.config.request_log_level, "RPC proxy request [%s]: %s", method, payload_str)

        # Try upstream providers with failover
        last_error = None
        last_status = None
        last_response_body = None

        current_sleep = self.server.config.backoff
        for attempt in range(self.server.config.retries):
            # Pick the current provider
            with self.server.provider_lock:
                provider_index = self.server.current_provider_index
                provider_url = self.server.rpc_urls[provider_index]
                provider_key = self.server.provider_keys[provider_index]

            stats = self.server.provider_stats[provider_key]
            stats.record_request(method)

            try:
                resp = self._try_upstream(provider_url, body, self.server.config.timeout)
                status_code = resp.status_code
                response_body = resp.content
            except (ConnectionError, Timeout, OSError) as e:
                # Connection-level failure — always retry
                error_msg = f"{e.__class__.__name__}: {e}"
                stats.record_failure(method, error_msg, http_status=None, max_error_replies=self.server.config.max_error_replies)
                total_requests = sum(s.request_count for s in self.server.provider_stats.values())
                logger.log(
                    self.server.config.switchover_log_level,
                    "RPC proxy %r: upstream %s connection error for %s: %s (attempt %d/%d, %d total requests)",
                    self.server.proxy_name,
                    provider_key,
                    method,
                    e.__class__.__name__,
                    attempt + 1,
                    self.server.config.retries,
                    total_requests,
                )
                last_error = f"{e.__class__.__name__} on {provider_key}"
                self._switch_provider()
                if attempt < self.server.config.retries - 1:
                    time.sleep(current_sleep)
                    current_sleep *= 1.5
                continue

            # Parse response JSON for failure detection
            parsed_response = None
            try:
                parsed_response = orjson.loads(response_body)
            except (orjson.JSONDecodeError, ValueError):
                pass

            # Optional response payload logging
            if logger.isEnabledFor(self.server.config.request_log_level):
                payload_str = _truncate_payload(response_body, self.server.config.log_max_size)
                logger.log(self.server.config.request_log_level, "RPC proxy response [%s] from %s (HTTP %d): %s", method, provider_key, status_code, payload_str)

            # Check if the response indicates a retryable failure
            if self.server.config.failure_handler(status_code, parsed_response):
                error_summary = _summarise_error(status_code, parsed_response)
                stats.record_failure(method, error_summary, http_status=status_code, max_error_replies=self.server.config.max_error_replies)
                total_requests = sum(s.request_count for s in self.server.provider_stats.values())
                logger.log(
                    self.server.config.switchover_log_level,
                    "RPC proxy %r: upstream %s returned retryable error for %s: %s (attempt %d/%d, %d total requests)",
                    self.server.proxy_name,
                    provider_key,
                    method,
                    error_summary,
                    attempt + 1,
                    self.server.config.retries,
                    total_requests,
                )
                last_error = f"{error_summary} from {provider_key}"
                last_status = status_code
                last_response_body = response_body
                self._switch_provider()
                if attempt < self.server.config.retries - 1:
                    time.sleep(current_sleep)
                    current_sleep *= 1.5
                continue

            # Success — forward response to caller
            self._maybe_auto_switch()
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)
            return

        # All attempts exhausted — return 502 with JSON-RPC error
        if last_response_body is not None:
            # Forward the last upstream error response as-is
            self.send_response(last_status or 502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(last_response_body)))
            self.end_headers()
            self.wfile.write(last_response_body)
        else:
            # Connection-level failures — synthesise a JSON-RPC error.
            # Use provider_keys (API-key-stripped domains) not raw URLs.
            providers_str = ", ".join(self.server.provider_keys)
            error_body = orjson.dumps(
                {
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32603,
                        "message": f"All upstream providers failed ({providers_str}): {last_error}",
                    },
                    "id": request_id,
                }
            )
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error_body)))
            self.end_headers()
            self.wfile.write(error_body)

    def _try_upstream(self, url: str, body: bytes, timeout: float) -> requests.Response:
        """Make a single POST request to an upstream provider.

        :param url:
            Upstream RPC URL.

        :param body:
            Raw request body bytes.

        :param timeout:
            Per-request timeout in seconds (connect, read).

        :return:
            The upstream HTTP response.

        :raises ConnectionError:
            On connection failure.

        :raises Timeout:
            On timeout.
        """
        return self.server.session.post(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            timeout=(min(timeout, 5.0), timeout),
        )

    def _switch_provider(self) -> None:
        """Advance to the next upstream provider in round-robin order."""
        with self.server.provider_lock:
            old_index = self.server.current_provider_index
            self.server.current_provider_index = (old_index + 1) % len(self.server.rpc_urls)
            self.server.requests_on_current_provider = 0

    def _maybe_auto_switch(self) -> None:
        """Auto-switch provider after N successful requests if configured."""
        if self.server.config.auto_switch_request_count <= 0:
            return
        with self.server.provider_lock:
            self.server.requests_on_current_provider += 1
            if self.server.requests_on_current_provider >= self.server.config.auto_switch_request_count:
                self.server.current_provider_index = (self.server.current_provider_index + 1) % len(self.server.rpc_urls)
                self.server.requests_on_current_provider = 0

    def log_message(self, format: str, *args) -> None:
        """Override default stderr logging to use Python logging."""
        logger.debug("RPC proxy HTTP: %s", format % args)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RPCProxy:
    """A running JSON-RPC failover proxy instance.

    Manages the lifecycle of a background threaded HTTP server that presents
    a single ``http://127.0.0.1:{port}`` endpoint while internally routing
    JSON-RPC requests across multiple upstream RPC providers with automatic
    failover, retry, and per-provider statistics collection.

    **Why this exists**

    Anvil (and other tools) accept only a single ``--fork-url`` and have no
    internal retry or failover logic. When the upstream RPC is slow, rate-limited,
    or temporarily unreachable, Anvil hangs indefinitely — causing downstream
    callers to timeout (e.g. ``eth_getTransactionCount`` timing out after 90 s).

    This proxy sits between the consumer and multiple upstream RPCs. If one
    upstream fails, the proxy transparently switches to the next one and retries,
    all within a configurable timeout budget. On shutdown it logs per-provider
    statistics so you can identify flaky or slow providers.

    **How it works**

    1. :py:func:`start_rpc_proxy` allocates a free localhost port and starts
       a :py:class:`~http.server.HTTPServer` (with
       :py:class:`~socketserver.ThreadingMixIn`) on a daemon thread.
    2. Every incoming ``POST`` is forwarded to the currently-active upstream.
       If the upstream returns a retryable error (as determined by the
       :py:data:`FailureHandler`), the proxy switches to the next upstream
       and retries — up to :py:data:`DEFAULT_RETRIES` times.
    3. Connection-level failures (timeouts, refused connections) are always
       retried without consulting the failure handler.
    4. After all retries are exhausted the proxy returns an ``HTTP 502`` with
       a JSON-RPC error body so the caller can distinguish proxy-level failures
       from upstream errors.
    5. Calling :py:meth:`close` stops the server and logs a per-provider
       summary to ``logger.info()``.

    **Lifecycle**

    Created by :py:func:`start_rpc_proxy`. The proxy runs until
    :py:meth:`close` is called. When used with
    :py:func:`~eth_defi.provider.anvil.launch_anvil`, the lifecycle is
    automatic: the proxy starts before Anvil and shuts down when
    :py:meth:`~eth_defi.provider.anvil.AnvilLaunch.close` is called.

    **Standalone usage**

    .. code-block:: python

        from eth_defi.provider.rpc_proxy import start_rpc_proxy

        # Start a proxy backed by two upstream RPCs
        proxy = start_rpc_proxy(
            [
                "https://eth-mainnet.alchemyapi.io/v2/YOUR_KEY",
                "https://rpc.ankr.com/eth",
            ]
        )

        # proxy.url is e.g. "http://127.0.0.1:23456"
        # Pass it to any tool that accepts a single JSON-RPC URL:
        #   anvil --fork-url {proxy.url}
        #   curl -X POST {proxy.url} -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'

        # Inspect statistics at any time
        for name, stats in proxy.get_stats().items():
            print(f"{name}: {stats.request_count} requests, {stats.failure_count} failures")

        # Shut down and see final statistics in the log
        proxy.close()

    **With custom parameters**

    .. code-block:: python

        import logging
        from eth_defi.provider.rpc_proxy import start_rpc_proxy

        proxy = start_rpc_proxy(
            rpc_urls=[
                "https://rpc-provider-a.example.com",
                "https://rpc-provider-b.example.com",
            ],
            timeout=15.0,  # 15 s per upstream attempt
            retries=5,  # try up to 5 times
            auto_switch_request_count=100,  # rotate providers every 100 requests
            switchover_log_level=logging.WARNING,
            request_log_level=logging.DEBUG,  # dump payloads at DEBUG level
            log_max_size=4096,  # truncate large payloads at 4 KiB
        )

    **Automatic integration with Anvil**

    When :py:func:`~eth_defi.provider.anvil.launch_anvil` receives a
    space-separated ``fork_url`` containing multiple RPC endpoints, it
    automatically starts an :py:class:`RPCProxy` and passes ``proxy.url``
    as Anvil's ``--fork-url``. The proxy's lifecycle is tied to
    :py:meth:`~eth_defi.provider.anvil.AnvilLaunch.close`:

    .. code-block:: python

        from eth_defi.provider.anvil import launch_anvil

        # Space-separated URLs trigger the proxy automatically
        launch = launch_anvil(
            fork_url="https://rpc-a.example.com https://rpc-b.example.com",
        )
        # launch.proxy is the RPCProxy instance
        # ...run your test...
        launch.close()  # stops Anvil, then stops the proxy and logs stats

    You can also pass an :py:class:`RPCProxy` you created yourself, or
    an :py:class:`RPCProxyConfig` to fine-tune settings, via the
    ``proxy_multiple_upstream`` parameter — see
    :py:func:`~eth_defi.provider.anvil.launch_anvil` for details.

    See also :py:class:`RPCProxyConfig`, :py:func:`start_rpc_proxy`,
    :py:class:`UpstreamRPCProviderStatistics`.
    """

    #: Human-readable name for this proxy instance, used in log messages.
    name: str

    #: Local port the proxy listens on
    port: int

    #: URL clients should connect to (``http://localhost:{port}``)
    url: str

    #: Per-provider statistics, keyed by provider display name
    #: (URL with API keys stripped) for easy lookup.
    provider_stats: dict[str, UpstreamRPCProviderStatistics]

    #: The background daemon thread running the server
    _server_thread: threading.Thread

    #: The HTTPServer instance (for shutdown)
    _http_server: _ThreadingHTTPServer

    def close(self) -> None:
        """Shut down the proxy server and log final statistics.

        Stops accepting new requests, waits for the server thread
        to finish, then logs a summary of per-provider statistics
        at ``INFO`` level.
        """
        self._http_server.shutdown()
        self._server_thread.join(timeout=10)
        self._log_stats()

    def get_stats(self) -> dict[str, UpstreamRPCProviderStatistics]:
        """Return per-provider statistics, keyed by display URL.

        :return:
            Dictionary mapping provider display name to its statistics.
        """
        return dict(self.provider_stats)

    def _log_stats(self) -> None:
        """Log final per-provider statistics."""
        logger.info("RPC proxy %r shutting down — final statistics:", self.name)
        for key, stats in self.provider_stats.items():
            failure_rate = (stats.failure_count / stats.request_count * 100) if stats.request_count > 0 else 0.0
            logger.info(
                "  Provider %s: %d requests, %d failures (%.1f%%), last failure: %s",
                key,
                stats.request_count,
                stats.failure_count,
                failure_rate,
                stats.last_failure.isoformat() if stats.last_failure else "never",
            )
            if stats.method_counts:
                top_methods = sorted(stats.method_counts.items(), key=lambda x: x[1], reverse=True)[:5]
                methods_str = ", ".join(f"{m}={c}" for m, c in top_methods)
                logger.info("    Top methods: %s", methods_str)
            if stats.error_replies:
                logger.info("    Recent errors (%d total):", len(stats.error_replies))
                for err in stats.error_replies[-3:]:
                    logger.info("      [%s] %s HTTP=%s: %s", err["timestamp"].isoformat(), err["method"], err["http_status"], err["error"])


def start_rpc_proxy(
    rpc_urls: list[str],
    port: int | None = None,
    config: RPCProxyConfig | None = None,
    **kwargs,
) -> RPCProxy:
    """Start a JSON-RPC failover proxy on a background thread.

    The proxy listens on ``localhost`` and forwards incoming JSON-RPC
    POST requests to the given upstream RPC URLs with automatic
    failover, retry, and statistics collection.

    :param rpc_urls:
        Upstream RPC endpoint URLs to cycle through.
        At least one URL is required.

    :param port:
        Local port to bind on ``127.0.0.1``.

        If ``None`` (the default), the server binds to port ``0`` which
        tells the operating system to assign a free ephemeral port
        atomically. The actual port is read back from the socket after
        binding and stored in :py:attr:`RPCProxy.port`. This avoids the
        TOCTOU race condition that occurs with
        :py:func:`~eth_defi.utils.find_free_port`: that function checks
        availability via ``connect()``, but under heavy parallel test
        execution (``pytest -n auto``) another process can grab the same
        port between the check and the ``bind()`` call, resulting in
        ``OSError: [Errno 98] Address already in use``.

        Pass an explicit port number only when you need a deterministic
        address (e.g. for debugging or firewall rules).

    :param config:
        Proxy configuration. If ``None``, a default :py:class:`RPCProxyConfig`
        is used. Individual fields can be overridden via ``**kwargs``.

    :param kwargs:
        Override individual :py:class:`RPCProxyConfig` fields.
        For example ``start_rpc_proxy(urls, timeout=10.0)`` is equivalent
        to ``start_rpc_proxy(urls, config=RPCProxyConfig(timeout=10.0))``.
        When both ``config`` and ``kwargs`` are provided, ``kwargs`` win.

    :return:
        A running :py:class:`RPCProxy` instance.
        Call :py:meth:`RPCProxy.close` when done.
    """
    assert len(rpc_urls) >= 1, f"At least one RPC URL is required, got {rpc_urls}"

    # Build effective configuration
    if config is None:
        config = RPCProxyConfig(**kwargs)
    elif kwargs:
        # Merge overrides into a copy of the provided config
        import dataclasses

        config = dataclasses.replace(config, **kwargs)

    # Port allocation is deferred to the OS bind() call below when port
    # is None, to avoid TOCTOU races under parallel test execution.

    # Create HTTP session with connection pooling
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=len(rpc_urls), pool_maxsize=10)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    # Build provider keys (URL with API keys stripped)
    provider_keys = [get_url_domain(url) for url in rpc_urls]

    # Create statistics for each provider
    provider_stats: dict[str, UpstreamRPCProviderStatistics] = {}
    for key, url in zip(provider_keys, rpc_urls):
        # Handle duplicate display names by appending an index
        display_key = key
        counter = 2
        while display_key in provider_stats:
            display_key = f"{key}#{counter}"
            counter += 1
        provider_keys[provider_keys.index(key)] = display_key
        provider_stats[display_key] = UpstreamRPCProviderStatistics(url=display_key)

    # Create and configure the server.
    # When no explicit port is given, bind to port 0 so the OS assigns an
    # available port atomically. This avoids a TOCTOU race condition when
    # multiple tests run in parallel (pytest -n auto): find_free_port()
    # checks availability via connect(), but another process can grab the
    # port between the check and the bind() call.
    bind_port = port if port is not None else 0
    server = _ThreadingHTTPServer(("127.0.0.1", bind_port), _ProxyRequestHandler)
    # Read back the actual port assigned by the OS
    port = server.server_address[1]

    proxy_name = config.name or f"rpc-proxy-{port}"
    server.config = config
    server.proxy_name = proxy_name
    server.rpc_urls = rpc_urls
    server.provider_keys = provider_keys
    server.provider_stats = provider_stats
    server.session = session
    server.provider_lock = threading.Lock()
    server.current_provider_index = 0
    server.requests_on_current_provider = 0

    # Start server on a daemon thread
    server_thread = threading.Thread(
        target=server.serve_forever,
        name=proxy_name,
        daemon=True,
    )
    server_thread.start()

    # Wait for the port to become available
    url = f"http://127.0.0.1:{port}"
    for _ in range(40):
        if is_localhost_port_listening(port, "127.0.0.1"):
            break
        time.sleep(0.05)
    else:
        server.shutdown()
        raise RuntimeError(f"RPC proxy {proxy_name!r} failed to start on port {port} within 2 seconds")

    logger.info(
        "RPC proxy %r started at %s with %d upstream providers: %s",
        proxy_name,
        url,
        len(rpc_urls),
        ", ".join(provider_keys),
    )

    return RPCProxy(
        name=proxy_name,
        port=port,
        url=url,
        provider_stats=provider_stats,
        _server_thread=server_thread,
        _http_server=server,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate_payload(data: bytes, max_size: int) -> str:
    """Truncate a payload for logging, returning a string representation.

    :param data:
        Raw bytes to display.

    :param max_size:
        Maximum number of bytes to include.

    :return:
        UTF-8 decoded string, truncated if necessary.
    """
    if len(data) <= max_size:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return repr(data[:max_size])
    try:
        truncated = data[:max_size].decode("utf-8", errors="replace")
    except Exception:
        truncated = repr(data[:max_size])
    return f"{truncated}... (truncated, total {len(data)} bytes)"


def _summarise_error(http_status: int, parsed_response: dict | None) -> str:
    """Create a short error summary string from an upstream response.

    :param http_status:
        HTTP status code.

    :param parsed_response:
        Parsed JSON body or ``None``.

    :return:
        Human-readable error summary.
    """
    if parsed_response and isinstance(parsed_response, dict):
        error = parsed_response.get("error")
        if isinstance(error, dict):
            msg = error.get("message", "")
            code = error.get("code", "")
            return f"HTTP {http_status}, JSON-RPC error {code}: {msg}"
    return f"HTTP {http_status}"
