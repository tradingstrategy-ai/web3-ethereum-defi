"""GMX market data disk cache.

Provides persistent caching for GMX market metadata to avoid repeated
slow RPC/API calls across application restarts. Uses SQLite-based
key-value storage with TTL support.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from eth_defi.gmx.constants import (
    DEFAULT_MARKET_CACHE_DIR,
    DISK_CACHE_APY_TTL_SECONDS,
    DISK_CACHE_MARKETS_TTL_SECONDS,
)
from eth_defi.sqlite_cache import PersistentKeyValueStore

logger = logging.getLogger(__name__)


class GMXMarketCache(PersistentKeyValueStore):
    """Persistent cache for GMX market data with TTL support.

    Extends PersistentKeyValueStore to add:
    - JSON encoding/decoding for market data
    - TTL (time-to-live) support with expiry checking
    - Chain-specific cache keys
    - Loading mode separation (rpc/graphql/rest_api)

    Cache entries are stored with metadata:
    {
        "data": <market_data>,
        "timestamp": <unix_timestamp>,
        "ttl": <seconds>,
        "loading_mode": "rest_api|graphql|rpc"
    }

    Example:

    .. code-block:: python

        cache = GMXMarketCache.get_cache("arbitrum")

        # Try to get cached markets
        markets = cache.get_markets("rest_api")
        if markets is None:
            # Cache miss or expired - load from API
            markets = load_markets_from_api()
            cache.set_markets(markets, "rest_api", ttl=3600)
    """

    def __init__(self, filename: Path, autocommit: bool = True):
        """Initialise GMX market cache.

        :param filename:
            Path to SQLite database file
        :param autocommit:
            Whether to commit after each write
        """
        # Ensure parent directory exists
        filename.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(filename=filename, autocommit=autocommit)

    @classmethod
    def get_cache(
        cls,
        chain: str,
        cache_dir: Optional[Path] = None,
        disabled: bool = False,
    ) -> Optional["GMXMarketCache"]:
        """Get or create cache instance for a chain.

        :param chain:
            Chain name (arbitrum, avalanche, etc.)
        :param cache_dir:
            Custom cache directory (optional, uses default if None)
        :param disabled:
            If True, returns None (cache disabled)
        :return:
            GMXMarketCache instance or None if disabled
        """
        if disabled:
            logger.debug("GMX market cache disabled")
            return None

        if cache_dir is None:
            cache_dir = Path(DEFAULT_MARKET_CACHE_DIR).expanduser()

        cache_file = cache_dir / f"markets_{chain}.sqlite"
        logger.debug("Using GMX market cache at %s", cache_file)

        return cls(filename=cache_file)

    def encode_value(self, value: dict) -> str:
        """Encode Python dict as JSON string.

        :param value:
            Dictionary to encode
        :return:
            JSON string
        """
        return json.dumps(value)

    def decode_value(self, value: str) -> dict:
        """Decode JSON string to Python dict.

        :param value:
            JSON string to decode
        :return:
            Dictionary
        """
        return json.loads(value)

    def _make_cache_entry(
        self,
        data: Any,
        ttl: int,
        loading_mode: str,
    ) -> dict:
        """Create cache entry with metadata.

        :param data:
            Data to cache
        :param ttl:
            Time-to-live in seconds
        :param loading_mode:
            Loading mode ('rest_api', 'graphql', or 'rpc')
        :return:
            Cache entry dictionary with metadata
        """
        return {
            "data": data,
            "timestamp": time.time(),
            "ttl": ttl,
            "loading_mode": loading_mode,
        }

    def _is_expired(self, entry: dict) -> bool:
        """Check if cache entry has expired.

        :param entry:
            Cache entry dictionary
        :return:
            True if expired, False otherwise
        """
        if not isinstance(entry, dict):
            return True

        timestamp = entry.get("timestamp")
        ttl = entry.get("ttl")

        if timestamp is None or ttl is None:
            return True

        age = time.time() - timestamp
        return age >= ttl

    def get_markets(
        self,
        loading_mode: str,
        check_expiry: bool = True,
    ) -> Optional[dict]:
        """Get cached markets data.

        :param loading_mode:
            Loading mode ('rest_api', 'graphql', or 'rpc')
        :param check_expiry:
            Whether to check TTL and return None if expired
        :return:
            Market data dict or None if not found/expired
        """
        cache_key = f"markets_{loading_mode}"

        try:
            entry = self.get(cache_key)
            if entry is None:
                logger.debug("Cache miss for %s markets", loading_mode)
                return None

            if check_expiry and self._is_expired(entry):
                age = time.time() - entry.get("timestamp", 0)
                logger.debug(
                    "Cache expired for %s markets (age: %.1fs, ttl: %s)",
                    loading_mode,
                    age,
                    entry.get("ttl"),
                )
                return None

            logger.debug("Cache hit for %s markets", loading_mode)
            return entry.get("data")

        except Exception as e:
            logger.warning("Failed to read from cache: %s", e)
            return None

    def set_markets(
        self,
        data: dict,
        loading_mode: str,
        ttl: Optional[int] = None,
    ) -> None:
        """Store markets data in cache.

        :param data:
            Markets dictionary to cache
        :param loading_mode:
            Loading mode ('rest_api', 'graphql', or 'rpc')
        :param ttl:
            Time-to-live in seconds (uses default if None)
        """
        if ttl is None:
            ttl = DISK_CACHE_MARKETS_TTL_SECONDS

        cache_key = f"markets_{loading_mode}"
        entry = self._make_cache_entry(data, ttl, loading_mode)

        try:
            self[cache_key] = entry
            logger.debug(
                "Cached %s markets (ttl: %ds, markets: %d)",
                loading_mode,
                ttl,
                len(data),
            )
        except Exception as e:
            logger.warning("Failed to write to cache: %s", e)

    def get_apy(
        self,
        period: str = "30d",
        check_expiry: bool = True,
    ) -> Optional[dict]:
        """Get cached APY data.

        :param period:
            APY period (1d, 7d, 30d, etc.)
        :param check_expiry:
            Whether to check TTL
        :return:
            APY data dict or None if not found/expired
        """
        cache_key = f"apy_{period}"

        try:
            entry = self.get(cache_key)
            if entry is None:
                return None

            if check_expiry and self._is_expired(entry):
                return None

            return entry.get("data")

        except Exception as e:
            logger.warning("Failed to read APY from cache: %s", e)
            return None

    def set_apy(
        self,
        data: dict,
        period: str = "30d",
        ttl: Optional[int] = None,
    ) -> None:
        """Store APY data in cache.

        :param data:
            APY dictionary to cache
        :param period:
            APY period
        :param ttl:
            Time-to-live in seconds (uses default if None)
        """
        if ttl is None:
            ttl = DISK_CACHE_APY_TTL_SECONDS

        cache_key = f"apy_{period}"
        entry = self._make_cache_entry(data, ttl, "rest_api")

        try:
            self[cache_key] = entry
            logger.debug("Cached APY data for period %s (ttl: %ds)", period, ttl)
        except Exception as e:
            logger.warning("Failed to write APY to cache: %s", e)

    def clear_expired(self) -> int:
        """Remove all expired cache entries.

        :return:
            Number of entries removed
        """
        removed = 0
        keys_to_remove = []

        # Iterate through all keys
        for key in list(self.keys()):
            try:
                entry = self[key]
                if self._is_expired(entry):
                    keys_to_remove.append(key)
            except Exception:
                continue

        # Remove expired entries
        for key in keys_to_remove:
            try:
                del self[key]
                removed += 1
            except Exception:
                pass

        if removed > 0:
            logger.debug("Cleared %d expired cache entries", removed)

        return removed
