"""
GMX Open Interest Data Retrieval Module

This module provides open interest data for GMX protocol markets,
replacing the gmx_python_sdk GetOpenInterest functionality.
"""

import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional, List

from eth_typing import HexAddress
from cchecksum import to_checksum_address

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.get_data import GetData


@dataclass
class OpenInterestInfo:
    """Information about open interest for a specific market.

    :param market_address: GMX market contract address
    :type market_address: HexAddress
    :param market_symbol: Symbol identifier for the market
    :type market_symbol: str
    :param long_open_interest: Long position open interest value
    :type long_open_interest: float
    :param short_open_interest: Short position open interest value  
    :type short_open_interest: float
    :param long_open_interest_with_pnl: Long open interest including PnL
    :type long_open_interest_with_pnl: float
    :param short_open_interest_with_pnl: Short open interest including PnL
    :type short_open_interest_with_pnl: float
    :param long_pnl: Long position PnL
    :type long_pnl: float
    :param short_pnl: Short position PnL
    :type short_pnl: float
    :param index_token_address: Address of the index token
    :type index_token_address: HexAddress
    :param long_token_address: Address of the long token
    :type long_token_address: HexAddress
    :param short_token_address: Address of the short token
    :type short_token_address: HexAddress
    """

    market_address: HexAddress
    market_symbol: str
    long_open_interest: float
    short_open_interest: float
    long_open_interest_with_pnl: float
    short_open_interest_with_pnl: float
    long_pnl: float
    short_pnl: float
    index_token_address: HexAddress
    long_token_address: HexAddress
    short_token_address: HexAddress


