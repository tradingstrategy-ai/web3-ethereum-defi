"""Throttle-aware Hypersync client wrapper with stream tuning.

Provides a drop-in replacement for :py:class:`hypersync.HypersyncClient`
that rate-limits every API call (``stream``, ``recv``, ``get_chain_id``,
``get_height``) through a shared SQLite-backed token bucket, and allows
centralised configuration of Hypersync ``StreamConfig`` tuning parameters
(concurrency, batch sizes, response byte limits).

This follows the same ``pyrate_limiter`` + ``SQLiteBucket`` throttling
pattern used for Hyperliquid, GRVT, Lighter and Derive sessions (see
e.g. :py:mod:`eth_defi.hyperliquid.session`), adapted for the async
Rust FFI client instead of ``requests.Session``.

For stream tuning parameter documentation see
`Envio HyperSync StreamConfig tuning <https://docs.envio.dev/docs/HyperSync/stream-config-tuning>`_.

Usage::

    from eth_defi.hypersync.session import create_throttled_hypersync_client

    # Create a client with custom concurrency for dense workloads
    client = create_throttled_hypersync_client(
        hypersync.ClientConfig(url=url, bearer_token=api_key),
        concurrency=20,
        batch_size=5000,
    )

    # Use exactly like a regular HypersyncClient — throttling is transparent
    # and StreamConfig is built automatically from stored tuning params
    receiver = await client.stream(query)
    res = await receiver.recv()
"""

import asyncio
import logging
import os
from pathlib import Path

import hypersync
from pyrate_limiter import BucketFullException, Duration, Limiter, RequestRate, SQLiteBucket

logger = logging.getLogger(__name__)

#: Default SQLite database path for Hypersync rate limiting state.
#:
#: Using SQLite ensures thread/process-safe rate limiting across
#: parallel workers and scanner instances that share the same API key.
HYPERSYNC_RATE_LIMIT_SQLITE_DATABASE = Path("~/.tradingstrategy/hypersync/rate-limit.sqlite").expanduser()

#: Conservative default: 150 RPM leaves 25% headroom below Hypersync's
#: 200 RPM limit.
DEFAULT_HYPERSYNC_REQUESTS_PER_MINUTE = 150

#: Disable internal Rust client retries so that 429 errors surface
#: immediately to Python-side retry logic with proper backoff and
#: durable progress saves. The Rust client's internal retries are
#: invisible to our rate limiter and waste API quota on tight loops.
DEFAULT_HYPERSYNC_MAX_NUM_RETRIES = 0


#: Stream tuning parameter names that map directly to
#: :py:class:`hypersync.StreamConfig` constructor kwargs.
#:
#: Detected at import time because the hypersync 1.1.0 Rust wheel
#: exposes different field names on different platforms:
#: macOS has ``response_bytes_ceiling``/``response_bytes_floor``,
#: Linux has ``response_bytes_target``.
def _detect_stream_tuning_params() -> tuple[str, ...]:
    """Introspect ``StreamConfig.__init__`` to find available tuning params."""
    import inspect

    sig = inspect.signature(hypersync.StreamConfig.__init__)
    available = set(sig.parameters.keys()) - {"self"}
    candidates = (
        "concurrency",
        "batch_size",
        "min_batch_size",
        "max_batch_size",
        # Platform-variant response byte params
        "response_bytes_ceiling",
        "response_bytes_floor",
        "response_bytes_target",
        "max_buffered_bytes",
    )
    return tuple(name for name in candidates if name in available)


_STREAM_TUNING_PARAMS = _detect_stream_tuning_params()


async def _acquire_async(limiter: Limiter, reason: str = "") -> None:
    """Acquire a rate limit slot, sleeping asynchronously if over budget.

    Uses ``asyncio.sleep`` instead of blocking ``time.sleep`` so the
    event loop is not blocked.
    """
    while True:
        try:
            limiter.try_acquire("hypersync")
            return
        except BucketFullException as e:
            delay = e.meta_info["remaining_time"]
            logger.info(
                "Hypersync throttle: waiting %.1fs before next request [%s]",
                delay,
                reason,
            )
            await asyncio.sleep(delay)


