"""
GMX GM Prices Data Module.

This module provides access to GM token prices data.
"""

import logging

logger = logging.getLogger(__name__)
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.get_data import GetData
from eth_defi.gmx.types import PriceData
from eth_defi.gmx.keys import (
    MAX_PNL_FACTOR_FOR_TRADERS,
    MAX_PNL_FACTOR_FOR_DEPOSITS,
    MAX_PNL_FACTOR_FOR_WITHDRAWALS,
)


class GetGMPrices(GetData):
    """GM token prices data provider for GMX protocol.

    Retrieves current prices and valuation data for GM (liquidity provider) tokens.
    GM tokens represent shares in GMX liquidity pools and their prices reflect the underlying
    value of the pooled assets plus accumulated fees.

    Supports three different pricing scenarios:

    - **Traders**: Prices for trading scenarios (most commonly used)
    - **Deposits**: Prices optimized for deposit operations
    - **Withdrawals**: Prices optimized for withdrawal operations

    Each pricing scenario uses different PNL factor configurations to account for
    the specific risk and fee structures associated with different operation types.
    """

    def __init__(self, config: GMXConfig, filter_swap_markets: bool = True):
        """Initialize GM prices data provider.

        :param config: GMXConfig instance containing chain and network info
        :param filter_swap_markets: Whether to filter out swap markets from results
        """
        super().__init__(config, filter_swap_markets)

    def get_price_traders(self) -> PriceData:
        """Get GM token prices for traders.

        Retrieves GM token prices optimized for trading scenarios,
        using the MAX_PNL_FACTOR_FOR_TRADERS configuration. This is the most
        commonly used price type for general trading operations.

        :return: Dictionary containing GM prices for traders
        """
        logger.debug("Getting GM prices for traders")
        return self._process_gm_prices_data(MAX_PNL_FACTOR_FOR_TRADERS)

    def get_price_deposit(self) -> PriceData:
        """Get GM token prices for deposits.

        This method retrieves GM token prices optimized for deposit scenarios,
        using the MAX_PNL_FACTOR_FOR_DEPOSITS configuration. These prices
        account for the specific risks associated with adding liquidity to pools.

        :return: Dictionary containing GM prices for deposits
        """
        logger.debug("Getting GM prices for deposits")
        return self._process_gm_prices_data(MAX_PNL_FACTOR_FOR_DEPOSITS)

    def get_price_withdraw(self) -> PriceData:
        """Get GM token prices for withdrawals.

        This method retrieves GM token prices optimized for withdrawal scenarios,
        using the MAX_PNL_FACTOR_FOR_WITHDRAWALS configuration. These prices
        account for the specific risks associated with removing liquidity from pools.

        :return: Dictionary containing GM prices for withdrawals
        """
        logger.debug("Getting GM prices for withdrawals")
        return self._process_gm_prices_data(MAX_PNL_FACTOR_FOR_WITHDRAWALS)

    def get_prices(self, price_type: str = "traders") -> PriceData:
        """Get GM token prices with specified price type.

        This is a unified method that calls the appropriate specific price method
        based on the price_type parameter. This provides a convenient interface
        when the price type needs to be determined dynamically.

        :param price_type: Type of price to retrieve ("traders", "deposits", "withdrawals")
        :return: Dictionary containing GM prices data
        """
        if price_type == "traders":
            return self.get_price_traders()
        elif price_type == "deposits":
            return self.get_price_deposit()
        elif price_type == "withdrawals":
            return self.get_price_withdraw()
        else:
            logger.debug(f"Unknown price type: {price_type}. Using 'traders' as default.")
            return self.get_price_traders()

    def _get_data_processing(self) -> PriceData:
        """Override base class method to return comprehensive GM prices data.

        This method provides the base class interface and returns all three
        GM price types (traders, deposits, withdrawals) in a single comprehensive
        response. This is called when users use the base GetData interface
        via the get_data() method.

        :return: Comprehensive GM prices data dictionary with all price types
        """
        try:
            logger.debug("Getting comprehensive GM prices data (all types)")

            # Get all three price types
            traders_data = self._process_gm_prices_data(MAX_PNL_FACTOR_FOR_TRADERS)
            deposits_data = self._process_gm_prices_data(MAX_PNL_FACTOR_FOR_DEPOSITS)
            withdrawals_data = self._process_gm_prices_data(MAX_PNL_FACTOR_FOR_WITHDRAWALS)

            # Create comprehensive response
            comprehensive_data = {
                "parameter": "gm_prices_all_types",
                "timestamp": int(time.time()),
                "chain": self.config.chain,
                "price_types": {
                    "traders": traders_data.get("gm_prices", {}),
                    "deposits": deposits_data.get("gm_prices", {}),
                    "withdrawals": withdrawals_data.get("gm_prices", {}),
                },
                "metadata": {"total_markets_traders": len(traders_data.get("gm_prices", {})), "total_markets_deposits": len(deposits_data.get("gm_prices", {})), "total_markets_withdrawals": len(withdrawals_data.get("gm_prices", {})), "description": "Comprehensive GM prices including traders, deposits, and withdrawals"},
            }

            # Add market summary for easy access
            all_markets = set()
            all_markets.update(traders_data.get("gm_prices", {}).keys())
            all_markets.update(deposits_data.get("gm_prices", {}).keys())
            all_markets.update(withdrawals_data.get("gm_prices", {}).keys())

            comprehensive_data["total_markets"] = len(all_markets)
            comprehensive_data["markets"] = sorted(list(all_markets))

            logger.debug(f"Retrieved comprehensive GM prices for {len(all_markets)} markets")
            return comprehensive_data

        except Exception as e:
            logger.error(f"Failed to get comprehensive GM prices data: {e}")
            # Return minimal structure on error
            return {"parameter": "gm_prices_all_types", "timestamp": int(time.time()), "error": str(e), "price_types": {"traders": {}, "deposits": {}, "withdrawals": {}}, "total_markets": 0}

    def _process_gm_prices_data(self, pnl_factor_type: bytes) -> PriceData:
        """Core data processing method for GM pool prices.

        This method orchestrates the entire GM price retrieval process:

        1. Filter swap markets if enabled
        2. Prepare contract queries for each market
        3. Execute queries concurrently using threading
        4. Process and format results

        The method uses the GMX Reader contract's getMarketTokenPrice function
        to retrieve raw price data, which is then converted from wei to USD
        by dividing by 10^30.

        :param pnl_factor_type: PNL factor type hash for datastore queries
        :return: Dictionary containing processed GM prices
        """
        try:
            logger.debug("Starting GM prices data processing")

            # Apply swap market filtering if enabled
            if self.filter_swap_markets:
                self._filter_swap_markets()

            # Get available markets after filtering
            available_markets = self.markets.get_available_markets()

            if not available_markets:
                logger.debug("No markets available after filtering")
                return {"gm_prices": {}, "parameter": "gm_prices", "timestamp": int(time.time())}

            # Prepare for concurrent processing
            market_queries = []
            market_symbols = []

            # Build market queries
            for market_key in available_markets:
                try:
                    # Get token addresses for this market
                    self._get_token_addresses(market_key)

                    if not self._long_token_address or not self._short_token_address:
                        logger.debug(f"Missing token addresses for market {market_key}")
                        continue

                    # Get index token address
                    index_token_address = self.markets.get_index_token_address(market_key)

                    # Get oracle prices as tuples
                    oracle_prices = self._get_oracle_prices(market_key, index_token_address, return_tuple=True)

                    if not oracle_prices or len(oracle_prices) < 3:
                        logger.debug(f"Missing or incomplete oracle prices for market {market_key}")
                        continue

                    # Build market info tuple
                    market = [
                        market_key,
                        index_token_address,
                        self._long_token_address,
                        self._short_token_address,
                    ]

                    # Create contract query (not executed yet)
                    query = self._make_market_token_price_query(
                        market,
                        oracle_prices[0],  # index price tuple
                        oracle_prices[1],  # long price tuple
                        oracle_prices[2],  # short price tuple
                        pnl_factor_type,
                    )

                    market_queries.append(query)
                    market_symbols.append(self.markets.get_market_symbol(market_key))

                except Exception as e:
                    logger.debug(f"Failed to prepare query for market {market_key}: {e}")
                    continue

            if not market_queries:
                logger.debug("No valid market queries prepared")
                return {"gm_prices": {}, "parameter": "gm_prices", "timestamp": int(time.time())}

            # Execute queries concurrently using threading
            logger.debug(f"Executing {len(market_queries)} market price queries concurrently")
            threaded_results = self._execute_threading(market_queries)

            # Process results
            prices_dict = {}
            for symbol, result in zip(market_symbols, threaded_results):
                try:
                    if result and len(result) > 0:
                        # Convert from wei to USD by dividing by 10^30
                        price_usd = result[0] / 10**30
                        prices_dict[symbol] = price_usd
                        logger.debug(f"Processed price for {symbol}: ${price_usd:.6f}")
                    else:
                        logger.debug(f"Empty result for {symbol}")
                except (TypeError, IndexError, ZeroDivisionError) as e:
                    logger.debug(f"Failed to process result for {symbol}: {e}")
                    continue

            # Prepare final output
            output = {
                "gm_prices": prices_dict,
                "parameter": "gm_prices",
                "timestamp": int(time.time()),
                "chain": self.config.chain,
                "total_markets": len(prices_dict),
            }

            # Files export functionality removed

            logger.debug(f"Successfully processed GM prices for {len(prices_dict)} markets")
            return output

        except Exception as e:
            logger.error(f"Failed to process GM prices data: {e}")
            return {"gm_prices": {}, "parameter": "gm_prices", "error": str(e), "timestamp": int(time.time())}

    def _make_market_token_price_query(
        self,
        market: list,
        index_price_tuple: tuple,
        long_price_tuple: tuple,
        short_price_tuple: tuple,
        pnl_factor_type: bytes,
    ):
        """Create a market token price query for the reader contract.

        This method creates an unexecuted Web3 contract call that retrieves
        the current GM token price for a specific market. The query uses the
        GMX Reader contract's getMarketTokenPrice function with the provided
        market information and oracle prices.

        :param market: List containing market contract addresses [market, index, long, short]
        :param index_price_tuple: Tuple of (min_price, max_price) for index token
        :param long_price_tuple: Tuple of (min_price, max_price) for long token
        :param short_price_tuple: Tuple of (min_price, max_price) for short token
        :param pnl_factor_type: PNL factor type hash for calculations
        :return: Unexecuted Web3 contract call
        """
        try:
            # Use maximize=True to get maximum prices in calculation
            maximize = True

            return self.reader_contract.functions.getMarketTokenPrice(
                self.datastore_contract.address,  # datastore address
                market,  # [market, index, long, short]
                index_price_tuple,  # (min, max) for index
                long_price_tuple,  # (min, max) for long
                short_price_tuple,  # (min, max) for short
                pnl_factor_type,  # pnl factor hash
                maximize,  # maximize prices
            )
        except Exception as e:
            logger.error(f"Failed to create market token price query: {e}")
            raise

    def _execute_threading(self, queries: list) -> list:
        """Execute multiple contract queries concurrently using threading.

        This method takes a list of unexecuted Web3 contract calls and
        executes them concurrently to improve performance. It includes
        timeout handling and fallback to sequential execution if needed.

        :param queries: List of unexecuted Web3 contract calls
        :return: List of query results in the same order as input
        """
        results = [None] * len(queries)

        try:
            with ThreadPoolExecutor(max_workers=min(10, len(queries))) as executor:
                # Submit all queries
                future_to_index = {}
                for i, query in enumerate(queries):
                    try:
                        future = executor.submit(query.call)
                        future_to_index[future] = i
                    except Exception as e:
                        logger.debug(f"Failed to submit query {i}: {e}")
                        continue

                # Collect results as they complete with timeout
                for future in as_completed(future_to_index, timeout=30):
                    index = future_to_index[future]
                    try:
                        result = future.result()
                        results[index] = result
                        logger.debug(f"Query {index} completed successfully")
                    except Exception as e:
                        logger.debug(f"Query {index} failed: {e}")
                        results[index] = None

        except Exception as e:
            logger.error(f"Threading execution failed: {e}")
            # Fallback to sequential execution
            logger.debug("Falling back to sequential execution")
            for i, query in enumerate(queries):
                try:
                    results[i] = query.call()
                except Exception as e:
                    logger.debug(f"Sequential query {i} failed: {e}")
                    results[i] = None

        return results
