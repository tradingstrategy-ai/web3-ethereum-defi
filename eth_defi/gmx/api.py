"""
GMX API Module

This module provides functionality for interacting with GMX APIs.
"""

import logging
import time
from typing import Any, Optional

import pandas as pd

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.constants import (
    GMX_API_URLS,
    GMX_API_URLS_BACKUP,
    _APY_CACHE_TTL_SECONDS,
    _MARKETS_CACHE_TTL_SECONDS,
    _MARKETS_INFO_CACHE_TTL_SECONDS,
    _TICKER_CACHE_TTL_SECONDS,
)
from eth_defi.gmx.retry import GMXRetryConfig, make_gmx_api_request

logger = logging.getLogger(__name__)

# Module-level cache for ticker prices with timestamps
# Key: chain name, Value: (tickers list, timestamp)
_TICKER_PRICES_CACHE: dict[str, tuple[list, float]] = {}

# Module-level cache for markets list
_MARKETS_CACHE: dict[str, tuple[dict, float]] = {}

# Module-level cache for markets info
_MARKETS_INFO_CACHE: dict[str, tuple[dict, float]] = {}

# Module-level cache for APY data (includes period in key)
_APY_CACHE: dict[str, tuple[dict, float]] = {}


def clear_ticker_prices_cache() -> None:
    """Clear the ticker prices cache.

    This function clears the module-level cache that stores ticker price data.
    Useful for testing or when fresh data is explicitly required.
    """
    global _TICKER_PRICES_CACHE
    _TICKER_PRICES_CACHE.clear()
    logger.debug("Ticker prices cache cleared")


def clear_markets_cache() -> None:
    """Clear the markets cache.

    This function clears the module-level cache that stores markets list data.
    Useful for testing or when fresh data is explicitly required.
    """
    global _MARKETS_CACHE
    _MARKETS_CACHE.clear()
    logger.debug("Markets cache cleared")


def clear_markets_info_cache() -> None:
    """Clear the markets info cache.

    This function clears the module-level cache that stores detailed markets info data.
    Useful for testing or when fresh data is explicitly required.
    """
    global _MARKETS_INFO_CACHE
    _MARKETS_INFO_CACHE.clear()
    logger.debug("Markets info cache cleared")


def clear_apy_cache() -> None:
    """Clear the APY cache.

    This function clears the module-level cache that stores APY data.
    Useful for testing or when fresh data is explicitly required.
    """
    global _APY_CACHE
    _APY_CACHE.clear()
    logger.debug("APY cache cleared")


