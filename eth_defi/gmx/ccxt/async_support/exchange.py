"""Async GMX exchange following CCXT patterns with true async I/O.

This module provides a full async implementation using aiohttp for HTTP calls,
AsyncWeb3 for blockchain operations, and async GraphQL for Subsquid queries.
"""

import asyncio
import os
from datetime import datetime
import logging
from pathlib import Path
from statistics import median
from typing import Any

import aiohttp
from ccxt.async_support import Exchange
from ccxt.base.errors import (
    ExchangeError,
    NotSupported,
    OrderNotFound,
)
from eth_utils import to_checksum_address
from web3 import AsyncWeb3

from eth_defi.chain import get_chain_name
from eth_defi.gmx.cache import GMXMarketCache
from eth_defi.gmx.ccxt.async_support.async_graphql import AsyncGMXSubsquidClient
from eth_defi.gmx.ccxt.async_support.async_http import async_make_gmx_api_request
from eth_defi.gmx.ccxt.properties import describe_gmx
from eth_defi.gmx.ccxt.validation import _validate_ohlcv_data_sufficiency
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.constants import GMX_MIN_COST_USD, PRECISION
from eth_defi.gmx.core import Markets
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.order.sltp_order import SLTPEntry, SLTPOrder, SLTPParams
from eth_defi.gmx.contracts import get_contract_addresses
from eth_defi.gmx.events import decode_gmx_event, extract_order_key_from_receipt
from eth_defi.gmx.utils import convert_raw_price_to_usd
from eth_defi.gmx.order_tracking import check_order_status
from eth_defi.gmx.verification import verify_gmx_order_execution
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.log_block_range import get_logs_max_block_range
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details

logger = logging.getLogger(__name__)


async def _async_scan_logs_chunked_for_trade_action(
    async_web3: AsyncWeb3,
    sync_web3,
    event_emitter: str,
    order_key: bytes,
    order_key_hex: str,
    from_block: int,
    to_block: int,
) -> dict | None:
    """Async scan EventEmitter logs in chunks for order execution event.

    Uses chunked queries to avoid RPC timeouts on large block ranges.

    :param async_web3:
        AsyncWeb3 instance for async get_logs calls
    :param sync_web3:
        Sync Web3 instance for event decoding (decode_gmx_event is sync)
    :param event_emitter:
        EventEmitter contract address
    :param order_key:
        The 32-byte order key to search for
    :param order_key_hex:
        The order key as hex string (with 0x prefix)
    :param from_block:
        Start block for scanning
    :param to_block:
        End block for scanning
    :return:
        trade_action dict if found, None otherwise
    """
    chunk_size = get_logs_max_block_range(async_web3)
    total_blocks = to_block - from_block + 1

    if total_blocks <= 0:
        return None

    logger.debug(
        "Scanning %d blocks for order %s in chunks of %d",
        total_blocks,
        order_key_hex[:18],
        chunk_size,
    )

    loop = asyncio.get_running_loop()

    for chunk_start in range(from_block, to_block + 1, chunk_size):
        chunk_end = min(chunk_start + chunk_size - 1, to_block)

        logger.debug(
            "Scanning blocks %d-%d for order %s",
            chunk_start,
            chunk_end,
            order_key_hex[:18],
        )

        try:
            logs = await async_web3.eth.get_logs(
                {
                    "address": event_emitter,
                    "fromBlock": chunk_start,
                    "toBlock": chunk_end,
                }
            )

            for log in logs:
                try:
                    # decode_gmx_event is sync, run in executor
                    event = await loop.run_in_executor(
                        None,
                        lambda l=log: decode_gmx_event(sync_web3, l),
                    )
                    if not event:
                        continue

                    if event.event_name not in ("OrderExecuted", "OrderCancelled", "OrderFrozen"):
                        continue

                    # Check if this event matches our order_key
                    event_order_key = event.topic1 or event.get_bytes32("key")
                    if event_order_key != order_key:
                        continue

                    # Found our order's execution event
                    logger.debug(
                        "Found %s event for order %s in block %d via chunked scan",
                        event.event_name,
                        order_key_hex[:18],
                        log["blockNumber"],
                    )

                    # Build trade_action dict from event
                    trade_action = {
                        "eventName": event.event_name,
                        "orderKey": order_key_hex,
                        "isLong": event.get_bool("isLong"),
                        "reason": event.get_string("reasonBytes") if event.event_name == "OrderCancelled" else None,
                        "transaction": {
                            "hash": log["transactionHash"].hex() if isinstance(log["transactionHash"], bytes) else log["transactionHash"],
                        },
                    }

                    # Get execution price if available
                    if event.event_name == "OrderExecuted":
                        exec_price = event.get_uint("executionPrice")
                        if exec_price:
                            trade_action["executionPrice"] = str(exec_price)

                    return trade_action

                except Exception as e:
                    logger.debug("Error decoding log: %s", e)
                    continue

        except Exception as e:
            logger.warning(
                "Error scanning blocks %d-%d for order %s: %s",
                chunk_start,
                chunk_end,
                order_key_hex[:18],
                e,
            )
            # Continue to next chunk - partial failure is acceptable

    return None