class ThrottledHypersyncClient:
    """Drop-in wrapper for :py:class:`hypersync.HypersyncClient` with
    built-in rate limiting and stream tuning.

    Throttles one-shot API calls (``stream``, ``get_chain_id``,
    ``get_height``) through a shared :py:class:`pyrate_limiter.Limiter`
    with an SQLite-backed token bucket.

    Stream tuning parameters (concurrency, batch sizes, response byte
    limits) are stored on the client and used to build a
    :py:class:`hypersync.StreamConfig` automatically when
    :py:meth:`stream` is called without an explicit config.

    ``recv()`` is intentionally **not** throttled — adding a
    Python-side delay on ``recv()`` only slows down buffer reads
    without reducing actual API load.  Internal Rust retries are
    disabled (``max_num_retries=0``) so 429 errors surface
    immediately to the Python-side retry logic.

    For stream tuning parameter documentation see
    `Envio HyperSync StreamConfig tuning <https://docs.envio.dev/docs/HyperSync/stream-config-tuning>`_.

    .. note::

       This class intentionally does **not** subclass
       ``hypersync.HypersyncClient`` (a Rust-backed PyO3 class).
       It relies on duck typing — callers that accept
       ``HypersyncClient`` should also accept this wrapper.
       Use :py:func:`is_hypersync_client` for ``isinstance``-style
       checks that accept both types.

    :param client:
        The underlying native :py:class:`hypersync.HypersyncClient`.

    :param limiter:
        Shared :py:class:`pyrate_limiter.Limiter` for rate limiting.

    :param concurrency:
        Number of requests in flight — the main throughput knob.
        ``None`` uses the Hypersync server default (10).

    :param batch_size:
        Initial block range for the first wave of requests, before
        density has been measured. ``None`` uses the server default (1000).

    :param min_batch_size:
        Lower limit on projected block count per request.
        ``None`` uses the server default.

    :param max_batch_size:
        Hard cap on blocks per request. ``None`` means no cap.

    :param response_bytes_ceiling:
        Upper target for response size in bytes (macOS wheel).
        ``None`` uses the server default.

    :param response_bytes_floor:
        Lower target for response size in bytes (macOS wheel).
        ``None`` uses the server default.

    :param response_bytes_target:
        Target response size in bytes (Linux wheel).
        ``None`` uses the server default.

    .. note::

       The hypersync 1.1.0 Rust wheel exposes different response-byte
       parameter names on different platforms. Pass whichever your
       platform supports; unsupported names are silently ignored.
    """

    def __init__(
        self,
        client: hypersync.HypersyncClient,
        limiter: Limiter,
        *,
        concurrency: int | None = None,
        batch_size: int | None = None,
        min_batch_size: int | None = None,
        max_batch_size: int | None = None,
        response_bytes_ceiling: int | None = None,
        response_bytes_floor: int | None = None,
        response_bytes_target: int | None = None,
        max_buffered_bytes: int | None = None,
    ):
        self._client = client
        self._limiter = limiter
        self.concurrency = concurrency
        self.batch_size = batch_size
        self.min_batch_size = min_batch_size
        self.max_batch_size = max_batch_size
        self.response_bytes_ceiling = response_bytes_ceiling
        self.response_bytes_floor = response_bytes_floor
        self.response_bytes_target = response_bytes_target
        self.max_buffered_bytes = max_buffered_bytes

    def create_stream_config(self, **overrides) -> hypersync.StreamConfig:
        """Build a :py:class:`hypersync.StreamConfig` from stored tuning params.

        Any keyword arguments override the stored values for this single
        call. Only non-``None`` values are passed to the constructor.

        Example::

            # Use all stored defaults
            config = client.create_stream_config()

            # Override concurrency for one call
            config = client.create_stream_config(concurrency=30)
        """
        kwargs = {}
        for name in _STREAM_TUNING_PARAMS:
            value = overrides.pop(name, None)
            if value is None:
                value = getattr(self, name, None)
            if value is not None:
                kwargs[name] = value
        if overrides:
            kwargs.update(overrides)
        return hypersync.StreamConfig(**kwargs)

    async def stream(self, query, config: hypersync.StreamConfig | None = None) -> hypersync.QueryResponseStream:
        """Open a stream, throttled at the setup level.

        When *config* is ``None``, a :py:class:`hypersync.StreamConfig`
        is built automatically from the stored tuning parameters via
        :py:meth:`create_stream_config`.  Pass an explicit config to
        override completely.
        """
        if config is None:
            config = self.create_stream_config()
        active = {name: getattr(config, name) for name in _STREAM_TUNING_PARAMS if getattr(config, name, None) is not None}
        if active:
            logger.info("Hypersync stream config: %s", ", ".join(f"{k}={v}" for k, v in active.items()))
        await _acquire_async(self._limiter, "stream-setup")
        return await self._client.stream(query, config)

    async def get_chain_id(self):
        """Get chain ID, throttled."""
        await _acquire_async(self._limiter, "get-chain-id")
        return await self._client.get_chain_id()

    async def get_height(self):
        """Get latest block height, throttled."""
        await _acquire_async(self._limiter, "get-height")
        return await self._client.get_height()

    def __repr__(self) -> str:
        active = {name: getattr(self, name) for name in _STREAM_TUNING_PARAMS if getattr(self, name, None) is not None}
        parts = [f"rpm={self._limiter}"]
        parts.extend(f"{k}={v}" for k, v in active.items())
        return f"ThrottledHypersyncClient({', '.join(parts)})"


