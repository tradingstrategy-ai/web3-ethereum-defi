"""
GMX API Module

This module provides functionality for interacting with GMX APIs.
"""

import logging
import time
from typing import Any, Optional

import pandas as pd
import requests
from eth_typing import HexAddress

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.market_depth import MarketDepthInfo, parse_market_depth
from eth_defi.gmx.constants import (
    GMX_API_URLS,
    GMX_API_URLS_BACKUP,
    GMX_API_V2_URLS,
    GMX_SUPPORTED_CHAINS,
    _APY_CACHE_TTL_SECONDS,
    _MARKETS_CACHE_TTL_SECONDS,
    _MARKETS_INFO_CACHE_TTL_SECONDS,
    _PAIRS_CACHE_TTL_SECONDS,
    _POSITIONS_CACHE_TTL_SECONDS,
    _RATES_CACHE_TTL_SECONDS,
    _TICKER_CACHE_TTL_SECONDS,
    _TOKEN_INFO_CACHE_TTL_SECONDS,
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

# Module-level cache for v2 positions data
_POSITIONS_CACHE: dict[str, tuple[Any, float]] = {}

# Module-level cache for v2 rates data (includes period in key)
_RATES_CACHE: dict[str, tuple[Any, float]] = {}

# Module-level cache for v2 pairs data
_PAIRS_CACHE: dict[str, tuple[Any, float]] = {}

# Module-level cache for v2 token info data
_TOKEN_INFO_CACHE: dict[str, tuple[Any, float]] = {}


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
        if self.chain.lower() not in GMX_SUPPORTED_CHAINS:
            raise ValueError(f"Unsupported chain: {self.chain}. Supported: {', '.join(GMX_SUPPORTED_CHAINS)}")

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

    @property
    def base_v2_url(self) -> str:
        """Get the REST API v2 URL for the configured chain.

        :return:
            GMX REST API v2 base URL for the current chain, or empty string
            if no v2 endpoint is configured for this chain.
        """
        return GMX_API_V2_URLS.get(self.chain.lower(), "")

    def _make_v2_request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> Any:
        """Make a request to the GMX REST API v2 with basic retry logic.

        The v2 API uses different base URLs hosted on DigitalOcean and does
        not have the multi-tier failover of v1.  This method retries up to
        3 times with exponential backoff on transient failures.

        :param endpoint:
            API endpoint path (e.g. ``"/positions"``, ``"/orders"``).
        :param params:
            Optional query parameters dict.
        :param timeout:
            HTTP request timeout in seconds.
        :return:
            API response parsed as a dict or list.
        :raises ValueError:
            When no v2 API URL is configured for the current chain.
        :raises RuntimeError:
            When all retry attempts fail.
        """
        base = self.base_v2_url
        if not base:
            raise ValueError(f"No GMX v2 API URL configured for chain: {self.chain!r}")

        url = f"{base}{endpoint}"
        last_error: Exception | None = None
        delay = 1.0

        for attempt in range(3):
            try:
                response = requests.get(url, params=params, timeout=timeout)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 2:
                    logger.warning(
                        "GMX v2 API attempt %d/3 failed for %s: %s — retrying in %.1fs",
                        attempt + 1,
                        endpoint,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    delay = min(delay * 2.0, 10.0)

        raise RuntimeError(f"GMX v2 API request failed after 3 attempts: {endpoint}") from last_error

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

    def get_market_depth(
        self,
        market_symbol: str | None = None,
        use_cache: bool = True,
    ) -> list[MarketDepthInfo]:
        """Get market depth information for all listed GMX markets.

        Parses the ``/markets/info`` REST response into structured
        :class:`~eth_defi.gmx.market_depth.MarketDepthInfo` dataclasses,
        giving a real-time view of:

        - Current long / short open interest (USD)
        - Remaining pool capacity before the reserve cap is hit
        - Funding and borrowing rates

        The response is cached for 60 seconds by default (``use_cache=True``),
        so calling this method repeatedly in a tight loop is safe.

        Example:

        .. code-block:: python

            api = GMXAPI(chain="arbitrum")
            eth_markets = api.get_market_depth(market_symbol="ETH")

            for m in eth_markets:
                print(f"{m.market_symbol}: longOI=${m.long_open_interest_usd:,.0f}")
                print(f"  Available long cap: ${m.available_long_oi_usd:,.0f}")
                print(f"  Available short cap: ${m.available_short_oi_usd:,.0f}")

        :param market_symbol:
            Optional case-insensitive substring filter applied to the market
            name.  For example ``"ETH"`` matches
            ``"ETH/USD [WETH-USDC]"`` but not ``"BTC/USD"``.
            If ``None``, all listed markets are returned.
        :param use_cache:
            Whether to use the module-level 60-second cache for
            ``/markets/info``.  Set to ``False`` to force a fresh API call.
        :return:
            List of :class:`~eth_defi.gmx.market_depth.MarketDepthInfo` for
            all active (``isListed=True``) markets, optionally filtered by
            *market_symbol*.
        :raises RuntimeError: If all API endpoints fail after retries
        """
        raw = self.get_markets_info(use_cache=use_cache)
        markets_list = raw.get("markets", [])

        result: list[MarketDepthInfo] = []
        for market_data in markets_list:
            info = parse_market_depth(market_data)
            if not info.is_listed:
                continue
            if market_symbol is not None and market_symbol.lower() not in info.market_symbol.lower():
                continue
            result.append(info)

        logger.debug(
            "get_market_depth: returned %d markets (filter=%r, chain=%s)",
            len(result),
            market_symbol,
            self.chain,
        )
        return result

    # ------------------------------------------------------------------
    # REST API v2 methods
    # ------------------------------------------------------------------

    def get_positions(
        self,
        address: HexAddress,
        include_related_orders: bool = False,
        use_cache: bool = True,
    ) -> Any:
        """Fetch open positions for a wallet address via the v2 API.

        Returns position data including unrealised PnL, fees, leverage,
        collateral amount, and liquidation price.  This endpoint is only
        available on the v2 REST API and is not present in v1.

        Example::

            api = GMXAPI(chain="arbitrum")
            positions = api.get_positions("0xAbC...")
            for pos in positions:
                print(pos["market"], pos["leverage"])

        :param address:
            Wallet address to query positions for (checksummed hex).
        :param include_related_orders:
            Include related SL/TP orders attached to each position.
        :param use_cache:
            Use the module-level 15-second cache.
        :return:
            Parsed API response — typically a list of position objects.
        :raises ValueError:
            When no v2 API URL is configured for the current chain.
        :raises RuntimeError:
            When all retry attempts fail.
        """
        cache_key = f"positions_{self.chain}_{address}"
        if use_cache and cache_key in _POSITIONS_CACHE:
            cached_data, cached_time = _POSITIONS_CACHE[cache_key]
            if time.time() - cached_time < _POSITIONS_CACHE_TTL_SECONDS:
                return cached_data

        params: dict[str, Any] = {"address": address}
        if include_related_orders:
            params["includeRelatedOrders"] = "true"

        data = self._make_v2_request("/positions", params=params)

        if use_cache:
            _POSITIONS_CACHE[cache_key] = (data, time.time())

        return data

    def get_orders(
        self,
        address: HexAddress,
    ) -> Any:
        """Fetch open orders for a wallet address via the v2 API.

        Returns all pending/open orders including limit orders, stop-loss,
        and take-profit orders.  Not available in v1.

        Example::

            api = GMXAPI(chain="arbitrum")
            orders = api.get_orders("0xAbC...")

        :param address:
            Wallet address to query orders for (checksummed hex).
        :return:
            Parsed API response — typically a list of order objects.
        :raises ValueError:
            When no v2 API URL is configured for the current chain.
        :raises RuntimeError:
            When all retry attempts fail.
        """
        return self._make_v2_request("/orders", params={"address": address})

    def get_rates(
        self,
        address: str | None = None,
        use_cache: bool = True,
    ) -> Any:
        """Fetch funding and borrowing rate snapshots via the v2 API.

        Returns the latest rate snapshots for all markets (or a single market
        when ``address`` is supplied).  Not available in v1.

        .. note::
            The ``/rates`` endpoint does not accept a ``period`` or
            ``average_by`` parameter — passing them results in a 400 error.

        Example::

            api = GMXAPI(chain="arbitrum")
            rates = api.get_rates()

        :param address:
            Optional market address to filter rates for a single market.
        :param use_cache:
            Use the module-level 60-second cache.
        :return:
            Parsed API response containing rate snapshots per market.
        :raises ValueError:
            When no v2 API URL is configured for the current chain.
        :raises RuntimeError:
            When all retry attempts fail.
        """
        cache_key = f"rates_{self.chain}_{address}"
        if use_cache and cache_key in _RATES_CACHE:
            cached_data, cached_time = _RATES_CACHE[cache_key]
            if time.time() - cached_time < _RATES_CACHE_TTL_SECONDS:
                return cached_data

        params: dict[str, Any] = {}
        if address is not None:
            params["address"] = address

        data = self._make_v2_request("/rates", params=params)

        if use_cache:
            _RATES_CACHE[cache_key] = (data, time.time())

        return data

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
        since: int | None = None,
    ) -> Any:
        """Fetch OHLCV candle data via the v2 API.

        Similar to :meth:`get_candlesticks` but uses the v2 endpoint which
        supports a ``since`` parameter (Unix milliseconds) for flexible
        historical data retrieval and pagination.

        Example::

            api = GMXAPI(chain="arbitrum")
            candles = api.get_ohlcv("ETH", timeframe="4h", limit=500)

            # With since parameter for historical data
            import time

            one_week_ago_ms = int((time.time() - 7 * 86400) * 1000)
            candles = api.get_ohlcv("BTC", timeframe="1h", since=one_week_ago_ms)

        :param symbol:
            Trading symbol (e.g. ``"ETH"``, ``"BTC"``).
        :param timeframe:
            Candle timeframe: ``"1m"``, ``"5m"``, ``"15m"``, ``"1h"``,
            ``"4h"``, ``"1d"``.  Default ``"1h"``.
        :param limit:
            Maximum number of candles to return.  Default ``100``.
        :param since:
            Unix timestamp in **milliseconds** for the earliest candle.
            ``None`` returns the most recent candles.
        :return:
            Parsed API response containing OHLCV candle data.
        :raises ValueError:
            When no v2 API URL is configured for the current chain.
        :raises RuntimeError:
            When all retry attempts fail.
        """
        params: dict[str, Any] = {
            "symbol": symbol,
            "timeframe": timeframe,
            "limit": limit,
        }
        if since is not None:
            params["since"] = since

        return self._make_v2_request("/prices/ohlcv", params=params)

    def get_token_info(
        self,
        use_cache: bool = True,
    ) -> Any:
        """Fetch comprehensive token information via the v2 API.

        Returns token metadata including current pricing and supply data.
        Richer than the v1 :meth:`get_tokens` endpoint.

        Example::

            api = GMXAPI(chain="arbitrum")
            tokens = api.get_token_info()

        :param use_cache:
            Use the module-level 60-second cache.
        :return:
            Parsed API response containing token info objects.
        :raises ValueError:
            When no v2 API URL is configured for the current chain.
        :raises RuntimeError:
            When all retry attempts fail.
        """
        cache_key = f"token_info_{self.chain}"
        if use_cache and cache_key in _TOKEN_INFO_CACHE:
            cached_data, cached_time = _TOKEN_INFO_CACHE[cache_key]
            if time.time() - cached_time < _TOKEN_INFO_CACHE_TTL_SECONDS:
                return cached_data

        data = self._make_v2_request("/tokens/info")

        if use_cache:
            _TOKEN_INFO_CACHE[cache_key] = (data, time.time())

        return data

    def get_pairs(
        self,
        use_cache: bool = True,
    ) -> Any:
        """Fetch all trading pairs via the v2 API.

        Returns the complete list of trading pairs available on the GMX
        protocol for the configured chain.  Not available in v1.

        Example::

            api = GMXAPI(chain="arbitrum")
            pairs = api.get_pairs()

        :param use_cache:
            Use the module-level 10-minute cache.
        :return:
            Parsed API response containing trading pair objects.
        :raises ValueError:
            When no v2 API URL is configured for the current chain.
        :raises RuntimeError:
            When all retry attempts fail.
        """
        cache_key = f"pairs_{self.chain}"
        if use_cache and cache_key in _PAIRS_CACHE:
            cached_data, cached_time = _PAIRS_CACHE[cache_key]
            if time.time() - cached_time < _PAIRS_CACHE_TTL_SECONDS:
                return cached_data

        data = self._make_v2_request("/pairs")

        if use_cache:
            _PAIRS_CACHE[cache_key] = (data, time.time())

        return data
