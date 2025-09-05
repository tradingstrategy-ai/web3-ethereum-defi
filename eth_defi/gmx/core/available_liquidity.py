"""
GMX Available Liquidity Data Retrieval Module

This module provides available liquidity data for GMX protocol markets
using efficient multicall batching instead of individual contract calls.
"""

import logging
from dataclasses import dataclass
from typing import Any, Iterable
from collections import defaultdict

import numpy as np

from eth_typing import HexAddress
from web3 import Web3

from eth_defi.event_reader.multicall_batcher import EncodedCall, read_multicall_chunked, EncodedCallResult
from eth_defi.event_reader.web3factory import Web3Factory, TunedWeb3Factory
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_datastore_contract
from eth_defi.gmx.core.get_data import GetData
from eth_defi.gmx.core.open_interest import GetOpenInterest
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.keys import pool_amount_key, open_interest_reserve_factor_key, reserve_factor_key
from eth_defi.provider.multi_provider import MultiProviderWeb3


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
    GMX available liquidity data retrieval class using efficient multicall batching.

    This class retrieves available liquidity information for all available GMX markets,
    with efficient multicall batching for better performance and reduced RPC usage.

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

        # Get DataStore contract address for multicalls
        self.datastore_address = get_datastore_contract(self.config.web3, self.config.chain).address

    def encode_multicalls_for_market(self, market_key: str, long_token: str, short_token: str) -> Iterable[EncodedCall]:
        """
        Generate multicall requests for a single market.

        For each market we need 6 calls:
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
        get_uint_signature = Web3.keccak(text="getUint(bytes32)")[:4]

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
            yield EncodedCall.from_keccak_signature(address=self.datastore_address, signature=get_uint_signature, function=func_name, data=key_bytes, extra_data={"market_key": market_key, "data_type": func_name})

    def generate_all_multicalls(self) -> Iterable[EncodedCall]:
        """
        Generate all multicall requests for all markets.

        :return: Iterable of all EncodedCall objects needed
        """
        available_markets = self.markets.get_available_markets()

        for market_key in available_markets:
            self._get_token_addresses(market_key)
            yield from self.encode_multicalls_for_market(market_key, self._long_token_address, self._short_token_address)

    def _get_data_processing(self) -> dict[str, Any]:
        """
        Generate the dictionary of available liquidity using efficient multicall batching.

        Returns
        -------
        available_liquidity: dict
            dictionary of available liquidity data with structure:
            {
                "long": {market_symbol: liquidity_value, ...},
                "short": {market_symbol: liquidity_value, ...},
                "parameter": "available_liquidity"
            }
        """
        self.log.debug("GMX v2 Available Liquidity using Multicall")

        # Get open interest data
        open_interest = GetOpenInterest(self.config).get_data()

        # Generate all multicall requests
        self.log.debug("Generating multicall requests...")
        encoded_calls = list(self.generate_all_multicalls())
        self.log.debug(f"Generated {len(encoded_calls)} multicall requests")

        # Create Web3Factory for multicall execution
        web3_factory = TunedWeb3Factory(rpc_config_line=self.config.web3.provider.endpoint_uri)

        # Execute all multicalls efficiently
        self.log.debug("Executing multicalls...")
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

        self.log.debug(f"Processed multicalls for {len(multicall_results)} markets")

        # Process results and calculate available liquidity
        available_markets = self.markets.get_available_markets()

        for market_key in available_markets:
            if market_key not in multicall_results:
                self.log.warning(f"No multicall results for market {market_key}")
                continue

            market_results = multicall_results[market_key]

            # Get market metadata
            self._get_token_addresses(market_key)
            market_symbol = self.markets.get_market_symbol(market_key)

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
                        return int.from_bytes(result_bytes, byteorder="big") if result_bytes else 0
                    else:
                        self.log.warning(f"Failed to get {result_key} for {market_symbol}")
                        return 0

                long_pool_amount = safe_extract_uint("long_pool_amount")
                short_pool_amount = safe_extract_uint("short_pool_amount")
                long_reserve_factor = safe_extract_uint("long_reserve_factor")
                short_reserve_factor = safe_extract_uint("short_reserve_factor")
                long_oi_reserve_factor = safe_extract_uint("long_oi_reserve_factor")
                short_oi_reserve_factor = safe_extract_uint("short_oi_reserve_factor")

                # Get oracle prices
                prices = OraclePrices(self.config.chain).get_recent_prices()
                if self._long_token_address not in prices:
                    self.log.warning(f"No oracle price for {self._long_token_address} in {market_symbol}")
                    continue

                token_price = np.median(
                    [
                        float(prices[self._long_token_address]["maxPriceFull"]) / oracle_precision,
                        float(prices[self._long_token_address]["minPriceFull"]) / oracle_precision,
                    ]
                )

                # Get reserved amounts from open interest
                if market_symbol not in open_interest.get("long", {}) or market_symbol not in open_interest.get("short", {}):
                    self.log.warning(f"No open interest data for {market_symbol}")
                    continue

                reserved_long = open_interest["long"][market_symbol]
                reserved_short = open_interest["short"][market_symbol]

                # Select the lesser of maximum value of pool reserves or open interest limit
                effective_long_reserve_factor = min(long_reserve_factor, long_oi_reserve_factor)
                effective_short_reserve_factor = min(short_reserve_factor, short_oi_reserve_factor)

                # Special handling for certain market types
                if "2" in market_symbol:
                    long_pool_amount = long_pool_amount / 2
                    short_pool_amount = short_pool_amount / 2

                # Calculate available liquidity
                # Long side calculation
                long_reserved_usd = (long_pool_amount / long_precision) * effective_long_reserve_factor * token_price / 10**30
                long_available_usd = max(0, long_reserved_usd - reserved_long)

                # Short side calculation
                short_reserved_usd = (short_pool_amount / short_precision) * effective_short_reserve_factor * token_price / 10**30
                short_available_usd = max(0, short_reserved_usd - reserved_short)

                # Store in output structure
                self.output["long"][market_symbol] = long_available_usd
                self.output["short"][market_symbol] = short_available_usd

                self.log.debug(f"{market_symbol}: Long=${long_available_usd:,.2f}, Short=${short_available_usd:,.2f}")

            except Exception as e:
                self.log.error(f"Failed to process market {market_symbol}: {e}")
                continue

        # Add parameter identifier for compatibility
        self.output["parameter"] = "available_liquidity"

        total_long = sum(v for v in self.output["long"].values() if isinstance(v, (int, float)))
        total_short = sum(v for v in self.output["short"].values() if isinstance(v, (int, float)))

        self.log.debug(f"Liquidity calculation complete: Total Long=${total_long:,.2f}, Total Short=${total_short:,.2f}, Markets processed: {len(self.output['long'])}")

        return self.output
