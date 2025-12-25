"""
GMX Open Interest Data Retrieval Module

This module provides open interest data for GMX protocol markets.
"""

import logging

logger = logging.getLogger(__name__)
from dataclasses import dataclass
from typing import Any

from eth_typing import HexAddress

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.get_data import GetData
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.types import MarketSymbol, USDAmount


# TODO: too slow
@dataclass(slots=True)
class OpenInterestInfo:
    """Open interest information for a specific GMX market."""

    #: GMX market contract address
    market_address: HexAddress
    #: Market symbol identifier
    market_symbol: MarketSymbol
    #: Long open interest in USD
    long_open_interest: USDAmount
    #: Short open interest in USD
    short_open_interest: USDAmount
    #: Total open interest in USD
    total_open_interest: USDAmount
    #: Address of the long token
    long_token_address: HexAddress
    #: Address of the short token
    short_token_address: HexAddress


class GetOpenInterest(GetData):
    """
    GMX open interest data retrieval class.

    This class retrieves open interest information for all available GMX markets,
    including position PnL calculations. It inherits from the GetData base class
    and provides specific implementation for open interest data processing.

    :param config: GMXConfig instance containing chain and network info
    :type config: GMXConfig
    :param filter_swap_markets: Whether to filter out swap markets from results
    :type filter_swap_markets: bool
    """

    def __init__(self, config: GMXConfig, filter_swap_markets: bool = True):
        """
        Initialize open interest data retrieval.

        :param config: GMXConfig instance containing chain and network info
        :type config: GMXConfig
        :param filter_swap_markets: Whether to filter out swap markets from results
        :type filter_swap_markets: bool
        """
        super().__init__(config, filter_swap_markets)

    def _get_data_processing(self) -> dict[str, Any]:
        """Generate the dictionary of open interest data.

        :returns: Dictionary of open interest data
        :rtype: dict
        """
        oracle_prices_dict = OraclePrices(self.config.chain).get_recent_prices()

        long_oi_output_list = []
        short_oi_output_list = []
        long_pnl_output_list = []
        short_pnl_output_list = []
        mapper = []
        long_precision_list = []

        available_markets = self.markets.get_available_markets()

        for market_key in available_markets:
            self._get_token_addresses(market_key)

            index_token_address = self.markets.get_index_token_address(market_key)

            # Skip markets with invalid index token addresses
            if index_token_address == "0x0000000000000000000000000000000000000000":
                logger.warning(f"Skipping market {market_key} with zero index token address")
                continue

            market = [
                market_key,
                index_token_address,
                self._long_token_address,
                self._short_token_address,
            ]

            min_price = int(oracle_prices_dict[index_token_address]["minPriceFull"])
            max_price = int(oracle_prices_dict[index_token_address]["maxPriceFull"])
            prices_list = [min_price, max_price]

            # If the market is a synthetic one we need to use the decimals
            # from the index token
            try:
                if self.markets.is_synthetic(market_key):
                    decimal_factor = self.markets.get_decimal_factor(
                        market_key,
                    )
                else:
                    decimal_factor = self.markets.get_decimal_factor(market_key, long=True)
            except KeyError:
                decimal_factor = self.markets.get_decimal_factor(market_key, long=True)

            oracle_factor = 30 - decimal_factor
            precision = 10 ** (decimal_factor + oracle_factor)
            long_precision_list = [*long_precision_list, precision]

            long_oi_with_pnl, long_pnl = self._get_pnl(market, prices_list, is_long=True)

            short_oi_with_pnl, short_pnl = self._get_pnl(market, prices_list, is_long=False)

            long_oi_output_list.append(long_oi_with_pnl)
            short_oi_output_list.append(short_oi_with_pnl)
            long_pnl_output_list.append(long_pnl)
            short_pnl_output_list.append(short_pnl)
            mapper.append(self.markets.get_market_symbol(market_key))

        # The values are already computed by _get_pnl, so we can use them directly
        long_oi_threaded_output = long_oi_output_list
        short_oi_threaded_output = short_oi_output_list
        long_pnl_threaded_output = long_pnl_output_list
        short_pnl_threaded_output = short_pnl_output_list

        for (
            market_symbol,
            long_oi,
            short_oi,
            long_pnl,
            short_pnl,
            long_precision,
        ) in zip(
            mapper,
            long_oi_threaded_output,
            short_oi_threaded_output,
            long_pnl_threaded_output,
            short_pnl_threaded_output,
            long_precision_list,
        ):
            precision = 10**30  # TODO: Why this value was used in the first place?
            long_value = (long_oi - long_pnl) / long_precision
            short_value = (short_oi - short_pnl) / precision

            logger.debug(f"{market_symbol} Long: ${self._format_number(long_value)}")
            logger.debug(f"{market_symbol} Short: ${self._format_number(short_value)}")

            self.output["long"][market_symbol] = long_value
            self.output["short"][market_symbol] = short_value

        self.output["parameter"] = "open_interest"

        return self.output

    @staticmethod
    def _format_number(value: float) -> str:
        """
        Format number for display using numerize-like formatting.

        :param value: Number to format
        :type value: float
        :return: Formatted string
        :rtype: str
        """
        try:
            if abs(value) >= 1_000_000_000:
                return f"{value / 1_000_000_000:.2f}B"
            elif abs(value) >= 1_000_000:
                return f"{value / 1_000_000:.2f}M"
            elif abs(value) >= 1_000:
                return f"{value / 1_000:.2f}K"
            else:
                return f"{value:.2f}"
        except Exception:
            return str(value)
