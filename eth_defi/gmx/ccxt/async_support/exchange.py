"""Async GMX exchange following CCXT patterns with true async I/O.

This module provides a full async implementation using aiohttp for HTTP calls,
AsyncWeb3 for blockchain operations, and async GraphQL for Subsquid queries.
"""

import asyncio
from datetime import datetime
import logging
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
from eth_defi.gmx.ccxt.async_support.async_graphql import AsyncGMXSubsquidClient
from eth_defi.gmx.ccxt.async_support.async_http import async_make_gmx_api_request
from eth_defi.gmx.ccxt.properties import describe_gmx
from eth_defi.gmx.ccxt.validation import _validate_ohlcv_data_sufficiency
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.constants import PRECISION
from eth_defi.gmx.core import Markets
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.order.sltp_order import SLTPEntry, SLTPOrder, SLTPParams
from eth_defi.gmx.contracts import get_contract_addresses
from eth_defi.gmx.utils import convert_raw_price_to_usd
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details

logger = logging.getLogger(__name__)


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
        sync_web3 = create_multi_provider_web3(self._rpc_url)
        self.web3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(self._rpc_url))

        # Detect chain
        if self._chain_id_override:
            chain_id = self._chain_id_override
        else:
            # Use sync web3 for initialization
            chain_id = sync_web3.eth.chain_id

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
        self.config = GMXConfig(sync_web3, user_wallet_address=self.wallet_address)

        # Initialize Subsquid client
        self.subsquid = AsyncGMXSubsquidClient(
            chain=self.chain,
            custom_endpoint=self._subsquid_endpoint,
        )
        await self.subsquid.__aenter__()

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
                            "cost": {"min": 10, "max": None},
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

    async def load_markets(self, reload: bool = False, params: dict | None = None) -> dict:
        """Load markets asynchronously.

        Args:
            reload: Force reload even if cached
            params: Additional parameters (CCXT compatibility)

        Returns:
            Dictionary mapping symbols to market info
        """
        if not reload and self.markets:
            return self.markets

        await self._ensure_session()

        # Use GraphQL by default for fast initialization (avoids slow RPC calls to Markets/Oracle)
        # Only use RPC path if explicitly requested via graphql_only=False
        use_graphql_only = not (params and params.get("graphql_only") is False) and not self.options.get("graphql_only") is False

        if use_graphql_only and self.subsquid:
            logger.info("Loading markets from GraphQL")
            return await self._load_markets_from_graphql()

        # Fetch markets list (this will need async version of Markets class)
        # For now, we'll call the sync method in executor as a bridge
        # TODO: Create fully async Markets implementation
        loop = asyncio.get_event_loop()

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
                    "cost": {"min": 10, "max": None},
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
        Queries the blockchain to get the current transaction status.

        :param id: Order ID (transaction hash)
        :type id: str
        :param symbol: Symbol (not used, for CCXT compatibility)
        :type symbol: str | None
        :param params: Additional parameters (not used)
        :type params: dict | None
        :return: CCXT-compatible order structure
        :rtype: dict
        :raises OrderNotFound: If order with given ID doesn't exist
        """
        # Check if order exists in stored orders
        if id in self._orders:
            order = self._orders[id].copy()

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

            # Fetch current transaction status from blockchain
            try:
                if id.startswith("0x"):
                    receipt = await self.web3.eth.get_transaction_receipt(id)
                    # Update status based on receipt
                    tx_success = receipt.get("status") == 1
                    order["status"] = "closed" if tx_success else "failed"

                    # GMX orders execute immediately, so update filled/remaining
                    if tx_success:
                        order["filled"] = order.get("amount")
                        order["remaining"] = 0.0
                    else:
                        order["filled"] = 0.0
                        order["remaining"] = order.get("amount")

                    # Update info with latest receipt data
                    if "info" not in order:
                        order["info"] = {}
                    order["info"]["receipt"] = receipt
                    order["info"]["block_number"] = receipt.get("blockNumber")
                    order["info"]["gas_used"] = receipt.get("gasUsed")
            except Exception as e:
                logger.warning("Could not fetch transaction receipt for %s: %s", id, e)

            return order

        # Order not in cache - try to fetch from blockchain directly
        # This handles orders from previous sessions or other strategies
        # Normalize ID: add "0x" prefix if missing (for backwards compatibility with old order IDs)
        normalized_id = id if id.startswith("0x") else f"0x{id}"

        if len(normalized_id) == 66:  # Valid tx hash length (0x + 64 hex chars)
            try:
                receipt = await self.web3.eth.get_transaction_receipt(normalized_id)
                tx = await self.web3.eth.get_transaction(normalized_id)

                # Build minimal order structure from transaction data
                tx_success = receipt.get("status") == 1
                order = {
                    "id": id,
                    "clientOrderId": None,
                    "datetime": self.iso8601(tx.get("blockNumber", 0) * 1000) if tx.get("blockNumber") else None,
                    "timestamp": tx.get("blockNumber", 0) * 1000 if tx.get("blockNumber") else None,
                    "lastTradeTimestamp": None,
                    "status": "closed" if tx_success else "failed",
                    "symbol": symbol if symbol else None,
                    "type": "market",
                    "side": None,  # Can't determine from tx alone
                    "price": None,
                    "amount": None,  # Can't determine from tx alone
                    "filled": None,  # Can't determine from tx alone
                    "remaining": 0.0 if tx_success else None,
                    "cost": None,
                    "trades": [],  # Empty list, not None - freqtrade expects a list
                    "fee": {
                        "currency": "ETH",
                        "cost": float(receipt.get("gasUsed", 0)) * float(tx.get("gasPrice", 0)) / 1e18,
                    },
                    "info": {
                        "receipt": receipt,
                        "transaction": tx,
                        "block_number": receipt.get("blockNumber"),
                        "gas_used": receipt.get("gasUsed"),
                    },
                    "average": None,
                    "fees": [],
                }

                logger.info("Fetched order %s from blockchain (not in cache)", id)
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

        # Sync wallet nonce
        # Note: AsyncWeb3 doesn't have sync methods, need to use await
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.wallet.sync_nonce, self.web3)

        logger.info("=" * 80)
        logger.info(
            "ORDER_TRACE: async create_order() CALLED symbol=%s, type=%s, side=%s, amount=%.8f",
            symbol,
            type,
            side,
            amount,
        )
        logger.info(
            "ORDER_TRACE: params: reduceOnly=%s, leverage=%s, collateral_symbol=%s",
            params.get("reduceOnly", False),
            params.get("leverage"),
            params.get("collateral_symbol"),
        )

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
        loop = asyncio.get_event_loop()
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

        logger.info(
            "ORDER_TRACE: async create_order() RETURNING order_id=%s, status=%s, filled=%.8f, cost=%.2f",
            order.get("id"),
            order.get("status"),
            order.get("filled", 0),
            order.get("cost", 0),
        )
        logger.info("=" * 80)

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
        loop = asyncio.get_event_loop()
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

        fee_cost = sltp_result.total_execution_fee / 1e18
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
            "fee": {
                "cost": fee_cost,
                "currency": "ETH",
            },
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
