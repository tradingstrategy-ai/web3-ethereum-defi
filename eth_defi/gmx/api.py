"""
GMX API Module

This module provides functionality for interacting with GMX APIs.
"""

from typing import Any, Optional
import requests
import pandas as pd

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.constants import GMX_API_URLS, GMX_API_URLS_BACKUP


class GMXAPI:
    """
    API interaction functionality for GMX protocol.
    """

    def __init__(self, config: GMXConfig):
        """
        Initialize API module.

        Args:
            config: GMX configuration object
        """
        self.config = config
        self.chain = config.get_chain()

        # Set base URLs based on chain
        if self.chain.lower() == "arbitrum":
            self.base_url = GMX_API_URLS["arbitrum"]
            self.backup_url = GMX_API_URLS_BACKUP["arbitrum"]
        else:
            self.base_url = GMX_API_URLS["avalanche"]
            self.backup_url = GMX_API_URLS_BACKUP["avalanche"]

    def _make_request(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """
        Make a request to the GMX API.

        Args:
            endpoint: API endpoint path
            params: Query parameters

        Returns:
            API response as dictionary
        """
        try:
            # Try primary URL
            url = f"{self.base_url}{endpoint}"
            response = requests.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            # Try backup URL on failure
            try:
                url = f"{self.backup_url}{endpoint}"
                response = requests.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as backup_e:
                raise RuntimeError(f"Failed to connect to GMX API: {str(backup_e)}") from e

    def get_tickers(self) -> dict[str, Any]:
        """
        Get current price information for all tokens.

        Returns:
            dictionary of token prices
        """
        return self._make_request("/prices/tickers")

    def get_signed_prices(self) -> dict[str, Any]:
        """
        Get signed prices for on-chain transactions.

        Returns:
            dictionary of signed prices
        """
        return self._make_request("/signed_prices/latest")

    def get_tokens(self) -> dict[str, Any]:
        """
        Get list of supported tokens.

        Returns:
            dictionary of token information
        """
        return self._make_request("/tokens")

    def get_candlesticks(self, token_symbol: str, period: str = "1h") -> dict[str, Any]:
        """
        Get historical price data.

        Args:
            token_symbol: Symbol of the token
            period: Time period ('1m', '5m', '15m', '1h', '4h', '1d')

        Returns:
            dictionary of candlestick data
        """
        params = {"tokenSymbol": token_symbol, "period": period}
        return self._make_request("/prices/candles", params=params)

    def get_candlesticks_dataframe(self, token_symbol: str, period: str = "1h") -> pd.DataFrame:
        """
        Get historical price data as pandas DataFrame.

        Args:
            token_symbol: Symbol of the token
            period: Time period ('1m', '5m', '15m', '1h', '4h', '1d')

        Returns:
            DataFrame of candlestick data
        """
        data = self.get_candlesticks(token_symbol, period)

        # Convert to DataFrame
        df = pd.DataFrame(data["candles"], columns=["timestamp", "open", "high", "low", "close"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")

        return df