def is_hypersync_client(obj) -> bool:
    """Check if *obj* is a Hypersync client (native or throttled).

    Use instead of ``isinstance(obj, hypersync.HypersyncClient)`` so that
    :py:class:`ThrottledHypersyncClient` wrappers are also accepted.
    """
    return isinstance(obj, (hypersync.HypersyncClient, ThrottledHypersyncClient))


async def open_hypersync_stream(
    client: "hypersync.HypersyncClient | ThrottledHypersyncClient",
    query: hypersync.Query,
) -> hypersync.QueryResponseStream:
    """Open a Hypersync stream, dispatching correctly for both client types.

    For :py:class:`ThrottledHypersyncClient`, calls ``stream(query)``
    which builds a :py:class:`hypersync.StreamConfig` from stored
    tuning parameters and logs them.

    For a native :py:class:`hypersync.HypersyncClient`, passes a bare
    ``StreamConfig()`` with all server defaults.

    Use this instead of calling ``client.stream(query, config)``
    directly so that tuning parameters are applied transparently.

    :param client:
        Either a native ``HypersyncClient`` or a
        ``ThrottledHypersyncClient``.

    :param query:
        The Hypersync query to stream.
    """
    if isinstance(client, ThrottledHypersyncClient):
        return await client.stream(query)
    else:
        return await client.stream(query, hypersync.StreamConfig())


def _create_limiter(
    requests_per_minute: int = DEFAULT_HYPERSYNC_REQUESTS_PER_MINUTE,
    db_path: Path = HYPERSYNC_RATE_LIMIT_SQLITE_DATABASE,
) -> Limiter:
    """Create a ``pyrate_limiter.Limiter`` with an SQLite-backed bucket."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return Limiter(
        RequestRate(requests_per_minute, Duration.MINUTE),
        bucket_class=SQLiteBucket,
        bucket_kwargs={"path": str(db_path)},
    )


def _get_positive_int_from_env(name: str, default: int | None = None) -> int | None:
    """Read a positive integer from an environment variable.

    :param name:
        Environment variable name.

    :param default:
        Value to return when the variable is unset or blank.

    :return:
        Parsed integer, or *default* when unset.

    :raises ValueError:
        When the value is present but not a positive integer.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a positive integer, got: {raw!r}") from None
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer, got: {value}")
    return value


