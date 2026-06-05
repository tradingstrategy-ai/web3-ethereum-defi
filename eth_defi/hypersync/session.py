"""Throttle-aware Hypersync client wrapper.

Provides a drop-in replacement for :py:class:`hypersync.HypersyncClient`
that rate-limits every API call (``stream``, ``recv``, ``get_chain_id``,
``get_height``) through a shared SQLite-backed token bucket.

This follows the same ``pyrate_limiter`` + ``SQLiteBucket`` throttling
pattern used for Hyperliquid, GRVT, Lighter and Derive sessions (see
e.g. :py:mod:`eth_defi.hyperliquid.session`), adapted for the async
Rust FFI client instead of ``requests.Session``.

Usage::

    from eth_defi.hypersync.session import create_throttled_hypersync_client

    client = create_throttled_hypersync_client(
        hypersync.ClientConfig(url=url, bearer_token=api_key),
    )

    # Use exactly like a regular HypersyncClient — throttling is transparent
    receiver = await client.stream(query, config)
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
    built-in rate limiting.

    Throttles one-shot API calls (``stream``, ``get_chain_id``,
    ``get_height``) through a shared :py:class:`pyrate_limiter.Limiter`
    with an SQLite-backed token bucket.

    ``recv()`` is intentionally **not** throttled — adding a
    Python-side delay on ``recv()`` only slows down buffer reads
    without reducing actual API load.  Internal Rust retries are
    disabled (``max_num_retries=0``) so 429 errors surface
    immediately to the Python-side retry logic.

    .. note::

       This class intentionally does **not** subclass
       ``hypersync.HypersyncClient`` (a Rust-backed PyO3 class).
       It relies on duck typing — callers that accept
       ``HypersyncClient`` should also accept this wrapper.
       Use :py:func:`is_hypersync_client` for ``isinstance``-style
       checks that accept both types.
    """

    def __init__(
        self,
        client: hypersync.HypersyncClient,
        limiter: Limiter,
    ):
        self._client = client
        self._limiter = limiter

    async def stream(self, query, config):
        """Open a stream, throttled at the setup level."""
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
        return f"ThrottledHypersyncClient(rpm={self._limiter})"


def is_hypersync_client(obj) -> bool:
    """Check if *obj* is a Hypersync client (native or throttled).

    Use instead of ``isinstance(obj, hypersync.HypersyncClient)`` so that
    :py:class:`ThrottledHypersyncClient` wrappers are also accepted.
    """
    return isinstance(obj, (hypersync.HypersyncClient, ThrottledHypersyncClient))


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


def get_hypersync_rpm_from_env() -> int:
    """Read ``HYPERSYNC_RPM`` from the environment.

    Returns :py:data:`DEFAULT_HYPERSYNC_REQUESTS_PER_MINUTE` when the
    variable is unset or blank.  Raises :py:class:`ValueError` with a
    clear message when a non-empty value cannot be parsed as an integer.
    """
    raw = os.environ.get("HYPERSYNC_RPM", "").strip()
    if not raw:
        return DEFAULT_HYPERSYNC_REQUESTS_PER_MINUTE
    try:
        rpm = int(raw)
    except ValueError:
        raise ValueError(f"HYPERSYNC_RPM must be a positive integer, got: {raw!r}") from None
    if rpm <= 0:
        raise ValueError(f"HYPERSYNC_RPM must be a positive integer, got: {rpm}")
    return rpm


def create_throttled_hypersync_client(
    config: hypersync.ClientConfig,
    requests_per_minute: int = DEFAULT_HYPERSYNC_REQUESTS_PER_MINUTE,
    limiter: Limiter | None = None,
) -> ThrottledHypersyncClient:
    """Create a Hypersync client with built-in SQLite-backed rate limiting.

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
    return ThrottledHypersyncClient(client, limiter)
