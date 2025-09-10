"""
GMX Claimable Fees Data Retrieval Module.

This module provides claimable fees data for GMX protocol markets. Optimised performance using multicall batching.
"""

import logging

logger = logging.getLogger(__name__)
import numpy as np
from typing import Any, Iterable
from collections import defaultdict

from eth_utils import keccak

from eth_defi.event_reader.multicall_batcher import EncodedCall, read_multicall_chunked, EncodedCallResult
from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.get_data import GetData
from eth_defi.gmx.keys import claimable_fee_amount_key
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.types import MarketData


class GetClaimableFees(GetData):
    """GMX claimable fees data retrieval with multicall optimization.

    Retrieves claimable fees information for all available GMX markets
    using multicall batching for better performance.
    """

    def __init__(self, config: GMXConfig):
        """Initialize claimable fees data retrieval.

        :param config: GMXConfig instance containing chain and network info
        """
        super().__init__(config)
        self.oracle_prices = OraclePrices(chain=config.chain).get_recent_prices()

    def _get_data_processing(self) -> MarketData:
        """Implementation of abstract method from GetData base class.

        :return: Claimable fees data dictionary
        """
        return self.get_claimable_fees()

    def get_claimable_fees(self) -> MarketData:
        """Get claimable fees data using multicall optimization.

        Uses multicall batching to query all claimable fee amounts in a single
        RPC call, significantly improving performance compared to sequential calls.

        :returns: Dictionary containing total claimable fees
        :rtype: dict
        """
        market_fees = self.get_per_market_claimable_fees()

        # Calculate total fees
        total_fees = sum(fee_data["total"] for fee_data in market_fees.values())

        return {"total_fees": total_fees, "parameter": "total_fees"}

    def get_per_market_claimable_fees(self) -> dict[str, dict[str, Any]]:
        """Get detailed claimable fees data for each market.

        :returns: Dictionary of market symbol to fee details
        :rtype: dict
        """
        logger.debug("GMX v2 Claimable Fees using Multicall")

        # Get available markets
        available_markets = self.markets.get_available_markets()
        if not available_markets:
            logger.warning("No markets available")
            return {}

        # Generate all multicall requests
        logger.debug("Generating multicall requests...")
        encoded_calls = list(self.generate_all_multicalls(available_markets))
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
            progress_bar_desc="Loading claimable fees data",
            max_workers=5,
        ):
            market_key = call_result.call.extra_data["market_key"]
            token_type = call_result.call.extra_data["token_type"]
            multicall_results[market_key][token_type] = call_result

        logger.debug(f"Processed multicalls for {len(multicall_results)} markets")

        # Process results
        market_fees = {}
        for market_key in available_markets:
            if market_key not in multicall_results:
                logger.warning(f"No multicall results for market {market_key}")
                continue

            self._get_token_addresses(market_key)
            market_symbol = self.markets.get_market_symbol(market_key)

            try:
                # Extract fees with error handling
                results = multicall_results[market_key]

                def safe_extract_fee(token_type: str) -> int:
                    if token_type in results and results[token_type].success:
                        result_bytes = results[token_type].result
                        return int.from_bytes(result_bytes, byteorder="big") if result_bytes else 0
                    else:
                        logger.warning(f"Failed to get {token_type} fees for {market_symbol}")
                        return 0

                long_claimable_fees = safe_extract_fee("long")
                short_claimable_fees = safe_extract_fee("short")

                # Calculate USD values
                long_decimal_factor = self.markets.get_decimal_factor(market_key=market_key, long=True)
                long_precision = 10 ** (long_decimal_factor - 1)
                oracle_precision = 10 ** (30 - long_decimal_factor)

                # Get long token price
                token_data = self.oracle_prices.get(self._long_token_address)
                if token_data:
                    long_token_price = np.median(
                        [
                            float(token_data["maxPriceFull"]) / oracle_precision,
                            float(token_data["minPriceFull"]) / oracle_precision,
                        ]
                    )
                else:
                    logger.warning(f"No oracle price data for token {self._long_token_address}")
                    long_token_price = 1.0  # Fallback

                # Convert to USD
                long_claimable_usd = (long_claimable_fees / long_precision) * long_token_price

                # Short fees are collected in USDC (6 decimals)
                short_claimable_usd = short_claimable_fees / (10**6)

                # Special handling for certain market types
                if "2" in market_symbol:
                    short_claimable_usd = 0

                # Store market fees
                market_fees[market_symbol] = {"long": long_claimable_usd, "short": short_claimable_usd, "total": long_claimable_usd + short_claimable_usd, "long_token": self._long_token_address, "short_token": self._short_token_address, "long_raw": long_claimable_fees, "short_raw": short_claimable_fees}

                logger.debug(f"{market_symbol} claimable fees: ${long_claimable_usd + short_claimable_usd:,.2f}")

            except Exception as e:
                logger.error(f"Failed to process market {market_symbol}: {e}")
                continue

        return market_fees

    def generate_all_multicalls(self, markets: dict[str, Any]) -> Iterable[EncodedCall]:
        """Generate all multicall requests for all markets.

        :param markets: Dictionary of available markets
        :return: Iterable of all EncodedCall objects needed
        """
        # DataStore.getUint() function signature: getUint(bytes32)
        get_uint_signature = keccak(text="getUint(bytes32)")[:4]

        for market_key in markets:
            self._get_token_addresses(market_key)

            # Create keys for DataStore queries
            long_key = claimable_fee_amount_key(market_key, self._long_token_address)
            short_key = claimable_fee_amount_key(market_key, self._short_token_address)

            # Create encoded calls for each token fee amount
            yield EncodedCall.from_keccak_signature(
                address=self.datastore_contract.address,
                signature=get_uint_signature,
                function="long_fees",
                data=long_key,
                extra_data={"market_key": market_key, "token_type": "long"},
            )

            yield EncodedCall.from_keccak_signature(
                address=self.datastore_contract.address,
                signature=get_uint_signature,
                function="short_fees",
                data=short_key,
                extra_data={"market_key": market_key, "token_type": "short"},
            )
