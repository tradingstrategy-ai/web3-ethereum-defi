"""
GMX Borrow APR Data Retrieval Module

This module provides borrow APR data for GMX protocol markets,
replacing the gmx_python_sdk GetBorrowAPR functionality with exact feature parity.
"""

import logging
from typing import Any

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.get_data import GetData


class GetBorrowAPR(GetData):
    """
    GMX borrow APR data retrieval class.

    This class retrieves borrow APR information for all available GMX markets,
    replacing the gmx_python_sdk GetBorrowAPR functionality with exact feature parity.

    :param config: GMXConfig instance containing chain and network info
    :type config: GMXConfig
    :param filter_swap_markets: Whether to filter out swap markets from results
    :type filter_swap_markets: bool
    """

    def __init__(self, config: GMXConfig, filter_swap_markets: bool = True):
        """
        Initialize borrow APR data retrieval.

        :param config: GMXConfig instance containing chain and network info
        :type config: GMXConfig
        :param filter_swap_markets: Whether to filter out swap markets from results
        :type filter_swap_markets: bool
        """
        super().__init__(config, filter_swap_markets)
        self.log = logging.getLogger(__name__)

    def _get_data_processing(self) -> dict[str, Any]:
        """
        Generate the dictionary of borrow APR data

        Returns
        -------
        funding_apr : dict
            dictionary of borrow data.

        """
        output_list = []
        mapper = []

        available_markets = self.markets.get_available_markets()

        for market_key in available_markets:
            index_token_address = self.markets.get_index_token_address(market_key)

            self._get_token_addresses(market_key)
            output = self._get_oracle_prices(
                market_key,
                index_token_address,
            )

            output_list.append(output)
            mapper.append(self.markets.get_market_symbol(market_key))

        threaded_output = self._execute_threading(output_list)

        for key, output in zip(mapper, threaded_output):
            self.output["long"][key] = (output[1] / 10**28) * 3600
            self.output["short"][key] = (output[2] / 10**28) * 3600

            self.log.debug("{}\nLong Borrow Hourly Rate: -{:.5f}%\nShort Borrow Hourly Rate: -{:.5f}%\n".format(key, self.output["long"][key], self.output["short"][key]))

        self.output["parameter"] = "borrow_apr"

        return self.output
