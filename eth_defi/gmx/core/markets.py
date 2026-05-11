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
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_datastore_contract, get_reader_contract, get_tokens_metadata_dict
from eth_defi.gmx.keys import MARKET_LIST, is_market_disabled_key
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.types import MarketData, MarketSymbol
from eth_defi.gmx.symbols import SYMBOL_NORMALISE

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

    def _get_available_markets_raw(self) -> list[tuple]:
        """
        Get the available markets from the reader contract.

        :return: List of raw output from the reader contract
        :rtype: List[tuple]
        """
        reader_contract = get_reader_contract(self.config.web3, self.config.chain)
        contract_addresses = get_contract_addresses(self.config.chain)
        data_store_contract_address = contract_addresses.datastore

        # Query the actual market count from the DataStore rather than
        # using a hardcoded limit that silently breaks when GMX adds markets.
        datastore_contract = get_datastore_contract(self.config.web3, self.config.chain)
        market_count = datastore_contract.functions.getAddressCount(MARKET_LIST).call()

        return reader_contract.functions.getMarkets(
            data_store_contract_address,
            0,
            market_count + 1,
        ).call()

    def _get_on_chain_market_count(self) -> int:
        """Read the live ``MARKET_LIST`` length from the DataStore.

        Used by :meth:`_process_markets` for partial-build detection — if the
        processed-markets count is strictly less than this value, the cache
        entry is marked ``partial=True`` so the next call refetches instead of
        permanently shadowing dropped markets.

        :return: Number of markets currently registered on-chain.
        :raises Exception: Propagates any RPC error from the DataStore call.
        """
        datastore_contract = get_datastore_contract(self.config.web3, self.config.chain)
        return datastore_contract.functions.getAddressCount(MARKET_LIST).call()

    def _check_markets_disabled_onchain(self, market_addresses: list[str]) -> dict[str, bool]:
        """Batch-check ``IS_MARKET_DISABLED`` for the supplied market addresses.

        Mirrors the proven Multicall3 pattern in
        :meth:`eth_defi.gmx.ccxt.exchange.GMX._filter_datastore_disabled_markets`.
        All ``DataStore.getBool(IS_MARKET_DISABLED)`` calls are batched into a
        single ``aggregate3`` round-trip with up to two attempts; an entry that
        still fails after two attempts is treated as **enabled** (conservative —
        the market goes through and a later trade attempt will surface any
        ground-truth disabled status).

        :param market_addresses: Checksummed GMX market token addresses.
        :return: Mapping of ``market_address`` -> ``True`` if the market is
            disabled on-chain, ``False`` otherwise.  All input addresses
            appear as keys in the returned dict.
        """
        result: dict[str, bool] = {addr: False for addr in market_addresses}
        if not market_addresses:
            return result

        try:
            chain = self.config.chain
            datastore = get_datastore_contract(self.config.web3, chain)
            multicall = get_multicall_contract(self.config.web3)

            # Encode one getBool() calldata per market.
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
                        if attempt < 2:
                            retry.append((market_addr, calldata))
                        else:
                            # Two attempts failed — treat conservatively as enabled.
                            logger.warning(
                                "IS_MARKET_DISABLED check failed for market %s after 2 attempts — treating as enabled",
                                market_addr,
                            )
                            result[market_addr] = False
                        continue
                    disabled = bool(int(return_data.hex(), 16))
                    result[market_addr] = disabled
                if not retry:
                    break
                pending = retry
        except Exception as e:
            # If Multicall3 itself blows up, log loudly and report every market as
            # enabled.  Better to let a disabled market through (the trade will
            # revert on-chain with a clear error) than to silently shrink the
            # cached set the way the oracle filter used to.
            logger.warning(
                "_check_markets_disabled_onchain failed, treating all markets as enabled: %s",
                e,
            )
            return {addr: False for addr in market_addresses}

        return result

    def _process_markets(self) -> dict:
        """
        Process the raw market data and return the results.

        Build pipeline (issue #67 redesign):

        1. **TTL fast path** — if a non-partial cache entry exists for this
           chain and was built within :data:`_CLASS_MARKETS_CACHE_TTL_MS`,
           return it without any RPC traffic.
        2. **Fetch + structural build** — read raw markets from the on-chain
           reader contract and synthesise metadata.  The oracle REST snapshot
           is fetched but **never used as an exclusion filter** — issue #67
           proved that filtering on oracle availability causes spurious
           ``ValueError: No GMX market found`` crashes whenever Pyth feeds
           lag a new listing.
        3. **On-chain liveness** — batch-check ``IS_MARKET_DISABLED`` via
           Multicall3 and drop markets that the DataStore says are disabled.
        4. **Partial-build detection** — compare the processed count to
           ``DataStore.MARKET_LIST`` size.  If the new build is partial *and*
           a prior complete entry exists, return the prior entry (logged as a
           warning) so a transient gap cannot permanently shrink the cached
           set.  Otherwise mark the new entry ``partial=True`` so it will
           refresh on every subsequent call.

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
        # NOTE: oracle_prices is still fetched for downstream consumers that may
        # rely on it (e.g. price warm-up), but it is intentionally NOT used to
        # exclude markets — see issue #67 for the failure mode the old filter
        # produced.
        try:
            self._get_oracle_prices()
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("Oracle prices fetch failed (non-fatal under new design): %s", exc)

        # Use token metadata for testnets from contracts module
        if self.config.chain == "arbitrum_sepolia":
            arbitrum_sepolia_token_metadata = get_tokens_metadata_dict(self.config.chain)
            token_metadata_dict.update(arbitrum_sepolia_token_metadata)

        # Get raw market data.
        raw_markets = self._get_available_markets_raw()
        logger.debug("Retrieved %s raw markets from contract", len(raw_markets))

        # 3. Synthesise metadata for every raw market — no oracle filtering.
        processed_markets: dict[str, dict] = {}

        for raw_market in raw_markets:
            try:
                # Checksum all addresses
                market_address = to_checksum_address(raw_market[0])
                index_token_address = to_checksum_address(raw_market[1])
                long_token_address = to_checksum_address(raw_market[2])
                short_token_address = to_checksum_address(raw_market[3])

                # Skip markets with zero index token address (except for special case)
                if index_token_address == "0x0000000000000000000000000000000000000000":
                    # Special case for wstETH market
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
                logger.debug("Skipping market %s: %s", raw_market[0], e)
                continue

        logger.debug("Built %d markets for chain %s (pre-disabled-check)", len(processed_markets), chain_key)

        # 4. On-chain liveness — drop markets the DataStore reports as disabled.
        if processed_markets:
            disabled_map = self._check_markets_disabled_onchain(list(processed_markets.keys()))
            disabled_addrs = [addr for addr, is_disabled in disabled_map.items() if is_disabled]
            for addr in disabled_addrs:
                logger.warning("Market %s is disabled on-chain (IS_MARKET_DISABLED=true) — excluding", addr)
                processed_markets.pop(addr, None)

        # 5. Empty-result guard — preserve the PR-#722 invariant.
        if not processed_markets:
            raise ValueError(
                f"Markets resolved to empty dict for chain {chain_key!r}. "
                f"raw_markets count: {len(raw_markets)}, "
                f"token_metadata_dict count: {len(token_metadata_dict)}. "
                "Likely a transient GMX API timeout or saturation — do not cache."
            )

        # 6. Partial-build detection — compare to on-chain MARKET_LIST count.
        partial = False
        try:
            on_chain_count = self._get_on_chain_market_count()
            partial = len(processed_markets) < on_chain_count
            if partial:
                logger.warning(
                    "Partial build for chain %s: processed=%d on_chain=%d — entry will refresh on next call",
                    chain_key,
                    len(processed_markets),
                    on_chain_count,
                )
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("On-chain market count check failed (assuming complete): %s", exc)

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
