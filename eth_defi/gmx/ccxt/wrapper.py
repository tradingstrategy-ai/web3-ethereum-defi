"""CCXT-Compatible Wrapper for GMX Protocol.

This module provides a CCXT-compatible synchronous interface for accessing GMX protocol
market data and trading functionality.

Example usage::

    from web3 import Web3
    from eth_defi.gmx.config import GMXConfig
    from eth_defi.gmx.ccxt import GMXCCXTWrapper

    # Initialize
    web3 = Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))
    config = GMXConfig(web3)
    exchange = GMXCCXTWrapper(config)

    # Fetch OHLCV data (CCXT-style)
    ohlcv = exchange.fetch_ohlcv("ETH/USD", "1h", limit=100)

.. note::
    GMX protocol does not provide volume data in candlesticks, so volume
    will always be 0 in the returned OHLCV arrays.
"""

from typing import Optional, Any
import time
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.api import GMXAPI


class GMXCCXT:
    """CCXT-compatible wrapper for GMX protocol market data and trading.

    This class provides a familiar CCXT-style interface for interacting with
    GMX protocol, implementing synchronous methods and data structures that match
    CCXT conventions. This allows traders to use GMX with minimal changes to
    existing CCXT-based trading systems.

    :ivar config: GMX configuration object
    :ivar api: GMX API client for market data
    :ivar markets: Dictionary of available markets (populated by load_markets)
    :ivar timeframes: Supported timeframe intervals
    :ivar markets_loaded: Flag indicating if markets have been loaded
    """

    def __init__(self, config: GMXConfig):
        """Initialize the CCXT wrapper with GMX configuration.

        :param config: GMX configuration object containing network settings and optional wallet information
        :type config: GMXConfig
        """
        self.config = config
        self.api = GMXAPI(config)
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

        This method fetches the list of supported tokens from GMX and constructs
        CCXT-compatible market structures. Markets are cached after the first load
        to improve performance.

        :param reload: If True, force reload markets even if already loaded
        :type reload: bool
        :return: Dictionary mapping unified symbols (e.g., "ETH/USD") to market info
        :rtype: dict[str, Any]

        Example::

            markets = exchange.load_markets()
            print(markets["ETH/USD"])
        """
        if self.markets_loaded and not reload:
            return self.markets

        # Fetch available tokens from GMX
        tokens_response = self.api.get_tokens()

        # Process tokens into CCXT-style markets
        # GMX tokens are priced in USD
        for token in tokens_response.get("tokens", []):
            symbol_name = token.get("symbol", "")
            if not symbol_name:
                continue

            # Create unified symbol (e.g., ETH/USD)
            unified_symbol = f"{symbol_name}/USD"

            self.markets[unified_symbol] = {
                "id": symbol_name,  # GMX uses simple token symbols
                "symbol": unified_symbol,  # CCXT unified symbol
                "base": symbol_name,  # Base currency (e.g., ETH)
                "quote": "USD",  # Quote currency (always USD for GMX)
                "baseId": symbol_name,
                "quoteId": "USD",
                "active": True,
                "type": "spot",  # GMX API provides this
                "spot": True,
                "swap": False,
                "future": False,
                "option": False,
                "contract": False,
                "precision": {
                    "amount": 8,
                    "price": 8,
                },
                "limits": {
                    "amount": {"min": None, "max": None},
                    "price": {"min": None, "max": None},
                    "cost": {"min": None, "max": None},
                },
                "info": token,  # Original token data from GMX
            }

        self.markets_loaded = True
        return self.markets

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
        :return: List of OHLCV candles, each as [timestamp_ms, open, high, low, close, volume]
        :rtype: list[list]
        :raises ValueError: If invalid symbol or timeframe

        .. note::
            Volume is always 0 as GMX API doesn't provide volume data

        Example::

            # Fetch last 100 hourly candles for ETH
            candles = exchange.fetch_ohlcv("ETH/USD", "1h", limit=100)

            # Fetch candles since specific time
            since = int(time.time() * 1000) - 86400000
            candles = exchange.fetch_ohlcv("ETH/USD", "1h", since=since)

            # Each candle: [timestamp, open, high, low, close, volume]
            for candle in candles:
                timestamp, o, h, l, c, v = candle
                print(f"{timestamp}: O:{o} H:{h} L:{l} C:{c} V:{v}")
        """
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
        timeframe: str = "1m",
        since: Optional[int] = None,
        limit: Optional[int] = None,
        use_tail: bool = True,
    ) -> list[list]:
        """Parse multiple OHLCV candles from GMX format to CCXT format.

        Converts GMX candlestick data (5 fields) to CCXT format (6 fields with volume).
        Applies filtering based on 'since' timestamp and 'limit' parameters.

        :param ohlcvs: List of raw OHLCV data from GMX API
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
        :return: List of parsed OHLCV candles in CCXT format
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

    def parse_ohlcv(
        self,
        ohlcv: list,
        market: Optional[dict[str, Any]] = None,
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

            seconds = exchange.parse_timeframe("1h")  # Returns 3600
            seconds = exchange.parse_timeframe("1d")  # Returns 86400
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

            now = exchange.milliseconds()
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

        :param dictionary: Dictionary to extract from
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

        :param dictionary: Dictionary to extract from
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
        :param keys: List of keys to exclude
        :type keys: list[str]
        :return: New dictionary without the specified keys
        :rtype: dict[str, Any]
        """
        return {k: v for k, v in dictionary.items() if k not in keys}
