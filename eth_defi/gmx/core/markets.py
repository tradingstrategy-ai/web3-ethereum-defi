"""
GMX Markets Data Module

This module provides access to GMX protocol market information and trading pairs.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from eth_typing import HexAddress
from eth_utils import to_checksum_address

from eth_defi.event_reader.multicall_batcher import get_multicall_contract
from eth_defi.gmx.api import GMXAPI
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_datastore_contract, get_reader_contract, get_tokens_metadata_dict
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.keys import MARKET_LIST, is_market_disabled_key
from eth_defi.gmx.symbols import SYMBOL_NORMALISE
from eth_defi.gmx.types import MarketData, MarketSymbol

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _MarketsCacheEntry:
    """A cached chain-wide markets map plus metadata for staleness/partial detection.

    :ivar markets: Processed markets dict, keyed by market contract address.
    :ivar fetched_at_ms: Epoch milliseconds when this entry was built.
    :ivar partial: ``True`` when ``len(markets) < on_chain_market_count`` — forces
        a re-fetch on every miss so a single-call shortfall cannot permanently
        shrink the cached set.
    """

    #: Processed markets dict, keyed by market contract address.
    markets: dict
    #: Epoch milliseconds when this entry was built.
    fetched_at_ms: int
    #: True when ``len(markets) < on_chain_market_count``. Forces re-fetch on every miss.
    partial: bool


# Class-level cache for markets data, shared across all Markets instances.
# Keyed by chain name to avoid re-fetching for the same network.  Each entry
# is wrapped in :class:`_MarketsCacheEntry` so we can apply a TTL and detect
# partial builds.
_CLASS_MARKETS_CACHE: dict[str, _MarketsCacheEntry] = {}

#: 5 minutes — long enough to keep order-path latency low, short enough for a
#: poisoned cache to self-heal within a single 1h candle.  Issue #67 saw 56
#: identical crash signatures over 24h precisely because the previous cache had
#: no expiry; this bound caps the blast radius of any future filter regression
#: at 5 minutes per process.
_CLASS_MARKETS_CACHE_TTL_MS: int = 5 * 60 * 1000

_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _normalize_rest_market(entry: dict) -> dict | None:
    """Normalise a single ``/markets`` REST response entry to a uniform schema.

    Handles two field-name shapes that appear across GMX API mirrors:

    * **gmxinfra.io**: ``marketToken``, ``indexToken``, ``longToken``, ``shortToken``
    * **gmxapi.ai**: ``marketTokenAddress``, ``indexTokenAddress``,
      ``longTokenAddress``, ``shortTokenAddress``

    :param entry: Raw dict from the ``markets`` list in the API response.
    :return: Normalised dict with snake_case keys, or ``None`` when the entry
        should be skipped (``isListed: false`` or zero index-token address).
    """
    is_listed = entry.get("isListed", True)
    if not is_listed:
        return None

    market_address = entry.get("marketToken") or entry.get("marketTokenAddress", "")
    index_token_address = entry.get("indexToken") or entry.get("indexTokenAddress", "")
    long_token_address = entry.get("longToken") or entry.get("longTokenAddress", "")
    short_token_address = entry.get("shortToken") or entry.get("shortTokenAddress", "")

    if not index_token_address or index_token_address.lower() == _ZERO_ADDRESS:
        return None

    return {
        "market_address": market_address,
        "index_token_address": index_token_address,
        "long_token_address": long_token_address,
        "short_token_address": short_token_address,
        "is_listed": bool(is_listed),
    }


@dataclass(slots=True)
class MarketInfo:
    """Information about a GMX market."""

    #: GMX market contract address
    gmx_market_address: HexAddress
    #: Symbol identifier for the market
    market_symbol: MarketSymbol
    #: Address of the index token
    index_token_address: HexAddress
    #: Metadata dictionary for the market token
    market_metadata: dict[str, Any]
    #: Metadata dictionary for the long token
    long_token_metadata: dict[str, Any]
    #: Address of the long token
    long_token_address: HexAddress
    #: Metadata dictionary for the short token
    short_token_metadata: dict[str, Any]
    #: Address of the short token
    short_token_address: HexAddress


class Markets:
    """
    GMX markets data provider.

    This class retrieves information about all trading markets available on GMX,
    replacing the gmx_python_sdk Markets class functionality.
    """

    def __init__(self, config: GMXConfig):
        """
        Initialise markets data provider.

        :param config: GMXConfig instance containing chain and network info
        """
        self.config = config
        self._special_wsteth_address = to_checksum_address("0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5")

    @classmethod
    def invalidate_cache(cls, chain: str | None = None) -> None:
        """Explicitly invalidate the class-level markets cache.

        Use this when a caller suspects the cached snapshot is stale — for
        example, when an order-builder fails to resolve a token that *should*
        be present (see :meth:`OrderArgumentParser._handle_missing_market_key`).

        :param chain: When provided, invalidate only this chain's entry.
            When ``None``, invalidate every chain entry currently in the
            cache.  Passing an unknown chain name is a no-op.
        """
        if chain is None:
            _CLASS_MARKETS_CACHE.clear()
        else:
            _CLASS_MARKETS_CACHE.pop(chain, None)

    def _get_token_metadata_dict(self) -> dict[HexAddress, dict]:
        """Get token metadata dictionary with correct decimals from GMX API.

        Uses get_tokens_metadata_dict which fetches decimals from the GMX API,
        ensuring correct price conversions for all tokens (e.g., BTC=8, ETH=18).
        """
        return get_tokens_metadata_dict(self.config.chain)

    def _get_oracle_prices(self) -> dict[str, dict]:
        """Get or fetch oracle prices."""
        try:
            oracle_prices = OraclePrices(chain=self.config.chain).get_recent_prices()
        except Exception as e:
            logger.debug("Failed to fetch oracle prices: %s", e)
            oracle_prices = {}

        return oracle_prices

    def _fetch_markets_from_rest(self) -> list[dict]:
        """Fetch the live market list from the REST ``/markets`` endpoint.

        Returns a normalised list of market dicts with keys: ``market_address``,
        ``index_token_address``, ``long_token_address``, ``short_token_address``,
        ``is_listed``. Entries with ``isListed: false`` and swap markets (zero
        index-token) are already filtered before return.

        ``use_cache=False`` is intentional — :meth:`_process_markets` owns caching
        via :data:`_CLASS_MARKETS_CACHE`. Adding a second API-level cache layer
        here would create two competing TTLs with no benefit.

        :return: Normalised list of listed, non-zero-index markets.
        :raises RuntimeError: When all REST endpoints fail after retries.
        """
        api = GMXAPI(chain=self.config.chain)
        data = api.get_markets(use_cache=False)
        raw_list: list[dict] = data.get("markets", [])
        result: list[dict] = []
        for entry in raw_list:
            normalised = _normalize_rest_market(entry)
            if normalised is not None:
                result.append(normalised)
        logger.debug(
            "_fetch_markets_from_rest: %d listed markets from REST (out of %d raw entries)",
            len(result),
            len(raw_list),
        )
        return result

    def _check_markets_disabled_onchain(self, market_addresses: list[str]) -> dict[str, bool]:
        """Batch-check ``IS_MARKET_DISABLED`` for the supplied addresses via Multicall3.

        Used by :meth:`_fetch_markets_from_onchain` when the REST ``/markets``
        endpoint is unavailable after all retries.

        :param market_addresses: Checksummed GMX market token addresses.
        :return: Mapping of ``market_address`` → ``True`` if disabled on-chain.
            All input addresses appear as keys; entries that fail after two
            attempts are treated as **enabled** (conservative fail-open).
        """
        result: dict[str, bool] = dict.fromkeys(market_addresses, False)
        if not market_addresses:
            return result

        try:
            chain = self.config.chain
            datastore = get_datastore_contract(self.config.web3, chain)
            multicall = get_multicall_contract(self.config.web3)

            batch: list[tuple[str, bytes]] = []
            for market_addr in market_addresses:
                key = is_market_disabled_key(market_addr)
                calldata = bytes.fromhex(
                    datastore.encode_abi(abi_element_identifier="getBool", args=[key])[2:]
                )
                batch.append((market_addr, calldata))

            datastore_addr = datastore.address
            pending = batch
            for attempt in range(1, 3):
                mc_calls = [(datastore_addr, True, data) for (_a, data) in pending]
                mc_results = multicall.functions.aggregate3(mc_calls).call()
                retry: list[tuple[str, bytes]] = []
                for (market_addr, calldata), (success, return_data) in zip(pending, mc_results):
                    if not success or not return_data:
                        if attempt == 1:
                            retry.append((market_addr, calldata))
                        else:
                            logger.warning(
                                "IS_MARKET_DISABLED check failed for %s after 2 attempts — treating as enabled",
                                market_addr,
                            )
                        continue
                    result[market_addr] = bool(int(return_data.hex(), 16))
                if not retry:
                    break
                pending = retry
        except Exception as exc:
            logger.warning(
                "_check_markets_disabled_onchain failed, treating all markets as enabled: %s",
                exc,
            )

        return result

    def _fetch_markets_from_onchain(self) -> list[dict]:
        """Fallback market source: on-chain ``SyntheticsReader.getMarkets()``.

        Used when :meth:`_fetch_markets_from_rest` fails after all retries.
        Replicates the pre-REST pipeline: DataStore count → reader batch →
        Multicall3 ``IS_MARKET_DISABLED`` filter.

        Unlike the REST path, zero-index-token entries (e.g. the wstETH market)
        are passed through so that :meth:`_process_markets` can apply the
        special-case remap.

        :return: List of market dicts with the same keys as
            :meth:`_fetch_markets_from_rest`: ``market_address``,
            ``index_token_address``, ``long_token_address``,
            ``short_token_address``, ``is_listed``.
        :raises Exception: Propagates any RPC error so the caller can surface it.
        """
        reader_contract = get_reader_contract(self.config.web3, self.config.chain)
        contract_addresses = get_contract_addresses(self.config.chain)
        datastore_contract = get_datastore_contract(self.config.web3, self.config.chain)
        market_count = datastore_contract.functions.getAddressCount(MARKET_LIST).call()

        raw = reader_contract.functions.getMarkets(
            contract_addresses.datastore,
            0,
            market_count + 1,
        ).call()

        result: list[dict] = [
            {
                "market_address": tup[0],
                "index_token_address": tup[1],
                "long_token_address": tup[2],
                "short_token_address": tup[3],
                "is_listed": True,
            }
            for tup in raw
        ]

        # Filter markets the DataStore reports as disabled.
        if result:
            addrs = [r["market_address"] for r in result]
            disabled_map = self._check_markets_disabled_onchain(addrs)
            result = [r for r in result if not disabled_map.get(r["market_address"], False)]

        logger.debug("_fetch_markets_from_onchain: %d enabled markets from on-chain", len(result))
        return result

    def get_available_markets(self) -> MarketData:
        """
        Get the available markets on a given chain.

        :return: Dictionary of the available markets
        :rtype: dict
        """
        return self._process_markets()

    def get_index_token_address(self, market_key: str) -> HexAddress:
        """
        Get index token address for a market.

        :param market_key: Market contract address
        :type market_key: str
        :return: Index token address
        :rtype: HexAddress
        """
        markets = self._process_markets()
        return markets.get(market_key, {}).get("index_token_address", None)

    def get_long_token_address(self, market_key: str) -> HexAddress:
        """
        Get long token address for a market.

        :param market_key: Market contract address
        :type market_key: str
        :return: Long token address
        :rtype: HexAddress
        """
        markets = self._process_markets()
        return markets.get(market_key, {}).get("long_token_address", None)

    def get_short_token_address(self, market_key: str) -> HexAddress:
        """
        Get short token address for a market.

        :param market_key: Market contract address
        :type market_key: str
        :return: Short token address
        :rtype: HexAddress
        """
        markets = self._process_markets()
        return markets.get(market_key, {}).get("short_token_address", None)

    def get_market_symbol(self, market_key: str) -> str:
        """
        Get market symbol for a market.

        :param market_key: Market contract address
        :type market_key: str
        :return: Market symbol
        :rtype: str
        """
        markets = self._process_markets()
        return markets.get(market_key, {}).get("market_symbol", None)

    def get_decimal_factor(self, market_key: str, long: bool = False, short: bool = False) -> int:
        """
        Get decimal factor for a market token.

        :param market_key: Market contract address
        :type market_key: str
        :param long: Get decimals for long token
        :type long: bool
        :param short: Get decimals for short token
        :type short: bool
        :return: Token decimal factor
        :rtype: int
        """
        markets = self._process_markets()
        if long:
            return markets[market_key]["long_token_metadata"]["decimals"]
        elif short:
            return markets[market_key]["short_token_metadata"]["decimals"]
        else:
            return markets[market_key]["market_metadata"]["decimals"]

    def is_synthetic(self, market_key: str) -> bool:
        """
        Check if a market is synthetic.

        :param market_key: Market contract address
        :type market_key: str
        :return: True if market is synthetic, False otherwise
        :rtype: bool
        """
        markets = self._process_markets()
        return markets[market_key]["market_metadata"].get("synthetic", False)

    def get_market_info(self, market_address: HexAddress) -> Optional[MarketInfo]:
        """
        Get detailed information for a specific market.

        :param market_address: Market contract address
        :type market_address: HexAddress
        :return: Market information or None if not found
        :rtype: Optional[MarketInfo]
        """
        markets = self._process_markets()
        if market_address in markets:
            market_data = markets[market_address]
            return MarketInfo(
                gmx_market_address=market_data["gmx_market_address"],
                market_symbol=market_data["market_symbol"],
                index_token_address=market_data["index_token_address"],
                market_metadata=market_data["market_metadata"],
                long_token_metadata=market_data["long_token_metadata"],
                long_token_address=market_data["long_token_address"],
                short_token_metadata=market_data["short_token_metadata"],
                short_token_address=market_data["short_token_address"],
            )
        else:
            return None

    def is_market_disabled(self, market_address: HexAddress) -> bool:
        """
        Check if a market is disabled.

        :param market_address: Market contract address
        :type market_address: HexAddress
        :return: True if market is disabled, False otherwise
        :rtype: bool
        """
        # For now, assume all markets in our processed list are enabled
        return market_address not in self._process_markets()

    def _process_markets(self) -> dict:
        """
        Process the raw market data and return the results.

        Build pipeline (issue #67 redesign):

        1. **TTL fast path** — if a non-partial cache entry exists for this
           chain and was built within :data:`_CLASS_MARKETS_CACHE_TTL_MS`,
           return it without any RPC traffic.
        2. **Fetch + structural build** — read listed markets from the REST
           ``/markets`` endpoint (via :meth:`_fetch_markets_from_rest`) and
           synthesise metadata.  ``isListed:false`` and zero-index-token
           entries are pre-filtered by the REST helper.
        3. **Partial-build detection** — compare the processed count to the
           raw REST market count.  If the new build is partial *and* a prior
           complete entry exists, return the prior entry (logged as a warning)
           so a transient gap cannot permanently shrink the cached set.
           Otherwise mark the new entry ``partial=True`` so it will refresh on
           every subsequent call.

        :return: Dictionary of processed markets, keyed by checksummed market
            contract address.
        :rtype: dict
        :raises ValueError: When the build produces an empty result and no
            usable prior cache entry exists (preserves the PR-#722 guard).
        """
        chain_key = self.config.chain
        now_ms = int(time.time() * 1000)

        # 1. TTL fast path — non-partial, fresh entries skip the rebuild entirely.
        cached_entry = _CLASS_MARKETS_CACHE.get(chain_key)
        if (
            cached_entry is not None
            and not cached_entry.partial
            and cached_entry.markets
            and (now_ms - cached_entry.fetched_at_ms) < _CLASS_MARKETS_CACHE_TTL_MS
        ):
            logger.debug("Returning cached markets data for chain %s (fresh)", chain_key)
            return cached_entry.markets

        logger.debug("Processing GMX markets data for chain %s...", chain_key)

        # 2. Pre-load necessary data.
        token_metadata_dict = self._get_token_metadata_dict()

        # Use token metadata for testnets from contracts module
        if self.config.chain == "arbitrum_sepolia":
            arbitrum_sepolia_token_metadata = get_tokens_metadata_dict(self.config.chain)
            token_metadata_dict.update(arbitrum_sepolia_token_metadata)

        # Get market list — REST /markets primary, on-chain SyntheticsReader fallback.
        try:
            rest_markets = self._fetch_markets_from_rest()
        except Exception as rest_exc:
            logger.warning(
                "REST /markets failed after retries — falling back to on-chain SyntheticsReader: %s",
                rest_exc,
            )
            rest_markets = self._fetch_markets_from_onchain()
        rest_markets_count = len(rest_markets)
        logger.debug("Retrieved %d markets (REST primary with on-chain fallback)", rest_markets_count)

        # 3. Synthesise metadata for every raw market — no oracle filtering.
        processed_markets: dict[str, dict] = {}

        for rest_market in rest_markets:
            try:
                # Checksum all addresses (REST returns mixed-case hex strings).
                market_address = to_checksum_address(rest_market["market_address"])
                index_token_address = to_checksum_address(rest_market["index_token_address"])
                long_token_address = to_checksum_address(rest_market["long_token_address"])
                short_token_address = to_checksum_address(rest_market["short_token_address"])

                # Special case: wstETH market uses zero index token in REST — remap it.
                if index_token_address == "0x0000000000000000000000000000000000000000":
                    if market_address == self._special_wsteth_address:
                        index_token_address = to_checksum_address("0x5979D7b546E38E414F7E9822514be443A4800529")
                    else:
                        logger.debug("Skipping market %s with zero index token address", market_address)
                        continue

                # Get metadata for all tokens.
                index_token_meta = token_metadata_dict.get(index_token_address)
                long_token_meta = token_metadata_dict.get(long_token_address)
                short_token_meta = token_metadata_dict.get(short_token_address)

                # Handle swap markets (when index token metadata is missing).
                if not index_token_meta:
                    logger.debug("Skipping market %s: no index token metadata (likely a swap market)", market_address)
                    continue

                # Verify index token has decimals.
                if "decimals" not in index_token_meta:
                    raise ValueError(f"Index token {index_token_address} missing decimals in GMX API response. Cannot safely process market {market_address}.")

                # Determine market symbol.
                market_symbol = index_token_meta["symbol"]
                if long_token_address == short_token_address:
                    market_symbol = f"{market_symbol}2"

                # Normalise versioned symbols to canonical names (e.g. "XAUT.v2" -> "XAUT").
                market_symbol = SYMBOL_NORMALISE.get(market_symbol, market_symbol)

                # Set synthetic flag for BTC2/ETH2 markets.
                index_token_meta["synthetic"] = long_token_address == short_token_address

                # Special case for wstETH market.
                if market_address == self._special_wsteth_address:
                    market_symbol = "wstETH"
                    index_token_address = to_checksum_address("0x5979D7b546E38E414F7E9822514be443A4800529")
                    index_token_meta = token_metadata_dict.get(index_token_address)
                    if not index_token_meta or "decimals" not in index_token_meta:
                        raise ValueError(f"wstETH token {index_token_address} not found in GMX API or missing decimals.")

                # Ensure metadata exists for all tokens (long/short tokens need decimals for collateral).
                if not long_token_meta or "decimals" not in long_token_meta:
                    raise ValueError(f"Long token {long_token_address} missing metadata or decimals for market {market_address}.")
                if not short_token_meta or "decimals" not in short_token_meta:
                    raise ValueError(f"Short token {short_token_address} missing metadata or decimals for market {market_address}.")

                # Store processed market.
                processed_markets[market_address] = {
                    "gmx_market_address": market_address,
                    "market_symbol": market_symbol,
                    "index_token_address": index_token_address,
                    "market_metadata": index_token_meta,
                    "long_token_metadata": long_token_meta,
                    "long_token_address": long_token_address,
                    "short_token_metadata": short_token_meta,
                    "short_token_address": short_token_address,
                }

            except Exception as e:
                logger.debug("Skipping market %s: %s", rest_market.get("market_address", "?"), e)
                continue

        logger.debug("Built %d markets for chain %s from REST /markets", len(processed_markets), chain_key)

        # 5. Empty-result guard — preserve the PR-#722 invariant.
        if not processed_markets:
            raise ValueError(
                f"Markets resolved to empty dict for chain {chain_key!r}. "
                f"rest_markets count: {rest_markets_count}, "
                f"token_metadata_dict count: {len(token_metadata_dict)}. "
                "Likely a transient GMX API timeout or saturation — do not cache."
            )

        # 6. Partial-build detection — compare processed to the raw REST count.
        partial = len(processed_markets) < rest_markets_count
        if partial:
            logger.warning(
                "Partial build for chain %s: processed=%d rest_count=%d — entry will refresh on next call",
                chain_key,
                len(processed_markets),
                rest_markets_count,
            )

        # 7. Partial-rebuild protection — keep a complete prior entry if the new
        # build is smaller.  Without this, a single mid-flight RPC blip could
        # poison the cache for the next 5 minutes.
        if partial and cached_entry is not None and not cached_entry.partial and len(cached_entry.markets) > len(processed_markets):
            logger.warning(
                "Keeping prior complete cache entry for %s (%d markets) over new partial build (%d markets)",
                chain_key,
                len(cached_entry.markets),
                len(processed_markets),
            )
            return cached_entry.markets

        # 8. Store the new entry.
        _CLASS_MARKETS_CACHE[chain_key] = _MarketsCacheEntry(
            markets=processed_markets,
            fetched_at_ms=now_ms,
            partial=partial,
        )
        logger.debug("Cached %d markets for chain %s (partial=%s)", len(processed_markets), chain_key, partial)

        return processed_markets
