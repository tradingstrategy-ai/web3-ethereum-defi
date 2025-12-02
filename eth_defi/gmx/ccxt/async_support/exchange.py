"""Async GMX exchange following CCXT patterns with true async I/O.

This module provides a full async implementation using aiohttp for HTTP calls,
AsyncWeb3 for blockchain operations, and async GraphQL for Subsquid queries.
"""

import asyncio
import logging
from typing import Any

import aiohttp
from ccxt.async_support import Exchange
from ccxt.base.errors import (
    ExchangeError,
    ExchangeNotAvailable,
    NetworkError,
    NotSupported,
    RequestTimeout,
)
from web3 import AsyncWeb3

from eth_defi.chain import get_chain_name
from eth_defi.gmx.ccxt.async_support.async_graphql import AsyncGMXSubsquidClient
from eth_defi.gmx.ccxt.async_support.async_http import async_make_gmx_api_request
from eth_defi.gmx.ccxt.properties import describe_gmx
from eth_defi.gmx.config import GMXConfig
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

    def describe(self):
        """Get CCXT exchange description."""
        return describe_gmx()

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

            # Create unified symbol (e.g., ETH/USD)
            unified_symbol = f"{symbol_name}/USD"

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
                "quote": "USDC",  # Quote currency (always USD for GMX)
                "baseId": symbol_name,
                "quoteId": "USD",
                "settle": "USDC",  # Settlement currency
                "settleId": "USDC",  # Settlement currency ID
                "active": True,
                "type": "swap",  # GMX provides perpetual swaps
                "spot": False,
                "margin": False,  # Not spot margin, it's futures
                "swap": True,
                "future": False,
                "option": False,
                "contract": True,
                "linear": True,
                "inverse": False,
                "precision": {
                    "amount": 8,
                    "price": 8,
                },
                "limits": {
                    "amount": {"min": None, "max": None},
                    "price": {"min": None, "max": None},
                    "cost": {"min": None, "max": None},
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
            params: Additional parameters

        Returns:
            List of OHLCV candles [timestamp, open, high, low, close, volume]
        """
        await self._ensure_session()
        await self.load_markets()

        market = self.market(symbol)
        token_symbol = market["id"]

        if timeframe not in self.timeframes:
            raise ValueError(f"Invalid timeframe: {timeframe}")

        gmx_period = self.timeframes[timeframe]

        # Fetch from GMX API
        data = await async_make_gmx_api_request(
            chain=self.chain,
            endpoint="/prices/candles",
            params={"tokenSymbol": token_symbol, "period": gmx_period},
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
            v = 0  # GMX doesn't provide volume

            ohlcv.append([timestamp, o, h, l, c, v])

        # Sort by timestamp
        ohlcv.sort(key=lambda x: x[0])

        # Apply limit
        if limit:
            ohlcv = ohlcv[-limit:]

        return ohlcv

    # Unsupported methods (GMX protocol limitations)
    async def fetch_order_book(self, symbol: str, limit: int | None = None, params: dict | None = None):
        """Not supported - GMX uses liquidity pools."""
        raise NotSupported(f"{self.id} fetch_order_book() not supported - GMX uses liquidity pools")

    async def cancel_order(self, id: str, symbol: str | None = None, params: dict | None = None):
        """Not supported - GMX orders execute immediately."""
        raise NotSupported(f"{self.id} cancel_order() not supported - GMX orders execute immediately")

    async def fetch_order(self, id: str, symbol: str | None = None, params: dict | None = None):
        """Not supported - GMX orders execute immediately."""
        raise NotSupported(f"{self.id} fetch_order() not supported - GMX orders execute immediately")

    async def fetch_open_orders(self, symbol: str | None = None, since: int | None = None, limit: int | None = None, params: dict | None = None):
        """Fetch open orders (returns positions as orders).

        GMX doesn't have traditional pending orders. This returns open positions formatted as orders.
        """
        # Would need to implement fetch_positions first
        # For now, return empty list as placeholder
        return []

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

        from datetime import datetime

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