def get_hypersync_rpm_from_env() -> int:
    """Read ``HYPERSYNC_RPM`` from the environment.

    Returns :py:data:`DEFAULT_HYPERSYNC_REQUESTS_PER_MINUTE` when the
    variable is unset or blank.  Raises :py:class:`ValueError` with a
    clear message when a non-empty value cannot be parsed as an integer.
    """
    return _get_positive_int_from_env("HYPERSYNC_RPM", DEFAULT_HYPERSYNC_REQUESTS_PER_MINUTE)


def get_hypersync_concurrency_from_env() -> int | None:
    """Read ``HYPERSYNC_CONCURRENCY`` from the environment.

    Returns ``None`` when the variable is unset or blank (meaning
    use the Hypersync server default of 10).  Raises
    :py:class:`ValueError` when the value is not a positive integer.
    """
    return _get_positive_int_from_env("HYPERSYNC_CONCURRENCY")


def create_throttled_hypersync_client(
    config: hypersync.ClientConfig,
    requests_per_minute: int = DEFAULT_HYPERSYNC_REQUESTS_PER_MINUTE,
    limiter: Limiter | None = None,
    *,
    concurrency: int | None = None,
    batch_size: int | None = None,
    min_batch_size: int | None = None,
    max_batch_size: int | None = None,
    response_bytes_ceiling: int | None = None,
    response_bytes_floor: int | None = None,
    response_bytes_target: int | None = None,
    max_buffered_bytes: int | None = None,
) -> ThrottledHypersyncClient:
    """Create a Hypersync client with built-in SQLite-backed rate limiting.

    For stream tuning parameter documentation see
    `Envio HyperSync StreamConfig tuning <https://docs.envio.dev/docs/HyperSync/stream-config-tuning>`_.

    :param config:
        Hypersync client configuration (URL, bearer token, etc.).

    :param requests_per_minute:
        Maximum API requests per minute.  Defaults to 150 (75% of the
        Hypersync free-tier 200 RPM limit).  Ignored when *limiter*
        is provided.

    :param limiter:
        Optional pre-existing :py:class:`pyrate_limiter.Limiter` to
        share across multiple clients (e.g. lead discovery + price
        scanning on the same API key).  When ``None``, a new limiter
        is created.

    :param concurrency:
        Number of requests in flight — the main throughput knob.
        ``None`` uses the Hypersync server default (10).

    :param batch_size:
        Initial block range for the first wave of requests.
        ``None`` uses the server default (1000).

    :param min_batch_size:
        Lower limit on projected block count per request.

    :param max_batch_size:
        Hard cap on blocks per request.

    :param response_bytes_ceiling:
        Upper target for response size in bytes (macOS wheel).

    :param response_bytes_floor:
        Lower target for response size in bytes (macOS wheel).

    :param response_bytes_target:
        Target response size in bytes (Linux wheel).

    :param max_buffered_bytes:
        Cap on bytes of fetched-but-undelivered data (Linux wheel).

    :return:
        A :py:class:`ThrottledHypersyncClient` wrapping a native
        ``HypersyncClient``.
    """

    # Disable Rust-side retries so 429 errors surface immediately to
    # Python-side retry logic. The caller's backoff loop handles
    # retries with durable progress saves between attempts.
    if config.max_num_retries is None:
        config.max_num_retries = DEFAULT_HYPERSYNC_MAX_NUM_RETRIES

    client = hypersync.HypersyncClient(config)

    if limiter is None:
        limiter = _create_limiter(requests_per_minute)

    logger.info(
        "Created throttled Hypersync client: url=%s, rpm=%d, max_retries=%s",
        config.url,
        requests_per_minute,
        config.max_num_retries,
    )
    return ThrottledHypersyncClient(
        client,
        limiter,
        concurrency=concurrency,
        batch_size=batch_size,
        min_batch_size=min_batch_size,
        max_batch_size=max_batch_size,
        response_bytes_ceiling=response_bytes_ceiling,
        response_bytes_floor=response_bytes_floor,
        response_bytes_target=response_bytes_target,
        max_buffered_bytes=max_buffered_bytes,
    )
