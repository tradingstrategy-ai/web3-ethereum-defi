"""
GMX Borrow APR Data Retrieval Module.

This module provides borrow APR data for GMX protocol markets.
"""

import logging

logger = logging.getLogger(__name__)
from typing import Any

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.get_data import GetData
from eth_defi.gmx.types import PositionSideData


class GetBorrowAPR(GetData):
    """GMX borrow APR data retrieval class.

    Retrieves borrow APR information for all available GMX markets,
    replacing the gmx_python_sdk GetBorrowAPR functionality.
    """

    def __init__(self, config: GMXConfig, filter_swap_markets: bool = True):
        """Initialize borrow APR data retrieval.

        :param config: GMXConfig instance containing chain and network info
        :param filter_swap_markets: Whether to filter out swap markets from results
        """
        super().__init__(config, filter_swap_markets)

    def _get_data_processing(self) -> PositionSideData:
        """Generate the dictionary of borrow APR data.

        :return: Dictionary of borrow APR data with long/short position data
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

            # Only add valid outputs to the list
            if output is not None:
                output_list.append(output)
                mapper.append(self.markets.get_market_symbol(market_key))

        # Handle case where no valid outputs were found
        if not output_list:
            self.output["parameter"] = "borrow_apr"
            return self.output

        threaded_output = self._execute_threading(output_list)

        for key, output in zip(mapper, threaded_output):
            if output is not None:  # Check that output is not None
                self.output["long"][key] = (output[1] / 10**28) * 3600
                self.output["short"][key] = (output[2] / 10**28) * 3600

                logger.debug("{}\nLong Borrow Hourly Rate: -{:.5f}%\nShort Borrow Hourly Rate: -{:.5f}%\n".format(key, self.output["long"][key], self.output["short"][key]))
            else:
                # Set default values for failed markets
                self.output["long"][key] = 0.0
                self.output["short"][key] = 0.0

        self.output["parameter"] = "borrow_apr"

        return self.output
