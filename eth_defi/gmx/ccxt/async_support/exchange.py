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

        # Fetch leverage data from Subsquid
        market_infos = await self.subsquid.get_market_infos(limit=200)

        leverage_by_market = {}
        for info in market_infos:
            addr = info.get("marketTokenAddress")
            min_collateral = info.get("minCollateralFactor")
            if addr and min_collateral:
                from cchecksum import to_checksum_address

                addr = to_checksum_address(addr)
                max_leverage = AsyncGMXSubsquidClient.calculate_max_leverage(min_collateral)
                if max_leverage:
                    leverage_by_market[addr] = max_leverage

        # Convert to CCXT format (reuse parsing logic from sync version)
        self.markets = {}
        for market_addr, market_data in available_markets.items():
            symbol = f"{market_data['index_token_symbol']}/USD"

            self.markets[symbol] = {
                "id": market_data["index_token_symbol"],
                "symbol": symbol,
                "base": market_data["index_token_symbol"],
                "quote": "USD",
                "active": True,
                "type": "swap",
                "spot": False,
                "margin": True,
                "swap": True,
                "future": False,
                "option": False,
                "contract": True,
                "settle": "USD",
                "settleId": "USD",
                "contractSize": 1,
                "linear": True,
                "inverse": False,
                "info": market_data,
                "precision": {
                    "amount": 8,
                    "price": 8,
                },
                "limits": {
                    "leverage": {
                        "min": 1.1,
                        "max": leverage_by_market.get(market_addr, 50),
                    },
                    "amount": {
                        "min": 0.00000001,
                        "max": None,
                    },
                    "price": {
                        "min": None,
                        "max": None,
                    },
                    "cost": {
                        "min": None,
                        "max": None,
                    },
                },
            }

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
            endpoint=f"/prices/candles/{token_symbol}",
            params={"period": gmx_period},
            session=self.session,
        )

        candles_data = data.get("candles", [])

        # Parse candles
        ohlcv = []
        for candle in candles_data:
            timestamp = candle.get("timestamp", 0) * 1000  # Convert to ms

            # Filter by since if provided
            if since and timestamp < since:
                continue

            o = float(candle.get("open", 0)) / 1e30
            h = float(candle.get("high", 0)) / 1e30
            l = float(candle.get("low", 0)) / 1e30
            c = float(candle.get("close", 0)) / 1e30
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
