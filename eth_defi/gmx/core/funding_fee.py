"""
GMX Funding APR Data Retrieval Module.

This module provides funding APR data for GMX protocol markets.
"""

from typing import Any

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.get_data import GetData
from eth_defi.gmx.types import PositionSideData
from eth_defi.gmx.core.open_interest import GetOpenInterest
from eth_defi.gmx.keys import apply_factor


class GetFundingFee(GetData):
    """GMX funding fee data retrieval class.

    Retrieves funding fee information for all available GMX markets,
    replacing the gmx_python_sdk GetFundingFee functionality.
    """

    def __init__(self, config: GMXConfig, filter_swap_markets: bool = True):
        """Initialize funding fee data retrieval.

        :param config: GMXConfig instance containing chain and network info
        :param filter_swap_markets: Whether to filter out swap markets from results
        """
        super().__init__(config, filter_swap_markets)

    def _get_data_processing(self) -> PositionSideData:
        """Generate the dictionary of funding APR data.

        :return: Dictionary of funding APR data with long/short position data
        """
        open_interest = GetOpenInterest(config=self.config).get_data()

        # define empty lists to pass to zip iterater later on
        mapper = []
        output_list = []
        long_interest_usd_list = []
        short_interest_usd_list = []

        available_markets = self.markets.get_available_markets()

        # loop markets
        for market_key in available_markets:
            symbol = self.markets.get_market_symbol(market_key)
            index_token_address = self.markets.get_index_token_address(market_key)
            self._get_token_addresses(market_key)

            # Skip markets with unknown symbols or missing open interest data
            if symbol == "UNKNOWN" or symbol not in open_interest.get("long", {}) or symbol not in open_interest.get("short", {}):
                self.log.debug(f"Skipping market {market_key} with symbol '{symbol}' - no open interest data available")
                continue

            # Skip markets with zero index token addresses
            if index_token_address == "0x0000000000000000000000000000000000000000":
                self.log.debug(f"Skipping market {market_key} with zero index token address")
                continue

            try:
                output = self._get_oracle_prices(
                    market_key,
                    index_token_address,
                )
            except Exception as e:
                self.log.error(f"Failed to get oracle prices for {market_key}: {e}")
                continue

            mapper.append(symbol)
            output_list.append(output)
            long_interest_usd_list = [
                *long_interest_usd_list,
                open_interest["long"][symbol] * 10**30,
            ]
            short_interest_usd_list = [
                *short_interest_usd_list,
                open_interest["short"][symbol] * 10**30,
            ]

        # Multithreaded call on contract
        threaded_output = self._execute_threading(output_list)
        for output, long_interest_usd, short_interest_usd, symbol in zip(threaded_output, long_interest_usd_list, short_interest_usd_list, mapper):
            market_info_dict = {
                "market_token": output[0][0],
                "index_token": output[0][1],
                "long_token": output[0][2],
                "short_token": output[0][3],
                "long_borrow_fee": output[1],
                "short_borrow_fee": output[2],
                "is_long_pays_short": output[4][0],
                "funding_factor_per_second": output[4][1],
            }

            long_funding_fee = self._get_funding_factor_per_period(market_info_dict, True, 3600, long_interest_usd, short_interest_usd)

            short_funding_fee = self._get_funding_factor_per_period(market_info_dict, False, 3600, long_interest_usd, short_interest_usd)

            self.output["long"][symbol] = long_funding_fee
            self.output["short"][symbol] = short_funding_fee

        self.output["parameter"] = "funding_apr"

        return self.output

    @staticmethod
    def _get_funding_factor_per_period(
        market_info: dict,
        is_long: bool,
        period_in_seconds: int,
        long_interest_usd: int,
        short_interest_usd: int,
    ):
        """For a given market, calculate the funding factor for a given period.

        :param market_info: market parameters returned from the reader contract
        :type market_info: dict
        :param is_long: direction of the position
        :type is_long: bool
        :param period_in_seconds: Want percentage rate we want to output to be in
        :type period_in_seconds: int
        :param long_interest_usd: expanded decimal long interest
        :type long_interest_usd: int
        :param short_interest_usd: expanded decimal short interest
        :type short_interest_usd: int
        """
        funding_factor_per_second = market_info["funding_factor_per_second"] * 10**-28

        long_pays_shorts = market_info["is_long_pays_short"]

        if is_long:
            is_larger_side = long_pays_shorts
        else:
            is_larger_side = not long_pays_shorts

        if is_larger_side:
            factor_per_second = funding_factor_per_second * -1
        else:
            if long_pays_shorts:
                larger_interest_usd = long_interest_usd
                smaller_interest_usd = short_interest_usd

            else:
                larger_interest_usd = short_interest_usd
                smaller_interest_usd = long_interest_usd

            if smaller_interest_usd > 0:
                ratio = larger_interest_usd * 10**30 / smaller_interest_usd

            else:
                ratio = 0

            factor_per_second = apply_factor(ratio, funding_factor_per_second)

        return factor_per_second * period_in_seconds
