"""
GMX Order Argument Parser

This module provides parameter parsing and validation for GMX orders.
Converts user-friendly parameters (symbols, USD amounts) into contract-ready format.
"""

import logging
import numpy as np
from web3 import Web3

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.precision import is_raw_usd_amount
from eth_defi.gmx.utils import determine_swap_route
from eth_defi.gmx.contracts import get_tokens_metadata_dict

logger = logging.getLogger(__name__)


class InvalidCollateralForMarketError(Exception):
    """The selected GMX market definitively does not accept the collateral token.

    Raised by :meth:`OrderArgumentParser._check_if_valid_collateral_for_market`
    when the market resolved cleanly and the collateral matches neither its
    long nor short token — a **definitive** rejection, as opposed to an
    *indeterminate* lookup failure (``KeyError`` — market absent from the
    markets snapshot), which callers must tolerate.

    Also raised by :meth:`OrderArgumentParser._handle_missing_swap_path` when a
    definitively rejected collateral has ``start_token == collateral`` — no
    swap leg will ever be built in that configuration, so shipping the order
    would burn gas and die on-chain as an ``InvalidCollateralTokenForMarket``
    keeper cancellation, feeding the circuit breaker (issue #1178, live
    incident 2026-07-01/02). Failing loudly pre-flight is strictly better.
    """


# Module-level cache for token metadata only.  The previous ``_MARKETS_CACHE``
# was removed in the issue-#67 fix — :class:`Markets` now owns a TTL'd cache
# that is invalidated centrally via :meth:`Markets.invalidate_cache`, so a
# parallel cache here would defeat the cache-coherence guarantee that the
# refresh-on-miss path depends on.
_TOKEN_METADATA_CACHE: dict[tuple[int, str], dict] = {}  # Key: (chain_id, chain_name)


def _get_token_metadata_dict(web3: Web3, chain: str, use_cache: bool = True) -> dict:
    """
    Get token metadata in SDK-compatible format with caching.

    Fetches token metadata from GMX API (not on-chain) because:
    - GMX API returns all tokens in a single call (much faster than 100+ RPC calls)
    - Synthetic tokens (APT, PEPE, etc.) don't exist on-chain, only in GMX API

    :param web3: Web3 connection instance (used only for cache key)
    :param chain: Network name
    :param use_cache: Whether to use cached values. Default is True.
    :return: Dictionary mapping addresses to token metadata
    """
    # Check cache first
    chain_id = web3.eth.chain_id
    cache_key = (chain_id, chain)

    if use_cache and cache_key in _TOKEN_METADATA_CACHE:
        return _TOKEN_METADATA_CACHE[cache_key].copy()

    # Fetch from GMX API - returns {address: {symbol, decimals, synthetic}}
    api_tokens = get_tokens_metadata_dict(chain)

    # Convert to format expected by ArgumentParser: {address: {symbol, address, decimals}}
    result = {}
    for address, metadata in api_tokens.items():
        result[address] = {
            "symbol": metadata["symbol"],
            "address": address,
            "decimals": metadata["decimals"],
        }

    # Cache the result
    if use_cache:
        _TOKEN_METADATA_CACHE[cache_key] = result.copy()

    return result