class GMXAPI:
    """
    API interaction functionality for GMX protocol.

    This class provides a unified interface to interact with GMX protocol APIs,
    supporting both Arbitrum and Avalanche networks. It handles automatic failover
    to backup URLs and provides both raw dictionary responses and pandas DataFrame
    conversions for price data.

    Example:

    .. code-block:: python

        # Initialize GMX API client
        config = GMXConfig(chain="arbitrum")
        gmx_api = GMXAPI(config)

        # Get current token prices
        tickers = gmx_api.get_tickers()

        # Get historical candlestick data as DataFrame
        df = gmx_api.get_candlesticks_dataframe("ETH", period="1h")
    """

    def __init__(
        self,
        config: Optional[GMXConfig] = None,
        chain: Optional[str] = None,
        retry_config: GMXRetryConfig | None = None,
    ):
        """
        Initialise the GMX API client with the provided configuration.

        :param config:
            GMX configuration object containing chain and other settings (optional if chain is provided)
        :type config: Optional[GMXConfig]
        :param chain:
            Chain name (arbitrum or avalanche) as an alternative to config (optional if config is provided)
        :type chain: Optional[str]
        :param retry_config:
            Retry behaviour for API requests.
            Defaults to production settings when ``None``.
        :raises ValueError: If neither config nor chain is provided
        """
        if config is not None:
            self.config = config
            self.chain = config.get_chain()
        elif chain is not None:
            self.config = None
            self.chain = chain
        else:
            raise ValueError("Either config or chain must be provided")

        self.retry_config = retry_config

        # Validate chain is supported
        supported_chains = ["arbitrum", "arbitrum_sepolia", "avalanche", "avalanche_fuji"]
        if self.chain.lower() not in supported_chains:
            raise ValueError(f"Unsupported chain: {self.chain}. Supported: {', '.join(supported_chains)}")

    @property
    def base_url(self) -> str:
        """
        Get the primary API URL for the configured chain.

        :return: Primary GMX API URL for the current chain
        :rtype: str
        """
        return GMX_API_URLS.get(self.chain.lower(), "")

    @property
    def backup_url(self) -> str:
        """
        Get the backup API URL for the configured chain.

        :return: Backup GMX API URL for the current chain
        :rtype: str
        """
        return GMX_API_URLS_BACKUP.get(self.chain.lower(), "")

    def _make_request(
        self,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """
        Make a request to the GMX API with retry logic and automatic failover to backup URL.

        This method uses the centralised retry logic from eth_defi.gmx.retry module.

        :param endpoint: API endpoint path (e.g., "/prices/tickers", "/signed_prices/latest")
        :param params: Optional query parameters
        :param timeout: HTTP request timeout in seconds
        :return: API response parsed as a dictionary
        :raises RuntimeError: When all retry and backup attempts fail
        """
        return make_gmx_api_request(
            chain=self.chain,
            endpoint=endpoint,
            params=params,
            timeout=timeout,
            retry_config=self.retry_config,
        )

    def get_tickers(self, use_cache: bool = True) -> dict[str, Any]:
        """
        Get current price information for all supported tokens.

        This endpoint provides real-time pricing data for all tokens supported
        by the GMX protocol on the configured network. Results are cached for
        10 seconds to reduce API calls.

        :param use_cache:
            Whether to use cached data if available (default True)
        :type use_cache: bool
        :return:
            Dictionary containing current price information for all tokens,
            typically including bid/ask prices, last price, and volume data
        :rtype: dict[str, Any]
        """
        # Check cache if enabled
        if use_cache and self.chain in _TICKER_PRICES_CACHE:
            cached_tickers, cached_time = _TICKER_PRICES_CACHE[self.chain]
            age = time.time() - cached_time

            if age < _TICKER_CACHE_TTL_SECONDS:
                logger.debug(
                    "Using cached ticker prices for %s (age: %.1fs)",
                    self.chain,
                    age,
                )
                return cached_tickers

        # Fetch fresh data
        response = self._make_request("/prices/tickers")

        # Cache the response
        if use_cache:
            _TICKER_PRICES_CACHE[self.chain] = (response, time.time())
            logger.debug("Cached ticker prices for %s", self.chain)

        # Log a small summary to help debugging without dumping full payloads
        sample = None
        if isinstance(response, list) and response:
            sample = {
                "token": response[3].get("tokenSymbol") or response[3].get("tokenAddress"),
                "maxPrice": response[3].get("maxPrice"),
                "minPrice": response[3].get("minPrice"),
            }

        return response

    def get_signed_prices(self) -> dict[str, Any]:
        """
        Get cryptographically signed prices for use in on-chain transactions.

        These signed prices are required for certain GMX protocol interactions
        that need price verification on-chain. The signatures ensure price
        authenticity and prevent manipulation.

        :return:
            Dictionary containing signed price data that can be submitted
            to smart contracts for price verification
        :rtype: dict[str, Any]
        """
        return self._make_request("/signed_prices/latest")

    def get_tokens(self) -> dict[str, Any]:
        """
        Get comprehensive information about all supported tokens.

        This endpoint provides detailed metadata about each token supported
        by the GMX protocol, including contract addresses, decimals, and
        other relevant token properties.

        :return:
            Dictionary containing detailed information about all supported tokens,
            including addresses, symbols, decimals, and other metadata
        :rtype: dict[str, Any]
        """
        return self._make_request("/tokens")

    def get_candlesticks(
        self,
        token_symbol: str,
        period: str = "1h",
        limit: int = 10000,
    ) -> dict[str, Any]:
        """
        Get historical price data in candlestick format for a specific token.

        This method retrieves OHLCV (Open, High, Low, Close, Volume) data
        for the specified token and time period.

        :param token_symbol:
            Symbol of the token to retrieve data for (e.g., "ETH", "BTC")
        :type token_symbol: str
        :param period:
            Time period for each candlestick. Supported values are:
            '1m', '5m', '15m', '1h', '4h', '1d'. Default is '1h'
        :type period: str
        :param limit:
            Maximum number of candles to retrieve. Default is 10000
        :type limit: int
        :return:
            Dictionary containing candlestick data with timestamps and OHLCV values
        :rtype: dict[str, Any]
        """
        params = {"tokenSymbol": token_symbol, "period": period, "limit": limit}
        return self._make_request("/prices/candles", params=params)

    def get_candlesticks_dataframe(
        self,
        token_symbol: str,
        period: str = "1h",
        limit: int = 10000,
    ) -> pd.DataFrame:
        """
        Get historical price data as a pandas DataFrame for easy analysis.

        This is a convenience method that fetches candlestick data and converts
        it into a pandas DataFrame with properly formatted timestamps and
        standardized column names.

        Example:

        .. code-block:: python

            # Get hourly ETH price data
            df = gmx_api.get_candlesticks_dataframe("ETH", period="1h")

            # DataFrame will have columns: timestamp, open, high, low, close
            print(df.head())

            # Calculate simple moving average
            df["sma_20"] = df["close"].rolling(window=20).mean()

        :param token_symbol:
            Symbol of the token to retrieve data for (e.g., "ETH", "BTC")
        :type token_symbol: str
        :param period:
            Time period for each candlestick. Supported values are:
            '1m', '5m', '15m', '1h', '4h', '1d'. Default is '1h'
        :type period: str
        :param limit:
            Maximum number of candles to retrieve. Default is 10000
        :type limit: int
        :return:
            pandas DataFrame with columns: timestamp (datetime), open (float),
            high (float), low (float), close (float)
        :rtype: pd.DataFrame
        """
        data = self.get_candlesticks(token_symbol, period, limit)

        # Convert to DataFrame
        df = pd.DataFrame(
            data["candles"],
            columns=["timestamp", "open", "high", "low", "close"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")

        return df

    def get_markets(self, use_cache: bool = True) -> dict[str, Any]:
        """Get list of all GMX trading markets.

        Fetches market metadata from /markets endpoint including market tokens,
        index tokens, collateral tokens, and listing status/dates.

        Uses existing retry logic with automatic backup failover via
        `make_gmx_api_request` from eth_defi.gmx.retry module.

        :param use_cache:
            Whether to use module-level cache (10 minute TTL)
        :return:
            Dictionary with 'markets' key containing list of market objects
        :raises RuntimeError: If all API endpoints fail after retries
        """
        # Check cache
        if use_cache:
            cache_key = f"markets_{self.chain}"
            if cache_key in _MARKETS_CACHE:
                cached_data, cached_time = _MARKETS_CACHE[cache_key]
                age = time.time() - cached_time
                if age < _MARKETS_CACHE_TTL_SECONDS:
                    logger.debug(
                        "Using cached markets data for %s (age: %.1fs)",
                        self.chain,
                        age,
                    )
                    return cached_data

        # Fetch from API with retry and backup failover
        endpoint = "/markets"
        data = make_gmx_api_request(
            chain=self.chain,
            endpoint=endpoint,
            params=None,
            timeout=10.0,
            retry_config=self.retry_config,
        )

        # Cache result
        if use_cache:
            cache_key = f"markets_{self.chain}"
            _MARKETS_CACHE[cache_key] = (data, time.time())
            logger.debug("Cached markets data for %s", self.chain)

        return data

    def get_markets_info(
        self,
        market_tokens_data: bool = True,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Get comprehensive market information including liquidity and rates.

        Fetches detailed market data from /markets/info endpoint including:
        - Open interest (long/short)
        - Available liquidity
        - Pool amounts
        - Funding rates (long/short)
        - Borrowing rates (long/short)
        - Net rates (long/short)
        - isListed status (critical for filtering)

        Uses existing retry logic with automatic backup failover via
        `make_gmx_api_request` from eth_defi.gmx.retry module.

        :param market_tokens_data:
            Include detailed market token data in response
        :param use_cache:
            Whether to use module-level cache (60 second TTL)
        :return:
            Dictionary with 'markets' key containing detailed market objects
        :raises RuntimeError: If all API endpoints fail after retries
        """
        # Check cache
        if use_cache:
            cache_key = f"markets_info_{self.chain}"
            if cache_key in _MARKETS_INFO_CACHE:
                cached_data, cached_time = _MARKETS_INFO_CACHE[cache_key]
                age = time.time() - cached_time
                if age < _MARKETS_INFO_CACHE_TTL_SECONDS:
                    logger.debug(
                        "Using cached markets info for %s (age: %.1fs)",
                        self.chain,
                        age,
                    )
                    return cached_data

        # Fetch from API with retry and backup failover
        endpoint = "/markets/info"
        params = {}
        if market_tokens_data:
            params["marketTokensData"] = "true"

        data = make_gmx_api_request(
            chain=self.chain,
            endpoint=endpoint,
            params=params,
            timeout=10.0,
            retry_config=self.retry_config,
        )

        # Cache result
        if use_cache:
            cache_key = f"markets_info_{self.chain}"
            _MARKETS_INFO_CACHE[cache_key] = (data, time.time())
            logger.debug("Cached markets info for %s", self.chain)

        return data

    def get_apy(
        self,
        period: str = "30d",
        use_cache: bool = True,
    ) -> dict[str, Any]:
        """Get APY data for GMX and GLV positions by market.

        Fetches annualised yield data from /apy endpoint for different time periods.
        Returns APY broken down by market token address including base APY and bonus APR.

        Uses existing retry logic with automatic backup failover via
        `make_gmx_api_request` from eth_defi.gmx.retry module.

        :param period:
            Time period for APY calculation. Valid values:
            '1d', '7d', '30d', '90d', '180d', '1y', 'total'
            Default: '30d'
        :param use_cache:
            Whether to use module-level cache (300 second TTL)
        :return:
            Dictionary with 'markets' key mapping market token addresses to APY data:
            {'markets': {'0x...': {'apy': 0.215, 'baseApy': 0.215, 'bonusApr': 0}}}
        :raises ValueError: If period is invalid
        :raises RuntimeError: If all API endpoints fail after retries

        Example:

        .. code-block:: python

            api = GMXAPI(config)
            apy_data = api.get_apy(period="30d")

            # Get APY for specific market
            market_token = "0xd62068697bCc92AF253225676D618B0C9f17C663"
            if market_token in apy_data.get("markets", {}):
                market_apy = apy_data["markets"][market_token]["apy"]
                print(f"30-day APY: {market_apy * 100:.2f}%")
        """
        # Validate period
        valid_periods = ["1d", "7d", "30d", "90d", "180d", "1y", "total"]
        if period not in valid_periods:
            raise ValueError(f"Invalid period '{period}'. Must be one of {valid_periods}")

        # Check cache (cache key includes period)
        if use_cache:
            cache_key = f"apy_{period}_{self.chain}"
            if cache_key in _APY_CACHE:
                cached_data, cached_time = _APY_CACHE[cache_key]
                age = time.time() - cached_time
                if age < _APY_CACHE_TTL_SECONDS:
                    logger.debug(
                        "Using cached APY data for %s period %s (age: %.1fs)",
                        self.chain,
                        period,
                        age,
                    )
                    return cached_data

        # Fetch from API with retry and backup failover
        endpoint = "/apy"
        params = {"period": period}

        data = make_gmx_api_request(
            chain=self.chain,
            endpoint=endpoint,
            params=params,
            timeout=10.0,
            retry_config=self.retry_config,
        )

        # Cache result
        if use_cache:
            cache_key = f"apy_{period}_{self.chain}"
            _APY_CACHE[cache_key] = (data, time.time())
            logger.debug("Cached APY data for %s period %s", self.chain, period)

        return data
