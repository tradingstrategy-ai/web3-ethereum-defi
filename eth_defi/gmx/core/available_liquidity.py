"""
GMX Available Liquidity Data Retrieval Module

This module provides available liquidity data for GMX protocol markets,
replacing the gmx_python_sdk GetAvailableLiquidity functionality with exact feature parity.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from eth_typing import HexAddress

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_datastore_contract
from eth_defi.gmx.core.get_data import GetData
from eth_defi.gmx.core.open_interest import GetOpenInterest
from eth_defi.gmx.core.oracle import OraclePrices


@dataclass
class LiquidityInfo:
    """
    Liquidity information for a specific GMX market.

    :param market_address: GMX market contract address
    :type market_address: HexAddress
    :param market_symbol: Market symbol identifier
    :type market_symbol: str
    :param long_liquidity: Available liquidity for long positions in USD
    :type long_liquidity: float
    :param short_liquidity: Available liquidity for short positions in USD
    :type short_liquidity: float
    :param total_liquidity: Total available liquidity in USD
    :type total_liquidity: float
    :param long_token_address: Address of the long token
    :type long_token_address: HexAddress
    :param short_token_address: Address of the short token
    :type short_token_address: HexAddress
    """

    market_address: HexAddress
    market_symbol: str
    long_liquidity: float
    short_liquidity: float
    total_liquidity: float
    long_token_address: HexAddress
    short_token_address: HexAddress


class GetAvailableLiquidity(GetData):
    """
    GMX available liquidity data retrieval class.

    This class retrieves available liquidity information for all available GMX markets,
    replacing the gmx_python_sdk GetAvailableLiquidity functionality with exact feature parity.

    :param config: GMXConfig instance containing chain and network info
    :type config: GMXConfig
    :param filter_swap_markets: Whether to filter out swap markets from results
    :type filter_swap_markets: bool
    """

    def __init__(self, config: GMXConfig, filter_swap_markets: bool = True):
        """
        Initialize available liquidity data retrieval.

        :param config: GMXConfig instance containing chain and network info
        :type config: GMXConfig
        :param filter_swap_markets: Whether to filter out swap markets from results
        :type filter_swap_markets: bool
        """
        super().__init__(config, filter_swap_markets)
        self.log = logging.getLogger(__name__)

    def _get_data_processing(self) -> dict[str, Any]:
        """
        Generate the dictionary of available liquidity

        Returns
        -------
        funding_apr: dict
            dictionary of available liquidity

        """
        self.log.info("GMX v2 Available Liquidity")

        open_interest = GetOpenInterest(self.config).get_data()

        reserved_long_list = []
        reserved_short_list = []
        token_price_list = []
        mapper = []
        long_pool_amount_list = []
        long_reserve_factor_list = []
        long_open_interest_reserve_factor_list = []
        short_pool_amount_list = []
        short_reserve_factor_list = []
        short_open_interest_reserve_factor_list = []
        long_precision_list = []
        short_precision_list = []

        available_markets = self.markets.get_available_markets()

        for market_key in available_markets:
            self._get_token_addresses(market_key)
            market_symbol = self.markets.get_market_symbol(market_key)
            long_decimal_factor = self.markets.get_decimal_factor(market_key=market_key, long=True)
            short_decimal_factor = self.markets.get_decimal_factor(market_key=market_key, short=True)
            long_precision = 10 ** (30 + long_decimal_factor)
            short_precision = 10 ** (30 + short_decimal_factor)
            oracle_precision = 10 ** (30 - long_decimal_factor)

            # collate market symbol to map dictionary later
            mapper.append(market_symbol)

            # LONG POOL
            (
                long_pool_amount,
                long_reserve_factor,
                long_open_interest_reserve_factor,
            ) = self.get_max_reserved_usd(market_key, self._long_token_address, True)
            reserved_long_list.append(open_interest["long"][market_symbol])
            long_pool_amount_list.append(long_pool_amount)
            long_reserve_factor_list.append(long_reserve_factor)
            long_open_interest_reserve_factor_list.append(long_open_interest_reserve_factor)
            long_precision_list.append(long_precision)

            # SHORT POOL
            (
                short_pool_amount,
                short_reserve_factor,
                short_open_interest_reserve_factor,
            ) = self.get_max_reserved_usd(market_key, self._short_token_address, False)
            reserved_short_list.append(open_interest["short"][market_symbol])
            short_pool_amount_list.append(short_pool_amount)
            short_reserve_factor_list.append(short_reserve_factor)
            short_open_interest_reserve_factor_list.append(short_open_interest_reserve_factor)
            short_precision_list.append(short_precision)

            # Calculate token price
            prices = OraclePrices(chain=self.config.chain).get_recent_prices()
            token_price = np.median(
                [
                    float(prices[self._long_token_address]["maxPriceFull"]) / oracle_precision,
                    float(prices[self._long_token_address]["minPriceFull"]) / oracle_precision,
                ]
            )
            token_price_list.append(token_price)

        # TODO - Series of sleeps to stop ratelimit on the RPC, should have
        # retry
        long_pool_amount_output = self._execute_threading(long_pool_amount_list)
        time.sleep(0.2)

        short_pool_amount_output = self._execute_threading(short_pool_amount_list)
        time.sleep(0.2)

        long_reserve_factor_list_output = self._execute_threading(long_reserve_factor_list)
        time.sleep(0.2)

        short_reserve_factor_list_output = self._execute_threading(short_reserve_factor_list)
        time.sleep(0.2)

        long_open_interest_reserve_factor_list_output = self._execute_threading(long_open_interest_reserve_factor_list)
        time.sleep(0.2)

        short_open_interest_reserve_factor_list_output = self._execute_threading(short_open_interest_reserve_factor_list)

        for (
            long_pool_amount,
            short_pool_amount,
            long_reserve_factor,
            short_reserve_factor,
            long_open_interest_reserve_factor,
            short_open_interest_reserve_factor,
            reserved_long,
            reserved_short,
            token_price,
            token_symbol,
            long_precision,
            short_precision,
        ) in zip(
            long_pool_amount_output,
            short_pool_amount_output,
            long_reserve_factor_list_output,
            short_reserve_factor_list_output,
            long_open_interest_reserve_factor_list_output,
            short_open_interest_reserve_factor_list_output,
            reserved_long_list,
            reserved_short_list,
            token_price_list,
            mapper,
            long_precision_list,
            short_precision_list,
        ):
            self.log.info(f"Token: {token_symbol}")

            # select the lesser of maximum value of pool reserves or open
            # interest limit
            long_reserve_factor = min(long_reserve_factor, long_open_interest_reserve_factor)

            if "2" in token_symbol:
                long_pool_amount = long_pool_amount / 2

            long_max_reserved_tokens = long_pool_amount * long_reserve_factor

            long_max_reserved_usd = long_max_reserved_tokens / long_precision * token_price

            long_liquidity = long_max_reserved_usd - float(reserved_long)

            self.log.info(f"Available Long Liquidity: ${self._format_number(long_liquidity)}")

            # select the lesser of maximum value of pool reserves or open
            # interest limit
            short_reserve_factor = min(short_reserve_factor, short_open_interest_reserve_factor)

            short_max_reserved_usd = short_pool_amount * short_reserve_factor

            short_liquidity = short_max_reserved_usd / short_precision - float(reserved_short)

            # If its a single side market need to calculate on token
            # amount rather than $ value
            if "2" in token_symbol:
                short_pool_amount = short_pool_amount / 2

                short_max_reserved_tokens = short_pool_amount * short_reserve_factor

                short_max_reserved_usd = short_max_reserved_tokens / short_precision * token_price

                short_liquidity = short_max_reserved_usd - float(reserved_short)

            self.log.info(f"Available Short Liquidity: ${self._format_number(short_liquidity)}")

            self.output["long"][token_symbol] = long_liquidity
            self.output["short"][token_symbol] = short_liquidity

        self.output["parameter"] = "available_liquidity"

        return self.output

    def _format_number(self, value: float) -> str:
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

    def get_max_reserved_usd(self, market: str, token: str, is_long: bool) -> tuple[Any, Any, Any]:
        """
        For a given market, long/short token and pool direction get the
        uncalled web3 functions to calculate pool size, pool reserve factor
        and open interest reserve factor

        Parameters
        ----------
        market: str
            contract address of GMX market.
        token: str
            contract address of long or short token.
        is_long: bool
            pass True for long pool or False for short.

        Returns
        -------
        pool_amount: web3.contract_obj
            uncalled web3 contract object for pool amount.
        reserve_factor: web3.contract_obj
            uncalled web3 contract object for pool reserve factor.
        open_interest_reserve_factor: web3.contract_obj
            uncalled web3 contract object for open interest reserve factor.

        """
        from web3 import Web3
        from eth_abi import encode

        # get web3 datastore object
        datastore = get_datastore_contract(self.config.web3, self.config.chain)

        # get hashed keys for datastore
        pool_amount_hash_data = self._pool_amount_key(market, token)
        reserve_factor_hash_data = self._reserve_factor_key(market, is_long)
        open_interest_reserve_factor_hash_data = self._open_interest_reserve_factor_key(market, is_long)

        pool_amount = datastore.functions.getUint(pool_amount_hash_data)
        reserve_factor = datastore.functions.getUint(reserve_factor_hash_data)
        open_interest_reserve_factor = datastore.functions.getUint(open_interest_reserve_factor_hash_data)

        return pool_amount, reserve_factor, open_interest_reserve_factor

    def _pool_amount_key(self, market: str, token: str) -> bytes:
        """Generate pool amount key for datastore query."""
        from web3 import Web3
        from eth_abi import encode

        POOL_AMOUNT = Web3.keccak(text="POOL_AMOUNT")
        return Web3.keccak(encode(["bytes32", "address", "address"], [POOL_AMOUNT, market, token]))

    def _reserve_factor_key(self, market: str, is_long: bool) -> bytes:
        """Generate reserve factor key for datastore query."""
        from web3 import Web3
        from eth_abi import encode

        RESERVE_FACTOR = Web3.keccak(text="RESERVE_FACTOR")
        return Web3.keccak(encode(["bytes32", "address", "bool"], [RESERVE_FACTOR, market, is_long]))

    def _open_interest_reserve_factor_key(self, market: str, is_long: bool) -> bytes:
        """Generate open interest reserve factor key for datastore query."""
        from web3 import Web3
        from eth_abi import encode

        OPEN_INTEREST_RESERVE_FACTOR = Web3.keccak(text="OPEN_INTEREST_RESERVE_FACTOR")
        return Web3.keccak(encode(["bytes32", "address", "bool"], [OPEN_INTEREST_RESERVE_FACTOR, market, is_long]))
