"""
GMX API Module

This module provides functionality for interacting with GMX APIs.
"""

from typing import Optional, Any
import logging
import time
import requests
import pandas as pd

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.constants import GMX_API_URLS, GMX_API_URLS_BACKUP

logger = logging.getLogger(__name__)


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
    ):
        """
        Initialise the GMX API client with the provided configuration.

        :param config:
            GMX configuration object containing chain and other settings (optional if chain is provided)
        :type config: Optional[GMXConfig]
        :param chain:
            Chain name (arbitrum or avalanche) as an alternative to config (optional if config is provided)
        :type chain: Optional[str]
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

        # Set base URLs based on the chain
        # Handle mainnet and testnet chains by mapping to appropriate API
        if self.chain.lower() == "arbitrum":
            self.base_url = GMX_API_URLS["arbitrum"]
            self.backup_url = GMX_API_URLS_BACKUP["arbitrum"]
        elif self.chain.lower() in ["avalanche", "avalanche_fuji"]:
            self.base_url = GMX_API_URLS["avalanche"]
            self.backup_url = GMX_API_URLS_BACKUP["avalanche"]
        elif self.chain.lower() == "arbitrum_sepolia":
            self.base_url = GMX_API_URLS["arbitrum_sepolia"]
        else:
            raise ValueError(f"Unsupported chain: {self.chain}. Supported: arbitrum, arbitrum_sepolia, avalanche")

    def _make_request(
        self,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
        timeout: float = 10.0,
        max_retries: int = 2,
        retry_delay: float = 0.1,
    ) -> dict[str, Any]:
        """
        Make a request to the GMX API with retry logic and automatic failover to backup URL.

        This method implements a robust retry strategy with exponential backoff. It first
        attempts to connect to the primary API URL with retries. If all primary attempts fail,
        it automatically fails over to the backup URL with the same retry logic. This ensures
        maximum reliability for API requests.

        :param endpoint:
            API endpoint path (e.g., "/prices/tickers", "/signed_prices/latest")
        :type endpoint: str
        :param params:
            Optional dictionary of query parameters to include in the request
        :type params: Optional[dict[str, Any]]
        :param timeout:
            HTTP request timeout in seconds (default: 10.0)
        :type timeout: float
        :param max_retries:
            Maximum number of retry attempts per endpoint (default: 2)
        :type max_retries: int
        :param retry_delay:
            Initial delay between retries in seconds with exponential backoff (default: 0.1s)
        :type retry_delay: float
        :return:
            API response parsed as a dictionary
        :rtype: dict[str, Any]
        :raises RuntimeError:
            When both primary and backup API URLs fail after all retry attempts
        """
        # Build list of URLs to try (primary first, then backup if available)
        urls_to_try = [(f"{self.base_url}{endpoint}", "primary")]
        if hasattr(self, "backup_url") and self.backup_url:
            urls_to_try.append((f"{self.backup_url}{endpoint}", "backup"))

        last_error = None

        # Try each URL with retries
        for url, url_type in urls_to_try:
            for attempt in range(max_retries):
                try:
                    response = requests.get(url, params=params, timeout=timeout)
                    response.raise_for_status()

                    if url_type == "backup" and attempt == 0:
                        logger.info("Successfully connected to %s GMX API for %s", url_type, endpoint)

                    return response.json()

                except requests.RequestException as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        # Exponential backoff: 0.1s, 0.2s
                        delay = retry_delay * (2**attempt)
                        logger.warning(
                            "Attempt %d/%d failed for %s API %s: %s. Retrying in %.1f seconds...",
                            attempt + 1,
                            max_retries,
                            url_type,
                            url,
                            e,
                            delay,
                        )
                        time.sleep(delay)
                    else:
                        logger.warning(
                            "All %d attempts failed for %s API %s: %s",
                            max_retries,
                            url_type,
                            url,
                            e,
                        )

        # If we get here, all URLs and retries failed
        error_msg = f"Failed to connect to GMX API endpoint {endpoint} after trying all available URLs"
        if last_error:
            error_msg += f". Last error: {last_error}"
        raise RuntimeError(error_msg) from last_error

    def get_tickers(self) -> dict[str, Any]:
        """
        Get current price information for all supported tokens.

        This endpoint provides real-time pricing data for all tokens supported
        by the GMX protocol on the configured network.

        :return:
            Dictionary containing current price information for all tokens,
            typically including bid/ask prices, last price, and volume data
        :rtype: dict[str, Any]
        """
        return self._make_request("/prices/tickers")

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
        :return:
            Dictionary containing candlestick data with timestamps and OHLCV values
        :rtype: dict[str, Any]
        """
        params = {"tokenSymbol": token_symbol, "period": period}
        return self._make_request("/prices/candles", params=params)

    def get_candlesticks_dataframe(
        self,
        token_symbol: str,
        period: str = "1h",
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
        :return:
            pandas DataFrame with columns: timestamp (datetime), open (float),
            high (float), low (float), close (float)
        :rtype: pd.DataFrame
        """
        data = self.get_candlesticks(token_symbol, period)

        # Convert to DataFrame
        df = pd.DataFrame(
            data["candles"],
            columns=["timestamp", "open", "high", "low", "close"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")

        return df
