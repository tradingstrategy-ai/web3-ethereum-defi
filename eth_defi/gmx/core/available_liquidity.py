"""
GMX Available Liquidity Data Retrieval Module.

This module provides available liquidity data for GMX protocol markets.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)
from typing import Any, Iterable
from collections import defaultdict

import numpy as np

from eth_typing import HexAddress
from eth_utils import keccak

from eth_defi.event_reader.multicall_batcher import EncodedCall, read_multicall_chunked, EncodedCallResult
from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_datastore_contract
from eth_defi.gmx.core.get_data import GetData
from eth_defi.gmx.core.open_interest import GetOpenInterest
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.keys import pool_amount_key, open_interest_reserve_factor_key, reserve_factor_key
from eth_defi.gmx.types import MarketSymbol, USDAmount, PositionSideData


@dataclass(slots=True)
class LiquidityInfo:
    """Liquidity information for a specific GMX market."""

    #: GMX market contract address
    market_address: HexAddress
    #: Market symbol identifier
    market_symbol: MarketSymbol
    #: Available liquidity for long positions in USD
    long_liquidity: USDAmount
    #: Available liquidity for short positions in USD
    short_liquidity: USDAmount
    #: Total available liquidity in USD
    total_liquidity: USDAmount
    #: Address of the long token
    long_token_address: HexAddress
    #: Address of the short token
    short_token_address: HexAddress


class GetAvailableLiquidity(GetData):
    """GMX available liquidity data retrieval using efficient multicall batching.

    Retrieves available liquidity information for all GMX markets with
    efficient multicall batching for better performance and reduced RPC usage.
    """

    def __init__(self, config: GMXConfig, filter_swap_markets: bool = True, use_original_approach: bool = False):
        """Initialize available liquidity data retrieval.

        :param config: GMXConfig instance containing chain and network info
        :param filter_swap_markets: Whether to filter out swap markets from results
        :param use_original_approach: Whether to use original individual calls instead of multicall
        """
        super().__init__(config, filter_swap_markets)
        self.use_original_approach = use_original_approach

        # Get DataStore contract address for multicalls
        self.datastore_address = get_datastore_contract(self.config.web3, self.config.chain).address

    def _get_data_processing(self) -> PositionSideData:
        """Route to the appropriate processing method based on configuration."""
        if self.use_original_approach:
            return self._get_data_processing_original_approach()
        else:
            return self._get_data_processing_multicall()

    def generate_multicall_requests(self, market_key: str, long_token: str, short_token: str) -> Iterable[EncodedCall]:
        """Generate multicall requests for liquidity data.

        For each market we need to query:
        - pool_amount for long token
        - pool_amount for short token
        - reserve_factor for long
        - reserve_factor for short
        - open_interest_reserve_factor for long
        - open_interest_reserve_factor for short

        :param market_key: Market address
        :param long_token: Long token address
        :param short_token: Short token address
        :return: Iterable of EncodedCall objects
        """
        # DataStore.getUint() function signature: getUint(bytes32)
        get_uint_signature = keccak(text="getUint(bytes32)")[:4]

        # Generate keys for DataStore queries
        long_pool_key = pool_amount_key(market_key, long_token)
        short_pool_key = pool_amount_key(market_key, short_token)
        long_reserve_key = reserve_factor_key(market_key, True)
        short_reserve_key = reserve_factor_key(market_key, False)
        long_oi_reserve_key = open_interest_reserve_factor_key(market_key, True)
        short_oi_reserve_key = open_interest_reserve_factor_key(market_key, False)

        # Create encoded calls for each data point
        calls = [
            ("long_pool_amount", long_pool_key),
            ("short_pool_amount", short_pool_key),
            ("long_reserve_factor", long_reserve_key),
            ("short_reserve_factor", short_reserve_key),
            ("long_oi_reserve_factor", long_oi_reserve_key),
            ("short_oi_reserve_factor", short_oi_reserve_key),
        ]

        for func_name, key_bytes in calls:
            yield EncodedCall.from_keccak_signature(
                address=self.datastore_address,
                signature=get_uint_signature,
                function=func_name,
                data=key_bytes,
                extra_data={"market_key": market_key, "data_type": func_name},
            )

    def generate_all_multicalls(self) -> Iterable[EncodedCall]:
        """
        Generate all multicall requests for all markets.

        :return: Iterable of all EncodedCall objects needed
        """
        available_markets = self.markets.get_available_markets()

        for market_key in available_markets:
            self._get_token_addresses(market_key)
            if self._long_token_address is None or self._short_token_address is None:
                logger.warning(f"Skipping market {market_key} due to missing token addresses")
                continue
            yield from self.generate_multicall_requests(market_key, self._long_token_address, self._short_token_address)

    # TODO: revove it
    def _get_data_processing_original_approach(self) -> PositionSideData:
        """
        Generate the dictionary of available liquidity using the original approach
        (individual web3 calls) for debugging comparison.
        """
        logger.info("GMX v2 Available Liquidity (Original Approach)")

        # Get open interest data like original
        from eth_defi.gmx.core.open_interest import GetOpenInterest

        open_interest = GetOpenInterest(self.config).get_data()

        # Get oracle prices once like original
        from eth_defi.gmx.core.oracle import OraclePrices

        prices = OraclePrices(self.config.chain).get_recent_prices()

        # Get available markets
        available_markets = self.markets.get_available_markets()

        for market_key in available_markets:
            self._get_token_addresses(market_key)
            if self._long_token_address is None or self._short_token_address is None:
                continue

            market_symbol = self.markets.get_market_symbol(market_key)

            # Skip if not in open interest data
            if market_symbol not in open_interest.get("long", {}) or market_symbol not in open_interest.get("short", {}):
                logger.warning(f"No open interest data for {market_symbol}")
                continue

            try:
                # Get decimal factors and precision - same as original
                long_decimal_factor = self.markets.get_decimal_factor(market_key=market_key, long=True)
                short_decimal_factor = self.markets.get_decimal_factor(market_key=market_key, long=False)
                long_precision = 10 ** (30 + long_decimal_factor)
                short_precision = 10 ** (30 + short_decimal_factor)
                oracle_precision = 10 ** (30 - long_decimal_factor)

                # Get reserved amounts from open interest
                reserved_long = open_interest["long"][market_symbol]
                reserved_short = open_interest["short"][market_symbol]

                # Get pool data using individual calls (like original)
                (long_pool_amount_call, long_reserve_factor_call, long_oi_reserve_factor_call) = self.get_max_reserved_usd(market_key, self._long_token_address, True)
                (short_pool_amount_call, short_reserve_factor_call, short_oi_reserve_factor_call) = self.get_max_reserved_usd(market_key, self._short_token_address, False)

                # Execute the calls (like original does with execute_threading)
                long_pool_amount = long_pool_amount_call.call()
                long_reserve_factor = long_reserve_factor_call.call()
                long_oi_reserve_factor = long_oi_reserve_factor_call.call()

                short_pool_amount = short_pool_amount_call.call()
                short_reserve_factor = short_reserve_factor_call.call()
                short_oi_reserve_factor = short_oi_reserve_factor_call.call()

                # Calculate token price exactly like original
                if self._long_token_address not in prices:
                    logger.warning(f"No oracle price for {self._long_token_address} in {market_symbol}")
                    continue

                token_price = np.median(
                    [
                        float(prices[self._long_token_address]["maxPriceFull"]) / oracle_precision,
                        float(prices[self._long_token_address]["minPriceFull"]) / oracle_precision,
                    ]
                )

                logger.info(f"Token: {market_symbol}")

                # LONG LIQUIDITY - exact same logic as original
                long_reserve_factor = min(long_reserve_factor, long_oi_reserve_factor)

                if "2" in market_symbol:
                    long_pool_amount = long_pool_amount / 2

                long_max_reserved_tokens = long_pool_amount * long_reserve_factor
                long_max_reserved_usd = long_max_reserved_tokens / long_precision * token_price
                long_liquidity = long_max_reserved_usd - float(reserved_long)

                logger.info(f"Available Long Liquidity: ${long_liquidity:,.2f}")

                # SHORT LIQUIDITY - exact same logic as original
                short_reserve_factor = min(short_reserve_factor, short_oi_reserve_factor)
                short_max_reserved_usd = short_pool_amount * short_reserve_factor
                short_liquidity = short_max_reserved_usd / short_precision - float(reserved_short)

                # Special handling for single side markets
                if "2" in market_symbol:
                    short_pool_amount = short_pool_amount / 2
                    short_max_reserved_tokens = short_pool_amount * short_reserve_factor
                    short_max_reserved_usd = short_max_reserved_tokens / short_precision * token_price
                    short_liquidity = short_max_reserved_usd - float(reserved_short)

                logger.info(f"Available Short Liquidity: ${short_liquidity:,.2f}")

                # Store results
                self.output["long"][market_symbol] = long_liquidity
                self.output["short"][market_symbol] = short_liquidity

            except Exception as e:
                logger.error(f"Failed to process market {market_symbol}: {e}")
                continue

        self.output["parameter"] = "available_liquidity"
        return self.output

    def _get_data_processing_multicall(self) -> PositionSideData:
        """Generate the dictionary of available liquidity using efficient multicall batching.

        :returns: Dictionary of available liquidity data with structure:
            {
                "long": {market_symbol: liquidity_value, ...},
                "short": {market_symbol: liquidity_value, ...},
                "parameter": "available_liquidity"
            }
        :rtype: dict
        """
        logger.debug("GMX v2 Available Liquidity using Multicall")

        # Get open interest data
        open_interest = GetOpenInterest(self.config).get_data()

        # Generate all multicall requests
        logger.debug("Generating multicall requests...")
        encoded_calls = list(self.generate_all_multicalls())
        logger.debug(f"Generated {len(encoded_calls)} multicall requests")

        # Create Web3Factory for multicall execution
        web3_factory = TunedWeb3Factory(rpc_config_line=self.config.web3.provider.endpoint_uri)

        # Execute all multicalls efficiently
        logger.debug("Executing multicalls...")
        multicall_results: dict[str, dict[str, EncodedCallResult]] = defaultdict(dict)

        for call_result in read_multicall_chunked(
            chain_id=self.config.web3.eth.chain_id,
            web3factory=web3_factory,
            calls=encoded_calls,
            block_identifier="latest",
            progress_bar_desc="Loading GMX liquidity data",
            max_workers=5,  # TODO: Make it dynamic
        ):
            market_key = call_result.call.extra_data["market_key"]
            data_type = call_result.call.extra_data["data_type"]
            multicall_results[market_key][data_type] = call_result

        logger.debug(f"Processed multicalls for {len(multicall_results)} markets")

        # Process results and calculate available liquidity
        available_markets = self.markets.get_available_markets()
        logger.debug(f"Processing {len(available_markets)} available markets")

        processed_count = 0
        for market_key in available_markets:
            if market_key not in multicall_results:
                logger.warning(f"No multicall results for market {market_key}")
                continue

            market_results = multicall_results[market_key]
            logger.debug(f"Processing market {market_key} with {len(market_results)} results")

            # Get market metadata
            self._get_token_addresses(market_key)
            if self._long_token_address is None or self._short_token_address is None:
                logger.warning(f"Skipping market {market_key} due to missing token addresses")
                continue

            market_symbol = self.markets.get_market_symbol(market_key)
            logger.debug(f"Processing market symbol: {market_symbol}")

            try:
                # Get decimal factors and precision values
                long_decimal_factor = self.markets.get_decimal_factor(market_key=market_key, long=True)
                short_decimal_factor = self.markets.get_decimal_factor(market_key=market_key, short=True)
                long_precision = 10 ** (30 + long_decimal_factor)
                short_precision = 10 ** (30 + short_decimal_factor)
                oracle_precision = 10 ** (30 - long_decimal_factor)

                # Extract multicall results with error handling
                def safe_extract_uint(result_key: str) -> int:
                    if result_key in market_results and market_results[result_key].success:
                        # Convert bytes result to integer (uint256)
                        result_bytes = market_results[result_key].result
                        value = int.from_bytes(result_bytes, byteorder="big") if result_bytes else 0
                        logger.debug(f"  {result_key}: {value} (bytes length: {len(result_bytes) if result_bytes else 0})")
                        return value
                    else:
                        logger.warning(f"Failed to get {result_key} for {market_symbol} - success: {market_results[result_key].success if result_key in market_results else 'missing'}")
                        return 0

                long_pool_amount = safe_extract_uint("long_pool_amount")
                short_pool_amount = safe_extract_uint("short_pool_amount")
                long_reserve_factor = safe_extract_uint("long_reserve_factor")
                short_reserve_factor = safe_extract_uint("short_reserve_factor")
                long_oi_reserve_factor = safe_extract_uint("long_oi_reserve_factor")
                short_oi_reserve_factor = safe_extract_uint("short_oi_reserve_factor")

                logger.debug(f"{market_symbol} extracted values:")
                logger.debug(f"  long_pool_amount={long_pool_amount}, short_pool_amount={short_pool_amount}")
                logger.debug(f"  long_reserve_factor={long_reserve_factor}, short_reserve_factor={short_reserve_factor}")
                logger.debug(f"  long_oi_reserve_factor={long_oi_reserve_factor}, short_oi_reserve_factor={short_oi_reserve_factor}")

                # Get oracle prices
                prices = OraclePrices(self.config.chain).get_recent_prices()
                if self._long_token_address not in prices:
                    logger.warning(f"No oracle price for {self._long_token_address} in {market_symbol}")
                    continue

                token_price = np.median(
                    [
                        float(prices[self._long_token_address]["maxPriceFull"]) / oracle_precision,
                        float(prices[self._long_token_address]["minPriceFull"]) / oracle_precision,
                    ]
                )
                logger.debug(f"{market_symbol}: token_price={token_price}, oracle_precision={oracle_precision}")

                # Get reserved amounts from open interest
                if market_symbol not in open_interest.get("long", {}) or market_symbol not in open_interest.get("short", {}):
                    logger.warning(f"No open interest data for {market_symbol}")
                    continue

                reserved_long = open_interest["long"][market_symbol]
                reserved_short = open_interest["short"][market_symbol]
                logger.debug(f"{market_symbol}: reserved_long={reserved_long}, reserved_short={reserved_short}")

                # Select the lesser of maximum value of pool reserves or open interest limit
                effective_long_reserve_factor = min(long_reserve_factor, long_oi_reserve_factor)
                effective_short_reserve_factor = min(short_reserve_factor, short_oi_reserve_factor)

                # Calculate available liquidity using original working formula
                # Long side calculation - match original logic
                if "2" in market_symbol:
                    long_pool_amount = long_pool_amount / 2

                long_max_reserved_tokens = long_pool_amount * effective_long_reserve_factor
                long_max_reserved_usd = long_max_reserved_tokens / long_precision * token_price
                long_available_usd = long_max_reserved_usd - reserved_long

                # Short side calculation - match original logic
                if "2" in market_symbol:
                    # For single side markets, calculate on token amount like original
                    short_pool_amount = short_pool_amount / 2
                    short_max_reserved_tokens = short_pool_amount * effective_short_reserve_factor
                    short_max_reserved_usd = short_max_reserved_tokens / short_precision * token_price
                    short_available_usd = short_max_reserved_usd - reserved_short
                else:
                    # For regular markets, use USD calculation like original
                    short_max_reserved_usd = short_pool_amount * effective_short_reserve_factor
                    short_available_usd = short_max_reserved_usd / short_precision - reserved_short

                logger.debug(f"{market_symbol} calculations:")
                logger.debug(f"  long_pool_amount={long_pool_amount}, long_precision={long_precision}")
                logger.debug(f"  effective_long_reserve_factor={effective_long_reserve_factor}")
                logger.debug(f"  short_pool_amount={short_pool_amount}, short_precision={short_precision}")
                logger.debug(f"  effective_short_reserve_factor={effective_short_reserve_factor}")
                # Store in output structure
                self.output["long"][market_symbol] = long_available_usd
                self.output["short"][market_symbol] = short_available_usd
                processed_count += 1

                logger.debug(f"{market_symbol}: Long=${long_available_usd:,.2f}, Short=${short_available_usd:,.2f}")

            except Exception as e:
                logger.error(f"Failed to process market {market_symbol}: {e}")
                continue

        logger.info(f"Successfully processed {processed_count} markets out of {len(available_markets)}")

        # Add parameter identifier for compatibility
        self.output["parameter"] = "available_liquidity"

        total_long = sum(v for v in self.output["long"].values() if isinstance(v, (int, float)))
        total_short = sum(v for v in self.output["short"].values() if isinstance(v, (int, float)))

        logger.debug(f"Liquidity calculation complete: Total Long=${total_long:,.2f}, Total Short=${total_short:,.2f}, Markets processed: {len(self.output['long'])}")

        return self.output

    def get_max_reserved_usd(self, market: str, token: str, is_long: bool) -> tuple:
        """For a given market, long/short token and pool direction get the
        uncalled web3 functions to calculate pool size, pool reserve factor
        and open interest reserve factor.

        :param market: contract address of GMX market
        :type market: str
        :param token: contract address of long or short token
        :type token: str
        :param is_long: pass True for long pool or False for short
        :type is_long: bool
        :returns: tuple containing:
            - pool_amount: uncalled web3 contract object for pool amount
            - reserve_factor: uncalled web3 contract object for pool reserve factor
            - open_interest_reserve_factor: uncalled web3 contract object for open interest reserve factor
        :rtype: tuple
        """
        from typing import Any

        # get web3 datastore object
        datastore = get_datastore_contract(self.config.web3, self.config.chain)

        # get hashed keys for datastore
        pool_amount_hash_data = pool_amount_key(market, token)
        reserve_factor_hash_data = reserve_factor_key(market, is_long)
        open_interest_reserve_factor_hash_data = open_interest_reserve_factor_key(market, is_long)

        pool_amount = datastore.functions.getUint(pool_amount_hash_data)
        reserve_factor = datastore.functions.getUint(reserve_factor_hash_data)
        open_interest_reserve_factor = datastore.functions.getUint(open_interest_reserve_factor_hash_data)

        return pool_amount, reserve_factor, open_interest_reserve_factor
