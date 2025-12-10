"""
GMX Data Retrieval Base Class.

This module provides the base class for retrieving various types of data from
GMX protocol, replacing the gmx_python_sdk GetData functionality.
"""

import logging
import json
import csv
from abc import ABC, abstractmethod
from functools import cached_property
from typing import Any, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from eth_typing import HexAddress
from eth_utils import to_checksum_address

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_reader_contract, get_datastore_contract, get_contract_addresses
from eth_defi.gmx.core.markets import Markets
from eth_defi.gmx.core.oracle import OraclePrices


class GetData(ABC):
    """
    Base class for GMX data retrieval operations.

    This class provides common functionality for retrieving and processing
    GMX protocol data, including market filtering, oracle price integration,
    and data export capabilities.

    :param config: GMXConfig instance containing chain and network info
    :type config: GMXConfig
    :param filter_swap_markets: Whether to filter out swap markets from results
    :type filter_swap_markets: bool
    """

    def __init__(self, config: GMXConfig, filter_swap_markets: bool = True):
        """Initialize data retrieval base class.

        :param config: GMXConfig instance containing chain and network info
        :param filter_swap_markets: Whether to filter out swap markets from results
        """
        self.config = config
        self.filter_swap_markets = filter_swap_markets
        self.log = logging.getLogger(self.__class__.__name__)

        # Oracle prices cache
        self._oracle_prices_cache = None

        # Data processing result cache to avoid redundant calls
        self._data_cache: Optional[dict[str, Any]] = None

        # Token addresses for current market being processed
        self._long_token_address: Optional[HexAddress] = None
        self._short_token_address: Optional[HexAddress] = None

        # Output structure for compatibility
        self.output = {"long": {}, "short": {}}

    @cached_property
    def markets(self) -> Markets:
        """Markets instance for retrieving market information."""
        return Markets(self.config)

    @cached_property
    def reader_contract(self):
        """Reader contract instance for data queries."""
        return get_reader_contract(self.config.web3, self.config.chain)

    @cached_property
    def datastore_contract(self):
        """DataStore contract instance for blockchain data access."""
        return get_datastore_contract(self.config.web3, self.config.chain)

    @cached_property
    def datastore_contract_address(self) -> HexAddress:
        """DataStore contract address."""
        contract_addresses = get_contract_addresses(self.config.chain)
        return contract_addresses.datastore

    def get_data(self, to_json: bool = False, to_csv: bool = False) -> dict[str, Any]:
        """
        Get data using the specific implementation and optionally export it.

        :param to_json: Whether to save data to JSON file
        :type to_json: bool
        :param to_csv: Whether to save data to CSV file
        :type to_csv: bool
        :return: Dictionary containing processed data
        :rtype: dict[str, Any]
        """
        if not hasattr(self.config, "web3") or self.config.web3 is None:
            raise ValueError("Web3 connection required in config")

        try:
            # Apply market filtering if requested
            if self.filter_swap_markets:
                self._filter_swap_markets()

            # Get data using specific implementation
            data = self._get_data_processing()

            # Export data if requested
            if to_json:
                self._save_to_json(data)

            if to_csv:
                self._save_to_csv(data)

            return data

        except Exception as e:
            self.log.error(f"Failed to get data: {e}")
            raise

    @abstractmethod
    def _get_data_processing(self) -> dict[str, Any]:
        """
        Abstract method for specific data processing implementation.

        Subclasses must implement this method to provide their specific
        data retrieval and processing logic.

        :return: Dictionary containing processed data
        :rtype: dict[str, Any]
        """
        pass

    def _get_token_addresses(self, market_key: HexAddress) -> None:
        """
        Get and cache token addresses for a specific market.

        :param market_key: Market contract address
        :type market_key: HexAddress
        """
        try:
            market_key = to_checksum_address(market_key)
            self._long_token_address = self.markets.get_long_token_address(market_key)
            self._short_token_address = self.markets.get_short_token_address(market_key)

            self.log.debug(f"Token addresses for {market_key}: Long: {self._long_token_address}, Short: {self._short_token_address}")
        except Exception as e:
            self.log.warning(f"Failed to get token addresses for {market_key}: {e}")
            self._long_token_address = None
            self._short_token_address = None

    def _filter_swap_markets(self) -> None:
        """
        Filter out swap markets from the markets instance.

        This modifies the markets.get_available_markets() result to exclude
        markets with 'SWAP' in their symbol.
        """
        try:
            available_markets = self.markets.get_available_markets()
            filtered_markets = {}

            for market_key, market_data in available_markets.items():
                market_symbol = market_data.get("market_symbol", "")
                if not market_symbol.startswith("SWAP"):
                    filtered_markets[market_key] = market_data

            self.log.debug(f"Filtered markets: {len(filtered_markets)} from {len(available_markets)}")

        except Exception as e:
            self.log.warning(f"Failed to filter swap markets: {e}")

    def _get_pnl(self, market: list, prices_list: list, is_long: bool, maximize: bool = False) -> tuple[int, int]:
        """
        Get open interest with PnL and PnL for a market.

        :param market: List containing [market_address, index_token, long_token, short_token]
        :type market: list
        :param prices_list: List containing [min_price, max_price]
        :type prices_list: list
        :param is_long: Whether to get long or short position data
        :type is_long: bool
        :param maximize: Whether to maximize the calculation
        :type maximize: bool
        :return: Tuple of (open_interest_with_pnl, pnl)
        :rtype: Tuple[int, int]
        """
        try:
            open_interest_pnl = self.reader_contract.functions.getOpenInterestWithPnl(self.datastore_contract_address, market, prices_list, is_long, maximize).call()

            pnl = self.reader_contract.functions.getPnl(self.datastore_contract_address, market, prices_list, is_long, maximize).call()

            return open_interest_pnl, pnl

        except Exception as e:
            self.log.warning(f"Failed to get PnL for market {market[0]}: {e}")
            return 0, 0

    def _get_oracle_prices(self, market_key: HexAddress, index_token_address: HexAddress, return_tuple: bool = False) -> Any:
        """
        Get oracle prices for a market's tokens.

        :param market_key: Market contract address
        :type market_key: HexAddress
        :param index_token_address: Index token address
        :type index_token_address: HexAddress
        :param return_tuple: Whether to return price tuple or market info
        :type return_tuple: bool
        :return: Price tuple or market info from reader contract
        :rtype: Any
        """
        try:
            # Get cached oracle prices or fetch new ones
            if self._oracle_prices_cache is None:
                oracle = OraclePrices(self.config.chain)
                self._oracle_prices_cache = oracle.get_recent_prices()

            oracle_prices_dict = self._oracle_prices_cache

            # Build price tuple for contract calls
            index_token_address = to_checksum_address(index_token_address)
            long_token_address = to_checksum_address(self._long_token_address)
            short_token_address = to_checksum_address(self._short_token_address)

            try:
                prices = (
                    (
                        int(oracle_prices_dict[index_token_address]["minPriceFull"]),
                        int(oracle_prices_dict[index_token_address]["maxPriceFull"]),
                    ),
                    (
                        int(oracle_prices_dict[long_token_address]["minPriceFull"]),
                        int(oracle_prices_dict[long_token_address]["maxPriceFull"]),
                    ),
                    (
                        int(oracle_prices_dict[short_token_address]["minPriceFull"]),
                        int(oracle_prices_dict[short_token_address]["maxPriceFull"]),
                    ),
                )
            # TODO: this needs to be here until GMX add stables to signed price
            except KeyError:
                # Fallback for stablecoins not in signed price API
                # Use $1.00 price (1 * 10^30 for GMX price format)
                stable_price = (1000000000000000000000000, 1000000000000000000000000)

                prices = (
                    (
                        int(oracle_prices_dict[index_token_address]["minPriceFull"]),
                        int(oracle_prices_dict[index_token_address]["maxPriceFull"]),
                    ),
                    (
                        int(oracle_prices_dict[long_token_address]["minPriceFull"]),
                        int(oracle_prices_dict[long_token_address]["maxPriceFull"]),
                    ),
                    stable_price,  # Use stable price for missing tokens
                )

            if return_tuple:
                return prices

            # Return market info from reader contract
            return self.reader_contract.functions.getMarketInfo(self.datastore_contract_address, prices, to_checksum_address(market_key))

        except Exception as e:
            self.log.warning(f"Failed to get oracle prices for {market_key}: {e}")
            if return_tuple:
                return (0, 0), (0, 0), (0, 0)
            return None

    @staticmethod
    def _format_market_info_output(output: tuple) -> dict[str, Any]:
        """
        Format market info output from reader contract into structured dictionary.

        :param output: Raw output tuple from getMarketInfo contract call
        :type output: tuple
        :return: Formatted market information
        :rtype: dict[str, Any]
        """
        try:
            return {
                "market_address": output[0][0],
                "index_address": output[0][1],
                "long_address": output[0][2],
                "short_address": output[0][3],
                "borrowingFactorPerSecondForLongs": output[1],
                "borrowingFactorPerSecondForShorts": output[2],
                "baseFunding_long_fundingFeeAmountPerSize_longToken": output[3][0][0][0],
                "baseFundinglong_fundingFeeAmountPerSize_shortToken": output[3][0][0][1],
                "baseFundingshort_fundingFeeAmountPerSize_longToken": output[3][0][1][0],
                "baseFundingshort_fundingFeeAmountPerSize_shortToken": output[3][0][1][1],
                "baseFundinglong_claimableFundingAmountPerSize_longToken": output[3][1][0][0],
                "baseFundinglong_claimableFundingAmountPerSize_shortToken": output[3][1][0][1],
                "baseFundingshort_claimableFundingAmountPerSize_longToken": output[3][1][1][0],
                "baseFundingshort_claimableFundingAmountPerSize_shortToken": output[3][1][1][1],
                "longsPayShorts": output[4][0],
                "fundingFactorPerSecond": output[4][1],
                "nextSavedFundingFactorPerSecond": output[4][2],
                "nextFunding_long_fundingFeeAmountPerSize_longToken": output[4][3][0][0],
                "nextFunding_long_fundingFeeAmountPerSize_shortToken": output[4][3][0][1],
                "nextFunding_baseFundingshort_fundingFeeAmountPerSize_longToken": output[4][3][1][0],
                "nextFunding_baseFundingshort_fundingFeeAmountPerSize_shortToken": output[4][3][1][1],
                "nextFunding_baseFundinglong_claimableFundingAmountPerSize_longToken": output[4][4][0][0],
                "nextFunding_baseFundinglong_claimableFundingAmountPerSize_shortToken": output[4][4][0][1],
                "nextFunding_baseFundingshort_claimableFundingAmountPerSize_longToken": output[4][4][1][0],
                "nextFunding_baseFundingshort_claimableFundingAmountPerSize_shortToken": output[4][4][1][1],
                "virtualPoolAmountForLongToken": output[5][0],
                "virtualPoolAmountForShortToken": output[5][1],
                "virtualInventoryForPositions": output[5][2],
                "isDisabled": output[6],
            }
        except (IndexError, TypeError) as e:
            logging.warning(f"Failed to format market info output: {e}")
            return {}

    def _execute_threading(self, contract_calls: list, max_workers: int = 5) -> list:
        """
        Execute multiple contract calls concurrently.

        :param contract_calls: List of contract call objects to execute
        :type contract_calls: list
        :param max_workers: Maximum number of concurrent workers
        :type max_workers: int
        :return: List of results in same order as input
        :rtype: list
        """
        results = [None] * len(contract_calls)

        # Filter out None values, non-callable objects, and their indices
        valid_calls = []
        for index, call in enumerate(contract_calls):
            # Check if it's not None and has a callable 'call' attribute
            if call is not None and hasattr(call, "call") and callable(getattr(call, "call")):
                valid_calls.append((index, call))
            elif isinstance(call, (int, float)):
                # If it's already a numeric value, use it directly
                results[index] = call

        if not valid_calls:
            return results

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_index = {executor.submit(call.call): index for index, call in valid_calls}

            for future in as_completed(future_to_index):
                original_index = future_to_index[future]
                try:
                    results[original_index] = future.result()
                except Exception as e:
                    self.log.warning(f"Contract call {original_index} failed: {e}")
                    results[original_index] = None

        return results

    def _save_to_json(self, data: dict[str, Any]) -> None:
        """
        Save data to JSON file.

        :param data: Data to save
        :type data: dict[str, Any]
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            parameter = data.get("parameter", "data")
            filename = f"{self.config.chain}_{parameter}_data_{timestamp}.json"

            json_data = {"chain": self.config.chain, "timestamp": timestamp, "data": data}

            with open(filename, "w") as f:
                json.dump(json_data, f, indent=2)

            self.log.info(f"Data saved to {filename}")

        except Exception as e:
            self.log.error(f"Failed to save JSON: {e}")

    def _save_to_csv(self, data: dict[str, Any]) -> None:
        """
        Save data to CSV file.

        :param data: Data to save
        :type data: dict[str, Any]
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            parameter = data.get("parameter", "data")

            # Handle long/short data structure
            if "long" in data and "short" in data and isinstance(data["long"], dict):
                # Save long data
                filename_long = f"{self.config.chain}_long_{parameter}_data_{timestamp}.csv"
                self._save_dict_to_csv(data["long"], filename_long)

                # Save short data
                filename_short = f"{self.config.chain}_short_{parameter}_data_{timestamp}.csv"
                self._save_dict_to_csv(data["short"], filename_short)
            else:
                # Save single data structure
                filename = f"{self.config.chain}_{parameter}_data_{timestamp}.csv"
                self._save_dict_to_csv(data, filename)

        except Exception as e:
            self.log.error(f"Failed to save CSV: {e}")

    def _save_dict_to_csv(self, data_dict: dict[str, Any], filename: str) -> None:
        """
        Save dictionary data to CSV file.

        :param data_dict: Dictionary to save
        :type data_dict: dict[str, Any]
        :param filename: Output filename
        :type filename: str
        """
        if not data_dict:
            return

        with open(filename, "w", newline="") as f:
            writer = csv.writer(f)

            # Add timestamp column
            writer.writerow(["Timestamp", "Market", "Value"])
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            for market, value in data_dict.items():
                writer.writerow([timestamp, market, value])

        self.log.info(f"Data saved to {filename}")