class GMX(Exchange):
    """Async GMX exchange with native async I/O.

    Full async implementation following CCXT patterns:
    - Uses aiohttp for HTTP calls (not thread pool wrappers)
    - Uses AsyncWeb3 for blockchain operations
    - Uses async GraphQL for Subsquid queries
    - Supports async context manager pattern
    - Implements proper session management and cleanup

    Example::

        async with GMX({"rpcUrl": "https://arb1.arbitrum.io/rpc"}) as exchange:
            markets = await exchange.load_markets()
            ticker = await exchange.fetch_ticker("ETH/USD")
    """

    # GMX markets that should be skipped due to deprecated or unsupported feeds
    EXCLUDED_SYMBOLS: set[str] = {
        "AI16Z",
        "BTC2",
        "ETH2",
        "GMX2",
        "SOL2",
        "ARB2",
        "APE_DEPRECATED",
    }

    def __init__(self, config: dict | None = None):
        """Initialize async GMX exchange.

        Args:
            config: CCXT-style configuration dict with:
                - rpcUrl: Arbitrum RPC endpoint (required)
                - privateKey: Private key for trading (optional)
                - chainId: Chain ID override (optional)
                - subsquidEndpoint: Custom Subsquid endpoint (optional)
        """
        # Initialize CCXT base class
        super().__init__(config or {})

        # Extract config parameters
        self._rpc_url = config.get("rpcUrl", "") if config else ""
        self._private_key = config.get("privateKey", "") if config else ""
        self._chain_id_override = config.get("chainId") if config else None
        self._subsquid_endpoint = config.get("subsquidEndpoint") if config else None

        # Async components (lazy initialization)
        self.session: aiohttp.ClientSession | None = None
        self.web3: AsyncWeb3 | None = None
        self.sync_web3 = None  # Sync Web3 for helper functions
        self.subsquid: AsyncGMXSubsquidClient | None = None
        self.config: GMXConfig | None = None
        self.wallet: HotWallet | None = None
        self.wallet_address: str | None = None
        self.chain: str | None = None

        # CCXT properties
        self.id = "gmx"
        self.name = "GMX"
        self.countries = ["US"]
        self.rateLimit = 1000
        self.has = describe_gmx()["has"]
        self.timeframes = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "1h": "1h",
            "4h": "4h",
            "1d": "1d",
        }

        # Initialize markets dict (required by CCXT)
        # Will be populated by load_markets()
        if not hasattr(self, "markets") or self.markets is None:
            self.markets = {}

        # Order cache - cleared on fresh runs to avoid stale data
        self._orders = {}

        # Consecutive failure tracking for auto-pause
        self._consecutive_failures = 0
        self._max_consecutive_failures = 3
        self._trading_paused = False
        self._trading_paused_reason = ""
        self._last_failed_tx_hash = None

    def describe(self):
        """Get CCXT exchange description."""
        return describe_gmx()

    def calculate_fee(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float,
        takerOrMaker: str = "taker",
        params: dict = None,
    ) -> dict:
        """Calculate trading fee for GMX positions.

        GMX uses dynamic fees based on pool balancing:
        - Position open/close: 0.04% (balanced) or 0.06% (imbalanced)
        - Normal swaps: 0.05% (balanced) or 0.07% (imbalanced)
        - Stablecoin swaps: 0.005% (balanced) or 0.02% (imbalanced)

        For backtesting, we use a fixed 0.06% (0.0006) which represents
        a realistic middle ground for position trading.

        Args:
            symbol: Trading pair symbol (e.g., "ETH/USD")
            type: Order type (e.g., "market", "limit")
            side: Order side ("buy" or "sell")
            amount: Order amount in base currency
            price: Order price
            takerOrMaker: "taker" or "maker" (not used for GMX)
            params: Additional parameters

        Returns:
            Fee dictionary with rate and cost
        """
        if params is None:
            params = {}

        # GMX fee rate: 0.06% (0.0006) for positions
        rate = 0.0006

        # Get market to determine fee currency
        market = None
        if hasattr(self, "markets") and self.markets and symbol in self.markets:
            market = self.markets[symbol]

        # Fee currency is the settlement currency (USDC for GMX)
        currency = market.get("settle", "USDC") if market else "USDC"

        # Calculate fee cost based on position notional value
        cost = amount * price * rate if price and amount else None

        return {
            "type": takerOrMaker,
            "currency": currency,
            "rate": rate,
            "cost": cost,
        }

    def _build_trading_fee(self, symbol: str, size_delta_usd: float) -> dict:
        """Build a CCXT fee dict for GMX trading fees.

        GMX charges 0.04-0.07% position fees depending on price impact direction.
        We use 0.06% as the standard rate (the conservative/common case).

        Fee is denominated in the settlement/quote currency (typically USDC).

        Args:
            symbol: Trading pair symbol
            size_delta_usd: Position size in USD

        Returns:
            CCXT fee dict with cost, currency, and rate

        See Also:
            https://docs.gmx.io/docs/trading#fees-and-rebates
        """
        rate = 0.0006  # 0.06% - matches calculate_fee()
        market = None
        if hasattr(self, "markets") and self.markets and symbol in self.markets:
            market = self.markets[symbol]
        currency = market.get("settle", "USDC") if market else "USDC"
        cost = abs(size_delta_usd) * rate if size_delta_usd else 0.0
        return {"cost": cost, "currency": currency, "rate": rate}

    async def _ensure_session(self):
        """Lazy-initialize aiohttp session and async components."""
        if self.session is not None:
            return  # Already initialized

        # Create aiohttp session with connection pooling
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            connector=aiohttp.TCPConnector(limit=100, limit_per_host=10),
        )

        # Initialize Web3 (convert to async)
        if not self._rpc_url:
            raise ValueError("rpcUrl is required in config")

        # Create sync Web3 first, then convert to async
        # Note: AsyncWeb3 requires async provider
        # Store sync_web3 for use with sync helper functions (extract_order_key_from_receipt, decode_gmx_event)
        self.sync_web3 = create_multi_provider_web3(self._rpc_url)
        self.web3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(self._rpc_url))

        # Detect chain
        if self._chain_id_override:
            chain_id = self._chain_id_override
        else:
            # Use sync web3 for initialization
            chain_id = self.sync_web3.eth.chain_id

        self.chain = get_chain_name(chain_id).lower()

        # Validate GMX support
        supported_chains = ["arbitrum", "arbitrum_sepolia", "avalanche"]
        if self.chain not in supported_chains:
            raise ValueError(f"GMX not supported on chain {self.chain} (chain_id: {chain_id})")

        # Create wallet if private key provided
        if self._private_key:
            self.wallet = HotWallet.from_private_key(self._private_key)
            # Note: Wallet nonce sync needs to be done separately in async
            self.wallet_address = self.wallet.address

        # Create GMX config
        # Note: GMXConfig expects sync Web3, we'll need to handle this
        self.config = GMXConfig(self.sync_web3, user_wallet_address=self.wallet_address)

        # Initialize Subsquid client
        self.subsquid = AsyncGMXSubsquidClient(
            chain=self.chain,
            custom_endpoint=self._subsquid_endpoint,
        )
        await self.subsquid.__aenter__()

        # Initialise disk cache for markets
        cache_disabled = self.options.get("disable_market_cache") is True or os.environ.get("GMX_DISABLE_MARKET_CACHE", "").lower() == "true"

        cache_dir = self.options.get("market_cache_dir")
        if cache_dir:
            cache_dir = Path(cache_dir)

        try:
            self._market_cache = GMXMarketCache.get_cache(
                chain=self.chain,
                cache_dir=cache_dir,
                disabled=cache_disabled,
            )
        except Exception as e:
            logger.warning("Failed to initialise market cache: %s", e)
            self._market_cache = None

        logger.info("Async GMX exchange session initialized for chain: %s", self.chain)

    async def close(self):
        """Close exchange connection and cleanup resources."""
        if self.subsquid:
            await self.subsquid.close()
            self.subsquid = None

        if self.session:
            await self.session.close()
            self.session = None

        self.web3 = None
        logger.info("Async GMX exchange session closed")

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, *args):
        """Async context manager exit."""
        await self.close()

    async def _load_markets_from_graphql(self) -> dict:
        """Load markets from GraphQL only (for backtesting - no RPC calls).

        Uses GMX API /tokens endpoint to fetch token metadata instead of hardcoding.

        :return: dictionary mapping unified symbols to market info
        :rtype: dict
        """
        try:
            market_infos = await self.subsquid.get_market_infos(limit=200)
            logger.info("Fetched %s markets from GraphQL", len(market_infos))

            # Fetch token data from GMX API using async HTTP
            tokens_data = await self._fetch_tokens_async()
            logger.debug("Fetched tokens from GMX API, type: %s, length: %s", type(tokens_data), len(tokens_data) if isinstance(tokens_data, (list, dict)) else "N/A")

            # Build address->symbol mapping (lowercase addresses for matching)
            address_to_symbol = {}
            if isinstance(tokens_data, dict):
                # If tokens_data is a dict, extract the list of tokens
                tokens_list = tokens_data.get("tokens", [])
            elif isinstance(tokens_data, list):
                tokens_list = tokens_data
            else:
                logger.error("Unexpected tokens_data format: %s", type(tokens_data))
                tokens_list = []

            # Build address->symbol mapping and token metadata (lowercase addresses for matching)
            self._token_metadata = {}
            for token in tokens_list:
                if not isinstance(token, dict):
                    continue
                address = token.get("address", "").lower()
                symbol = token.get("symbol", "")
                decimals = token.get("decimals")
                if address and symbol:
                    if decimals is None:
                        raise ValueError(f"GMX API did not return decimals for token {symbol} ({address}). Cannot safely convert prices.")
                    address_to_symbol[address] = symbol
                    # Store full token metadata including decimals for price conversion
                    self._token_metadata[address] = {
                        "decimals": decimals,
                        "synthetic": token.get("synthetic", False),
                        "symbol": symbol,
                    }

            logger.debug("Built address mapping for %s tokens, metadata for %s tokens", len(address_to_symbol), len(self._token_metadata))

            markets_dict = {}
            for market_info in market_infos:
                try:
                    index_token_addr = market_info.get("indexTokenAddress", "").lower()
                    market_token_addr = market_info.get("marketTokenAddress", "")

                    # Look up symbol from GMX API tokens data
                    symbol_name = address_to_symbol.get(index_token_addr)

                    if not symbol_name:
                        logger.debug("Skipping market with unknown index token: %s", index_token_addr)
                        continue  # Skip unknown tokens

                    # Skip excluded symbols
                    if symbol_name in self.EXCLUDED_SYMBOLS:
                        continue

                    # Use Freqtrade futures format (consistent with regular load_markets)
                    unified_symbol = f"{symbol_name}/USDC:USDC"

                    # Calculate max leverage from minCollateralFactor
                    min_collateral_factor = market_info.get("minCollateralFactor")
                    max_leverage = 50.0  # Default
                    if min_collateral_factor:
                        max_leverage = AsyncGMXSubsquidClient.calculate_max_leverage(min_collateral_factor) or 50.0

                    maintenance_margin_rate = 1.0 / max_leverage if max_leverage > 0 else 0.02

                    markets_dict[unified_symbol] = {
                        "id": symbol_name,
                        "symbol": unified_symbol,
                        "base": symbol_name,
                        "quote": "USDC",
                        "baseId": symbol_name,
                        "quoteId": "USDC",
                        "settle": "USDC",
                        "settleId": "USDC",
                        "active": True,
                        "type": "swap",
                        "spot": False,
                        "margin": True,
                        "swap": True,
                        "future": True,
                        "option": False,
                        "contract": True,
                        "linear": True,
                        "inverse": False,
                        "contractSize": self.parse_number("1"),
                        "precision": {
                            "amount": self.parse_number(self.parse_precision("8")),
                            "price": self.parse_number(self.parse_precision("8")),
                        },
                        "limits": {
                            "amount": {"min": None, "max": None},
                            "price": {"min": None, "max": None},
                            "cost": {"min": GMX_MIN_COST_USD, "max": None},
                            "leverage": {"min": 1.1, "max": max_leverage},
                        },
                        "maintenanceMarginRate": maintenance_margin_rate,
                        "info": {
                            "market_token": market_token_addr,
                            "index_token": market_info.get("indexTokenAddress"),
                            "long_token": market_info.get("longTokenAddress"),
                            "short_token": market_info.get("shortTokenAddress"),
                            "graphql_only": True,  # Flag to indicate this was loaded from GraphQL
                        },
                    }
                except Exception as e:
                    logger.debug("Failed to process market %s: %s", market_info.get("marketTokenAddress"), e)
                    continue

            self.markets = markets_dict
            self.symbols = list(self.markets.keys())

            logger.info("Loaded %s markets from GraphQL", len(self.markets))
            logger.debug("Market symbols: %s", self.symbols)
            return self.markets

        except Exception as e:
            logger.error("Failed to load markets from GraphQL: %s", e)
            # Return empty markets rather than failing completely
            self.markets = {}
            self.symbols = []
            return self.markets

    async def _fetch_tokens_async(self) -> list[dict]:
        """Fetch token data from GMX API asynchronously."""
        try:
            tokens_data = await async_make_gmx_api_request(
                chain=self.chain,
                endpoint="/tokens",
                session=self.session,
                timeout=10.0,
            )
            return tokens_data
        except Exception as e:
            logger.error("Failed to fetch tokens from GMX API: %s", e)
            return []

    async def _load_markets_from_rest_api(self) -> dict:
        """Load markets from GMX REST API asynchronously (fast, comprehensive).

        Fetches market data from /markets/info endpoint which provides:
        - Market metadata (tokens, addresses)
        - Open interest and liquidity
        - Funding rates
        - isListed status

        Uses disk cache for persistence across restarts.

        :return: dictionary mapping unified symbols to market info
        :rtype: dict
        """
        # Try disk cache first
        if self._market_cache:
            try:
                cached_markets = self._market_cache.get_markets(
                    loading_mode="rest_api",
                    check_expiry=True,
                )
                if cached_markets:
                    logger.info("Loaded %s markets from disk cache", len(cached_markets))
                    self.markets = cached_markets
                    self.symbols = list(self.markets.keys())
                    return self.markets
            except Exception as e:
                logger.warning("Failed to load from disk cache: %s", e)

        try:
            # Fetch markets from /markets/info endpoint
            logger.debug("Fetching markets from REST API /markets/info endpoint")
            markets_info = await async_make_gmx_api_request(
                chain=self.chain,
                endpoint="/markets/info",
                params={"marketTokensData": "true"},
                session=self.session,
                timeout=10.0,
            )

            # Fetch token metadata for symbol mapping
            tokens_data = await self._fetch_tokens_async()

            # Build address->token mapping (lowercase for matching)
            self._token_metadata = {}
            if isinstance(tokens_data, dict):
                tokens_list = tokens_data.get("tokens", [])
            elif isinstance(tokens_data, list):
                tokens_list = tokens_data
            else:
                tokens_list = []

            for token in tokens_list:
                if not isinstance(token, dict):
                    continue
                address = token.get("address", "").lower()
                symbol = token.get("symbol", "")
                decimals = token.get("decimals")
                if address and symbol and decimals is not None:
                    self._token_metadata[address] = {
                        "decimals": decimals,
                        "synthetic": token.get("synthetic", False),
                        "symbol": symbol,
                    }

            # Process markets from /markets/info
            markets_dict = {}
            markets_list = markets_info.get("markets", []) if isinstance(markets_info, dict) else []

            for market in markets_list:
                try:
                    # Get market addresses
                    market_token = market.get("marketToken", "")
                    index_token = market.get("indexToken", "").lower()
                    long_token = market.get("longToken", "").lower()
                    short_token = market.get("shortToken", "").lower()

                    # Check if market is listed
                    is_listed = market.get("isListed", True)
                    if not is_listed:
                        logger.debug("Skipping unlisted market %s", market_token)
                        continue

                    # Special case: wstETH market
                    is_wsteth_market = market_token.lower() == "0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5".lower()

                    # Get index token metadata
                    index_meta = self._token_metadata.get(index_token, {})
                    symbol_name = index_meta.get("symbol")

                    if not symbol_name:
                        logger.debug("Skipping market with unknown index token: %s", index_token)
                        continue

                    # Skip excluded symbols
                    if symbol_name in self.EXCLUDED_SYMBOLS:
                        logger.debug("Skipping excluded symbol: %s", symbol_name)
                        continue

                    # Check if synthetic market (long_token == short_token, not wstETH)
                    is_synthetic = (long_token == short_token) and not is_wsteth_market

                    # Create unified symbol
                    if is_synthetic:
                        unified_symbol = f"{symbol_name}/USDC:USDC2"
                    else:
                        unified_symbol = f"{symbol_name}/USDC:USDC"

                    # Get leverage info from subsquid if available
                    max_leverage = 50.0  # Default
                    min_collateral_factor = None

                    if self.subsquid:
                        try:
                            market_infos = await self.subsquid.get_market_infos(limit=200)
                            for mi in market_infos:
                                if mi.get("marketTokenAddress", "").lower() == market_token.lower():
                                    mcf = mi.get("minCollateralFactor")
                                    if mcf:
                                        min_collateral_factor = mcf
                                        max_leverage = AsyncGMXSubsquidClient.calculate_max_leverage(mcf) or 50.0
                                        break
                        except Exception as e:
                            logger.debug("Failed to fetch leverage for %s: %s", market_token, e)

                    maintenance_margin_rate = 1.0 / max_leverage if max_leverage > 0 else 0.02

                    # Build CCXT-compatible market structure
                    markets_dict[unified_symbol] = {
                        "id": symbol_name,
                        "symbol": unified_symbol,
                        "base": symbol_name,
                        "quote": "USDC",
                        "baseId": symbol_name,
                        "quoteId": "USDC",
                        "settle": "USDC",
                        "settleId": "USDC",
                        "active": True,
                        "type": "swap",
                        "spot": False,
                        "margin": True,
                        "swap": True,
                        "future": True,
                        "option": False,
                        "contract": True,
                        "linear": True,
                        "inverse": False,
                        "contractSize": self.parse_number("1"),
                        "maker": 0.0003,
                        "taker": 0.0006,
                        "precision": {
                            "amount": self.parse_number(self.parse_precision("8")),
                            "price": self.parse_number(self.parse_precision("8")),
                        },
                        "limits": {
                            "amount": {"min": None, "max": None},
                            "price": {"min": None, "max": None},
                            "cost": {"min": GMX_MIN_COST_USD, "max": None},
                            "leverage": {"min": 1.1, "max": max_leverage},
                        },
                        "maintenanceMarginRate": maintenance_margin_rate,
                        "info": {
                            "market_token": market_token,
                            "index_token": market.get("indexToken"),
                            "long_token": market.get("longToken"),
                            "short_token": market.get("shortToken"),
                            "min_collateral_factor": min_collateral_factor,
                            "max_leverage": max_leverage,
                            "is_synthetic": is_synthetic,
                            "rest_api": True,  # Flag indicating REST API source
                            **market,  # Include all REST API fields
                        },
                    }

                except Exception as e:
                    logger.debug("Failed to process market %s: %s", market.get("marketToken"), e)
                    continue

            self.markets = markets_dict
            self.symbols = list(self.markets.keys())

            # Save to disk cache
            if self._market_cache and self.markets:
                try:
                    self._market_cache.set_markets(
                        data=self.markets,
                        loading_mode="rest_api",
                        ttl=None,  # Use default TTL
                    )
                    logger.debug("Saved %s markets to disk cache", len(self.markets))
                except Exception as e:
                    logger.warning("Failed to save to disk cache: %s", e)

            logger.info("Loaded %s markets from REST API", len(self.markets))
            return self.markets

        except Exception as e:
            logger.error("Failed to load markets from REST API: %s", e)
            self.markets = {}
            self.symbols = []
            return self.markets

    async def load_markets(self, reload: bool = False, params: dict | None = None) -> dict:
        """Load markets asynchronously.

        Loading modes (in priority order):
        1. REST API (DEFAULT) - Fast (1-2s), official GMX endpoint, comprehensive data
        2. GraphQL - Fast (1-2s), requires subsquid
        3. RPC - Slow (87-217s), most comprehensive on-chain data

        Use options or params to control loading mode:
        - options={'rest_api_mode': False} - Disable REST API mode
        - options={'graphql_only': True} - Force GraphQL mode
        - params={'graphql_only': True} - Force GraphQL mode (CCXT style)

        Args:
            reload: Force reload even if cached
            params: Additional parameters (CCXT compatibility)

        Returns:
            Dictionary mapping symbols to market info
        """
        if not reload and self.markets:
            return self.markets

        await self._ensure_session()

        # Determine loading mode based on configuration
        rest_api_disabled = (params and params.get("rest_api_mode") is False) or self.options.get("rest_api_mode") is False

        use_graphql_only = (params and params.get("graphql_only") is True) or self.options.get("graphql_only") is True

        # Loading mode selection:
        # 1. If REST API not disabled and not forcing GraphQL -> REST API (NEW DEFAULT)
        # 2. If GraphQL explicitly requested -> GraphQL
        # 3. Otherwise -> RPC (fallback)

        if not rest_api_disabled and not use_graphql_only:
            logger.info("Loading markets from REST API (default mode)")
            return await self._load_markets_from_rest_api()

        if use_graphql_only and self.subsquid:
            logger.info("Loading markets from GraphQL (graphql_only=True)")
            return await self._load_markets_from_graphql()

        # RPC mode (fallback)
        # Fetch markets list (this will need async version of Markets class)
        # For now, we'll call the sync method in executor as a bridge
        # TODO: Create fully async Markets implementation
        logger.info("Loading markets from RPC (Core Markets module)")
        loop = asyncio.get_running_loop()

        markets_instance = Markets(self.config)
        available_markets = await loop.run_in_executor(None, markets_instance.get_available_markets)

        # Fetch leverage data from subsquid if available
        leverage_by_market = {}
        min_collateral_by_market = {}
        if self.subsquid:
            try:
                market_infos = await self.subsquid.get_market_infos(limit=200)
                for market_info in market_infos:
                    market_addr = market_info.get("marketTokenAddress")
                    min_collateral_factor = market_info.get("minCollateralFactor")
                    if market_addr and min_collateral_factor:
                        market_addr = to_checksum_address(market_addr)
                        max_leverage = AsyncGMXSubsquidClient.calculate_max_leverage(min_collateral_factor)
                        if max_leverage is not None:
                            leverage_by_market[market_addr] = max_leverage
                            min_collateral_by_market[market_addr] = min_collateral_factor
            except Exception as e:
                logger.warning("Failed to fetch leverage data from subsquid: %s", e)

        # Process markets into CCXT-style format (matching sync version exactly)
        for market_address, market_data in available_markets.items():
            symbol_name = market_data.get("market_symbol", "")
            if not symbol_name or symbol_name == "UNKNOWN":
                continue

            if symbol_name in self.EXCLUDED_SYMBOLS:
                logger.debug(
                    "Skipping excluded GMX market %s (address %s)",
                    symbol_name,
                    market_address,
                )
                continue

            # Create unified symbol for Freqtrade futures (e.g., ETH/USDC:USDC)
            unified_symbol = f"{symbol_name}/USDC:USDC"

            # Get max leverage for this market
            max_leverage = leverage_by_market.get(market_address)
            min_collateral_factor = min_collateral_by_market.get(market_address)

            # Calculate maintenance margin rate from min collateral factor
            # maintenanceMarginRate = 1 / max_leverage (approximately)
            # If max_leverage is 50x, maintenance margin is ~2%
            # Default to 0.02 (2%, equivalent to 50x leverage) if not available
            maintenance_margin_rate = (1.0 / max_leverage) if max_leverage else 0.02

            self.markets[unified_symbol] = {
                "id": symbol_name,  # GMX market symbol
                "symbol": unified_symbol,  # CCXT unified symbol
                "base": symbol_name,  # Base currency (e.g., ETH)
                "quote": "USDC",  # Quote currency (settlement in USDC)
                "baseId": symbol_name,
                "quoteId": "USDC",
                "settle": "USDC",  # Settlement currency
                "settleId": "USDC",  # Settlement currency ID
                "active": True,
                "type": "swap",  # GMX provides perpetual swaps
                "spot": False,
                "margin": True,
                "swap": True,
                "future": True,
                "option": False,
                "contract": True,
                "linear": True,
                "inverse": False,
                "contractSize": self.parse_number("1"),
                "maker": 0.0003,
                "taker": 0.0006,
                "precision": {
                    "amount": self.parse_number(self.parse_precision("8")),
                    "price": self.parse_number(self.parse_precision("8")),
                },
                "limits": {
                    "amount": {"min": None, "max": None},
                    "price": {"min": None, "max": None},
                    "cost": {"min": GMX_MIN_COST_USD, "max": None},
                    "leverage": {"min": 1.1, "max": max_leverage},
                },
                "maintenanceMarginRate": maintenance_margin_rate,
                "info": {
                    "market_token": market_address,  # Market contract address
                    "index_token": market_data.get("index_token_address"),
                    "long_token": market_data.get("long_token_address"),
                    "short_token": market_data.get("short_token_address"),
                    "min_collateral_factor": min_collateral_factor,
                    "max_leverage": max_leverage,
                    **market_data,
                },
            }

        # Update symbols list (CCXT compatibility)
        self.symbols = list(self.markets.keys())

        return self.markets

    async def fetch_markets(self, params: dict | None = None) -> list[dict]:
        """Fetch all available markets.

        Args:
            params: Additional parameters

        Returns:
            List of market structures
        """
        markets = await self.load_markets(reload=True)
        return list(markets.values())

    def market(self, symbol: str) -> dict:
        """Get market structure for symbol.

        This is a sync method (not async) following CCXT patterns.
        Markets must be loaded before calling this.

        Args:
            symbol: Market symbol (e.g., "ETH/USD")

        Returns:
            Market structure dict

        Raises:
            ValueError: If markets not loaded or symbol not found
        """
        if not self.markets:
            raise ValueError(f"Markets not loaded for {symbol}. Call 'await exchange.load_markets()' first.")

        if symbol not in self.markets:
            raise ValueError(f"Market {symbol} not found. Available: {list(self.markets.keys())}")

        return self.markets[symbol]

    async def fetch_ticker(self, symbol: str, params: dict | None = None) -> dict:
        """Fetch ticker for a single market.

        Args:
            symbol: Market symbol (e.g., "ETH/USD")
            params: Additional parameters

        Returns:
            Ticker dictionary with price and stats
        """
        await self._ensure_session()
        await self.load_markets()

        market = self.market(symbol)
        token_symbol = market["id"]

        # Fetch from GMX API
        data = await async_make_gmx_api_request(
            chain=self.chain,
            endpoint="/prices/tickers",
            session=self.session,
        )

        # Find ticker for this token
        ticker_data = None
        if isinstance(data, list):
            for item in data:
                if item.get("tokenSymbol") == token_symbol:
                    ticker_data = item
                    break

        if not ticker_data:
            raise ExchangeError(f"Ticker data not found for {symbol}")

        # Parse to CCXT format
        min_price = float(ticker_data.get("minPrice", 0)) / 1e30
        max_price = float(ticker_data.get("maxPrice", 0)) / 1e30
        last = (min_price + max_price) / 2

        return {
            "symbol": symbol,
            "timestamp": None,
            "datetime": None,
            "high": max_price,
            "low": min_price,
            "bid": min_price,
            "ask": max_price,
            "last": last,
            "close": last,
            "baseVolume": None,
            "quoteVolume": None,
            "info": ticker_data,
        }

    async def fetch_tickers(self, symbols: list[str] | None = None, params: dict | None = None) -> dict:
        """Fetch tickers for multiple markets concurrently.

        Args:
            symbols: List of symbols (if None, fetch all)
            params: Additional parameters

        Returns:
            Dictionary mapping symbols to tickers
        """
        await self.load_markets()

        if symbols is None:
            symbols = list(self.markets.keys())

        # Fetch all concurrently
        tasks = [self.fetch_ticker(symbol, params) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Build result dict, filtering out errors
        tickers = {}
        for symbol, result in zip(symbols, results):
            if not isinstance(result, Exception):
                tickers[symbol] = result

        return tickers

    async def fetch_apy(
        self,
        symbol: str | None = None,
        period: str = "30d",
        params: dict | None = None,
    ) -> dict[str, Any] | float | None:
        """Fetch APY (Annual Percentage Yield) data for GMX markets asynchronously.

        Retrieves yield data from GMX REST API with disk caching support.
        Can fetch APY for a specific market or all markets at once.

        :param symbol:
            CCXT market symbol (e.g., "ETH/USDC:USDC").
            If None, returns APY for all markets as a dictionary.
        :param period:
            Time period for APY calculation.
            Valid values: '1d', '7d', '30d', '90d', '180d', '1y', 'total'
            Default: '30d'
        :param params:
            Optional parameters (not used currently)
        :return:
            If symbol is specified: float APY value or None if not found
            If symbol is None: dict mapping symbols to APY values

        Example::

            # Fetch 30-day APY for specific market
            apy = await gmx.fetch_apy("ETH/USDC:USDC", period="30d")
            print(f"ETH/USDC APY: {apy * 100:.2f}%")

            # Fetch APY for all markets
            all_apy = await gmx.fetch_apy(period="7d")
            for symbol, apy_value in all_apy.items():
                print(f"{symbol}: {apy_value * 100:.2f}%")
        """
        params = params or {}

        # Ensure session is initialized
        await self._ensure_session()

        # Ensure markets are loaded
        if not self.markets:
            await self.load_markets()

        # Try disk cache first
        cached_apy = None
        if self._market_cache:
            try:
                cached_apy = self._market_cache.get_apy(period=period, check_expiry=True)
                if cached_apy:
                    logger.debug("Using cached APY data for period %s", period)
            except Exception as e:
                logger.warning("Failed to read APY from disk cache: %s", e)

        # Fetch from API if cache miss
        if cached_apy is None:
            try:
                apy_response = await async_make_gmx_api_request(
                    chain=self.chain,
                    endpoint="/apy",
                    params={"period": period},
                    session=self.session,
                    timeout=10.0,
                )
                cached_apy = apy_response.get("markets", {}) if isinstance(apy_response, dict) else {}

                # Save to disk cache
                if self._market_cache and cached_apy:
                    try:
                        self._market_cache.set_apy(
                            data=cached_apy,
                            period=period,
                            ttl=None,  # Use default TTL from constants
                        )
                        logger.debug("Saved APY data to disk cache for period %s", period)
                    except Exception as e:
                        logger.warning("Failed to save APY to disk cache: %s", e)

            except Exception as e:
                logger.error("Failed to fetch APY data from API: %s", e)
                return None if symbol else {}

        # Build mapping of market token address to CCXT symbol
        market_token_to_symbol = {}
        for ccxt_symbol, market_info in self.markets.items():
            market_token = market_info["info"].get("market_token", "").lower()
            if market_token:
                market_token_to_symbol[market_token] = ccxt_symbol

        # If symbol specified, return APY for that market only
        if symbol is not None:
            # Get market info for this symbol
            market = self.market(symbol)
            market_token = market["info"].get("market_token", "").lower()

            # Look up APY by market token address (case-insensitive)
            for addr, apy_data in cached_apy.items():
                if addr.lower() == market_token:
                    # Return base APY value
                    return apy_data.get("apy", 0.0)

            # Market not found in APY data
            logger.warning("No APY data found for %s (market token: %s)", symbol, market_token)
            return None

        # Return APY for all markets (map from market token addresses to CCXT symbols)
        result = {}
        for market_token_addr, apy_data in cached_apy.items():
            market_token_lower = market_token_addr.lower()

            # Find CCXT symbol for this market token
            if market_token_lower in market_token_to_symbol:
                ccxt_symbol = market_token_to_symbol[market_token_lower]
                result[ccxt_symbol] = apy_data.get("apy", 0.0)

        return result

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: int | None = None,
        limit: int | None = None,
        params: dict | None = None,
    ) -> list[list]:
        """Fetch OHLCV candlestick data.

        Args:
            symbol: Market symbol
            timeframe: Candle interval (1m, 5m, 15m, 1h, 4h, 1d)
            since: Start timestamp in ms (for filtering)
            limit: Max number of candles
            params: Additional parameters (e.g., {"skip_validation": True})

        Returns:
            List of OHLCV candles [timestamp, open, high, low, close, volume]

        Raises:
            ValueError: If invalid timeframe
            InsufficientHistoricalDataError: If insufficient data for requested time range (when since is specified)
        """
        await self._ensure_session()
        await self.load_markets()

        market = self.market(symbol)
        token_symbol = market["id"]

        if timeframe not in self.timeframes:
            raise ValueError(f"Invalid timeframe: {timeframe}")

        gmx_period = self.timeframes[timeframe]

        # Default limit if not provided
        if limit is None:
            limit = 10000

        # Fetch from GMX API
        data = await async_make_gmx_api_request(
            chain=self.chain,
            endpoint="/prices/candles",
            params={"tokenSymbol": token_symbol, "period": gmx_period, "limit": limit},
            session=self.session,
        )

        candles_data = data.get("candles", [])

        # Parse candles
        # API returns candles as arrays: [timestamp, open, high, low, close]
        ohlcv = []
        for candle in candles_data:
            # candle is an array: [timestamp, open, high, low, close]
            timestamp = int(candle[0]) * 1000  # Convert to ms

            # Filter by since if provided
            if since and timestamp < since:
                continue

            o = float(candle[1])  # open
            h = float(candle[2])  # high
            l = float(candle[3])  # low
            c = float(candle[4])  # close
            v = 1.0  # GMX doesn't provide volume, use dummy value to avoid Freqtrade filtering

            ohlcv.append([timestamp, o, h, l, c, v])

        # Sort by timestamp
        ohlcv.sort(key=lambda x: x[0])

        # Apply limit
        if limit:
            ohlcv = ohlcv[-limit:]

        # Validate data sufficiency for backtesting
        _validate_ohlcv_data_sufficiency(
            ohlcv=ohlcv,
            symbol=symbol,
            timeframe=timeframe,
            since=since,
            params=params,
        )

        return ohlcv

    # Unsupported methods (GMX protocol limitations)
    async def fetch_order_book(self, symbol: str, limit: int | None = None, params: dict | None = None):
        """Not supported - GMX uses liquidity pools."""
        raise NotSupported(f"{self.id} fetch_order_book() not supported - GMX uses liquidity pools")

    def clear_order_cache(self):
        """Clear the in-memory order cache.

        Call this when switching strategies or starting a fresh session
        to avoid stale order data from previous runs.
        """
        self._orders = {}
        logger.info("Cleared order cache")

    async def cancel_order(self, id: str, symbol: str | None = None, params: dict | None = None):
        """Not supported - GMX orders execute immediately."""
        raise NotSupported(f"{self.id} cancel_order() not supported - GMX orders execute immediately")

    def _get_token_decimals(self, market: dict | None) -> int | None:
        """Get token decimals from market metadata.

        Token metadata is populated during load_markets() from the GMX API,
        which returns correct decimals for each token (e.g., BTC=8, ETH=18).

        :param market: Market structure with info containing index_token address
        :return: Token decimals or None if not found
        """
        if not market or not isinstance(market, dict):
            raise ValueError("Market must be provided to get token decimals.")

        # Token metadata is populated during load_markets from GMX API
        if not getattr(self, "_token_metadata", None):
            raise ValueError("Token metadata not loaded. Ensure load_markets() was called first.")

        info = market.get("info", {}) or {}
        index_addr = (info.get("index_token") or info.get("indexTokenAddress") or "").lower()
        if not index_addr:
            raise ValueError(f"Market {market.get('symbol', 'unknown')} has no index_token address. Cannot determine decimals.")

        token_meta = self._token_metadata.get(index_addr)
        if not token_meta:
            raise ValueError(f"Token metadata not found for index token {index_addr}. Ensure load_markets() was called.")

        decimals = token_meta.get("decimals")
        if decimals is None:
            raise ValueError(f"Decimals not found for token {index_addr}. GMX API response is missing decimals.")

        return int(decimals)

    def _convert_price_to_usd(self, raw_price: float | int | None, market: dict | None) -> float | None:
        """Convert GMX raw price to USD using the standard formula.

        Uses the same conversion as open_positions.py:
            price_usd = raw_price / 10^(30 - token_decimals)

        :param raw_price: Raw price from GMX (may be in 30-decimal format or already USD)
        :param market: Market structure to get token decimals
        :return: Price in USD or None
        """
        if raw_price is None:
            return None

        try:
            v = float(raw_price)
        except Exception:
            return None

        # If already looks like a valid USD price, return as-is
        # This handles data that's already been converted (e.g., from GetOpenPositions)
        if 0.01 <= v <= 1_000_000:
            return v

        # Get token decimals from market metadata
        token_decimals = self._get_token_decimals(market)
        if token_decimals is None:
            # Cannot convert without knowing token decimals
            # Return as-is - caller should ensure proper conversion upstream
            return v

        return convert_raw_price_to_usd(v, token_decimals)

    async def fetch_order(self, id: str, symbol: str | None = None, params: dict | None = None):
        """Fetch order by ID (transaction hash).

        Returns the order that was created with the given transaction hash.
        For orders with status "open", this method checks the GMX DataStore
        and EventEmitter to determine if the keeper has executed the order.

        GMX uses a two-phase execution model:
        1. Order Creation - User submits, receives status "open"
        2. Keeper Execution - Keeper executes, status changes to "closed" or "cancelled"

        This method is called by Freqtrade to poll for order status updates.

        :param id: Order ID (transaction hash of order creation)
        :type id: str
        :param symbol: Symbol (not used, for CCXT compatibility)
        :type symbol: str | None
        :param params: Additional parameters (not used)
        :type params: dict | None
        :return: CCXT-compatible order structure
        :rtype: dict
        :raises OrderNotFound: If order with given ID doesn't exist
        """
        logger.debug(
            "ORDER_TRACE: fetch_order() CALLED (async) - order_id=%s, symbol=%s",
            id[:16] if id else "None",
            symbol,
        )

        # Check if order exists in stored orders
        if id in self._orders:
            order = self._orders[id].copy()
            logger.info(
                "ORDER_TRACE: fetch_order(%s) - FOUND IN CACHE (async) - status=%s, filled=%.8f, remaining=%.8f",
                id[:16],
                order.get("status"),
                order.get("filled", 0),
                order.get("remaining", 0),
            )

            # Convert price fields to USD if present and market known
            try:
                mkt = None
                sym = order.get("symbol")
                if sym and getattr(self, "markets_loaded", False):
                    mkt = self.market(sym)
                if "price" in order:
                    order["price"] = self._convert_price_to_usd(order.get("price"), mkt)
                if "average" in order:
                    order["average"] = self._convert_price_to_usd(order.get("average"), mkt)
            except Exception:
                pass

            # If already closed/cancelled/failed, return cached status
            if order.get("status") in ("closed", "cancelled", "failed"):
                logger.debug("fetch_order(%s): returning cached status=%s", id[:16], order.get("status"))
                return order

            # Order is "open" - check if keeper has executed
            order_key_hex = order.get("info", {}).get("order_key")
            if not order_key_hex:
                logger.warning("fetch_order(%s): no order_key stored, cannot check execution status", id[:16])
                return order

            order_key = bytes.fromhex(order_key_hex)

            # Check if order still pending in DataStore (using sync call via asyncio)
            try:
                # Run sync function in thread pool
                status_result = await asyncio.get_running_loop().run_in_executor(None, lambda: check_order_status(self.web3, order_key, self.chain))
            except Exception as e:
                logger.warning("fetch_order(%s): error checking order status: %s", id[:16], e)
                return order

            if status_result.is_pending:
                # Still waiting for keeper execution
                logger.debug("fetch_order(%s): order still pending (waiting for keeper)", id[:16])
                return order

            # Order no longer pending - verify execution result
            if status_result.execution_receipt:
                verification = verify_gmx_order_execution(
                    self.web3,
                    status_result.execution_receipt,
                    order_key,
                )

                if verification.success:
                    # Order executed successfully
                    order["status"] = "closed"
                    order["filled"] = order["amount"]
                    order["remaining"] = 0.0

                    # Convert raw execution_price using token-specific decimals
                    # verification.execution_price is in raw format (30 decimals)
                    symbol = order.get("symbol")
                    if symbol and verification.execution_price:
                        market = self.markets[symbol]
                        order["average"] = self._convert_price_to_usd(verification.execution_price, market)
                    else:
                        # Fallback: keep raw value if symbol not available (shouldn't happen)
                        order["average"] = verification.execution_price

                    order["lastTradeTimestamp"] = self.milliseconds()

                    # Calculate cost based on actual execution price
                    if order.get("average") and order["amount"]:
                        order["cost"] = order["amount"] * order["average"]

                    # Update fee: replace execution fee (ETH gas) with trading fee (USD)
                    if order.get("fee"):
                        order["info"]["execution_fee_eth"] = order["fee"].get("cost")
                    size_for_fee = verification.size_delta_usd or order.get("cost")
                    if size_for_fee:
                        order["fee"] = self._build_trading_fee(order.get("symbol", ""), size_for_fee)

                    # Update info with verification data
                    order["info"]["execution_tx_hash"] = status_result.execution_tx_hash
                    order["info"]["execution_receipt"] = status_result.execution_receipt
                    order["info"]["execution_block"] = status_result.execution_block
                    order["info"]["verification"] = {
                        "execution_price": verification.execution_price,
                        "size_delta_usd": verification.size_delta_usd,
                        "pnl_usd": verification.pnl_usd,
                        "price_impact_usd": verification.price_impact_usd,
                        "event_count": verification.event_count,
                        "event_names": verification.event_names,
                        "is_long": verification.is_long,
                        "fees": {
                            "position_fee": verification.fees.position_fee,
                            "borrowing_fee": verification.fees.borrowing_fee,
                            "funding_fee": verification.fees.funding_fee,
                        }
                        if verification.fees
                        else None,
                    }

                    logger.info(
                        "ORDER_TRACE: fetch_order(%s) - Order EXECUTED (async) at price=%.2f, size_usd=%.2f - RETURNING status=closed",
                        id[:16],
                        verification.execution_price or 0,
                        verification.size_delta_usd or 0,
                    )
                else:
                    # Order was cancelled or frozen
                    order["status"] = "cancelled"
                    order["filled"] = 0.0
                    order["remaining"] = order["amount"]

                    # Update info with cancellation details
                    order["info"]["execution_tx_hash"] = status_result.execution_tx_hash
                    order["info"]["execution_receipt"] = status_result.execution_receipt
                    order["info"]["execution_block"] = status_result.execution_block
                    order["info"]["cancellation_reason"] = verification.decoded_error or verification.reason
                    order["info"]["event_names"] = verification.event_names

                    logger.warning(
                        "ORDER_TRACE: fetch_order(%s) - Order CANCELLED (async) - reason=%s, events=%s - RETURNING status=cancelled",
                        id[:16],
                        verification.decoded_error or verification.reason,
                        verification.event_names,
                    )

                # Update cache with new status
                self._orders[id] = order

            else:
                # Order removed from DataStore but no execution receipt found
                # check_order_status() already logged detailed diagnostics (Subsquid + log scan)
                logger.warning(
                    "fetch_order(%s): order removed from DataStore but no execution event found (see check_order_status logs for details)",
                    id[:16],
                )

            return order

        # Order not in cache - try to fetch from blockchain directly
        # This handles orders from previous sessions (e.g., after bot restart)
        # Follow GMX SDK flow: extract order_key → query execution status → return correct status
        logger.info(
            "ORDER_TRACE: fetch_order(%s) - NOT IN CACHE (async), fetching from blockchain (e.g., after bot restart)",
            id[:16] if id else "None",
        )
        normalized_id = id if id.startswith("0x") else f"0x{id}"

        if len(normalized_id) == 66:  # Valid tx hash length (0x + 64 hex chars)
            try:
                receipt = await self.web3.eth.get_transaction_receipt(normalized_id)
                tx = await self.web3.eth.get_transaction(normalized_id)

                tx_success = receipt.get("status") == 1
                if not tx_success:
                    # Transaction failed - return failed order
                    order = {
                        "id": id,
                        "clientOrderId": None,
                        "datetime": self.iso8601(tx.get("blockNumber", 0) * 1000) if tx.get("blockNumber") else None,
                        "timestamp": tx.get("blockNumber", 0) * 1000 if tx.get("blockNumber") else None,
                        "lastTradeTimestamp": None,
                        "status": "failed",
                        "symbol": symbol if symbol else None,
                        "type": "market",
                        "side": None,
                        "price": None,
                        "amount": None,
                        "filled": 0.0,
                        "remaining": None,
                        "cost": None,
                        "trades": [],
                        "fee": {
                            "currency": "ETH",
                            "cost": float(receipt.get("gasUsed", 0)) * float(tx.get("gasPrice", 0)) / 1e18,
                        },
                        "info": {
                            "creation_receipt": receipt,
                            "transaction": tx,
                        },
                        "average": None,
                        "fees": [],
                    }
                    logger.info("fetch_order(%s): tx failed, status=failed", id[:16])
                    return order

                # Transaction succeeded - extract order_key to verify execution
                # Run sync function in executor
                try:
                    order_key = await asyncio.get_running_loop().run_in_executor(
                        None,
                        lambda: extract_order_key_from_receipt(self.sync_web3, receipt),
                    )
                except ValueError as e:
                    logger.warning("fetch_order(%s): could not extract order_key: %s", id[:16], e)
                    order_key = None

                if not order_key:
                    # No order_key - can't verify execution, assume still pending
                    logger.warning("fetch_order(%s): no order_key, returning status=open", id[:16])
                    order = {
                        "id": id,
                        "clientOrderId": None,
                        "datetime": self.iso8601(tx.get("blockNumber", 0) * 1000) if tx.get("blockNumber") else None,
                        "timestamp": tx.get("blockNumber", 0) * 1000 if tx.get("blockNumber") else None,
                        "lastTradeTimestamp": None,
                        "status": "open",
                        "symbol": symbol if symbol else None,
                        "type": "market",
                        "side": None,
                        "price": None,
                        "amount": None,
                        "filled": None,
                        "remaining": None,
                        "cost": None,
                        "trades": [],
                        "fee": {
                            "currency": "ETH",
                            "cost": float(receipt.get("gasUsed", 0)) * float(tx.get("gasPrice", 0)) / 1e18,
                        },
                        "info": {
                            "creation_receipt": receipt,
                            "transaction": tx,
                        },
                        "average": None,
                        "fees": [],
                    }
                    return order

                # Follow GMX SDK flow: Query TradeAction via GraphQL (Subsquid)
                order_key_hex = "0x" + order_key.hex()
                trade_action = None

                try:
                    subsquid = AsyncGMXSubsquidClient(chain=self.config.get_chain())
                    # No timeout - this is a historical query, not waiting for new data
                    trade_action = await subsquid.get_trade_action_by_order_key(
                        order_key_hex,
                        timeout_seconds=0,  # Don't wait, just check if exists
                        poll_interval=0.5,
                    )
                except Exception as e:
                    logger.debug("fetch_order(%s): Subsquid query failed: %s", id[:16], e)

                # Fallback: Query EventEmitter logs if Subsquid failed
                if trade_action is None:
                    logger.debug("fetch_order(%s): Falling back to EventEmitter logs", id[:16])

                    try:
                        addresses = get_contract_addresses(self.config.get_chain())
                        event_emitter = addresses.eventemitter
                        creation_block = receipt.get("blockNumber", 0)
                        current_block = await self.web3.eth.block_number

                        # Use chunked scanning to avoid RPC timeouts on large block ranges
                        trade_action = await _async_scan_logs_chunked_for_trade_action(
                            self.web3,
                            self.sync_web3,
                            event_emitter,
                            order_key,
                            order_key_hex,
                            creation_block,
                            current_block,
                        )

                    except Exception as e:
                        logger.debug("fetch_order(%s): EventEmitter query failed: %s", id[:16], e)

                # Process the trade action result
                if trade_action is None:
                    # No execution found - still pending or lost
                    logger.warning(
                        "ORDER_TRACE: fetch_order(%s) - NO EXECUTION FOUND (async, checked Subsquid + EventEmitter) - RETURNING status=open (might be lost/pending)",
                        id[:16],
                    )
                    order = {
                        "id": id,
                        "clientOrderId": None,
                        "datetime": self.iso8601(tx.get("blockNumber", 0) * 1000) if tx.get("blockNumber") else None,
                        "timestamp": tx.get("blockNumber", 0) * 1000 if tx.get("blockNumber") else None,
                        "lastTradeTimestamp": None,
                        "status": "open",
                        "symbol": symbol if symbol else None,
                        "type": "market",
                        "side": None,
                        "price": None,
                        "amount": None,
                        "filled": None,
                        "remaining": None,
                        "cost": None,
                        "trades": [],
                        "fee": {
                            "currency": "ETH",
                            "cost": float(receipt.get("gasUsed", 0)) * float(tx.get("gasPrice", 0)) / 1e18,
                        },
                        "info": {
                            "creation_receipt": receipt,
                            "transaction": tx,
                            "order_key": order_key_hex,
                        },
                        "average": None,
                        "fees": [],
                    }
                    return order

                # Check event type
                event_name = trade_action.get("eventName", "")

                if event_name in ("OrderCancelled", "OrderFrozen"):
                    # Order cancelled/frozen
                    error_reason = trade_action.get("reason") or f"Order {event_name.lower()}"
                    logger.info(
                        "ORDER_TRACE: fetch_order(%s) - Order CANCELLED/FROZEN (async) - reason=%s - RETURNING status=cancelled",
                        id[:16],
                        error_reason,
                    )

                    timestamp = self.milliseconds()
                    order = {
                        "id": id,
                        "clientOrderId": None,
                        "timestamp": timestamp,
                        "datetime": self.iso8601(timestamp),
                        "lastTradeTimestamp": timestamp,
                        "symbol": symbol,
                        "type": "market",
                        "side": None,
                        "price": None,
                        "amount": None,
                        "cost": None,
                        "average": None,
                        "filled": 0.0,
                        "remaining": None,
                        "status": "cancelled",
                        "fee": {
                            "currency": "ETH",
                            "cost": float(receipt.get("gasUsed", 0)) * float(tx.get("gasPrice", 0)) / 1e18,
                        },
                        "trades": [],
                        "info": {
                            "creation_receipt": receipt,
                            "transaction": tx,
                            "order_key": order_key_hex,
                            "event_name": event_name,
                            "cancel_reason": error_reason,
                        },
                    }
                    return order

                # Order executed successfully (OrderExecuted event)
                raw_exec_price = trade_action.get("executionPrice")
                execution_price = None
                if raw_exec_price and symbol:
                    market = self.markets.get(symbol)
                    if market:
                        execution_price = self._convert_price_to_usd(float(raw_exec_price), market)

                execution_tx_hash = trade_action.get("transaction", {}).get("hash")
                is_long = trade_action.get("isLong")

                logger.info(
                    "ORDER_TRACE: fetch_order(%s) - Order EXECUTED (async) at price=%.2f, size_usd=%.2f - RETURNING status=closed",
                    id[:16],
                    execution_price or 0,
                    float(trade_action.get("sizeDeltaUsd", 0)) / 1e30 if trade_action.get("sizeDeltaUsd") else 0,
                )

                timestamp = self.milliseconds()
                order = {
                    "id": id,
                    "clientOrderId": None,
                    "timestamp": timestamp,
                    "datetime": self.iso8601(timestamp),
                    "lastTradeTimestamp": timestamp,
                    "symbol": symbol,
                    "type": "market",
                    "side": None,  # Unknown from tx alone
                    "price": execution_price,
                    "amount": None,  # Unknown from tx alone
                    "cost": None,
                    "average": execution_price,
                    "filled": None,  # Unknown from tx alone
                    "remaining": 0.0,
                    "status": "closed",
                    "fee": {
                        "currency": "ETH",
                        "cost": float(receipt.get("gasUsed", 0)) * float(tx.get("gasPrice", 0)) / 1e18,
                    },
                    "trades": [],
                    "info": {
                        "creation_receipt": receipt,
                        "transaction": tx,
                        "execution_tx_hash": execution_tx_hash,
                        "order_key": order_key_hex,
                        "execution_price": execution_price,
                        "is_long": is_long,
                        "event_name": event_name,
                        "pnl_usd": float(trade_action.get("pnlUsd", 0)) / 1e30 if trade_action.get("pnlUsd") else None,
                        "size_delta_usd": float(trade_action.get("sizeDeltaUsd", 0)) / 1e30 if trade_action.get("sizeDeltaUsd") else None,
                        "price_impact_usd": float(trade_action.get("priceImpactUsd", 0)) / 1e30 if trade_action.get("priceImpactUsd") else None,
                    },
                }
                return order

            except Exception as e:
                logger.warning("Could not fetch transaction %s from blockchain: %s", id, e)
                # Fall through to raise OrderNotFound

        # Order not found anywhere
        raise OrderNotFound(f"{self.id} order {id} not found in stored orders or on blockchain")

    async def fetch_open_orders(self, symbol: str | None = None, since: int | None = None, limit: int | None = None, params: dict | None = None):
        """Fetch open orders (returns positions as orders).

        GMX doesn't have traditional pending orders. We mirror the sync adapter
        by returning current positions formatted as orders using GetOpenPositions
        as the single source of truth. This keeps Freqtrade's dashboard in sync
        with actual on-chain positions and avoids silent desyncs when a close
        transaction partially/fully fails.
        """
        positions = await self.fetch_positions(symbols=[symbol] if symbol else None, params=params)

        orders: list[dict] = []
        for pos in positions:
            order = {
                "id": pos.get("id"),
                "clientOrderId": None,
                "timestamp": pos.get("timestamp"),
                "datetime": pos.get("datetime"),
                "lastTradeTimestamp": pos.get("timestamp"),
                "symbol": pos.get("symbol"),
                "type": "market",
                "side": "buy" if pos.get("side") == "long" else "sell",
                "price": pos.get("entryPrice") or pos.get("markPrice"),
                "amount": pos.get("contracts"),
                "cost": pos.get("notional"),
                "average": pos.get("entryPrice"),
                "filled": pos.get("contracts"),
                "remaining": 0.0,
                "status": "open",
                "fee": None,
                "trades": [],
                "info": pos,
            }
            orders.append(order)

        if limit:
            orders = orders[:limit]

        return orders

    async def fetch_my_trades(self, symbol: str | None = None, since: int | None = None, limit: int | None = None, params: dict | None = None):
        """Fetch user trade history from Subsquid."""
        await self._ensure_session()
        await self.load_markets()

        # TODO: Implement GraphQL query for user trades via Subsquid
        # For now, return empty list
        return []

    async def fetch_trades(self, symbol: str, since: int | None = None, limit: int | None = None, params: dict | None = None):
        """Fetch public trades from Subsquid."""
        await self._ensure_session()
        await self.load_markets()

        # TODO: Implement GraphQL query for public trades via Subsquid
        # For now, return empty list
        return []

    async def fetch_positions(self, symbols: list[str] | None = None, params: dict | None = None) -> list[dict]:
        """Fetch all open positions for the account (contract-based)."""
        logger.debug("ORDER_TRACE: fetch_positions() CALLED (async) - symbols=%s", symbols)
        await self._ensure_session()
        await self.load_markets()

        params = params or {}

        wallet = params.get("wallet_address", self.wallet_address)
        if not wallet:
            raise ValueError("wallet_address must be provided in GMXConfig or params")

        positions_manager = GetOpenPositions(self.config)
        positions = positions_manager.get_data(wallet)

        result: list[dict] = []
        for position_key, data in positions.items():
            market_symbol = data.get("market_symbol", "")
            unified_symbol = f"{market_symbol}/USDC:USDC"

            if symbols and unified_symbol not in symbols:
                continue

            position_size_usd = float(data.get("position_size", 0) or 0)
            entry_price = data.get("entry_price")
            mark_price = data.get("mark_price")
            percent_profit = float(data.get("percent_profit", 0) or 0)
            collateral_usd = float(data.get("initial_collateral_amount_usd", 0) or 0)

            contracts = None
            if entry_price and entry_price > 0:
                try:
                    contracts = position_size_usd / float(entry_price)
                except Exception:
                    contracts = None

            notional = None
            if contracts and mark_price:
                try:
                    notional = contracts * float(mark_price)
                except Exception:
                    notional = None

            unrealized_pnl = None
            if position_size_usd:
                unrealized_pnl = position_size_usd * (percent_profit / 100)

            timestamp = self.milliseconds()

            result.append(
                {
                    "id": position_key,
                    "symbol": unified_symbol,
                    "timestamp": timestamp,
                    "datetime": self.iso8601(timestamp),
                    "isolated": False,
                    "hedged": False,
                    "side": "long" if data.get("is_long", True) else "short",
                    "contracts": contracts,
                    "contractSize": self.parse_number("1"),
                    "entryPrice": entry_price,
                    "markPrice": mark_price,
                    "notional": notional,
                    "leverage": data.get("leverage"),
                    "collateral": collateral_usd,
                    "initialMargin": collateral_usd,
                    "maintenanceMargin": None,
                    "initialMarginPercentage": None,
                    "maintenanceMarginPercentage": 0.01,
                    "unrealizedPnl": unrealized_pnl,
                    "liquidationPrice": None,
                    "marginRatio": None,
                    "percentage": percent_profit,
                    "info": data,
                }
            )

        # Log summary of positions found
        logger.info(
            "ORDER_TRACE: fetch_positions() RETURNING %d position(s) (async)",
            len(result),
        )
        for pos in result:
            logger.info(
                "ORDER_TRACE:   - Position: symbol=%s, side=%s, size=%.8f, entry_price=%.2f, unrealized_pnl=%.2f, leverage=%.1fx",
                pos.get("symbol"),
                pos.get("side"),
                pos.get("contracts", 0) if pos.get("contracts") else 0,
                pos.get("entryPrice", 0) if pos.get("entryPrice") else 0,
                pos.get("unrealizedPnl", 0) if pos.get("unrealizedPnl") else 0,
                pos.get("leverage", 0) if pos.get("leverage") else 0,
            )

        return result

    async def fetch_balance(self, params: dict | None = None) -> dict:
        """Fetch account balance.

        Args:
            params: Additional parameters

        Returns:
            Balance dictionary in CCXT format
        """
        await self._ensure_session()

        # For async, we'd need to implement async balance fetching
        # This is a placeholder that raises NotSupported
        raise NotSupported(f"{self.id} fetch_balance() async implementation pending")

    async def fetch_open_interest(self, symbol: str, params: dict | None = None) -> dict:
        """Fetch current open interest for a symbol.

        Args:
            symbol: Unified symbol (e.g., "ETH/USD")
            params: Additional parameters

        Returns:
            Open interest dictionary with long/short breakdown
        """
        await self._ensure_session()
        await self.load_markets()

        market = self.market(symbol)
        market_address = params.get("market_address", market["info"]["market_token"]) if params else market["info"]["market_token"]

        # Fetch from Subsquid
        market_infos = await self.subsquid.get_market_infos(
            market_address=market_address,
            limit=1,
            order_by="id_DESC",
        )

        if not market_infos:
            raise ValueError(f"No market info found for {symbol}")

        raw_info = market_infos[0]

        # Parse open interest
        long_oi_usd_raw = raw_info.get("longOpenInterestUsd", 0)
        short_oi_usd_raw = raw_info.get("shortOpenInterestUsd", 0)

        long_oi_usd = float(long_oi_usd_raw) / 1e30 if long_oi_usd_raw else 0.0
        short_oi_usd = float(short_oi_usd_raw) / 1e30 if short_oi_usd_raw else 0.0
        total_oi_usd = long_oi_usd + short_oi_usd

        long_oi_tokens_raw = raw_info.get("longOpenInterestInTokens", 0)
        short_oi_tokens_raw = raw_info.get("shortOpenInterestInTokens", 0)

        long_oi_tokens = 0.0
        short_oi_tokens = 0.0
        total_oi_tokens = None

        if market and (long_oi_tokens_raw or short_oi_tokens_raw):
            try:
                index_token_address = market["info"]["index_token"]
                decimals = self.subsquid.get_token_decimals(index_token_address)

                long_oi_tokens = float(long_oi_tokens_raw) / (10**decimals) if long_oi_tokens_raw else 0.0
                short_oi_tokens = float(short_oi_tokens_raw) / (10**decimals) if short_oi_tokens_raw else 0.0
                total_oi_tokens = long_oi_tokens + short_oi_tokens
            except (KeyError, TypeError, ValueError):
                pass

        timestamp = self.milliseconds()

        return {
            "symbol": symbol,
            "baseVolume": None,
            "quoteVolume": None,
            "openInterestAmount": total_oi_tokens,
            "openInterestValue": total_oi_usd,
            "timestamp": timestamp,
            "datetime": self.iso8601(timestamp),
            "info": {
                "longOpenInterest": long_oi_usd,
                "shortOpenInterest": short_oi_usd,
                "longOpenInterestUsd": long_oi_usd_raw,
                "shortOpenInterestUsd": short_oi_usd_raw,
                "longOpenInterestTokens": long_oi_tokens,
                "shortOpenInterestTokens": short_oi_tokens,
                "longOpenInterestInTokens": long_oi_tokens_raw,
                "shortOpenInterestInTokens": short_oi_tokens_raw,
                **raw_info,
            },
        }

    async def fetch_open_interest_history(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: int | None = None,
        limit: int | None = None,
        params: dict | None = None,
    ) -> list[dict]:
        """Fetch historical open interest data.

        Args:
            symbol: Unified symbol (e.g., "ETH/USD")
            timeframe: Time interval (note: data is snapshot-based)
            since: Start timestamp in milliseconds
            limit: Maximum number of records (default: 100)
            params: Additional parameters

        Returns:
            List of historical open interest snapshots
        """
        await self._ensure_session()
        await self.load_markets()

        if params is None:
            params = {}

        if limit is None:
            limit = 120

        market = self.market(symbol)
        market_address = params.get("market_address", market["info"]["market_token"])

        market_infos = await self.subsquid.get_market_infos(
            market_address=market_address,
            limit=limit,
        )

        result = []
        for info in market_infos:
            # Parse each info using similar logic to fetch_open_interest
            long_oi_usd = float(info.get("longOpenInterestUsd", 0)) / 1e30
            short_oi_usd = float(info.get("shortOpenInterestUsd", 0)) / 1e30
            total_oi_usd = long_oi_usd + short_oi_usd

            timestamp = self.milliseconds()

            result.append(
                {
                    "symbol": symbol,
                    "openInterestAmount": None,
                    "openInterestValue": total_oi_usd,
                    "timestamp": timestamp,
                    "datetime": self.iso8601(timestamp),
                    "info": info,
                }
            )

        return result

    async def fetch_open_interests(self, symbols: list[str] | None = None, params: dict | None = None) -> dict:
        """Fetch open interest for multiple symbols.

        Args:
            symbols: List of symbols (if None, fetch all)
            params: Additional parameters

        Returns:
            Dictionary mapping symbols to open interest data
        """
        await self.load_markets()

        if symbols is None:
            symbols = list(self.markets.keys())

        result = {}
        tasks = []
        valid_symbols = []

        for symbol in symbols:
            try:
                tasks.append(self.fetch_open_interest(symbol, params))
                valid_symbols.append(symbol)
            except Exception:
                continue

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for symbol, oi_result in zip(valid_symbols, results):
                if not isinstance(oi_result, Exception):
                    result[symbol] = oi_result

        return result

    async def fetch_funding_rate(self, symbol: str, params: dict | None = None) -> dict:
        """Fetch current funding rate for a symbol.

        Args:
            symbol: Unified symbol (e.g., "ETH/USD")
            params: Additional parameters

        Returns:
            Funding rate dictionary with long/short rates
        """
        await self._ensure_session()
        await self.load_markets()

        if params is None:
            params = {}

        market = self.market(symbol)
        market_address = params.get("market_address", market["info"]["market_token"])

        # Fetch from Subsquid
        market_infos = await self.subsquid.get_market_infos(
            market_address=market_address,
            limit=1,
            order_by="id_DESC",
        )

        if not market_infos:
            raise ValueError(f"No market info found for {symbol}")

        info = market_infos[0]

        # Parse funding rate
        funding_per_second = float(info.get("fundingFactorPerSecond", 0)) / 1e30
        longs_pay_shorts = info.get("longsPayShorts", True)

        if longs_pay_shorts:
            long_funding = funding_per_second
            short_funding = -funding_per_second
        else:
            long_funding = -funding_per_second
            short_funding = funding_per_second

        timestamp = self.milliseconds()

        return {
            "symbol": symbol,
            "fundingRate": funding_per_second,
            "longFundingRate": long_funding,
            "shortFundingRate": short_funding,
            "fundingTimestamp": timestamp,
            "fundingDatetime": datetime.fromtimestamp(timestamp / 1000).isoformat() + "Z",
            "timestamp": timestamp,
            "datetime": datetime.fromtimestamp(timestamp / 1000).isoformat() + "Z",
            "info": info,
        }

    async def fetch_funding_rate_history(
        self,
        symbol: str,
        since: int | None = None,
        limit: int | None = None,
        params: dict | None = None,
    ) -> list[dict]:
        """Fetch historical funding rate data.

        Args:
            symbol: Unified symbol (e.g., "ETH/USD")
            since: Start timestamp in milliseconds
            limit: Maximum number of records (default: 100)
            params: Additional parameters

        Returns:
            List of historical funding rate snapshots
        """
        await self._ensure_session()
        await self.load_markets()

        if params is None:
            params = {}

        if limit is None:
            limit = 10

        # Cap limit to avoid GraphQL response size limits with 115 markets
        # Each marketInfo has many fields, Subsquid can't handle large responses
        limit = min(limit, 10)

        market = self.market(symbol)
        market_address = params.get("market_address", market["info"]["market_token"])

        market_infos = await self.subsquid.get_market_infos(
            market_address=market_address,
            limit=limit,
        )

        result = []
        for info in market_infos:
            funding_per_second = float(info.get("fundingFactorPerSecond", 0)) / 1e30
            longs_pay_shorts = info.get("longsPayShorts", True)

            timestamp = self.milliseconds()

            result.append(
                {
                    "symbol": symbol,
                    "fundingRate": funding_per_second,
                    "longFundingRate": funding_per_second if longs_pay_shorts else -funding_per_second,
                    "shortFundingRate": -funding_per_second if longs_pay_shorts else funding_per_second,
                    "fundingTimestamp": timestamp,
                    "fundingDatetime": datetime.fromtimestamp(timestamp / 1000).isoformat() + "Z",
                    "timestamp": timestamp,
                    "datetime": datetime.fromtimestamp(timestamp / 1000).isoformat() + "Z",
                    "info": info,
                }
            )

        return result

    async def fetch_funding_history(
        self,
        symbol: str | None = None,
        since: int | None = None,
        limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch funding fee payment history for positions.

        GMX V2 does not track historical funding fee payments per position.
        This method returns an empty list to indicate no funding history is available.
        Freqtrade will calculate funding fees as 0.0 when summing the empty list.

        :param symbol: Unified symbol (e.g., "ETH/USD", "BTC/USD") - not used
        :type symbol: str | None
        :param since: Timestamp in milliseconds - not used
        :type since: int | None
        :param limit: Maximum number of records - not used
        :type limit: int | None
        :param params: Additional parameters - not used
        :type params: dict[str, Any] | None
        :returns: Empty list (GMX doesn't provide funding history)
        :rtype: list[dict[str, Any]]

        .. note::
            GMX V2 does not track historical funding fee payments. Funding fees
            are continuously accrued and settled, but the protocol does not
            maintain a queryable history of past payments.

            If you need funding rate history (not payment history), use
            fetch_funding_rate_history() instead.
        """
        logger.warning("fetch_funding_history() called but GMX V2 does not track historical funding fee payments. Returning empty list (funding fees will be calculated as 0.0).")
        return []

    async def fetch_funding_rates(self, symbols: list[str] | None = None, params: dict | None = None) -> dict:
        """Fetch funding rates for multiple symbols.

        Args:
            symbols: List of symbols (if None, fetch all)
            params: Additional parameters

        Returns:
            Dictionary mapping symbols to funding rates
        """
        await self.load_markets()

        if symbols is None:
            symbols = list(self.markets.keys())

        result = {}
        tasks = []
        valid_symbols = []

        for symbol in symbols:
            try:
                tasks.append(self.fetch_funding_rate(symbol, params))
                valid_symbols.append(symbol)
            except Exception:
                continue

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for symbol, fr_result in zip(valid_symbols, results):
                if not isinstance(fr_result, Exception):
                    result[symbol] = fr_result

        return result

    def _parse_sltp_params(self, params: dict) -> tuple:
        """Parse CCXT-style SL/TP params into GMX SLTPEntry objects.

        Supports both CCXT unified (stopLossPrice/takeProfitPrice) and object (stopLoss/takeProfit) styles.

        :param params: CCXT parameters dict containing SL/TP configuration
        :return: Tuple of (stop_loss_entry, take_profit_entry)
        """
        stop_loss_entry = None
        take_profit_entry = None

        # Parse Stop Loss
        if "stopLossPrice" in params:
            stop_loss_entry = SLTPEntry(
                trigger_price=params["stopLossPrice"],
                close_percent=1.0,
                auto_cancel=True,
            )
        elif "stopLoss" in params:
            sl = params["stopLoss"]
            if isinstance(sl, dict):
                stop_loss_entry = SLTPEntry(
                    trigger_price=sl.get("triggerPrice"),
                    trigger_percent=sl.get("triggerPercent"),
                    close_percent=sl.get("closePercent", 1.0),
                    close_size_usd=sl.get("closeSizeUsd"),
                    auto_cancel=sl.get("autoCancel", True),
                )
            else:
                stop_loss_entry = SLTPEntry(trigger_price=sl, close_percent=1.0)

        # Parse Take Profit
        if "takeProfitPrice" in params:
            take_profit_entry = SLTPEntry(
                trigger_price=params["takeProfitPrice"],
                close_percent=1.0,
                auto_cancel=True,
            )
        elif "takeProfit" in params:
            tp = params["takeProfit"]
            if isinstance(tp, dict):
                take_profit_entry = SLTPEntry(
                    trigger_price=tp.get("triggerPrice"),
                    trigger_percent=tp.get("triggerPercent"),
                    close_percent=tp.get("closePercent", 1.0),
                    close_size_usd=tp.get("closeSizeUsd"),
                    auto_cancel=tp.get("autoCancel", True),
                )
            else:
                take_profit_entry = SLTPEntry(trigger_price=tp, close_percent=1.0)

        return stop_loss_entry, take_profit_entry

    async def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict | None = None,
    ) -> dict:
        """Create and execute a GMX order asynchronously.

        This is the async version of the sync create_order() method.
        Supports bundled SL/TP orders and standalone SL/TP creation.

        :param symbol: Market symbol (e.g., 'ETH/USD', 'BTC/USD')
        :param type: Order type ('market' or 'limit')
        :param side: Order side ('buy' for long, 'sell' for short)
        :param amount: Order size in base currency contracts (e.g., ETH for ETH/USD). Use params['size_usd'] for USD-based sizing.
        :param price: Price for limit orders. For market orders, used to convert amount to USD if provided.
        :param params: Additional parameters:
            - size_usd (float): GMX Extension - Order size in USD (alternative to amount parameter)
            - leverage (float): Leverage multiplier
            - collateral_symbol (str): Collateral token
            - slippage_percent (float): Slippage tolerance
            - execution_buffer (float): Gas buffer multiplier
            - stopLoss/takeProfit: SL/TP configuration
        :return: CCXT-compatible order structure
        """
        if params is None:
            params = {}

        # Require wallet for order creation
        if not self.wallet:
            raise ValueError(
                "Wallet required for order creation. Provide 'privateKey' or 'wallet' in constructor parameters.",
            )

        # Check if trading is paused due to consecutive failures
        if self._trading_paused:
            # Get current wallet balance for detailed error message
            try:
                loop = asyncio.get_running_loop()
                eth_balance = await loop.run_in_executor(None, self.web3.eth.get_balance, self.wallet.address)
                eth_balance_eth = eth_balance / 1e18
                # Estimate USD value (using $2000/ETH as approximation)
                eth_balance_usd = eth_balance_eth * 2000
                balance_str = f"{eth_balance_eth:.6f} ETH (${eth_balance_usd:.2f})"
            except Exception as e:
                balance_str = f"<failed to fetch: {e}>"

            # Get the last failed transaction hash if available
            last_tx_hash = getattr(self, "_last_failed_tx_hash", None)
            tx_link = f"TX: https://arbiscan.io/tx/{last_tx_hash}" if last_tx_hash else ""

            # Build detailed error message
            error_msg = f"🛑 GMX BOT PAUSED: {self._consecutive_failures} order failures detected.\n\n{self._trading_paused_reason}\n\nWallet balance: {balance_str}\n{tx_link}\n\nACTION REQUIRED: Increase executionBuffer in config (try 2.0x or higher), top up wallet if needed, then restart bot."
            logger.error(error_msg)
            # Raise BaseError which becomes OperationalException (stops bot, sends EXCEPTION to Telegram)
            from ccxt.base.errors import BaseError

            raise BaseError(error_msg)

        # Sync wallet nonce
        # Note: AsyncWeb3 doesn't have sync methods, need to use await
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.wallet.sync_nonce, self.web3)

        # logger.debug("=" * 80)
        # logger.debug(
        #     "ORDER_TRACE: async create_order() CALLED symbol=%s, type=%s, side=%s, amount=%.8f",
        #     symbol,
        #     type,
        #     side,
        #     amount,
        # )
        # logger.debug(
        #     "ORDER_TRACE: params: reduceOnly=%s, leverage=%s, collateral_symbol=%s",
        #     params.get("reduceOnly", False),
        #     params.get("leverage"),
        #     params.get("collateral_symbol"),
        # )

        # Ensure markets are loaded
        if not self.markets_loaded or not self.markets:
            await self.load_markets()

        # Parse SL/TP parameters
        sl_entry, tp_entry = self._parse_sltp_params(params)

        # Check for standalone SL/TP order types
        if type in ["stop_loss", "take_profit"]:
            return await self._create_standalone_sltp_order(
                symbol,
                type,
                side,
                amount,
                params,
            )

        # Bundled approach: SL/TP with position opening
        if sl_entry or tp_entry:
            return await self._create_order_with_sltp(
                symbol,
                type,
                side,
                amount,
                price,
                params,
                sl_entry,
                tp_entry,
            )

        # Standard order creation (no SLTP)
        raise NotSupported(f"{self.id} async create_order() for standard orders not yet implemented")

    async def _create_order_with_sltp(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None,
        params: dict,
        sl_entry,
        tp_entry,
    ) -> dict:
        """Create order with bundled SL/TP (async version).

        Opens position + SL + TP in a single multicall transaction.

        :param symbol: Market symbol
        :param type: Order type
        :param side: Order side
        :param amount: Order size in USD
        :param price: Price (for limit orders)
        :param params: Additional CCXT parameters
        :param sl_entry: Stop loss configuration
        :param tp_entry: Take profit configuration
        :return: CCXT-compatible order structure
        """
        # Only support bundled SL/TP for opening positions (buy side)
        if side != "buy":
            raise ValueError("Bundled SL/TP only supported for opening positions (side='buy'). Use standalone SL/TP for existing positions.")

        # Convert CCXT params to GMX params
        gmx_params = await self._convert_ccxt_to_gmx_params_async(symbol, type, side, amount, price, params)

        # Get market and token info
        normalized_symbol = self._normalize_symbol(symbol)
        market = self.markets[normalized_symbol]

        collateral_symbol = gmx_params["collateral_symbol"]
        leverage = gmx_params["leverage"]
        size_delta_usd = gmx_params["size_delta_usd"]
        slippage_percent = gmx_params.get("slippage_percent", 0.003)
        execution_buffer = gmx_params.get("execution_buffer", 2.2)

        # Get token addresses from market
        chain = self.chain
        market_address = market["info"]["market_token"]
        collateral_address = market["info"]["long_token"]
        index_token_address = market["info"]["index_token"]

        if not collateral_address or not index_token_address:
            raise ValueError(f"Could not resolve token addresses for {symbol} market")

        # Calculate collateral amount from size and leverage
        collateral_usd = size_delta_usd / leverage

        # Get token details (sync operation in executor)
        loop = asyncio.get_running_loop()
        token_details = await loop.run_in_executor(
            None,
            fetch_erc20_details,
            self.web3,
            collateral_address,
            self.web3.eth.chain_id,
        )

        # Get oracle prices (sync operation in executor)
        oracle = OraclePrices(self.chain)
        oracle_prices = await loop.run_in_executor(None, oracle.get_recent_prices)

        # Get collateral token price
        if collateral_address not in oracle_prices:
            raise ValueError(f"No oracle price available for collateral token {collateral_address}")

        price_data = oracle_prices[collateral_address]
        raw_price = median([float(price_data["maxPriceFull"]), float(price_data["minPriceFull"])])
        collateral_token_price = raw_price / (10 ** (PRECISION - token_details.decimals))

        # Calculate collateral amount in token units
        collateral_tokens = collateral_usd / collateral_token_price
        collateral_amount = int(collateral_tokens * (10**token_details.decimals))

        # Ensure token approval
        await self._ensure_token_approval_async(
            collateral_symbol,
            token_details.symbol,
            size_delta_usd,
            leverage,
            collateral_address,
            token_details,
        )

        # Create SLTPOrder instance (sync operation)
        sltp_order = SLTPOrder(
            config=self.config,
            market_key=to_checksum_address(market_address),
            collateral_address=to_checksum_address(collateral_address),
            index_token_address=to_checksum_address(index_token_address),
            is_long=True,
        )

        # Build SLTPParams
        sltp_params = SLTPParams(
            stop_loss=sl_entry,
            take_profit=tp_entry,
        )

        # Create bundled order (sync operation in executor)
        sltp_result = await loop.run_in_executor(
            None,
            sltp_order.create_increase_order_with_sltp,
            size_delta_usd,
            collateral_amount,
            sltp_params,
            slippage_percent,
            None,  # swap_path
            execution_buffer,
            False,  # auto_cancel
            None,  # data_list
        )

        logger.info("SL/TP result created: entry_price=%s, sl_trigger=%s, tp_trigger=%s", sltp_result.entry_price, sltp_result.stop_loss_trigger_price, sltp_result.take_profit_trigger_price)

        # Sign transaction
        transaction = sltp_result.transaction
        if "nonce" in transaction:
            del transaction["nonce"]

        signed_tx = await loop.run_in_executor(
            None,
            self.wallet.sign_transaction_with_new_nonce,
            transaction,
        )

        # Submit to blockchain
        tx_hash_bytes = await self.web3.eth.send_raw_transaction(signed_tx.rawTransaction)
        tx_hash = self.web3.to_hex(tx_hash_bytes)

        # Wait for confirmation
        receipt = await self.web3.eth.wait_for_transaction_receipt(tx_hash_bytes)

        # Convert to CCXT format
        order = self._parse_sltp_result_to_ccxt(
            sltp_result,
            symbol,
            side,
            type,
            amount,
            tx_hash,
            receipt,
        )

        logger.debug(
            "ORDER_TRACE: async create_order() RETURNING order_id=%s, status=%s, filled=%.8f, cost=%.2f",
            order.get("id"),
            order.get("status"),
            order.get("filled", 0),
            order.get("cost", 0),
        )
        # logger.debug("=" * 80)

        return order

    def _normalize_symbol(self, symbol: str) -> str:
        """Normalize symbol to match market keys.

        :param symbol: Input symbol (e.g., 'ETH/USD' or 'ETH/USDC:USDC')
        :return: Normalized symbol
        """
        # If symbol already has settlement token, return as-is
        if ":" in symbol:
            return symbol

        # Add USDC settlement for GMX futures
        return f"{symbol}:USDC"

    async def _convert_ccxt_to_gmx_params_async(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None,
        params: dict,
    ) -> dict:
        """Convert CCXT parameters to GMX parameters (async version).

        :param symbol: Market symbol
        :param type: Order type
        :param side: Order side
        :param amount: Order size in base currency contracts (e.g., ETH for ETH/USD). Use params['size_usd'] for USD-based sizing.
        :param price: Price for limit orders. For market orders, used to convert amount to USD if provided.
        :param params: Additional parameters
        :return: GMX parameters dict
        """
        leverage = params.get("leverage", 1.0)
        collateral_symbol = params.get("collateral_symbol", "USDC")
        slippage_percent = params.get("slippage_percent", 0.003)
        execution_buffer = params.get("execution_buffer", 2.2)

        # GMX Extension: Support direct USD sizing via size_usd parameter
        if "size_usd" in params:
            # Direct USD amount (GMX-native approach)
            size_delta_usd = params["size_usd"]
        else:
            # Standard CCXT: amount is in base currency, convert to USD
            if price:
                size_delta_usd = amount * price
            else:
                # For market orders, fetch current price
                ticker = await self.fetch_ticker(symbol)
                current_price = ticker["last"]
                size_delta_usd = amount * current_price

        return {
            "symbol": symbol,
            "collateral_symbol": collateral_symbol,
            "leverage": leverage,
            "size_delta_usd": size_delta_usd,
            "slippage_percent": slippage_percent,
            "execution_buffer": execution_buffer,
        }

    async def _ensure_token_approval_async(
        self,
        requested_symbol: str,
        actual_symbol: str,
        size_delta_usd: float,
        leverage: float,
        collateral_address: str,
        token_details,
    ):
        """Ensure token approval for order creation (async version).

        :param requested_symbol: Symbol requested by user
        :param actual_symbol: Actual token symbol from contract
        :param size_delta_usd: Position size in USD
        :param leverage: Leverage multiplier
        :param collateral_address: Token address
        :param token_details: Token details object
        """
        # Skip for native tokens
        if requested_symbol in ["ETH", "AVAX"]:
            logger.debug("Using native %s - no approval needed", requested_symbol)
            return

        # Get contract addresses
        loop = asyncio.get_running_loop()
        contract_addresses = get_contract_addresses(self.chain)
        spender_address = contract_addresses.syntheticsrouter

        # Check current allowance
        wallet_address = self.wallet.address
        token_contract = token_details.contract

        current_allowance = await loop.run_in_executor(
            None,
            token_contract.functions.allowance(
                to_checksum_address(wallet_address),
                spender_address,
            ).call,
        )

        # Calculate required amount
        required_collateral_usd = (size_delta_usd / leverage) * 1.1

        # Get oracle prices
        oracle = OraclePrices(self.chain)
        oracle_prices = await loop.run_in_executor(None, oracle.get_recent_prices)

        if collateral_address not in oracle_prices:
            raise ValueError(f"No oracle price available for {collateral_address}")

        price_data = oracle_prices[collateral_address]
        raw_price = median([float(price_data["maxPriceFull"]), float(price_data["minPriceFull"])])
        token_price = raw_price / (10 ** (PRECISION - token_details.decimals))

        required_tokens = required_collateral_usd / token_price
        required_amount = int(required_tokens * (10**token_details.decimals))

        # Check if approval needed
        if current_allowance >= required_amount:
            logger.debug("Sufficient %s allowance exists", actual_symbol)
            return

        # Approve large amount
        approve_amount = 1_000_000_000 * (10**token_details.decimals)

        logger.info("Approving %.0f %s...", approve_amount / (10**token_details.decimals), actual_symbol)

        # Build and send approval transaction
        approve_tx = await loop.run_in_executor(
            None,
            token_contract.functions.approve(spender_address, approve_amount).build_transaction,
            {
                "from": to_checksum_address(wallet_address),
                "gas": 100_000,
                "gasPrice": self.web3.eth.gas_price,
            },
        )

        if "nonce" in approve_tx:
            del approve_tx["nonce"]

        signed_approve_tx = await loop.run_in_executor(
            None,
            self.wallet.sign_transaction_with_new_nonce,
            approve_tx,
        )

        approve_tx_hash = await self.web3.eth.send_raw_transaction(signed_approve_tx.rawTransaction)
        approve_receipt = await self.web3.eth.wait_for_transaction_receipt(approve_tx_hash, timeout=120)

        if approve_receipt["status"] != 1:
            raise Exception(f"Token approval failed: {approve_tx_hash.hex()}")

        logger.info("Token approval successful!")

    def _parse_sltp_result_to_ccxt(
        self,
        sltp_result,
        symbol: str,
        side: str,
        type: str,
        amount: float,
        tx_hash: str,
        receipt: dict,
    ) -> dict:
        """Convert SLTPOrderResult to CCXT order structure.

        :param sltp_result: SLTPOrderResult
        :param symbol: CCXT symbol
        :param side: Order side
        :param type: Order type
        :param amount: Order size
        :param tx_hash: Transaction hash
        :param receipt: Transaction receipt
        :return: CCXT-compatible order structure
        """
        timestamp = self.milliseconds()
        tx_success = receipt.get("status") == 1
        status = "closed" if tx_success else "failed"

        # Build info dict
        info = {
            "tx_hash": tx_hash,
            "receipt": receipt,
            "block_number": receipt.get("blockNumber"),
            "gas_used": receipt.get("gasUsed"),
            "total_execution_fee": sltp_result.total_execution_fee,
            "main_order_fee": sltp_result.main_order_fee,
            "stop_loss_fee": sltp_result.stop_loss_fee,
            "take_profit_fee": sltp_result.take_profit_fee,
            "entry_price": sltp_result.entry_price,
            "has_stop_loss": sltp_result.stop_loss_trigger_price is not None,
            "has_take_profit": sltp_result.take_profit_trigger_price is not None,
        }

        if sltp_result.stop_loss_trigger_price is not None:
            info["stop_loss_trigger_price"] = sltp_result.stop_loss_trigger_price
        if sltp_result.take_profit_trigger_price is not None:
            info["take_profit_trigger_price"] = sltp_result.take_profit_trigger_price

        # Store execution fee in info (ETH gas paid to keeper)
        info["execution_fee_eth"] = sltp_result.total_execution_fee / 1e18

        mark_price = sltp_result.entry_price
        filled_amount = amount if tx_success else 0.0
        remaining_amount = 0.0 if tx_success else amount

        return {
            "id": tx_hash,
            "clientOrderId": None,
            "timestamp": timestamp,
            "datetime": self.iso8601(timestamp),
            "lastTradeTimestamp": timestamp if tx_success else None,
            "symbol": symbol,
            "type": type,
            "side": side,
            "price": mark_price,
            "amount": amount,
            "cost": amount * mark_price if tx_success and mark_price else None,  # Cost in stake currency = amount * price
            "average": mark_price if tx_success else None,
            "filled": filled_amount,
            "remaining": remaining_amount,
            "status": status,
            "fee": self._build_trading_fee(symbol, amount),
            "trades": None,
            "info": info,
        }

    async def _create_standalone_sltp_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        params: dict,
    ) -> dict:
        """Create standalone SL/TP order for existing position (async version).

        This method creates a standalone stop-loss or take-profit order for an
        existing GMX position asynchronously. Unlike bundled orders (created with
        position opening), these are created separately after a position is already open.

        Used by Freqtrade's ``create_stoploss()`` method when
        ``stoploss_on_exchange=True``.

        :param symbol: Market symbol (e.g., "ETH/USDC:USDC")
        :param type: Order type ("stop_loss" or "take_profit")
        :param side: Order side ("sell" to close long, "buy" to close short)
        :param amount: Position size in USD to close
        :param params: Parameters dict containing:

            - ``stopLossPrice`` or ``takeProfitPrice``: Trigger price (float)
            - ``leverage``: Position leverage (required)
            - ``collateral_symbol``: Collateral token (optional, inferred from symbol)
            - ``slippage_percent``: Slippage tolerance (default: 0.003)
            - ``execution_buffer``: Execution fee buffer multiplier (default: 2.5)

        :return: CCXT-compatible order structure
        :raises NotSupported: Async version not yet implemented
        :raises InvalidOrder: If required parameters are missing
        """
        # Async implementation would require async GMXTrading methods
        raise NotSupported(f"{self.id} async standalone SLTP orders not yet implemented. Use sync GMX exchange class for standalone SL/TP orders in Freqtrade.")

    async def create_market_buy_order(
        self,
        symbol: str,
        amount: float,
        params: dict | None = None,
    ) -> dict:
        """Create a market buy order (long position) asynchronously.

        :param symbol: Market symbol
        :param amount: Order size in USD
        :param params: Additional parameters (can include SL/TP)
        :return: CCXT-compatible order structure
        """
        return await self.create_order(symbol, "market", "buy", amount, None, params)

    async def create_market_sell_order(
        self,
        symbol: str,
        amount: float,
        params: dict | None = None,
    ) -> dict:
        """Create a market sell order (short position) asynchronously.

        :param symbol: Market symbol
        :param amount: Order size in USD
        :param params: Additional parameters (can include SL/TP)
        :return: CCXT-compatible order structure
        """
        return await self.create_order(symbol, "market", "sell", amount, None, params)

    def clear_order_cache(self):
        """Clear the in-memory order cache.

        Call this when switching strategies or starting a fresh session
        to avoid stale order data from previous runs.
        """
        self._orders = {}
        logger.info("Cleared order cache (async)")

    def reset_failure_counter(self):
        """Reset consecutive failure counter and resume trading.

        Call this after fixing issues that caused transaction failures
        (e.g., topping up wallet gas, adjusting execution buffer).

        Example::

            # After topping up wallet
            gmx.reset_failure_counter()
            # Trading can now resume
        """
        self._consecutive_failures = 0
        self._trading_paused = False
        self._trading_paused_reason = ""
        self._last_failed_tx_hash = None
        logger.info(
            "Reset failure counter - trading resumed (async). Consecutive failures: %d, Paused: %s",
            self._consecutive_failures,
            self._trading_paused,
        )
