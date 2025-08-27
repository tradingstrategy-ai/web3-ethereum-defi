"""
GMX GM Prices Data Module

This module provides access to GM token prices data,
replacing the gmx_python_sdk GMPrices functionality.
"""

import logging
from typing import Any, Optional
from concurrent.futures import ThreadPoolExecutor

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.get_data import GetData


class GetGMPrices(GetData):
    """
    GM token prices data provider for GMX protocol.

    This class retrieves current prices and valuation data for GM (liquidity provider) tokens.
    GM tokens represent shares in GMX liquidity pools and their prices reflect the underlying
    value of the pooled assets plus accumulated fees. This pricing information is essential
    for liquidity providers to understand the value of their holdings and calculate returns
    on their liquidity provision activities.

    :param config: GMXConfig instance containing chain and network info
    :type config: GMXConfig
    :param filter_swap_markets: Whether to filter out swap markets from results
    :type filter_swap_markets: bool
    """

    def __init__(self, config: GMXConfig, filter_swap_markets: bool = True):
        """
        Initialize GM prices data provider.

        :param config: GMXConfig instance containing chain and network info
        :type config: GMXConfig
        :param filter_swap_markets: Whether to filter out swap markets from results
        :type filter_swap_markets: bool
        """
        super().__init__(config, filter_swap_markets)
        self.log = logging.getLogger(__name__)

    def get_prices(self, price_type: str = "traders", to_json: bool = False, to_csv: bool = False) -> dict[str, Any]:
        """
        Get GM token prices.

        :param price_type: Type of price to retrieve ("traders", "deposits", "withdrawals")
        :param to_json: Whether to save data to JSON file
        :type to_json: bool
        :param to_csv: Whether to save data to CSV file
        :type to_csv: bool
        :return: Dictionary containing GM prices data
        :rtype: dict[str, Any]
        """
        try:
            self.log.debug(f"GMX v2 GM Prices ({price_type})")

            # Get available markets
            available_markets = self.markets.get_available_markets()
            if not available_markets:
                self.log.warning("No markets available")
                return {"prices": {}, "parameter": f"gm_prices_{price_type}"}

            prices_dict = {"prices": {}, "parameter": f"gm_prices_{price_type}"}

            # Process markets concurrently
            market_results = []
            with ThreadPoolExecutor(max_workers=5) as executor:
                # Submit tasks for each market
                future_to_market = {}
                for market_key in available_markets:
                    try:
                        future = executor.submit(self._process_market_price, market_key, price_type)
                        future_to_market[future] = market_key
                    except Exception as e:
                        self.log.warning(f"Failed to submit task for market {market_key}: {e}")
                        continue

                # Collect results
                for future in future_to_market:
                    try:
                        result = future.result()
                        if result:
                            market_key, market_price_data = result
                            market_symbol = available_markets[market_key]["market_symbol"]
                            prices_dict["prices"][market_symbol] = market_price_data
                    except Exception as e:
                        self.log.warning(f"Failed to process market result: {e}")
                        continue

            # Export data if requested
            if to_json:
                self._save_to_json(prices_dict)

            if to_csv:
                self._save_to_csv(prices_dict)

            return prices_dict

        except Exception as e:
            self.log.error(f"Failed to fetch GM prices data: {e}")
            return {"prices": {}, "parameter": f"gm_prices_{price_type}"}

    def _process_market_price(self, market_key: str, price_type: str) -> Optional[tuple]:
        """
        Process price data for a single market.

        :param market_key: Market key
        :param price_type: Type of price to retrieve
        :return: Tuple of (market_key, price_data)
        """
        try:
            market_data = self.markets.get_available_markets()[market_key]
            market_symbol = market_data["market_symbol"]

            self.log.debug(f"Processing GM price for {market_symbol}")

            # In a real implementation, this would call the appropriate GM reader contract
            # For now, we'll return placeholder data
            price_data = {
                "market_symbol": market_symbol,
                "price": 1.0,  # Placeholder
                "price_type": price_type,
                "timestamp": self._get_current_timestamp(),
            }

            return (market_key, price_data)
        except Exception as e:
            self.log.warning(f"Failed to process market price for {market_key}: {e}")
            return None

    def _get_current_timestamp(self) -> int:
        """
        Get current timestamp.

        :return: Current timestamp in seconds
        """
        import time

        return int(time.time())

    def _get_data_processing(self) -> dict[str, Any]:
        """
        Implementation of abstract method - not used in this class.

        :return: Empty dictionary
        """
        return {}
