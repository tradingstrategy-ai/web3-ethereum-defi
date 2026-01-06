"""
GMX GLV Stats Data Retrieval Module.

This module provides GLV statistics data for GMX protocol using efficient
multicall batching.
"""

import logging

logger = logging.getLogger(__name__)
from functools import cached_property
from typing import Any, Optional
from collections import defaultdict

from eth_abi import encode
from eth_utils import keccak, to_checksum_address
from eth_typing import HexAddress

from eth_defi.event_reader.multicall_batcher import EncodedCall, read_multicall_chunked, EncodedCallResult
from eth_defi.event_reader.web3factory import TunedWeb3Factory
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.get_data import GetData
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.contracts import get_glv_reader_contract
from eth_defi.gmx.types import MarketData
from eth_defi.compat import encode_abi_compat
from eth_defi.gmx.keys import MAX_PNL_FACTOR_FOR_TRADERS


class GlvStats(GetData):
    """GMX GLV statistics data retrieval with multicall optimization.

    Retrieves GLV information including prices and composition
    using multicall batching for better performance.
    """

    def __init__(self, config: GMXConfig, filter_swap_markets: bool = True):
        """Initialize GLV stats data retrieval.

        :param config: GMXConfig instance containing chain and network info
        :param filter_swap_markets: Whether to filter out swap markets from results
        """
        super().__init__(config, filter_swap_markets)

    @cached_property
    def glv_reader_contract(self):
        """GLV Reader contract instance for GLV data queries."""
        return get_glv_reader_contract(self.config.web3, self.config.chain)

    def get_glv_stats(self) -> MarketData:
        """Get GLV statistics using multicall optimization.

        :return: Dictionary containing GLV statistics
        """
        return self.get_glv_stats_multicall()

    def get_glv_stats_multicall(self) -> dict[str, Any]:
        """
        Get GLV statistics data using multicall optimization.

        This method uses multicall batching to query all GLV data in fewer
        RPC calls, significantly improving performance.

        :return: Dictionary containing GLV statistics
        :rtype: dict[str, Any]
        """
        # Return cached data if available
        if self._data_cache is not None:
            logger.debug("Returning cached GLV stats data")
            return self._data_cache

        logger.debug("GMX v2 GLV Stats using Multicall")

        # Get oracle prices once for all markets
        oracle = OraclePrices(self.config.chain)
        self._oracle_prices_cache = oracle.get_recent_prices()

        # Get GLV info list (already batched by the contract)
        glv_info_dict = self._get_glv_info_list()

        if not glv_info_dict:
            logger.debug("No GLV markets available")
            return {}

        # Generate all multicall requests
        logger.debug("Generating multicall requests...")
        encoded_calls, call_metadata = self.generate_all_multicalls(glv_info_dict)
        logger.debug("Generated %s multicall requests", len(encoded_calls))

        if not encoded_calls:
            logger.debug("No valid multicall requests generated")
            return glv_info_dict

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
            progress_bar_desc="Loading GMX GLV statistics",
            max_workers=5,
        ):
            glv_address = call_result.call.extra_data["glv_address"]
            call_type = call_result.call.extra_data["call_type"]
            call_key = call_result.call.extra_data.get("call_key", "default")

            # Enhanced debugging for failed calls
            if not call_result.success:
                logger.debug("Multicall failed - GLV: %s, Type: %s, Key: %s, Error: %s", glv_address, call_type, call_key, getattr(call_result, "error", "Unknown"))
            else:
                logger.debug("Multicall success - GLV: %s, Type: %s, Key: %s, Result length: %s", glv_address, call_type, call_key, len(call_result.result) if call_result.result else 0)
            multicall_results[glv_address][f"{call_type}_{call_key}"] = call_result

        logger.debug("Processed multicalls for %s GLVs", len(multicall_results))

        # Process results and build final GLV statistics
        result = self.process_multicall_results(glv_info_dict, multicall_results, call_metadata)

        # Cache the result for future calls
        self._data_cache = result

        return result

    def generate_all_multicalls(self, glv_info_dict: dict) -> tuple[list[EncodedCall], dict]:
        """
        Generate all multicall requests for GLV statistics.

        For each GLV, we need:
        - getGlvTokenPrice() call
        - balanceOf() calls for each market composition
        - getMarketTokenPrice() calls for each market (GM prices)

        :param glv_info_dict: Dictionary of GLV information
        :return: Tuple of (encoded_calls, call_metadata)
        """
        encoded_calls = []
        call_metadata = {}

        for glv_address, glv_info in glv_info_dict.items():
            try:
                logger.debug("Processing GLV %s with %s markets", glv_address, len(glv_info["glv_market_addresses"]))

                # Prepare price data for this GLV
                index_token_prices = []
                long_token_price = None
                short_token_price = None
                valid_markets_count = 0

                # Build price data for all markets in this GLV
                for market_address in glv_info["glv_market_addresses"]:
                    index_token_address = self.markets.get_index_token_address(market_address)

                    # Get oracle prices for this market
                    oracle_prices = self._build_oracle_prices_tuple(market_address, index_token_address)
                    if oracle_prices:
                        index_token_prices.append(oracle_prices[0])  # Index token price
                        # Use the last market's long/short prices for GLV price calculation
                        long_token_price = oracle_prices[1]
                        short_token_price = oracle_prices[2]
                        valid_markets_count += 1
                        logger.debug("Valid oracle prices found for market %s", market_address)
                    else:
                        logger.debug("No valid oracle prices for market %s", market_address)

                logger.debug("GLV %s: %s/%s markets have valid oracle prices", glv_address, valid_markets_count, len(glv_info["glv_market_addresses"]))

                # Generate GLV token price call
                if index_token_prices and long_token_price and short_token_price:
                    glv_price_call = self.create_glv_token_price_call(glv_address, glv_info["glv_market_addresses"], index_token_prices, long_token_price, short_token_price)
                    if glv_price_call:
                        encoded_calls.append(glv_price_call)
                        logger.debug("Added GLV price call for %s", glv_address)
                    else:
                        logger.debug("Failed to create GLV price call for %s", glv_address)
                else:
                    logger.debug("Insufficient price data for GLV %s: index_prices=%s, long_price=%s, short_price=%s", glv_address, len(index_token_prices), long_token_price is not None, short_token_price is not None)

                # Generate balance calls for GLV composition
                balance_calls_created = 0
                for market_address in glv_info["glv_market_addresses"]:
                    balance_call = self.create_balance_of_call(glv_address, market_address)
                    if balance_call:
                        encoded_calls.append(balance_call)
                        balance_calls_created += 1
                logger.debug("Created %s balance calls for GLV %s", balance_calls_created, glv_address)

                # Generate GM price calls for each market
                gm_price_calls_created = 0
                for market_address in glv_info["glv_market_addresses"]:
                    gm_price_call = self.create_gm_price_call(glv_address, market_address)
                    if gm_price_call:
                        encoded_calls.append(gm_price_call)
                        gm_price_calls_created += 1
                    else:
                        logger.debug("Failed to create GM price call for market %s in GLV %s", market_address, glv_address)
                logger.debug("Created %s GM price calls for GLV %s", gm_price_calls_created, glv_address)

                # Store metadata for processing
                call_metadata[glv_address] = {
                    "glv_info": glv_info,
                    "index_token_prices": index_token_prices,
                    "long_token_price": long_token_price,
                    "short_token_price": short_token_price,
                }

            except Exception as e:
                logger.error("Failed to generate multicalls for GLV %s: %s", glv_address, e)
                continue

        return encoded_calls, call_metadata

    def create_glv_token_price_call(self, glv_address: str, market_addresses: list, index_token_prices: list, long_token_price: tuple, short_token_price: tuple) -> Optional[EncodedCall]:
        """Create multicall for GLV token price."""
        try:
            # Use encode_abi_compat for getGlvTokenPrice
            # The correct parameter order is:
            # (dataStore, marketAddresses, indexTokenPrices, longTokenPrice, shortTokenPrice, glv, maximize)
            call_data = encode_abi_compat(
                self.glv_reader_contract,
                "getGlvTokenPrice",
                [
                    to_checksum_address(self.datastore_contract.address),
                    [to_checksum_address(addr) for addr in market_addresses],
                    index_token_prices,
                    long_token_price,
                    short_token_price,
                    to_checksum_address(glv_address),
                    True,  # maximize
                ],
            )

            # Handle both bytes and string returns from encode_abi_compat
            if isinstance(call_data, str):
                # Remove '0x' prefix if present and convert to bytes
                call_data = call_data.replace("0x", "")
                data_bytes = bytes.fromhex(call_data) if call_data else b""
            else:
                data_bytes = call_data if isinstance(call_data, bytes) else b""

            return EncodedCall(
                address=to_checksum_address(self.glv_reader_contract.address),
                data=data_bytes,
                func_name="getGlvTokenPrice",
                extra_data={
                    "glv_address": glv_address,
                    "call_type": "glv_price",
                    "call_key": "default",
                },
            )
        except Exception as e:
            logger.error("Failed to create GLV token price call for %s: %s", glv_address, e)
            return None

    def create_balance_of_call(self, glv_address: str, market_address: str) -> Optional[EncodedCall]:
        """Create multicall for ERC20 balanceOf."""
        try:
            # balanceOf(address) signature
            balance_of_sig = keccak(text="balanceOf(address)")[:4]

            call_data = encode(["address"], [to_checksum_address(glv_address)])

            return EncodedCall.from_keccak_signature(
                address=to_checksum_address(market_address),  # Market token contract
                signature=balance_of_sig,
                function="balanceOf",
                data=call_data if isinstance(call_data, bytes) else bytes.fromhex(call_data),
                extra_data={
                    "glv_address": glv_address,
                    "call_type": "balance",
                    "call_key": market_address,
                },
            )
        except Exception as e:
            logger.error("Failed to create balance call for GLV %s, market %s: %s", glv_address, market_address, e)
            return None

    def create_gm_price_call(self, glv_address: str, market_address: str) -> Optional[EncodedCall]:
        """Create multicall for GM token price."""
        try:
            # Get token addresses for this market
            try:
                long_token_address = self.markets.get_long_token_address(market_address)
                short_token_address = self.markets.get_short_token_address(market_address)
                index_token_address = self.markets.get_index_token_address(market_address)

                # Validate that we have all required token addresses
                if not long_token_address or not short_token_address or not index_token_address:
                    logger.debug("Skipping GM price call for market %s due to missing token addresses", market_address)
                    return None

            except (ValueError, TypeError) as e:
                logger.debug("Skipping GM price call for market %s: %s", market_address, e)
                return None

            # Build oracle prices for this market
            oracle_prices = self._build_oracle_prices_tuple(market_address, index_token_address)
            if not oracle_prices:
                logger.debug("Skipping GM price call for market %s due to missing oracle prices", market_address)
                return None

            # Validate that all required addresses are checksummed
            try:
                market_address = to_checksum_address(market_address)
                index_token_address = to_checksum_address(index_token_address)
                long_token_address = to_checksum_address(long_token_address)
                short_token_address = to_checksum_address(short_token_address)
            except Exception as e:
                logger.debug("Skipping GM price call for market %s due to invalid addresses: %s", market_address, e)
                return None

            # Use encode_abi_compat for getMarketTokenPrice
            call_data = encode_abi_compat(
                self.reader_contract,
                "getMarketTokenPrice",
                [
                    to_checksum_address(self.datastore_contract.address),
                    [
                        market_address,
                        index_token_address,
                        long_token_address,
                        short_token_address,
                    ],
                    oracle_prices[0],  # index token price
                    oracle_prices[1],  # long token price
                    oracle_prices[2],  # short token price
                    MAX_PNL_FACTOR_FOR_TRADERS,
                    True,  # maximize
                ],
            )

            # Handle both bytes and string returns from encode_abi_compat
            if isinstance(call_data, str):
                # Remove '0x' prefix if present and convert to bytes
                call_data = call_data.replace("0x", "")
                data_bytes = bytes.fromhex(call_data) if call_data else b""
            else:
                data_bytes = call_data if isinstance(call_data, bytes) else b""

            return EncodedCall(
                address=to_checksum_address(self.reader_contract.address),
                data=data_bytes,
                func_name="getMarketTokenPrice",
                extra_data={
                    "glv_address": glv_address,
                    "call_type": "gm_price",
                    "call_key": market_address,
                },
            )
        except Exception as e:
            logger.debug("Failed to create GM price call for market %s: %s", market_address, e)
            return None

    def _build_oracle_prices_tuple(self, market_address: str, index_token_address: HexAddress) -> Optional[tuple]:
        """Build oracle prices tuple for a market."""
        try:
            oracle_prices_dict = self._oracle_prices_cache
            logger.debug("Building oracle prices for market %s, cached oracle tokens: %s", market_address, list(oracle_prices_dict.keys()))

            # Get token addresses directly from the market
            try:
                long_token_address_raw = self.markets.get_long_token_address(market_address)
                short_token_address_raw = self.markets.get_short_token_address(market_address)

                logger.debug("Market %s tokens - index: %s, long: %s, short: %s", market_address, index_token_address, long_token_address_raw, short_token_address_raw)

                if not long_token_address_raw or not short_token_address_raw:
                    logger.debug("Invalid token addresses for market %s: long=%s, short=%s", market_address, long_token_address_raw, short_token_address_raw)
                    return None

                long_token_address = to_checksum_address(long_token_address_raw)
                short_token_address = to_checksum_address(short_token_address_raw)
                index_token_address = to_checksum_address(index_token_address)
            except (ValueError, TypeError) as e:
                logger.debug("Failed to get token addresses for market %s: %s", market_address, e)
                return None

            # Check if all required addresses have oracle data
            if index_token_address not in oracle_prices_dict:
                logger.debug("Missing oracle data for index token: %s in market %s", index_token_address, market_address)
                return None
            if long_token_address not in oracle_prices_dict:
                logger.debug("Missing oracle data for long token: %s in market %s", long_token_address, market_address)
                return None

            try:
                # Try to get short token price, fallback to stable price if missing
                if short_token_address in oracle_prices_dict:
                    short_price = (
                        int(oracle_prices_dict[short_token_address]["minPriceFull"]),
                        int(oracle_prices_dict[short_token_address]["maxPriceFull"]),
                    )
                else:
                    # Fallback for stablecoins (typically USDC/USDT)
                    stable_price = (1000000000000000000000000000000, 1000000000000000000000000000000)  # $1 in 30 decimals
                    short_price = stable_price

                prices = (
                    # indexTokenPrice
                    (
                        int(oracle_prices_dict[index_token_address]["minPriceFull"]),
                        int(oracle_prices_dict[index_token_address]["maxPriceFull"]),
                    ),
                    # longTokenPrice
                    (
                        int(oracle_prices_dict[long_token_address]["minPriceFull"]),
                        int(oracle_prices_dict[long_token_address]["maxPriceFull"]),
                    ),
                    # shortTokenPrice
                    short_price,
                )

                # Validate that all prices are valid integers
                for price_tuple in prices:
                    if not isinstance(price_tuple, tuple) or len(price_tuple) != 2:
                        logger.debug("Invalid price tuple format: %s", price_tuple)
                        return None
                    for price in price_tuple:
                        if not isinstance(price, int) or price <= 0:
                            logger.debug("Invalid price value: %s", price)
                            return None

                return prices

            except (KeyError, ValueError, TypeError) as e:
                logger.debug("Failed to extract price data: %s", e)
                return None

        except Exception as e:
            logger.debug("Failed to build oracle prices tuple: %s", e)
            return None

    def process_multicall_results(self, glv_info_dict: dict, multicall_results: dict, call_metadata: dict) -> dict[str, Any]:
        """
        Process multicall results and build final GLV statistics.

        :param glv_info_dict: Original GLV information
        :param multicall_results: Results from multicall execution
        :param call_metadata: Metadata for processing calls
        :return: Complete GLV statistics dictionary
        """
        for glv_address, glv_info in glv_info_dict.items():
            if glv_address not in multicall_results:
                logger.debug("No multicall results for GLV %s", glv_address)
                continue

            try:
                results = multicall_results[glv_address]
                metadata = call_metadata.get(glv_address, {})

                logger.debug("Processing results for GLV %s: %s", glv_address, list(results.keys()))

                # Extract GLV token price
                glv_price = self.extract_glv_price(results)
                if glv_price:
                    glv_info_dict[glv_address]["glv_price"] = glv_price
                    logger.debug("GLV price for %s: %s", glv_address, glv_price)
                else:
                    logger.debug("No GLV price extracted for %s", glv_address)

                # Build markets metadata
                markets_metadata = {}
                for market_address in glv_info["glv_market_addresses"]:
                    market_symbol = self.markets.get_market_symbol(market_address)

                    # Extract balance
                    balance = self.extract_balance(results, market_address)

                    # Extract GM price
                    gm_price = self.extract_gm_price(results, market_address)

                    # Filter out markets with None symbols
                    if market_symbol is not None and market_symbol != "None":
                        markets_metadata[market_address] = {
                            "address": market_address,
                            "market symbol": market_symbol,
                            "balance": balance,
                            "gm price": gm_price,
                        }

                glv_info_dict[glv_address]["markets_metadata"] = markets_metadata

            except Exception as e:
                logger.error("Failed to process results for GLV %s: %s", glv_address, e)
                continue

        return glv_info_dict

    def extract_glv_price(self, results: dict) -> float:
        """Extract GLV price from multicall results."""
        try:
            glv_price_result = results.get("glv_price_default")
            if glv_price_result and glv_price_result.success:
                # Decode the result - getGlvTokenPrice returns (uint256, uint256, uint256)
                result_bytes = glv_price_result.result
                if result_bytes and len(result_bytes) >= 32:
                    # First uint256 is the price
                    price_raw = int.from_bytes(result_bytes[:32], byteorder="big")
                    price_float = price_raw * 10**-30
                    logger.debug("Extracted GLV price: %s raw -> %s", price_raw, price_float)
                    return price_float
                else:
                    logger.debug("Invalid result bytes for GLV price: length=%s", len(result_bytes) if result_bytes else 0)
            elif glv_price_result:
                error_msg = getattr(glv_price_result, "error", "Unknown error")
                logger.debug("GLV price call failed: %s", error_msg)
        except Exception as e:
            logger.error("Failed to extract GLV price: %s", e)
        return 0.0

    def extract_balance(self, results: dict, market_address: str) -> float:
        """Extract balance from multicall results."""
        try:
            balance_result = results.get(f"balance_{market_address}")
            if balance_result and balance_result.success:
                result_bytes = balance_result.result
                if result_bytes:
                    balance_raw = int.from_bytes(result_bytes, byteorder="big")
                    return balance_raw / 10**18
        except Exception as e:
            logger.error("Failed to extract balance for %s: %s", market_address, e)
        return 0.0

    def extract_gm_price(self, results: dict, market_address: str) -> float:
        """Extract GM price from multicall results."""
        try:
            gm_price_key = f"gm_price_{market_address}"
            logger.debug("Looking for GM price key: %s in results keys: %s", gm_price_key, list(results.keys()))

            gm_price_result = results.get(gm_price_key)
            if gm_price_result:
                logger.debug("Found GM price result for %s: success=%s", market_address, gm_price_result.success)

                if not gm_price_result.success:
                    error_msg = getattr(gm_price_result, "error", "Unknown error")
                    logger.debug("GM price call failed for %s: %s", market_address, error_msg)
                    return 0.0

                result_bytes = gm_price_result.result
                logger.debug("GM price result bytes length for %s: %s", market_address, len(result_bytes) if result_bytes else 0)

                if result_bytes and len(result_bytes) >= 32:
                    # getMarketTokenPrice returns (uint256, uint256) - we want the first one
                    price_raw = int.from_bytes(result_bytes[:32], byteorder="big")
                    price_float = price_raw / 10**30
                    logger.debug("GM price for %s: %s raw -> %s", market_address, price_raw, price_float)
                    return price_float
                else:
                    logger.debug("Empty or invalid result bytes for GM price %s: length=%s", market_address, len(result_bytes) if result_bytes else 0)
            else:
                logger.debug("No GM price result found for %s - expected key: %s", market_address, gm_price_key)
        except Exception as e:
            logger.error("Failed to extract GM price for %s: %s", market_address, e)
        return 0.0

    def _get_glv_info_list(self) -> dict:
        """
        Call glvReader to get the list of GLV markets live.
        This method is already optimized by the contract (batched call).

        :return: Dictionary of GLV info
        """
        try:
            raw_output = self.glv_reader_contract.functions.getGlvInfoList(to_checksum_address(self.datastore_contract.address), 0, 10).call()

            glvs = {}
            for raw_glv in raw_output:
                glvs[raw_glv[0][0]] = {
                    "glv_address": raw_glv[0][0],
                    "long_address": raw_glv[0][1],
                    "short_address": raw_glv[0][2],
                    "glv_market_addresses": raw_glv[1],
                }

            return glvs
        except Exception as e:
            logger.error("Failed to get GLV info list: %s", e)
            return {}

    def _get_data_processing(self) -> dict[str, Any]:
        """
        Implementation of abstract method from GetData base class.

        :return: GLV statistics data
        :rtype: dict[str, Any]
        """
        return self.get_glv_stats_multicall()
