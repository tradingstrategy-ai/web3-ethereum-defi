"""Async GMX exchange following CCXT patterns with true async I/O.

This module provides a full async implementation using aiohttp for HTTP calls,
AsyncWeb3 for blockchain operations, and async GraphQL for Subsquid queries.
"""

import asyncio
from datetime import datetime
import logging
from typing import Any

import aiohttp
from ccxt.async_support import Exchange
from ccxt.base.errors import (
    ExchangeError,
    ExchangeNotAvailable,
    NetworkError,
    NotSupported,
    OrderNotFound,
    RequestTimeout,
)
from web3 import AsyncWeb3

from eth_defi.chain import get_chain_name
from eth_defi.gmx.ccxt.async_support.async_graphql import AsyncGMXSubsquidClient
from eth_defi.gmx.ccxt.errors import InsufficientHistoricalDataError
from eth_defi.gmx.ccxt.validation import _validate_ohlcv_data_sufficiency
from eth_defi.gmx.ccxt.async_support.async_http import async_make_gmx_api_request
from eth_defi.gmx.ccxt.properties import describe_gmx
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.open_positions import GetOpenPositions
from eth_defi.gmx.core import Markets
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3

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
            logger.info(f"Fetched {len(market_infos)} markets from GraphQL")

            # Fetch token data from GMX API using async HTTP
            tokens_data = await self._fetch_tokens_async()
            logger.debug(f"Fetched tokens from GMX API, type: {type(tokens_data)}, length: {len(tokens_data) if isinstance(tokens_data, (list, dict)) else 'N/A'}")

            # Build address->symbol mapping (lowercase addresses for matching)
            address_to_symbol = {}
            if isinstance(tokens_data, dict):
                # If tokens_data is a dict, extract the list of tokens
                tokens_list = tokens_data.get("tokens", [])
            elif isinstance(tokens_data, list):
                tokens_list = tokens_data
            else:
                logger.error(f"Unexpected tokens_data format: {type(tokens_data)}")
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

            logger.debug(f"Built address mapping for {len(address_to_symbol)} tokens, metadata for {len(self._token_metadata)} tokens")

            markets_dict = {}
            for market_info in market_infos:
                try:
                    index_token_addr = market_info.get("indexTokenAddress", "").lower()
                    market_token_addr = market_info.get("marketTokenAddress", "")

                    # Look up symbol from GMX API tokens data
                    symbol_name = address_to_symbol.get(index_token_addr)

                    if not symbol_name:
                        logger.debug(f"Skipping market with unknown index token: {index_token_addr}")
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
                    logger.debug(f"Failed to process market {market_info.get('marketTokenAddress')}: {e}")
                    continue

            self.markets = markets_dict
            self.symbols = list(self.markets.keys())

            logger.info(f"Loaded {len(self.markets)} markets from GraphQL")
            logger.debug(f"Market symbols: {self.symbols}")
            return self.markets

        except Exception as e:
            logger.error(f"Failed to load markets from GraphQL: {e}")
            # Return empty markets rather than failing completely
            self.markets = {}
            self.symbols = []
            return self.markets

    async def _fetch_tokens_async(self) -> list[dict]:
        """Fetch token data from GMX API asynchronously."""
        from eth_defi.gmx.ccxt.async_support.async_http import async_make_gmx_api_request

        try:
            tokens_data = await async_make_gmx_api_request(
                chain=self.chain,
                endpoint="/tokens",
                session=self.session,
                timeout=10.0,
            )
            return tokens_data
        except Exception as e:
            logger.error(f"Failed to fetch tokens from GMX API: {e}")
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
                        from eth_utils import to_checksum_address

                        market_addr = to_checksum_address(market_addr)
                        max_leverage = AsyncGMXSubsquidClient.calculate_max_leverage(min_collateral_factor)
                        if max_leverage is not None:
                            leverage_by_market[market_addr] = max_leverage
                            min_collateral_by_market[market_addr] = min_collateral_factor
            except Exception as e:
                logger.warning(f"Failed to fetch leverage data from subsquid: {e}")

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
        from eth_defi.gmx.utils import convert_raw_price_to_usd

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
                logger.warning(f"Could not fetch transaction receipt for {id}: {e}")

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

                logger.info(f"Fetched order {id} from blockchain (not in cache)")
                return order

            except Exception as e:
                logger.warning(f"Could not fetch transaction {id} from blockchain: {e}")
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
        from datetime import datetime

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
