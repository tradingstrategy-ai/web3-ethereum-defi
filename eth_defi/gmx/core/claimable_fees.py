"""
GMX Claimable Fees Data Retrieval Module

This module provides claimable fees data for GMX protocol markets,
replacing the gmx_python_sdk GetClaimableFees functionality with exact feature parity.
"""

import logging
from typing import Any

import numpy as np
from numerize import numerize

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.get_data import GetData
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.contracts import get_datastore_contract
from eth_defi.gmx.keys import claimable_fee_amount_key


class GetClaimableFees(GetData):
    """
    GMX claimable fees data retrieval class.

    This class retrieves claimable fees information for all available GMX markets,
    replacing the gmx_python_sdk GetClaimableFees functionality with exact feature parity.

    :param config: GMXConfig instance containing chain and network info
    :type config: GMXConfig
    :param filter_swap_markets: Whether to filter out swap markets from results
    :type filter_swap_markets: bool
    """

    def __init__(self, config: GMXConfig, filter_swap_markets: bool = True):
        """
        Initialize claimable fees data retrieval.

        :param config: GMXConfig instance containing chain and network info
        :type config: GMXConfig
        :param filter_swap_markets: Whether to filter out swap markets from results
        :type filter_swap_markets: bool
        """
        super().__init__(config, filter_swap_markets)
        self.log = logging.getLogger(__name__)

    def _get_data_processing(self) -> dict[str, Any]:
        """
        Get total fees dictionary

        Returns
        -------
        funding_apr : dict
            dictionary of total fees for week so far.

        """
        total_fees = 0
        long_output_list = []
        short_output_list = []
        long_precision_list = []
        long_token_price_list = []
        mapper = []

        available_markets = self.markets.get_available_markets()

        for market_key in available_markets:
            self._get_token_addresses(market_key)
            market_symbol = self.markets.get_market_symbol(market_key)
            long_decimal_factor = self.markets.get_decimal_factor(market_key=market_key, long=True)
            long_precision = 10 ** (long_decimal_factor - 1)
            oracle_precision = 10 ** (30 - long_decimal_factor)

            # uncalled web3 object for long fees
            long_output = self._get_claimable_fee_amount(market_key, self._long_token_address)

            prices = OraclePrices(chain=self.config.chain).get_recent_prices()
            long_token_price = np.median(
                [
                    float(prices[self._long_token_address]["maxPriceFull"]) / oracle_precision,
                    float(prices[self._long_token_address]["minPriceFull"]) / oracle_precision,
                ]
            )

            long_token_price_list.append(long_token_price)
            long_precision_list.append(long_precision)

            # uncalled web3 object for short fees
            short_output = self._get_claimable_fee_amount(market_key, self._short_token_address)

            # add the uncalled web3 objects to list
            long_output_list = [*long_output_list, long_output]
            short_output_list = [*short_output_list, short_output]

            # add the market symbol to a list to use to map to dictionary later
            mapper.append(market_symbol)

        # feed the uncalled web3 objects into threading function
        long_threaded_output = self._execute_threading(long_output_list)
        short_threaded_output = self._execute_threading(short_output_list)

        for (
            long_claimable_fees,
            short_claimable_fees,
            long_precision,
            long_token_price,
            token_symbol,
        ) in zip(
            long_threaded_output,
            short_threaded_output,
            long_precision_list,
            long_token_price_list,
            mapper,
        ):
            # convert raw outputs into USD value
            long_claimable_usd = (long_claimable_fees / long_precision) * long_token_price

            # TODO - currently all short fees are collected in USDC which is
            # 6 decimals
            short_claimable_usd = short_claimable_fees / (10**6)

            if "2" in token_symbol:
                short_claimable_usd = 0

            self.log.debug(f"Token: {token_symbol}")

            self.log.debug(
                f"""Long Claimable Fees:
                 ${numerize.numerize(long_claimable_usd)}"""
            )

            self.log.debug(
                f"""Short Claimable Fees:
                 ${numerize.numerize(short_claimable_usd)}"""
            )

            total_fees += long_claimable_usd + short_claimable_usd

        return {"total_fees": total_fees, "parameter": "total_fees"}

    def _get_claimable_fee_amount(self, market_address: str, token_address: str):
        """
        For a given market and long/short side of the pool get the raw output
        for pending fees

        Parameters
        ----------
        market_address : str
            addess of the GMX market.
        token_address : str
            address of either long or short collateral token.

        Returns
        -------
        claimable_fee : web3 datastore obj
            uncalled obj of the datastore contract.

        """
        datastore = get_datastore_contract(self.config.web3, self.config.chain)

        # create hashed key to query the datastore
        claimable_fees_amount_hash_data = claimable_fee_amount_key(market_address, token_address)

        claimable_fee = datastore.functions.getUint(claimable_fees_amount_hash_data)

        return claimable_fee
