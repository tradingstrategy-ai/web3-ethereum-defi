"""
GMX Pool TVL Data Retrieval Module

This module provides pool TVL data for GMX protocol markets.
"""

import logging

import numpy as np
from typing import Iterable, Optional
from collections import defaultdict

from eth_utils import keccak

from eth_defi.event_reader.multicall_batcher import EncodedCall, read_multicall_chunked, EncodedCallResult
from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.get_data import GetData
from eth_defi.gmx.keys import pool_amount_key
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.types import TVLData

logger = logging.getLogger(__name__)


class GetPoolTVL(GetData):
    """GMX pool TVL data retrieval using multicall optimization.

    Retrieves pool TVL information for all available GMX markets using
    multicall batching for better performance while maintaining identical results
    to the original gmx-python-sdk implementation.
    """

    def __init__(self, config: GMXConfig):
        """Initialize pool TVL data retrieval.

        :param config: GMXConfig instance containing chain and network info
        """
        super().__init__(config)
        self.oracle_prices = OraclePrices(chain=config.chain).get_recent_prices()

    def _get_data_processing(self) -> TVLData:
        """Implementation of abstract method from GetData base class.

        :return: Pool TVL data dictionary
        """
        return self.get_pool_balances()

    def get_pool_balances(self) -> Optional[TVLData]:
        """Get pool balances using DataStore contract with multicall optimization.

        This method uses multicall batching to query all pool amounts in a single
        RPC call, significantly improving performance compared to sequential calls.

        :return: Dictionary of total USD value per pool with structure:
            {
                "MARKET_SYMBOL": {
                    "total_tvl": float,
                    "long_token": str (address),
                    "short_token": str (address)
                }
            }
        """
        logger.debug("GMX v2 Pool TVL using Multicall")

        # Get available markets
        markets = self.markets.get_available_markets()
        if not markets:
            logger.debug("No markets available")
            return {}

        # Generate all multicall requests
        logger.debug("Generating multicall requests...")
        encoded_calls = list(self.generate_all_multicalls(markets))
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
            progress_bar_desc="Loading pool TVL data",
            max_workers=5,
        ):
            market_key = call_result.call.extra_data["market_key"]
            token_type = call_result.call.extra_data["token_type"]
            multicall_results[market_key][token_type] = call_result

        logger.debug(f"Processed multicalls for {len(multicall_results)} markets")

        # Process results
        pool_tvl_dict = {}
        for market_key, market_data in markets.items():
            if market_key not in multicall_results:
                logger.debug(f"No multicall results for market {market_key}")
                continue

            market_symbol = market_data["market_symbol"]
            self._get_token_addresses(market_key)

            try:
                # Extract balances with error handling
                results = multicall_results[market_key]

                def safe_extract_balance(token_type: str) -> int:
                    if token_type in results and results[token_type].success:
                        result_bytes = results[token_type].result
                        return int.from_bytes(result_bytes, byteorder="big") if result_bytes else 0
                    else:
                        logger.debug(f"Failed to get {token_type} balance for {market_symbol}")
                        return 0

                long_balance = safe_extract_balance("long")
                short_balance = safe_extract_balance("short")

                # Calculate USD values
                oracle_precision = 10 ** (30 - market_data["long_token_metadata"]["decimals"])
                long_usd = self._calculate_usd_value(self._long_token_address, long_balance, oracle_precision)

                short_oracle_precision = 10 ** (30 - market_data["short_token_metadata"]["decimals"])
                short_usd = self._calculate_usd_value(self._short_token_address, short_balance, short_oracle_precision)

                # Store result
                pool_tvl_dict[market_symbol] = {
                    "total_tvl": long_usd + short_usd,
                    "long_token": self._long_token_address,
                    "short_token": self._short_token_address,
                }

                logger.debug(f"{market_symbol} TVL: ${pool_tvl_dict[market_symbol]['total_tvl']:,.2f}")

            except Exception as e:
                logger.error(f"Failed to process market {market_symbol}: {e}")
                continue

        return pool_tvl_dict

    def generate_all_multicalls(self, markets: dict) -> Iterable[EncodedCall]:
        """Generate all multicall requests for all markets.

        :param markets: Dictionary of available markets
        :return: Iterable of all EncodedCall objects needed
        """
        # DataStore.getUint() function signature: getUint(bytes32)
        get_uint_signature = keccak(text="getUint(bytes32)")[:4]

        for market_key in markets:
            self._get_token_addresses(market_key)

            # Create keys for DataStore queries
            long_key = pool_amount_key(market_key, self._long_token_address)
            short_key = pool_amount_key(market_key, self._short_token_address)

            # Create encoded calls for each token balance
            yield EncodedCall.from_keccak_signature(address=self.datastore_contract.address, signature=get_uint_signature, function="long_balance", data=long_key, extra_data={"market_key": market_key, "token_type": "long"})

            yield EncodedCall.from_keccak_signature(address=self.datastore_contract.address, signature=get_uint_signature, function="short_balance", data=short_key, extra_data={"market_key": market_key, "token_type": "short"})

    def _calculate_usd_value(self, token_address: str, token_balance: int, oracle_precision: int) -> float:
        """
        Calculate USD value exactly like original gmx-python-sdk.

        Uses median of oracle max/min prices with proper precision handling.
        """
        try:
            token_data = self.oracle_prices.get(token_address)
            if not token_data:
                logger.debug(f"No oracle price data for token {token_address}")
                return float(token_balance)

            token_price = np.median(
                [
                    float(token_data["maxPriceFull"]) / oracle_precision,
                    float(token_data["minPriceFull"]) / oracle_precision,
                ]
            )
            return token_price * token_balance
        except (KeyError, TypeError, ValueError) as e:
            logger.debug(f"Error calculating USD value for {token_address}: {e}")
            return float(token_balance)