class GetOpenInterest(GetData):
    """
    GMX open interest data retrieval class.

    This class retrieves open interest information for all available GMX markets,
    including position PnL calculations. It inherits from the GetData base class
    and provides specific implementation for open interest data processing.

    :param config: GMXConfig instance containing chain and network info
    :type config: GMXConfig
    :param filter_swap_markets: Whether to filter out swap markets from results
    :type filter_swap_markets: bool
    """

    def __init__(self, config: GMXConfig, filter_swap_markets: bool = True):
        """
        Initialize open interest data retrieval.

        :param config: GMXConfig instance containing chain and network info
        :type config: GMXConfig
        :param filter_swap_markets: Whether to filter out swap markets from results
        :type filter_swap_markets: bool
        """
        super().__init__(config, filter_swap_markets)
        self.log = logging.getLogger(__name__)

    def _get_data_processing(self) -> dict[str, Any]:
        """
        Process open interest data for all available markets.

        This method implements the abstract method from GetData base class,
        providing specific logic for retrieving and processing open interest data.

        :return: Dictionary containing processed open interest data
        :rtype: Dict[str, Any]
        """
        try:
            available_markets = self.markets.get_available_markets()

            if not available_markets:
                self.log.warning("No markets available for open interest calculation")
                return {"parameter": "open_interest", "long": {}, "short": {}}

            # Prepare contract calls for threading
            contract_calls = []
            market_info_list = []

            for market_address, market_data in available_markets.items():
                try:
                    market_address = to_checksum_address(market_address)
                    self._get_token_addresses(market_address)

                    if not self._long_token_address or not self._short_token_address:
                        self.log.warning(f"Skipping market {market_address}: missing token addresses")
                        continue

                    index_token_address = market_data.get("index_token_address")
                    if not index_token_address:
                        self.log.warning(f"Skipping market {market_address}: missing index token address")
                        continue

                    market_info_list.append({
                        "market_address": market_address,
                        "market_symbol": market_data.get("market_symbol", "UNKNOWN"),
                        "index_token_address": to_checksum_address(index_token_address),
                        "long_token_address": self._long_token_address,
                        "short_token_address": self._short_token_address,
                    })

                    # Get oracle price tuple for contract calls
                    prices = self._get_oracle_prices(
                        market_address, 
                        index_token_address, 
                        return_tuple=True
                    )

                    if not prices or prices == ((0, 0), (0, 0), (0, 0)):
                        self.log.warning(f"Skipping market {market_address}: invalid prices")
                        continue

                    # Prepare market list for contract calls
                    market_list = [
                        market_address,
                        index_token_address,
                        self._long_token_address,
                        self._short_token_address,
                    ]

                    # Add contract calls for long positions
                    contract_calls.append(
                        self.reader_contract.functions.getOpenInterest(
                            self.datastore_contract_address, market_list, prices, True
                        )
                    )

                    # Add contract calls for short positions
                    contract_calls.append(
                        self.reader_contract.functions.getOpenInterest(
                            self.datastore_contract_address, market_list, prices, False
                        )
                    )

                except Exception as e:
                    self.log.warning(f"Error preparing market {market_address}: {e}")
                    continue

            if not contract_calls:
                self.log.warning("No valid contract calls prepared")
                return {"parameter": "open_interest", "long": {}, "short": {}}

            # Execute contract calls with threading
            self.log.debug(f"Executing {len(contract_calls)} contract calls with threading")
            results = self._execute_threading(contract_calls, max_workers=5)

            return self._process_open_interest_results(market_info_list, results)

        except Exception as e:
            self.log.error(f"Failed to process open interest data: {e}")
            return {"parameter": "open_interest", "long": {}, "short": {}}

    def _process_open_interest_results(self, market_info_list: list[dict], 
                                     results: list[Any]) -> dict[str, Any]:
        """
        Process the results from contract calls into structured open interest data.

        :param market_info_list: List of market information dictionaries
        :type market_info_list: List[Dict]
        :param results: List of contract call results
        :type results: List[Any]
        :return: Processed open interest data
        :rtype: Dict[str, Any]
        """
        long_data = {}
        short_data = {}
        processed_markets = []

        try:
            # Process results in pairs (long, short) for each market
            for i, market_info in enumerate(market_info_list):
                long_result_index = i * 2
                short_result_index = i * 2 + 1

                if (long_result_index >= len(results) or 
                    short_result_index >= len(results) or
                    results[long_result_index] is None or 
                    results[short_result_index] is None):
                    self.log.warning(f"Missing results for market {market_info['market_symbol']}")
                    continue

                try:
                    market_address = market_info["market_address"]
                    market_symbol = market_info["market_symbol"]

                    # Get raw open interest values
                    long_open_interest = results[long_result_index]
                    short_open_interest = results[short_result_index]

                    # Get PnL data for this market
                    long_oi_with_pnl, long_pnl = self._get_pnl_for_market(market_info, True)
                    short_oi_with_pnl, short_pnl = self._get_pnl_for_market(market_info, False)

                    # Convert to human readable format (divide by 10^30 for GMX format)
                    long_oi_readable = long_open_interest / (10 ** 30)
                    short_oi_readable = short_open_interest / (10 ** 30)
                    long_oi_with_pnl_readable = long_oi_with_pnl / (10 ** 30)
                    short_oi_with_pnl_readable = short_oi_with_pnl / (10 ** 30)
                    long_pnl_readable = long_pnl / (10 ** 30)
                    short_pnl_readable = short_pnl / (10 ** 30)

                    # Store in output format
                    long_data[market_symbol] = long_oi_readable
                    short_data[market_symbol] = short_oi_readable

                    # Create detailed info object
                    open_interest_info = OpenInterestInfo(
                        market_address=market_address,
                        market_symbol=market_symbol,
                        long_open_interest=long_oi_readable,
                        short_open_interest=short_oi_readable,
                        long_open_interest_with_pnl=long_oi_with_pnl_readable,
                        short_open_interest_with_pnl=short_oi_with_pnl_readable,
                        long_pnl=long_pnl_readable,
                        short_pnl=short_pnl_readable,
                        index_token_address=market_info["index_token_address"],
                        long_token_address=market_info["long_token_address"],
                        short_token_address=market_info["short_token_address"],
                    )

                    processed_markets.append(open_interest_info)

                    self.log.debug(f"Processed open interest for {market_symbol}: "
                                 f"Long={long_oi_readable:.2f}, Short={short_oi_readable:.2f}")

                except Exception as e:
                    self.log.warning(f"Error processing market {market_info.get('market_symbol', 'UNKNOWN')}: {e}")
                    continue

            self.log.info(f"Successfully processed open interest for {len(processed_markets)} markets")

            return {
                "parameter": "open_interest",
                "long": long_data,
                "short": short_data,
                "detailed_info": processed_markets,
            }

        except Exception as e:
            self.log.error(f"Error processing open interest results: {e}")
            return {"parameter": "open_interest", "long": {}, "short": {}}

    def _get_pnl_for_market(self, market_info: dict, is_long: bool) -> tuple:
        """
        Get PnL data for a specific market and position type.

        :param market_info: Market information dictionary
        :type market_info: Dict
        :param is_long: Whether to get long or short position data
        :type is_long: bool
        :return: Tuple of (open_interest_with_pnl, pnl)
        :rtype: tuple
        """
        try:
            # Get oracle prices for this market
            prices = self._get_oracle_prices(
                market_info["market_address"],
                market_info["index_token_address"],
                return_tuple=True
            )

            if not prices or prices == ((0, 0), (0, 0), (0, 0)):
                return 0, 0

            # Prepare market list for PnL calculation
            market_list = [
                market_info["market_address"],
                market_info["index_token_address"],
                market_info["long_token_address"],
                market_info["short_token_address"],
            ]

            # Get PnL data using base class method
            return self._get_pnl(market_list, prices, is_long, maximize=False)

        except Exception as e:
            self.log.warning(f"Failed to get PnL for market {market_info.get('market_symbol', 'UNKNOWN')}: {e}")
            return 0, 0

    def get_open_interest_info(self) -> List[OpenInterestInfo]:
        """
        Get detailed open interest information for all markets.

        :return: List of OpenInterestInfo objects with detailed data
        :rtype: List[OpenInterestInfo]
        """
        data = self.get_data()
        return data.get("detailed_info", [])

    def get_total_open_interest(self) -> Dict[str, float]:
        """
        Get total open interest across all markets.

        :return: Dictionary with total long and short open interest
        :rtype: Dict[str, float]
        """
        try:
            data = self.get_data()
            
            total_long = sum(data.get("long", {}).values())
            total_short = sum(data.get("short", {}).values())
            
            return {
                "total_long": total_long,
                "total_short": total_short,
                "total_combined": total_long + total_short,
            }
            
        except Exception as e:
            self.log.error(f"Failed to calculate total open interest: {e}")
            return {"total_long": 0.0, "total_short": 0.0, "total_combined": 0.0}