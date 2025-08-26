"""
GMX Available Liquidity Data Module

This module provides access to available liquidity data across GMX markets,
replacing the gmx_python_sdk GetAvailableLiquidity functionality.
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional, Any, List
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from eth_typing import HexAddress
from cchecksum import to_checksum_address

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
    Available liquidity data provider for GMX protocol.

    This class retrieves real-time liquidity information showing how much
    capital is available for trading in each market. It inherits from GetData
    base class and replaces the gmx_python_sdk GetAvailableLiquidity functionality.

    :param config: GMXConfig instance containing chain and network info
    :type config: GMXConfig
    :param filter_swap_markets: Whether to filter out swap markets from results
    :type filter_swap_markets: bool
    """

    def __init__(self, config: GMXConfig, filter_swap_markets: bool = True):
        """
        Initialize liquidity data provider.

        :param config: GMXConfig instance containing chain and network info
        :type config: GMXConfig
        :param filter_swap_markets: Whether to filter out swap markets from results
        :type filter_swap_markets: bool
        """
        super().__init__(config, filter_swap_markets)
        self.log = logging.getLogger(__name__)

    def _get_data_processing(self) -> dict[str, Any]:
        """
        Process available liquidity data for all markets.

        This method implements the abstract method from GetData base class,
        providing specific logic for retrieving and processing liquidity data.

        :return: Dictionary containing processed liquidity data
        :rtype: Dict[str, Any]
        """
        try:
            self.log.info("GMX v2 Available Liquidity")

            # Get open interest data first
            open_interest = GetOpenInterest(self.config).get_data(to_json=False)

            # Initialize data lists for processing
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
            if not available_markets:
                self.log.warning("No markets available")
                return {"parameter": "available_liquidity", "long": {}, "short": {}}

            # Get oracle prices once
            if self._oracle_prices_cache is None:
                oracle_prices_obj = OraclePrices(chain=self.config.chain)
                self._oracle_prices_cache = oracle_prices_obj.get_recent_prices()

            for market_key in available_markets:
                try:
                    self._get_token_addresses(market_key)
                    market_symbol = self.markets.get_market_symbol(market_key)
                    
                    long_decimal_factor = self.markets.get_decimal_factor(market_key=market_key, long=True, short=False)
                    short_decimal_factor = self.markets.get_decimal_factor(market_key=market_key, long=False, short=True)
                    long_precision = 10 ** (30 + long_decimal_factor)
                    short_precision = 10 ** (30 + short_decimal_factor)
                    oracle_precision = 10 ** (30 - long_decimal_factor)

                    # Map market symbol
                    mapper.append(market_symbol)

                    # Get reserved amounts from open interest
                    reserved_long_list.append(open_interest.get("long", {}).get(market_symbol, 0))
                    reserved_short_list.append(open_interest.get("short", {}).get(market_symbol, 0))

                    # LONG POOL data
                    (
                        long_pool_amount,
                        long_reserve_factor,
                        long_open_interest_reserve_factor,
                    ) = self._get_max_reserved_usd(market_key, self._long_token_address, True)
                    
                    long_pool_amount_list.append(long_pool_amount)
                    long_reserve_factor_list.append(long_reserve_factor)
                    long_open_interest_reserve_factor_list.append(long_open_interest_reserve_factor)
                    long_precision_list.append(long_precision)

                    # SHORT POOL data
                    (
                        short_pool_amount,
                        short_reserve_factor,
                        short_open_interest_reserve_factor,
                    ) = self._get_max_reserved_usd(market_key, self._short_token_address, False)
                    
                    short_pool_amount_list.append(short_pool_amount)
                    short_reserve_factor_list.append(short_reserve_factor)
                    short_open_interest_reserve_factor_list.append(short_open_interest_reserve_factor)
                    short_precision_list.append(short_precision)

                    # Calculate token price using oracle data
                    if self._long_token_address in self._oracle_prices_cache:
                        token_price = np.median([
                            float(self._oracle_prices_cache[self._long_token_address]["maxPriceFull"]) / oracle_precision,
                            float(self._oracle_prices_cache[self._long_token_address]["minPriceFull"]) / oracle_precision,
                        ])
                        token_price_list.append(token_price)
                    else:
                        self.log.warning(f"No oracle price for {self._long_token_address}")
                        token_price_list.append(0)

                except Exception as e:
                    self.log.warning(f"Failed to process market {market_key}: {e}")
                    continue

            # Execute contract calls with threading and exponential backoff retry mechanism
            long_pool_amount_output = self._execute_threading_with_retry(long_pool_amount_list)
            short_pool_amount_output = self._execute_threading_with_retry(short_pool_amount_list)
            long_reserve_factor_list_output = self._execute_threading_with_retry(long_reserve_factor_list)
            short_reserve_factor_list_output = self._execute_threading_with_retry(short_reserve_factor_list)
            long_open_interest_reserve_factor_list_output = self._execute_threading_with_retry(long_open_interest_reserve_factor_list)
            short_open_interest_reserve_factor_list_output = self._execute_threading_with_retry(short_open_interest_reserve_factor_list)

            # Process results
            return self._process_liquidity_results(
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
            )

        except Exception as e:
            self.log.error(f"Failed to fetch liquidity data: {e}")
            return {"parameter": "available_liquidity", "long": {}, "short": {}}



    def _process_liquidity_results(
        self,
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
    ) -> dict[str, Any]:
        """
        Process the contract call results and calculate available liquidity.
        
        :return: Dictionary containing processed liquidity data
        :rtype: Dict[str, Any]
        """
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
            try:
                self.log.info(f"Token: {token_symbol}")

                # Select the lesser of maximum value of pool reserves or open interest limit
                long_reserve_factor = min(long_reserve_factor, long_open_interest_reserve_factor)

                if "2" in token_symbol:
                    long_pool_amount = long_pool_amount / 2

                long_max_reserved_tokens = long_pool_amount * long_reserve_factor
                long_max_reserved_usd = long_max_reserved_tokens / long_precision * token_price
                long_liquidity = long_max_reserved_usd - float(reserved_long)

                self.log.info(f"Available Long Liquidity: ${self._format_number(long_liquidity)}")

                # Select the lesser of maximum value of pool reserves or open interest limit
                short_reserve_factor = min(short_reserve_factor, short_open_interest_reserve_factor)

                short_max_reserved_usd = short_pool_amount * short_reserve_factor
                short_liquidity = short_max_reserved_usd / short_precision - float(reserved_short)

                # If it's a single side market need to calculate on token amount rather than $ value
                if "2" in token_symbol:
                    short_pool_amount = short_pool_amount / 2
                    short_max_reserved_tokens = short_pool_amount * short_reserve_factor
                    short_max_reserved_usd = short_max_reserved_tokens / short_precision * token_price
                    short_liquidity = short_max_reserved_usd - float(reserved_short)

                self.log.info(f"Available Short Liquidity: ${self._format_number(short_liquidity)}")

                self.output["long"][token_symbol] = long_liquidity
                self.output["short"][token_symbol] = short_liquidity

            except Exception as e:
                self.log.warning(f"Failed to process liquidity for {token_symbol}: {e}")
                continue

        self.output["parameter"] = "available_liquidity"
        return self.output

    def _format_number(self, value: float) -> str:
        """
        Format number for logging display.
        
        :param value: Number to format
        :type value: float
        :return: Formatted string
        :rtype: str
        """
        try:
            if abs(value) >= 1_000_000:
                return f"{value/1_000_000:.2f}M"
            elif abs(value) >= 1_000:
                return f"{value/1_000:.2f}K"
            else:
                return f"{value:.2f}"
        except Exception:
            return str(value)

    def _get_max_reserved_usd(self, market: str, token: str, is_long: bool) -> tuple:
        """
        Get uncalled web3 functions to calculate pool size, pool reserve factor and open interest reserve factor.

        :param market: Contract address of GMX market
        :type market: str
        :param token: Contract address of long or short token
        :type token: str
        :param is_long: True for long pool or False for short
        :type is_long: bool
        :return: Tuple of uncalled web3 contract objects
        :rtype: tuple
        """
        try:
            # Get web3 datastore object
            datastore = get_datastore_contract(self.config.web3, self.config.chain)
            
            # Generate hash keys for datastore queries
            pool_amount_hash_data = self._pool_amount_key(market, token)
            reserve_factor_hash_data = self._reserve_factor_key(market, is_long)
            open_interest_reserve_factor_hash_data = self._open_interest_reserve_factor_key(market, is_long)

            pool_amount = datastore.functions.getUint(pool_amount_hash_data)
            reserve_factor = datastore.functions.getUint(reserve_factor_hash_data)
            open_interest_reserve_factor = datastore.functions.getUint(open_interest_reserve_factor_hash_data)

            return pool_amount, reserve_factor, open_interest_reserve_factor
            
        except Exception as e:
            self.log.warning(f"Failed to get max reserved USD for {market}: {e}")
            return None, None, None

    def _pool_amount_key(self, market: str, token: str) -> bytes:
        """
        Generate pool amount key for datastore query.
        
        :param market: Market address
        :type market: str
        :param token: Token address  
        :type token: str
        :return: Hash key for datastore
        :rtype: bytes
        """
        from web3 import Web3
        from eth_abi import encode
        POOL_AMOUNT = Web3.keccak(text="POOL_AMOUNT")
        return Web3.keccak(encode(["bytes32", "address", "address"], [POOL_AMOUNT, market, token]))
        
    def _reserve_factor_key(self, market: str, is_long: bool) -> bytes:
        """
        Generate reserve factor key for datastore query.
        
        :param market: Market address
        :type market: str
        :param is_long: Whether for long positions
        :type is_long: bool
        :return: Hash key for datastore
        :rtype: bytes
        """
        from web3 import Web3
        from eth_abi import encode
        RESERVE_FACTOR = Web3.keccak(text="RESERVE_FACTOR")
        return Web3.keccak(encode(["bytes32", "address", "bool"], [RESERVE_FACTOR, market, is_long]))
        
    def _open_interest_reserve_factor_key(self, market: str, is_long: bool) -> bytes:
        """
        Generate open interest reserve factor key for datastore query.
        
        :param market: Market address
        :type market: str
        :param is_long: Whether for long positions
        :type is_long: bool
        :return: Hash key for datastore
        :rtype: bytes
        """
        from web3 import Web3
        from eth_abi import encode
        OPEN_INTEREST_RESERVE_FACTOR = Web3.keccak(text="OPEN_INTEREST_RESERVE_FACTOR")
        return Web3.keccak(encode(["bytes32", "address", "bool"], [OPEN_INTEREST_RESERVE_FACTOR, market, is_long]))

    def _execute_threading_with_retry(self, contract_calls: list, max_workers: int = 5, 
                                     max_retries: int = 3, initial_backoff: float = 0.1, 
                                     max_backoff: float = 1.0) -> list:
        """
        Execute multiple contract calls concurrently with exponential backoff retry mechanism.

        :param contract_calls: List of contract call objects to execute
        :type contract_calls: list
        :param max_workers: Maximum number of concurrent workers
        :type max_workers: int
        :param max_retries: Maximum number of retry attempts
        :type max_retries: int
        :param initial_backoff: Initial backoff time in seconds
        :type initial_backoff: float
        :param max_backoff: Maximum backoff time in seconds
        :type max_backoff: float
        :return: List of results in same order as input
        :rtype: list
        """
        import random
        
        results = [None] * len(contract_calls)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {}
            for index, call in enumerate(contract_calls):
                future_to_index[executor.submit(self._call_with_retry, call, max_retries, 
                                              initial_backoff, max_backoff)] = index
            
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    results[index] = future.result()
                except Exception as e:
                    self.log.warning(f"Contract call {index} failed after retries: {e}")
                    results[index] = None
        
        return results

    def _call_with_retry(self, call, max_retries: int, initial_backoff: float, max_backoff: float):
        """
        Execute a single contract call with exponential backoff retry logic.
        
        :param call: Contract call object to execute
        :param max_retries: Maximum number of retry attempts
        :param initial_backoff: Initial backoff time in seconds
        :param max_backoff: Maximum backoff time in seconds
        :return: Result of the contract call
        """
        import random
        
        attempts = 0
        backoff = initial_backoff
        
        while attempts < max_retries:
            try:
                return call.call()
            except Exception as e:
                attempts += 1
                if attempts >= max_retries:
                    raise e
                
                # Add jitter to avoid thundering herd
                jitter = random.uniform(0, 0.1 * backoff)
                wait_time = min(backoff + jitter, max_backoff)
                
                self.log.debug(f"Call failed: {str(e)}. Retrying in {wait_time:.2f} seconds (attempt {attempts}/{max_retries})")
                time.sleep(wait_time)
                
                # Exponential backoff
                backoff = min(backoff * 2, max_backoff)