class OrderArgumentParser:
    """
    Parses and validates order parameters for GMX protocol.

    Converts user-friendly parameters into contract-ready format:
    - Symbol names → token addresses
    - USD amounts → wei amounts with proper decimals
    - Calculates missing parameters from leverage
    - Validates collateral compatibility
    - Determines optimal swap paths
    """

    def __init__(
        self,
        config,
        is_increase: bool = False,
        is_decrease: bool = False,
        is_swap: bool = False,
    ):
        """
        Initialize parser for specific order type.

        :param config: GMXConfigManager with chain and network info
        :param is_increase: True for opening/increasing positions
        :param is_decrease: True for closing/decreasing positions
        :param is_swap: True for token swaps
        """
        self.config = config
        self.parameters_dict = None
        self.is_increase = is_increase
        self.is_decrease = is_decrease
        self.is_swap = is_swap

        # Get web3 connection - config could be GMXConfig or GMXConfigManager
        if hasattr(config, "get_web3_connection"):
            self.web3 = config.get_web3_connection()
        else:
            # GMXConfig has web3 attribute directly
            self.web3 = config.web3

        # Get chain name for caching - handle both GMXConfig and GMXConfigManager
        if hasattr(config, "chain"):
            # GMXConfig has chain attribute
            chain = config.chain
        else:
            # GMXConfigManager has get_chain() method
            chain = config.get_chain()

        # Resolve markets via :class:`Markets`.  The class-level cache inside
        # ``Markets`` carries its own 5-minute TTL and invalidation surface
        # (see ``Markets.invalidate_cache``), so we do NOT layer a second
        # cache here — having two caches with independent staleness was
        # exactly the failure mode behind issue #67.
        user_wallet_address = getattr(config, "user_wallet_address", None) or getattr(config, "_user_wallet_address", None)
        gmx_config = GMXConfig(self.web3, user_wallet_address=user_wallet_address)
        self._gmx_config_for_refresh = gmx_config  # Held so the miss-retry path can re-resolve.
        self.markets = Markets(gmx_config).get_available_markets()

        #: True once :meth:`_handle_missing_market_key` has performed its
        #: one allowed cache-refresh retry.  Bounding the retry per parser
        #: instance prevents an infinite loop when the index token is
        #: structurally absent from GMX.
        self._market_key_refresh_attempted: bool = False

        #: Tri-state collateral-acceptance verdict recorded by
        #: :meth:`_handle_missing_collateral_address`: ``True`` = the market
        #: verifiably accepts the collateral; ``False`` = **definitively
        #: rejected** (market resolved, collateral matches neither token);
        #: ``None`` = indeterminate (could not verify — e.g. market absent
        #: from the markets snapshot) or the check never ran (caller supplied
        #: ``collateral_address`` directly).  Only ``False`` may block an
        #: order (see :meth:`_handle_missing_swap_path`).
        self._collateral_directly_supported: bool | None = None

        if is_increase:
            self.required_keys = [
                "chain",
                "index_token_address",
                "market_key",
                "start_token_address",
                "collateral_address",
                "swap_path",
                "is_long",
                "size_delta_usd",
                "initial_collateral_delta",
                "slippage_percent",
            ]

        if is_decrease:
            self.required_keys = [
                "chain",
                "index_token_address",
                "market_key",
                "start_token_address",
                "collateral_address",
                "is_long",
                "size_delta_usd",
                "initial_collateral_delta",
                "slippage_percent",
            ]

        if is_swap:
            self.required_keys = [
                "chain",
                "start_token_address",
                "out_token_address",
                "initial_collateral_delta",
                "swap_path",
                "slippage_percent",
            ]

        self.missing_base_key_methods = {
            "chain": self._handle_missing_chain,
            "index_token_address": self._handle_missing_index_token_address,
            "market_key": self._handle_missing_market_key,
            "start_token_address": self._handle_missing_start_token_address,
            "out_token_address": self._handle_missing_out_token_address,
            "collateral_address": self._handle_missing_collateral_address,
            "swap_path": self._handle_missing_swap_path,
            "is_long": self._handle_missing_is_long,
            "slippage_percent": self._handle_missing_slippage_percent,
        }

    def process_parameters_dictionary(self, parameters_dict):
        """
        Process and validate order parameters.

        :param parameters_dict: User-supplied parameters
        :return: Complete, validated parameters ready for contract interaction
        """
        missing_keys = self._determine_missing_keys(parameters_dict)

        self.parameters_dict = parameters_dict

        for missing_key in missing_keys:
            if missing_key in self.missing_base_key_methods:
                self.missing_base_key_methods[missing_key]()

        if not self.is_swap:
            self.calculate_missing_position_size_info_keys()
            # Only check leverage for increase orders, not decrease orders
            if self.is_increase:
                self._check_if_max_leverage_exceeded()

        if self.is_increase:
            if self._calculate_initial_collateral_usd() < 2:
                msg = "Position size must be backed by >$2 of collateral!"
                raise Exception(msg)

        self._format_size_info()

        return self.parameters_dict

    def _determine_missing_keys(self, parameters_dict):
        """Compare keys in dictionary to required keys for order type."""
        return [key for key in self.required_keys if key not in parameters_dict]

    def _handle_missing_chain(self):
        """Chain must be supplied by user."""
        msg = "Please pass chain name in parameters dictionary!"
        raise Exception(msg)

    def _handle_missing_index_token_address(self):
        """Resolve index token address from symbol."""
        try:
            token_symbol = self.parameters_dict["index_token_symbol"]

            # Special handling for BTC on avalanche
            if token_symbol == "BTC" and self.parameters_dict["chain"] == "avalanche":
                token_symbol = "WBTC.b"
        except KeyError:
            msg = "Index Token Address and Symbol not provided!"
            raise Exception(msg)

        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        self.parameters_dict["index_token_address"] = self.find_key_by_symbol(tokens, token_symbol)

    def _handle_missing_market_key(self):
        """Resolve market key from index token address.

        When multiple markets share the same index token (e.g. BTC has both
        WBTC-USDC and tBTC-tBTC pools), the collateral symbol is used to
        disambiguate: the market whose long_token or short_token matches the
        requested collateral is preferred.  Without this, dict-order determines
        which pool is selected — causing non-deterministic collateral validation
        failures across restarts.
        """
        index_token_address = self.parameters_dict["index_token_address"]

        # Normalise to checksum address before any comparison
        if index_token_address:
            index_token_address = Web3.to_checksum_address(index_token_address)

        # Special handling for WBTC on arbitrum — compare with normalised form
        if index_token_address == Web3.to_checksum_address("0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"):
            index_token_address = "0x47904963fc8b2340414262125aF798B9655E58Cd"

        logger.info(
            "_handle_missing_market_key: resolving market_key for index_token_address=%s",
            index_token_address,
        )

        # Collect ALL markets that match the index token — there may be several
        # (e.g. BTC/USD has both WBTC-USDC and tBTC-tBTC pools on Arbitrum).
        all_matches = self.find_all_market_keys_by_index_address(self.markets, index_token_address)

        if not all_matches:
            # Force a cache refresh once per parser instance before giving up.
            # The class-level Markets cache may be stale (e.g. a new GMX
            # listing happened since the parser was constructed); a single
            # forced refresh covers that case without risking an infinite
            # retry loop when the token is genuinely absent.
            if not self._market_key_refresh_attempted:
                self._market_key_refresh_attempted = True
                chain = self.parameters_dict.get("chain")
                logger.warning(
                    "_handle_missing_market_key: index_token_address=%s not in cached markets — invalidating cache for chain=%s and retrying once",
                    index_token_address,
                    chain,
                )
                Markets.invalidate_cache(chain)
                self.markets = Markets(self._gmx_config_for_refresh).get_available_markets()
                all_matches = self.find_all_market_keys_by_index_address(self.markets, index_token_address)

            if not all_matches:
                available = [v.get("index_token_address") for v in self.markets.values()]
                logger.info(
                    "_handle_missing_market_key: NOT FOUND for index_token_address=%s — available=%s",
                    index_token_address,
                    available,
                )
                msg = f"No GMX market found for index_token_address={index_token_address!r} after forced cache refresh. Available index_token_addresses: {available}"
                raise ValueError(msg)

        if len(all_matches) == 1:
            market_key = all_matches[0]
        else:
            # Multiple pools share this index token.  Disambiguate by collateral.
            collateral_symbol = self.parameters_dict.get("collateral_token_symbol") or self.parameters_dict.get("collateral_symbol")
            market_key = None
            if collateral_symbol:
                tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
                # Resolve collateral symbol to address
                collateral_address = None
                for addr, meta in tokens.items():
                    if meta.get("symbol") == collateral_symbol:
                        collateral_address = Web3.to_checksum_address(addr)
                        break

                if collateral_address:
                    for candidate_key in all_matches:
                        candidate = self.markets[candidate_key]
                        if collateral_address in (
                            candidate.get("long_token_address"),
                            candidate.get("short_token_address"),
                        ):
                            market_key = candidate_key
                            logger.info(
                                "_handle_missing_market_key: selected market %s (collateral %s matches pool tokens)",
                                market_key,
                                collateral_symbol,
                            )
                            break

            if market_key is None:
                # No collateral hint or no match — prefer USDC-paired pools.
                # Standard "<base>-USDC" pools (WBTC-USDC, WETH-USDC, BONK-USDC)
                # always have orders-of-magnitude deeper liquidity than the
                # synthetic single-sided alternatives (tBTC-tBTC etc.), so this
                # mirrors the catalog's USDC_PAIRED default selection strategy.
                # See ``eth_defi.gmx.core.market_catalog.MarketSelection``.
                usdc_candidates = [
                    key
                    for key in all_matches
                    if "USDC"
                    in (
                        (self.markets[key].get("long_token_metadata", {}).get("symbol", "") or "").upper(),
                        (self.markets[key].get("short_token_metadata", {}).get("symbol", "") or "").upper(),
                    )
                ]
                if usdc_candidates:
                    market_key = usdc_candidates[0]
                    logger.info(
                        "_handle_missing_market_key: %d markets share index_token %s, no explicit collateral hint — picked USDC-paired pool %s (USDC candidates: %s, all candidates: %s)",
                        len(all_matches),
                        index_token_address,
                        market_key,
                        usdc_candidates,
                        all_matches,
                    )
                else:
                    market_key = all_matches[0]
                    logger.warning(
                        "_handle_missing_market_key: %d markets share index_token %s, no USDC-paired pool found and no collateral hint — using first match %s. All candidates: %s",
                        len(all_matches),
                        index_token_address,
                        market_key,
                        all_matches,
                    )

        self.parameters_dict["market_key"] = market_key

    def _handle_missing_start_token_address(self):
        """Resolve start token address from symbol."""
        try:
            start_token_symbol = self.parameters_dict["start_token_symbol"]

            # Special handling for BTC on arbitrum
            if start_token_symbol == "BTC" and self.parameters_dict["chain"] == "arbitrum":
                self.parameters_dict["start_token_address"] = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"
                return

        except KeyError:
            msg = "Start Token Address and Symbol not provided!"
            raise Exception(msg)

        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        self.parameters_dict["start_token_address"] = self.find_key_by_symbol(tokens, start_token_symbol)

    def _handle_missing_out_token_address(self):
        """Resolve output token address from symbol."""
        try:
            out_token_symbol = self.parameters_dict["out_token_symbol"]
        except KeyError:
            msg = "Out Token Address and Symbol not provided!"
            raise Exception(msg)

        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        self.parameters_dict["out_token_address"] = self.find_key_by_symbol(tokens, out_token_symbol)

    def _handle_missing_collateral_address(self):
        """Resolve collateral address from symbol."""
        try:
            collateral_token_symbol = self.parameters_dict["collateral_token_symbol"]

            # Debug logging for collateral token flow
            logger.info(
                "COLLATERAL_TRACE: OrderArgumentParser._handle_missing_collateral_address()\n  collateral_token_symbol=%s\n  chain=%s",
                collateral_token_symbol,
                self.parameters_dict["chain"],
            )

            # Special handling for BTC on arbitrum
            if collateral_token_symbol == "BTC" and self.parameters_dict["chain"] == "arbitrum":
                self.parameters_dict["collateral_address"] = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"
                return
        except KeyError:
            msg = "Collateral Token Address and Symbol not provided!"
            raise Exception(msg)

        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        collateral_address = self.find_key_by_symbol(tokens, collateral_token_symbol)

        # Strict collateral validation with exception CLASSIFICATION (issue
        # #1178). Two very different failures used to be conflated by a bare
        # ``except Exception`` here:
        #
        # - InvalidCollateralForMarketError — the market resolved and the
        #   collateral matches neither of its tokens: a DEFINITIVE rejection.
        #   Recorded as ``False`` so _handle_missing_swap_path can fail loudly
        #   when no swap leg will be built (start == collateral). When a real
        #   swap leg exists (start != collateral), the GMX router converts via
        #   ``swap_path`` and the order is still valid (issue #67 flow).
        # - KeyError — market_key absent from the markets snapshot: we could
        #   NOT verify either way (stale/partial snapshot). Recorded as
        #   ``None`` and tolerated — never block an order on "couldn't check".
        #
        # Always set collateral_address so the router has a target.
        try:
            is_directly_supported = self._check_if_valid_collateral_for_market(collateral_address)
        except InvalidCollateralForMarketError:
            is_directly_supported = False
            logger.info(
                "COLLATERAL_TRACE: collateral %s not directly accepted by market_key %s — a swap_path can convert it only when start_token differs from the collateral",
                collateral_token_symbol,
                self.parameters_dict.get("market_key"),
            )
        except KeyError as exc:
            is_directly_supported = None
            logger.warning(
                "COLLATERAL_TRACE: could not verify collateral %s against market_key %s (%r missing from markets snapshot) — proceeding unverified",
                collateral_token_symbol,
                self.parameters_dict.get("market_key"),
                exc,
            )
        self._collateral_directly_supported = is_directly_supported
        logger.info(
            "COLLATERAL_TRACE: Resolved collateral address:\n  collateral_token_symbol=%s → collateral_address=%s\n  is_directly_supported_by_market=%s",
            collateral_token_symbol,
            collateral_address,
            is_directly_supported,
        )

        if not self.is_swap:
            self.parameters_dict["collateral_address"] = collateral_address
            logger.info(
                "COLLATERAL_TRACE: Final collateral address set:\n  parameters_dict['collateral_address']=%s",
                self.parameters_dict["collateral_address"],
            )

    def _handle_missing_swap_path(self):
        """Determine swap path between tokens."""
        if self.is_swap:
            markets = self.markets
            try:
                self.parameters_dict["swap_path"] = determine_swap_route(
                    markets,
                    self.parameters_dict["start_token_address"],
                    self.parameters_dict["out_token_address"],
                    chain=self.parameters_dict["chain"],
                )[0]
            except TypeError:
                error_message = f"No markets available for {self.parameters_dict['start_token_address']} token"
                raise RuntimeError(error_message)

        # No swap needed if start token == collateral token
        elif self.parameters_dict["start_token_address"] == self.parameters_dict["collateral_address"]:
            # Fail loud on a definitively-rejected collateral (issue #1178).
            # When start == collateral, NO swap leg will be built below — so the
            # issue-#67 "router converts via swap_path" fallback cannot apply.
            # Shipping the order would burn gas and die on-chain as an
            # InvalidCollateralTokenForMarket keeper cancel + circuit-breaker
            # lock. Only ``False`` (definitive rejection) blocks; ``None``
            # (indeterminate / unchecked) and ``True`` proceed as before.
            if self._collateral_directly_supported is False:
                raise InvalidCollateralForMarketError(f"Collateral is not accepted by the selected market and no swap path can convert it (start_token == collateral_address):\n  market_key: {self.parameters_dict.get('market_key')}\n  collateral_address: {self.parameters_dict['collateral_address']}\n  Hint: choose a market that accepts this collateral, or supply a start_token different from the collateral so a swap route can be built.")
            self.parameters_dict["swap_path"] = []

        else:
            markets = self.markets
            self.parameters_dict["swap_path"] = determine_swap_route(
                markets,
                self.parameters_dict["start_token_address"],
                self.parameters_dict["collateral_address"],
                chain=self.parameters_dict["chain"],
            )[0]

    @staticmethod
    def _handle_missing_is_long(self):
        """is_long must be supplied by user."""
        msg = "Please indicate if position is_long!"
        raise Exception(msg)

    @staticmethod
    def _handle_missing_slippage_percent(self):
        """slippage_percent must be supplied by user."""
        msg = "Please indicate slippage!"
        raise Exception(msg)

    def _check_if_valid_collateral_for_market(self, collateral_address: str) -> bool:
        """Check whether ``collateral_address`` is directly held by the market.

        Returns ``True`` when the collateral matches the market's long or short
        token — a **definitive accept**. Raises
        :class:`InvalidCollateralForMarketError` (a definitive *reject*) when
        the market resolves but the collateral matches neither token. Lets a
        ``KeyError`` propagate when ``market_key`` is absent from the markets
        snapshot — an *indeterminate* result the caller must distinguish from a
        reject (see :meth:`_handle_missing_collateral_address`).

        :param collateral_address: Collateral token contract address.
        :returns: ``True`` when the collateral is directly accepted.
        :raises InvalidCollateralForMarketError: When ``collateral_address`` is
            not the long or short token of the selected market, with market_key,
            valid token addresses, and a hint in the message.
        :raises KeyError: When ``market_key`` is not present in the markets
            snapshot — the caller treats this as indeterminate, not a reject.
        """
        market_key = self.parameters_dict["market_key"]

        # Special handling for WBTC market
        if self.parameters_dict["market_key"] == "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f":
            market_key = "0x47c031236e19d024b42f8AE6780E44A573170703"

        market = self.markets[market_key]

        if collateral_address in (
            market["long_token_address"],
            market["short_token_address"],
        ):
            return True

        # Rejection is already definitive at this point (address check above
        # proved it) — metadata is cosmetic for the message only. Use .get()
        # defensively so a market dict missing long/short_token_metadata
        # (adversarial-review finding) raises the intended
        # InvalidCollateralForMarketError instead of a KeyError that the
        # caller would misclassify as "indeterminate" and let the order
        # proceed unverified.
        long_symbol = market.get("long_token_metadata", {}).get("symbol", "?")
        short_symbol = market.get("short_token_metadata", {}).get("symbol", "?")
        msg = f"Not a valid collateral for selected market!\n  market_key: {market_key}\n  collateral_address: {collateral_address}\n  valid long_token: {market['long_token_address']} ({long_symbol})\n  valid short_token: {market['short_token_address']} ({short_symbol})\n  Hint: set collateral_symbol to the long or short token symbol of this market."
        raise InvalidCollateralForMarketError(msg)

    @staticmethod
    def find_key_by_symbol(input_dict: dict, search_symbol: str):
        """Find token address by symbol in metadata dict."""
        for key, value in input_dict.items():
            if value.get("symbol") == search_symbol:
                # Debug logging for collateral token flow
                logger.info(
                    "COLLATERAL_TRACE: find_key_by_symbol() FOUND\n  search_symbol=%s → address=%s\n  token_metadata=%s",
                    search_symbol,
                    key,
                    value,
                )
                return key
        msg = f'"{search_symbol}" not a known token for GMX v2!'
        raise Exception(msg)

    @staticmethod
    def find_all_market_keys_by_index_address(input_dict: dict, index_token_address: str) -> list[str]:
        """Return all market keys whose index token matches ``index_token_address``.

        Multiple pools can share the same index token (e.g. BTC/USD has WBTC-USDC
        and tBTC-tBTC on Arbitrum).  Unlike :meth:`find_market_key_by_index_address`,
        this method returns every match so callers can apply secondary disambiguation
        (e.g. by collateral token).

        :param input_dict: Markets dict keyed by market address (from :class:`Markets`)
        :param index_token_address: Index token address to search for
        :return: List of matching market address keys (may be empty)
        """
        checksum_address = Web3.to_checksum_address(index_token_address) if index_token_address else None
        lower_address = checksum_address.lower() if checksum_address else None
        # Compare lowercase on both sides so the method works regardless of whether
        # stored addresses are checksummed (RPC/GraphQL path) or lowercase (REST API path).
        matches = [key for key, value in input_dict.items() if value.get("index_token_address", "").lower() == lower_address]
        # Stable deterministic order: standard pools (long_token != short_token) before
        # single-token loop pools (ETH2, BTC2 where long==short), then by market address.
        # This ensures the same pool is picked regardless of the iteration order of the
        # input_dict (e.g. after a REST-API market refresh reorders the cache).
        if len(matches) > 1:
            matches.sort(
                key=lambda k: (
                    1 if input_dict[k].get("long_token_address", "").lower() == input_dict[k].get("short_token_address", "").lower() else 0,
                    k.lower(),
                )
            )
        logger.info(
            "find_all_market_keys_by_index_address: address=%s found %d match(es): %s",
            checksum_address,
            len(matches),
            matches,
        )
        return matches

    @staticmethod
    def find_market_key_by_index_address(input_dict: dict, index_token_address: str):
        """Find market key by index token address.

        Returns the first matching market key.  When multiple pools share the same
        index token, use :meth:`find_all_market_keys_by_index_address` together with
        collateral-based disambiguation instead.

        Normalises ``index_token_address`` to EIP-55 checksum form before comparing
        against stored market entries (which are already checksummed by :class:`Markets`).
        """
        checksum_address = Web3.to_checksum_address(index_token_address) if index_token_address else None
        logger.info("find_market_key_by_index_address: searching for checksummed address=%s", checksum_address)
        for key, value in input_dict.items():
            if value.get("index_token_address") == checksum_address:
                logger.info("find_market_key_by_index_address: FOUND market_key=%s", key)
                return key
        available = [v.get("index_token_address") for v in input_dict.values()]
        logger.info(
            "find_market_key_by_index_address: NOT FOUND for %s — available index_token_addresses: %s",
            checksum_address,
            available,
        )
        return None

    def calculate_missing_position_size_info_keys(self):
        """Calculate missing parameters from size/collateral/leverage combinations."""
        # Both size and collateral provided
        if "size_delta_usd" in self.parameters_dict and "initial_collateral_delta" in self.parameters_dict:
            return self.parameters_dict

        # For decrease orders (SL/TP), only size_delta_usd is required
        # initial_collateral_delta defaults to 0 (GMX handles collateral withdrawal)
        if self.is_decrease and "size_delta_usd" in self.parameters_dict and "initial_collateral_delta" not in self.parameters_dict:
            self.parameters_dict["initial_collateral_delta"] = 0
            return self.parameters_dict

        # Leverage + collateral provided, calculate size
        elif "leverage" in self.parameters_dict and "initial_collateral_delta" in self.parameters_dict and "size_delta_usd" not in self.parameters_dict:
            initial_collateral_delta_usd = self._calculate_initial_collateral_usd()
            self.parameters_dict["size_delta_usd"] = self.parameters_dict["leverage"] * initial_collateral_delta_usd
            return self.parameters_dict

        # Size + leverage provided, calculate collateral
        elif "size_delta_usd" in self.parameters_dict and "leverage" in self.parameters_dict and "initial_collateral_delta" not in self.parameters_dict:
            collateral_usd = self.parameters_dict["size_delta_usd"] / self.parameters_dict["leverage"]
            self.parameters_dict["initial_collateral_delta"] = self._calculate_initial_collateral_tokens(collateral_usd)
            return self.parameters_dict

        else:
            potential_missing_keys = '"size_delta_usd", "initial_collateral_delta", or "leverage"!'
            msg = f"Required keys are missing or provided incorrectly, please check: {potential_missing_keys}"
            raise Exception(msg)

    def _get_oracle_address_for_token(self, token_address: str, chain: str) -> str:
        """Map testnet token addresses to mainnet equivalents for oracle lookups."""
        # Testnet to mainnet token address mapping
        testnet_to_mainnet_tokens = {
            # Arbitrum Sepolia → Arbitrum mainnet
            "0x980B62Da83eFf3D4576C647993b0c1D7faf17c73": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH
            "0xF79cE1Cf38A09D572b021B4C5548b75A14082F12": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f",  # BTC
            "0x3253a335E7bFfB4790Aa4C25C4250d206E9b9773": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
            "0xD5DdAED48B09fa1D7944bd662CB05265FCD7077C": "0x2bcC6D6CdBbDC0a4071e48bb3B969b06B3330c07",  # SOL
        }

        # For testnet chains, map to mainnet addresses
        if chain in ["arbitrum_sepolia", "avalanche_fuji"]:
            return testnet_to_mainnet_tokens.get(token_address, token_address)
        return token_address

    def _calculate_initial_collateral_usd(self):
        """Calculate USD value of initial collateral tokens."""
        initial_collateral_delta_amount = self.parameters_dict["initial_collateral_delta"]
        prices = OraclePrices(self.parameters_dict["chain"]).get_recent_prices()

        # Map testnet address to mainnet for oracle lookup
        oracle_address = self._get_oracle_address_for_token(self.parameters_dict["start_token_address"], self.parameters_dict["chain"])

        price = np.median(
            [
                float(prices[oracle_address]["maxPriceFull"]),
                float(prices[oracle_address]["minPriceFull"]),
            ]
        )

        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        oracle_factor = tokens[self.parameters_dict["start_token_address"]]["decimals"] - 30
        price = price * 10**oracle_factor

        return price * initial_collateral_delta_amount

    def _calculate_initial_collateral_tokens(self, collateral_usd: float):
        """Calculate token amount from USD value."""
        prices = OraclePrices(self.parameters_dict["chain"]).get_recent_prices()

        # Map testnet address to mainnet for oracle lookup
        oracle_address = self._get_oracle_address_for_token(self.parameters_dict["start_token_address"], self.parameters_dict["chain"])

        price = np.median(
            [
                float(prices[oracle_address]["maxPriceFull"]),
                float(prices[oracle_address]["minPriceFull"]),
            ]
        )

        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])
        oracle_factor = tokens[self.parameters_dict["start_token_address"]]["decimals"] - 30
        price = price * 10**oracle_factor

        return collateral_usd / price

    def _format_size_info(self):
        """Convert amounts to wei with proper decimal precision."""
        if not self.is_swap:
            # USD amounts need 10**30 precision
            # If size_delta_usd is already a large int (> 10^20), it's in raw format (30 decimals)
            # This happens when closing positions using the exact on-chain position size
            size_delta_value = self.parameters_dict["size_delta_usd"]
            if is_raw_usd_amount(size_delta_value):
                # Already in raw format with 30 decimals, use as-is.
                self.parameters_dict["size_delta"] = size_delta_value
                logger.debug(
                    "PRECISION: size_delta_usd is raw int (%s), using as-is",
                    size_delta_value,
                )
            else:
                # Human-readable format, multiply by 10^30
                self.parameters_dict["size_delta"] = int(size_delta_value * 10**30)

        # Apply token-specific decimal factor - use start token for swaps, collateral token for positions
        tokens = _get_token_metadata_dict(self.web3, self.parameters_dict["chain"])

        if self.is_swap:
            # For swaps, use start token decimals
            decimal = tokens[self.parameters_dict["start_token_address"]]["decimals"]
        else:
            # For positions, collateral is in start_token initially, then swapped to collateral_token
            # So we need to use start_token decimals since initial_collateral_delta is in start_token
            decimal = tokens[self.parameters_dict["start_token_address"]]["decimals"]

        self.parameters_dict["initial_collateral_delta"] = int(self.parameters_dict["initial_collateral_delta"] * 10**decimal)

    def _check_if_max_leverage_exceeded(self):
        """Validate leverage doesn't exceed maximum (100x)."""
        collateral_usd_value = self._calculate_initial_collateral_usd
        leverage_requested = self.parameters_dict["size_delta_usd"] / collateral_usd_value()

        max_leverage = 100
        if leverage_requested > max_leverage:
            msg = f'Leverage requested "x{leverage_requested:.2f}" cannot exceed x100!'
            raise Exception(msg)
