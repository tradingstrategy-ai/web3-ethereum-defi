"""
GMX API Module

This module provides functionality for interacting with GMX APIs.
"""

import logging
from typing import Optional, Any
import pandas as pd

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.constants import GMX_API_URLS, GMX_API_URLS_BACKUP
from eth_defi.gmx.retry import make_gmx_api_request

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
        max_retries: int = 2,
        retry_delay: float = 0.1,
    ) -> dict[str, Any]:
        """
        Make a request to the GMX API with retry logic and automatic failover to backup URL.

        This method uses the centralized retry logic from eth_defi.gmx.retry module.

        :param endpoint: API endpoint path (e.g., "/prices/tickers", "/signed_prices/latest")
        :param params: Optional query parameters
        :param timeout: HTTP request timeout in seconds
        :param max_retries: Maximum retry attempts per URL
        :param retry_delay: Initial delay between retries (exponential backoff)
        :return: API response parsed as a dictionary
        :raises RuntimeError: When all retry and backup attempts fail
        """
        return make_gmx_api_request(
            chain=self.chain,
            endpoint=endpoint,
            params=params,
            timeout=timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )

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
        response = self._make_request("/prices/tickers")

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
