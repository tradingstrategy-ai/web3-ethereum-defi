"""CCXT-Compatible Wrapper for GMX Protocol.

This module provides a CCXT-compatible synchronous interface for accessing GMX protocol
market data and trading functionality.

Example usage::

    from web3 import Web3
    from eth_defi.gmx.config import GMXConfig
    from eth_defi.gmx.ccxt import GMXCCXT

    # Initialize
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig(web3)
    gmx = GMXCCXT(config)

    # Fetch OHLCV data (CCXT-style)
    ohlcv = gmx.fetch_ohlcv("ETH/USD", "1h", limit=100)

.. note::
    GMX protocol does not provide volume data in candlesticks, so volume
    will always be 0 in the returned OHLCV arrays.
"""

from datetime import datetime
from typing import Optional, Any
import time
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.api import GMXAPI
from eth_defi.gmx.graphql.client import GMXSubsquidClient
from eth_defi.gmx.core.markets import Markets


class GMXCCXT:
    """
    CCXT-compatible wrapper for GMX protocol market data and trading.

    This class provides a familiar CCXT-style interface for interacting with
    GMX protocol, implementing synchronous methods and data structures that match
    CCXT conventions. This allows traders to use GMX with minimal changes to
    existing CCXT-based trading systems.

    :ivar config: GMX configuration object
    :vartype config: GMXConfig
    :ivar api: GMX API client for market data
    :vartype api: GMXAPI
    :ivar subsquid: Subsquid GraphQL client for historical data
    :vartype subsquid: GMXSubsquidClient
    :ivar markets: Dictionary of available markets (populated by load_markets)
    :vartype markets: Dict[str, Any]
    :ivar timeframes: Supported timeframe intervals
    :vartype timeframes: Dict[str, str]
    :ivar markets_loaded: Flag indicating if markets have been loaded
    :vartype markets_loaded: bool
    """

    def __init__(
        self,
        config: GMXConfig,
        subsquid_endpoint: Optional[str] = None,
    ):
        """
        Initialize the CCXT wrapper with GMX configuration.

        :param config: GMX configuration object containing network settings and optional wallet information
        :type config: GMXConfig
        :param subsquid_endpoint: Optional Subsquid GraphQL endpoint URL
        :type subsquid_endpoint: Optional[str]
        """
        self.config = config
        self.api = GMXAPI(config)

        # Initialize Subsquid client with chain from config
        chain = config.get_chain()
        self.subsquid = GMXSubsquidClient(chain=chain, custom_endpoint=subsquid_endpoint)

        self.markets: dict[str, Any] = {}
        self.markets_loaded = False

        # Timeframes supported by GMX API
        # Maps CCXT-style timeframe strings to GMX API periods
        self.timeframes = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "1h": "1h",
            "4h": "4h",
            "1d": "1d",
        }

    def load_markets(self, reload: bool = False) -> dict[str, Any]:
        """Load available markets from GMX protocol.

        This method fetches the list of supported markets from GMX and constructs
        CCXT-compatible market structures. Markets are cached after the first load
        to improve performance.

        :param reload: If True, force reload markets even if already loaded
        :type reload: bool
        :return: dictionary mapping unified symbols (e.g., "ETH/USD") to market info
        :rtype: dict[str, Any]

        Example::

            markets = gmx.load_markets()
            print(markets["ETH/USD"])
        """
        if self.markets_loaded and not reload:
            return self.markets

        # Fetch available markets from GMX using Markets class
        markets_instance = Markets(self.config)
        available_markets = markets_instance.get_available_markets()

        # Process markets into CCXT-style format
        for market_address, market_data in available_markets.items():
            symbol_name = market_data.get("market_symbol", "")
            if not symbol_name or symbol_name == "UNKNOWN":
                continue

            # Create unified symbol (e.g., ETH/USD)
            unified_symbol = f"{symbol_name}/USD"

            self.markets[unified_symbol] = {
                "id": symbol_name,  # GMX market symbol
                "symbol": unified_symbol,  # CCXT unified symbol
                "base": symbol_name,  # Base currency (e.g., ETH)
                "quote": "USD",  # Quote currency (always USD for GMX)
                "baseId": symbol_name,
                "quoteId": "USD",
                "active": True,
                "type": "swap",  # GMX provides perpetual swaps
                "spot": False,
                "swap": True,
                "future": False,
                "option": False,
                "contract": True,
                "linear": True,
                "precision": {
                    "amount": 8,
                    "price": 8,
                },
                "limits": {
                    "amount": {"min": None, "max": None},
                    "price": {"min": None, "max": None},
                    "cost": {"min": None, "max": None},
                },
                "info": {
                    "market_token": market_address,  # Market contract address
                    "index_token": market_data.get("index_token_address"),
                    "long_token": market_data.get("long_token_address"),
                    "short_token": market_data.get("short_token_address"),
                    **market_data,
                },
            }

        self.markets_loaded = True
        return self.markets

    def fetch_markets(
        self,
        params: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch all available markets from GMX protocol.

        This method fetches market data from GMX and returns it as a list of market structures.
        Unlike load_markets(), this method does not cache the results and always fetches fresh data.

        :param params: Additional parameters (not used currently)
        :type params: Optional[Dict[str, Any]]
        :returns: List of market structures
        :rtype: List[Dict[str, Any]]

        Example::

            markets = gmx.fetch_markets()
            for market in markets:
                print(f"{market['symbol']}: {market['base']}/{market['quote']}")
        """
        if params is None:
            params = {}

        # Fetch available markets from GMX using Markets class
        markets_instance = Markets(self.config)
        available_markets = markets_instance.get_available_markets()

        markets = []

        # Process markets into CCXT-style format
        for market_address, market_data in available_markets.items():
            symbol_name = market_data.get("market_symbol", "")
            if not symbol_name or symbol_name == "UNKNOWN":
                continue

            unified_symbol = f"{symbol_name}/USD"

            market = {
                "id": symbol_name,
                "symbol": unified_symbol,
                "base": symbol_name,
                "quote": "USD",
                "baseId": symbol_name,
                "quoteId": "USD",
                "active": True,
                "type": "swap",  # GMX provides perpetual swaps
                "spot": False,
                "swap": True,
                "future": False,
                "option": False,
                "contract": True,
                "linear": True,
                "precision": {
                    "amount": 8,
                    "price": 8,
                },
                "limits": {
                    "amount": {"min": None, "max": None},
                    "price": {"min": None, "max": None},
                    "cost": {"min": None, "max": None},
                },
                "info": {
                    "market_token": market_address,  # Market contract address
                    "index_token": market_data.get("index_token_address"),
                    "long_token": market_data.get("long_token_address"),
                    "short_token": market_data.get("short_token_address"),
                    **market_data,
                },
            }
            markets.append(market)

        return markets

    def market(self, symbol: str) -> dict[str, Any]:
        """Get market information for a specific trading pair.

        :param symbol: Unified symbol (e.g., "ETH/USD")
        :type symbol: str
        :return: Market information dictionary
        :rtype: dict[str, Any]
        :raises ValueError: If markets haven't been loaded or symbol not found
        """
        if not self.markets_loaded:
            raise ValueError("Markets not loaded. Call load_markets() first.")

        if symbol not in self.markets:
            raise ValueError(f"Market {symbol} not found. Available markets: {list(self.markets.keys())}")

        return self.markets[symbol]

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        since: Optional[int] = None,
        limit: Optional[int] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> list[list]:
        """Fetch historical OHLCV (Open, High, Low, Close, Volume) candlestick data.

        This method follows CCXT conventions for fetching historical market data.
        It returns a list of OHLCV candles where each candle is a list of
        [timestamp, open, high, low, close, volume].

        :param symbol: Unified symbol (e.g., "ETH/USD", "BTC/USD")
        :type symbol: str
        :param timeframe: Candlestick interval - "1m", "5m", "15m", "1h", "4h", "1d"
        :type timeframe: str
        :param since: Unix timestamp in milliseconds for the earliest candle to fetch (GMX API returns recent candles, filtering is done client-side)
        :type since: Optional[int]
        :param limit: Maximum number of candles to return
        :type limit: Optional[int]
        :param params: Additional parameters (e.g., {"until": timestamp_ms})
        :type params: Optional[dict[str, Any]]
        :return: list of OHLCV candles, each as [timestamp_ms, open, high, low, close, volume]
        :rtype: list[list]
        :raises ValueError: If invalid symbol or timeframe

        .. note::
            Volume is always 0 as GMX API doesn't provide volume data

        Example::

            # Fetch last 100 hourly candles for ETH
            candles = gmx.fetch_ohlcv("ETH/USD", "1h", limit=100)

            # Fetch candles since specific time
            since = int(time.time() * 1000) - 86400000
            candles = gmx.fetch_ohlcv("ETH/USD", "1h", since=since)

            # Each candle: [timestamp, open, high, low, close, volume]
            for candle in candles:
                timestamp, o, h, l, c, v = candle
                print(f"{timestamp}: O:{o} H:{h} L:{l} C:{c} V:{v}")
        """
        # TODO: Keeping fot CCXT compatibility. Not using this parameter for now
        if params is None:
            params = {}

        # Ensure markets are loaded
        self.load_markets()

        # Get market info and extract GMX token symbol
        market_info = self.market(symbol)
        token_symbol = market_info["id"]  # GMX token symbol (e.g., "ETH")

        # Validate timeframe
        if timeframe not in self.timeframes:
            raise ValueError(f"Invalid timeframe: {timeframe}. Supported: {list(self.timeframes.keys())}")

        gmx_period = self.timeframes[timeframe]

        # Fetch candlestick data from GMX API
        response = self.api.get_candlesticks(token_symbol, gmx_period)

        # Parse the response
        candles_data = response.get("candles", [])

        # Parse OHLCV data
        ohlcv = self.parse_ohlcvs(candles_data, market_info, timeframe, since, limit)

        return ohlcv

    def parse_ohlcvs(
        self,
        ohlcvs: list[list],
        market: Optional[dict[str, Any]] = None,
        timeframe: str = "1m",  # CCXT uses this format so adding this for interface compatibility
        since: Optional[int] = None,
        limit: Optional[int] = None,
        use_tail: bool = True,
    ) -> list[list]:
        """Parse multiple OHLCV candles from GMX format to CCXT format.

        Converts GMX candlestick data (5 fields) to CCXT format (6 fields with volume).
        Applies filtering based on 'since' timestamp and 'limit' parameters.

        :param ohlcvs: list of raw OHLCV data from GMX API
        :type ohlcvs: list[list]
        :param market: Market information dictionary (optional)
        :type market: Optional[dict[str, Any]]
        :param timeframe: Candlestick interval
        :type timeframe: str
        :param since: Filter candles after this timestamp (ms)
        :type since: Optional[int]
        :param limit: Maximum number of candles to return
        :type limit: Optional[int]
        :param use_tail: If True, return the most recent candles when limiting
        :type use_tail: bool
        :return: list of parsed OHLCV candles in CCXT format
        :rtype: list[list]
        """
        parsed = [self.parse_ohlcv(ohlcv, market) for ohlcv in ohlcvs]

        # Sort by timestamp (ascending)
        parsed = sorted(parsed, key=lambda x: x[0])

        # Filter by 'since' parameter if provided
        if since is not None:
            parsed = [candle for candle in parsed if candle[0] >= since]

        # Apply limit
        if limit is not None and len(parsed) > limit:
            if use_tail:
                # Return the most recent 'limit' candles
                parsed = parsed[-limit:]
            else:
                # Return the oldest 'limit' candles
                parsed = parsed[:limit]

        return parsed

    def fetch_open_interest(
        self,
        symbol: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Fetch current open interest for a symbol.

        This method returns the current open interest data for both long and short
        positions on GMX protocol using the fast Subsquid GraphQL endpoint.

        :param symbol: Unified symbol (e.g., "ETH/USD", "BTC/USD")
        :type symbol: str
        :param params: Additional parameters - can include "market_address" to query specific market
        :type params: Optional[dict[str, Any]]
        :returns: dictionary with open interest information::

            {
                "symbol": "ETH/USD",
                "baseVolume": 0,  # Not provided by GMX
                "quoteVolume": 0,  # Not provided by GMX
                "openInterestAmount": 0,  # Not provided by GMX
                "openInterestValue": 123456789.0,  # Total OI in USD
                "longOpenInterest": 62000000.0,  # Long positions in USD
                "shortOpenInterest": 61456789.0,  # Short positions in USD
                "timestamp": 1234567890000,
                "datetime": "2021-01-01T00:00:00.000Z",
                "info": {...},  # Raw Subsquid data
            }

        :rtype: dict[str, Any]
        :raises ValueError: If invalid symbol or markets not loaded

        Example::

            # Get current open interest for ETH
            oi = exchange.fetch_open_interest("ETH/USD")
            print(f"Total OI: ${oi['openInterestValue']:,.0f}")
            print(f"Long OI: ${oi['longOpenInterest']:,.0f}")
            print(f"Short OI: ${oi['shortOpenInterest']:,.0f}")

        .. note::
            Data is fetched from Subsquid GraphQL endpoint for fast access.
            GMX provides open interest in USD value only.
        """
        if params is None:
            params = {}

        # Ensure markets are loaded
        self.load_markets()

        # Get market info
        market_info = self.market(symbol)
        market_address = params.get("market_address", market_info["info"]["market_token"])

        # Fetch latest market info from Subsquid (fast)
        market_infos = self.subsquid.get_market_infos(
            market_address=market_address,
            limit=1,
            order_by="id_DESC",
        )

        if not market_infos:
            raise ValueError(f"No market info found for {symbol}")

        info = market_infos[0]

        # Parse 30-decimal USD values
        long_oi = float(info.get("longOpenInterestUsd", 0)) / 1e30
        short_oi = float(info.get("shortOpenInterestUsd", 0)) / 1e30
        total_oi = long_oi + short_oi

        timestamp = self.milliseconds()

        return {
            "symbol": symbol,
            "baseVolume": 0,  # GMX doesn't provide volume
            "quoteVolume": 0,  # GMX doesn't provide volume
            "openInterestAmount": 0,  # GMX doesn't provide this (contracts)
            "openInterestValue": total_oi,  # Total in USD
            "longOpenInterest": long_oi,  # GMX-specific field
            "shortOpenInterest": short_oi,  # GMX-specific field
            "timestamp": timestamp,
            "datetime": datetime.fromtimestamp(timestamp / 1000).isoformat() + "Z",
            "info": info,
        }

    def fetch_open_interest_history(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: Optional[int] = None,
        limit: Optional[int] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch historical open interest data from Subsquid.

        Retrieves historical open interest snapshots from the GMX Subsquid GraphQL endpoint.
        Data includes long and short open interest values over time.

        :param symbol: Unified symbol (e.g., "ETH/USD")
        :type symbol: str
        :param timeframe: Time interval (note: data is snapshot-based, not aggregated)
        :type timeframe: str
        :param since: Start timestamp in milliseconds
        :type since: Optional[int]
        :param limit: Maximum number of records (default: 100)
        :type limit: Optional[int]
        :param params: Additional parameters (e.g., {"market_address": "0x..."})
        :type params: Optional[dict[str, Any]]
        :returns: list of historical open interest snapshots
        :rtype: list[dict[str, Any]]
        :raises ValueError: If invalid symbol or markets not loaded

        Example::

            # Get historical OI for ETH
            history = exchange.fetch_open_interest_history("ETH/USD", limit=50)
            for snapshot in history:
                print(f"{snapshot['datetime']}: ${snapshot['openInterestValue']:,.0f}")

        .. note::
            Data is fetched from Subsquid GraphQL endpoint.
            Returns snapshots, not time-aggregated data.
        """
        if params is None:
            params = {}

        if limit is None:
            limit = 100

        market_address = params.get("market_address")

        market_infos = self.subsquid.get_market_infos(
            market_address=market_address,
            limit=limit,
        )

        result = []
        for info in market_infos:
            long_oi = float(info.get("longOpenInterestUsd", 0)) / 1e30
            short_oi = float(info.get("shortOpenInterestUsd", 0)) / 1e30
            total_oi = long_oi + short_oi

            # Try to extract timestamp from ID (format: "blockNumber-index" or similar)
            # If not available, use current time
            timestamp_ms = None
            try:
                info_id = info.get("id", "")
                # Subsquid IDs often contain block number or timestamp
                # For now, we'll use current time if no explicit timestamp field
                timestamp_ms = self.milliseconds()
            except Exception:
                timestamp_ms = self.milliseconds()

            result.append(
                {
                    "symbol": symbol,
                    "openInterestAmount": 0,  # GMX doesn't provide this in contracts
                    "openInterestValue": total_oi,
                    "longOpenInterest": long_oi,
                    "shortOpenInterest": short_oi,
                    "timestamp": timestamp_ms,
                    "datetime": datetime.fromtimestamp(timestamp_ms / 1000).isoformat() + "Z",
                    "info": info,
                }
            )

        return result

    def fetch_funding_rate(
        self,
        symbol: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Fetch current funding rate for a symbol.

        This method returns the current funding rate for both long and short
        positions on GMX protocol using the fast Subsquid GraphQL endpoint.

        :param symbol: Unified symbol (e.g., "ETH/USD", "BTC/USD")
        :type symbol: str
        :param params: Additional parameters - can include "market_address" to query specific market
        :type params: Optional[dict[str, Any]]
        :returns: dictionary with funding rate information::

            {
                "symbol": "ETH/USD",
                "fundingRate": 0.0001,  # Per-second rate (as decimal)
                "longFundingRate": 0.0001,  # Long position rate (per-second)
                "shortFundingRate": -0.0001,  # Short position rate (per-second)
                "fundingTimestamp": 1234567890000,
                "fundingDatetime": "2021-01-01T00:00:00.000Z",
                "timestamp": 1234567890000,
                "datetime": "2021-01-01T00:00:00.000Z",
                "info": {...},  # Raw Subsquid data
            }

        :rtype: dict[str, Any]
        :raises ValueError: If invalid symbol or markets not loaded

        Example::

            # Get current funding rate for BTC
            fr = exchange.fetch_funding_rate("BTC/USD")
            # Convert per-second to hourly
            hourly_rate = fr["fundingRate"] * 3600
            print(f"Hourly funding: {hourly_rate:.6f}")

            # Positive rate = longs pay shorts
            # Negative rate = shorts pay longs

        .. note::
            Data is fetched from Subsquid GraphQL endpoint for fast access.
            Rates are per-second values. Multiply by 3600 for hourly rate.
        """
        if params is None:
            params = {}

        # Ensure markets are loaded
        self.load_markets()

        # Get market info
        market_info = self.market(symbol)
        market_address = params.get("market_address", market_info["info"]["market_token"])

        # Fetch latest market info from Subsquid (fast)
        market_infos = self.subsquid.get_market_infos(
            market_address=market_address,
            limit=1,
            order_by="id_DESC",
        )

        if not market_infos:
            raise ValueError(f"No market info found for {symbol}")

        info = market_infos[0]

        # Parse 30-decimal funding rate values
        funding_per_second = float(info.get("fundingFactorPerSecond", 0)) / 1e30
        longs_pay_shorts = info.get("longsPayShorts", True)

        # Determine direction based on longsPayShorts flag
        if longs_pay_shorts:
            long_funding = funding_per_second
            short_funding = -funding_per_second
        else:
            long_funding = -funding_per_second
            short_funding = funding_per_second

        timestamp = self.milliseconds()

        return {
            "symbol": symbol,
            "fundingRate": funding_per_second,  # Per-second rate
            "longFundingRate": long_funding,  # GMX-specific field
            "shortFundingRate": short_funding,  # GMX-specific field
            "fundingTimestamp": timestamp,
            "fundingDatetime": datetime.fromtimestamp(timestamp / 1000).isoformat() + "Z",
            "timestamp": timestamp,
            "datetime": datetime.fromtimestamp(timestamp / 1000).isoformat() + "Z",
            "info": info,
        }

    def fetch_funding_rate_history(
        self,
        symbol: str,
        since: Optional[int] = None,
        limit: Optional[int] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch historical funding rate data from Subsquid.

        Retrieves historical funding rate snapshots from the GMX Subsquid GraphQL endpoint.
        Data includes funding rates per second and direction (longs pay shorts or vice versa).

        :param symbol: Unified symbol (e.g., "ETH/USD")
        :type symbol: str
        :param since: Start timestamp in milliseconds
        :type since: Optional[int]
        :param limit: Maximum number of records (default: 100)
        :type limit: Optional[int]
        :param params: Additional parameters (e.g., {"market_address": "0x..."})
        :type params: Optional[dict[str, Any]]
        :returns: list of historical funding rate snapshots
        :rtype: list[dict[str, Any]]
        :raises ValueError: If invalid symbol or markets not loaded

        Example::

            # Get historical funding rates for BTC
            history = exchange.fetch_funding_rate_history("BTC/USD", limit=50)
            for snapshot in history:
                rate = snapshot["fundingRate"]
                print(f"{snapshot['datetime']}: {rate * 100:.6f}% per hour")

        .. note::
            Data is fetched from Subsquid GraphQL endpoint.
            Funding rates are per-second values, multiply by 3600 for hourly rate.
        """
        if params is None:
            params = {}

        if limit is None:
            limit = 100

        market_address = params.get("market_address")
        since_seconds = since // 1000 if since else None

        market_infos = self.subsquid.get_market_infos(
            market_address=market_address,
            limit=limit,
        )

        result = []
        for info in market_infos:
            funding_per_second = float(info.get("fundingFactorPerSecond", 0)) / 1e30
            longs_pay_shorts = info.get("longsPayShorts", True)

            # Try to extract timestamp from ID or use current time
            timestamp_ms = None
            try:
                info_id = info.get("id", "")
                # Subsquid IDs often contain block number or timestamp
                # For now, we'll use current time if no explicit timestamp field
                timestamp_ms = self.milliseconds()
            except Exception:
                timestamp_ms = self.milliseconds()

            datetime_str = datetime.fromtimestamp(timestamp_ms / 1000).isoformat() + "Z"

            result.append(
                {
                    "symbol": symbol,
                    "fundingRate": funding_per_second,
                    "longFundingRate": funding_per_second if longs_pay_shorts else -funding_per_second,
                    "shortFundingRate": -funding_per_second if longs_pay_shorts else funding_per_second,
                    "fundingTimestamp": timestamp_ms,
                    "fundingDatetime": datetime_str,
                    "timestamp": timestamp_ms,
                    "datetime": datetime_str,
                    "info": info,
                }
            )

        return result

    def parse_ohlcv(
        self,
        ohlcv: list,
        market: Optional[dict[str, Any]] = None,  # CCXT uses this format so adding this for interface compatibility
    ) -> list:
        """Parse a single OHLCV candle from GMX format to CCXT format.

        GMX returns: [timestamp_seconds, open, high, low, close]
        CCXT expects: [timestamp_ms, open, high, low, close, volume]

        :param ohlcv: Single candle data from GMX [timestamp_s, open, high, low, close]
        :type ohlcv: list
        :param market: Market information dictionary (optional)
        :type market: Optional[dict[str, Any]]
        :return: Parsed candle in CCXT format [timestamp_ms, open, high, low, close, volume]
        :rtype: list

        .. note::
            Volume is set to 0 as GMX doesn't provide it
        """
        # GMX format: [timestamp (seconds), open, high, low, close]
        # CCXT format: [timestamp (milliseconds), open, high, low, close, volume]

        if len(ohlcv) < 5:
            raise ValueError(f"Invalid OHLCV data: expected at least 5 fields, got {len(ohlcv)}")

        timestamp_seconds = ohlcv[0]
        timestamp_ms = int(timestamp_seconds * 1000)  # Convert to milliseconds

        return [
            timestamp_ms,  # Timestamp in milliseconds
            float(ohlcv[1]),  # Open
            float(ohlcv[2]),  # High
            float(ohlcv[3]),  # Low
            float(ohlcv[4]),  # Close
            0,  # Volume (GMX doesn't provide volume data)
        ]

    def parse_timeframe(self, timeframe: str) -> int:
        """Convert timeframe string to duration in seconds.

        :param timeframe: Timeframe string (e.g., "1m", "1h", "1d")
        :type timeframe: str
        :return: Duration in seconds
        :rtype: int

        Example::

            seconds = gmx.parse_timeframe("1h")  # Returns 3600
            seconds = gmx.parse_timeframe("1d")  # Returns 86400
        """
        timeframe_mapping = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400,
        }

        if timeframe not in timeframe_mapping:
            raise ValueError(f"Invalid timeframe: {timeframe}")

        return timeframe_mapping[timeframe]

    def milliseconds(self) -> int:
        """Get current Unix timestamp in milliseconds.

        :return: Current timestamp in milliseconds
        :rtype: int

        Example::

            now = gmx.milliseconds()
            print(f"Current time: {now} ms")
        """
        return int(time.time() * 1000)

    def safe_integer(
        self,
        dictionary: dict[str, Any],
        key: str,
        default: Optional[int] = None,
    ) -> Optional[int]:
        """Safely extract an integer value from a dictionary.

        :param dictionary: dictionary to extract from
        :type dictionary: dict[str, Any]
        :param key: Key to look up
        :type key: str
        :param default: Default value if key not found
        :type default: Optional[int]
        :return: Integer value or default
        :rtype: Optional[int]
        """
        value = dictionary.get(key, default)
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    def safe_string(
        self,
        dictionary: dict[str, Any],
        key: str,
        default: Optional[str] = None,
    ) -> Optional[str]:
        """Safely extract a string value from a dictionary.

        :param dictionary: dictionary to extract from
        :type dictionary: dict[str, Any]
        :param key: Key to look up
        :type key: str
        :param default: Default value if key not found
        :type default: Optional[str]
        :return: String value or default
        :rtype: Optional[str]
        """
        value = dictionary.get(key, default)
        if value is None:
            return default
        return str(value)

    def sum(self, a: float, b: float) -> float:
        """Add two numbers safely.

        :param a: First number
        :type a: float
        :param b: Second number
        :type b: float
        :return: Sum of a and b
        :rtype: float
        """
        return a + b

    def omit(self, dictionary: dict[str, Any], keys: list[str]) -> dict[str, Any]:
        """Create a new dictionary excluding specified keys.

        :param dictionary: Source dictionary
        :type dictionary: dict[str, Any]
        :param keys: list of keys to exclude
        :type keys: list[str]
        :return: New dictionary without the specified keys
        :rtype: dict[str, Any]
        """
        return {k: v for k, v in dictionary.items() if k not in keys}
