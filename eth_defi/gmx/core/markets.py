"""
GMX Markets Data Module

This module provides access to GMX protocol market information and trading pairs.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from eth_typing import HexAddress
from web3 import Web3

from cchecksum import to_checksum_address

from eth_defi.gmx.config import GMXConfigManager
from eth_defi.gmx.contracts import get_contract_addresses, get_reader_contract, get_tokens_address_dict
from eth_defi.gmx.core.oracle import OraclePrices


@dataclass
class MarketInfo:
    """Information about a GMX market.

    :param gmx_market_address: GMX market contract address
    :type gmx_market_address: HexAddress
    :param market_symbol: Symbol identifier for the market
    :type market_symbol: str
    :param index_token_address: Address of the index token
    :type index_token_address: HexAddress
    :param market_metadata: Metadata dictionary for the market token
    :type market_metadata: dict
    :param long_token_metadata: Metadata dictionary for the long token
    :type long_token_metadata: dict
    :param long_token_address: Address of the long token
    :type long_token_address: HexAddress
    :param short_token_metadata: Metadata dictionary for the short token
    :type short_token_metadata: dict
    :param short_token_address: Address of the short token
    :type short_token_address: HexAddress
    """

    gmx_market_address: HexAddress
    market_symbol: str
    index_token_address: HexAddress
    market_metadata: dict
    long_token_metadata: dict
    long_token_address: HexAddress
    short_token_metadata: dict
    short_token_address: HexAddress


class Markets:
    """
    GMX markets data provider.

    This class retrieves information about all trading markets available on GMX,
    replacing the gmx_python_sdk Markets class functionality.
    """

    def __init__(self, config: GMXConfigManager):
        """
        Initialize markets data provider.

        :param config: GMXConfigManager instance containing chain and network info
        """
        self.config = config
        self.info = self._process_markets()
        self.log = logging.getLogger(__name__)

    def get_index_token_address(self, market_key: str) -> HexAddress:
        """
        Get index token address for a market.

        :param market_key: Market contract address
        :type market_key: str
        :return: Index token address
        :rtype: HexAddress
        """
        return self.info[market_key]["index_token_address"]

    def get_long_token_address(self, market_key: str) -> HexAddress:
        """
        Get long token address for a market.

        :param market_key: Market contract address
        :type market_key: str
        :return: Long token address
        :rtype: HexAddress
        """
        return self.info[market_key]["long_token_address"]

    def get_short_token_address(self, market_key: str) -> HexAddress:
        """
        Get short token address for a market.

        :param market_key: Market contract address
        :type market_key: str
        :return: Short token address
        :rtype: HexAddress
        """
        return self.info[market_key]["short_token_address"]

    def get_market_symbol(self, market_key: str) -> str:
        """
        Get market symbol for a market.

        :param market_key: Market contract address
        :type market_key: str
        :return: Market symbol
        :rtype: str
        """
        return self.info[market_key]["market_symbol"]

    def get_decimal_factor(self, market_key: str, long: bool = False, short: bool = False) -> int:
        """
        Get decimal factor for a market token.

        :param market_key: Market contract address
        :type market_key: str
        :param long: Get decimals for long token
        :type long: bool
        :param short: Get decimals for short token
        :type short: bool
        :return: Token decimal factor
        :rtype: int
        """
        if long:
            return self.info[market_key]["long_token_metadata"]["decimals"]
        elif short:
            return self.info[market_key]["short_token_metadata"]["decimals"]
        else:
            return self.info[market_key]["market_metadata"]["decimals"]

    def is_synthetic(self, market_key: str) -> bool:
        """
        Check if a market is synthetic.

        :param market_key: Market contract address
        :type market_key: str
        :return: True if market is synthetic, False otherwise
        :rtype: bool
        """
        return self.info[market_key]["market_metadata"].get("synthetic", False)

    def get_available_markets(self):
        """
        Get the available markets on a given chain.

        :return: Dictionary of the available markets
        :rtype: dict
        """
        logging.debug("Getting Available Markets..")
        return self._process_markets()

    def _get_available_markets_raw(self) -> tuple:
        """
        Get the available markets from the reader contract.

        :return: Tuple of raw output from the reader contract
        :rtype: tuple
        """
        # Get web3 from config if available, otherwise we need it passed
        if hasattr(self.config, "web3"):
            web3 = self.config.web3
        else:
            raise ValueError("Web3 connection required")

        reader_contract = get_reader_contract(web3, self.config.chain)
        contract_addresses = get_contract_addresses(self.config.chain)
        data_store_contract_address = contract_addresses.datastore

        return reader_contract.functions.getMarkets(data_store_contract_address, 0, 50).call()

    def _process_markets(self) -> dict:
        """
        Call and process the raw market data.

        :return: Dictionary of decoded market data
        :rtype: dict
        """
        token_address_dict = get_tokens_address_dict(self.config.chain)
        raw_markets = self._get_available_markets_raw()

        decoded_markets: dict = {}
        for raw_market in raw_markets:
            try:
                if not self._check_if_index_token_in_signed_prices_api(raw_market[1]):
                    continue

                # Check if we have metadata for all required tokens
                index_token_meta = token_address_dict.get(raw_market[1])
                long_token_meta = token_address_dict.get(raw_market[2])
                short_token_meta = token_address_dict.get(raw_market[3])

                if not index_token_meta:
                    # Skip if we don't have index token metadata
                    continue

                market_symbol = index_token_meta["symbol"]

                if raw_market[2] == raw_market[3]:
                    market_symbol = f"{market_symbol}2"

                decoded_markets[to_checksum_address(raw_market[0])] = {
                    "gmx_market_address": to_checksum_address(raw_market[0]),
                    "market_symbol": market_symbol,
                    "index_token_address": to_checksum_address(raw_market[1]),
                    "market_metadata": index_token_meta,
                    "long_token_metadata": long_token_meta or {"symbol": "UNKNOWN", "decimals": 18},
                    "long_token_address": to_checksum_address(raw_market[2]),
                    "short_token_metadata": short_token_meta or {"symbol": "UNKNOWN", "decimals": 18},
                    "short_token_address": to_checksum_address(raw_market[3]),
                }

                # Special case for wstETH market
                if to_checksum_address(raw_market[0]) == to_checksum_address("0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5"):
                    decoded_markets[to_checksum_address(raw_market[0])]["market_symbol"] = "wstETH"
                    decoded_markets[to_checksum_address(raw_market[0])]["index_token_address"] = to_checksum_address("0x5979D7b546E38E414F7E9822514be443A4800529")

            except Exception as e:
                # If there's any error processing this market, skip it and continue
                self.log.warning(f"Skipping market {raw_market[0]}: {e}")
                continue

        return decoded_markets

    def _check_if_index_token_in_signed_prices_api(self, index_token_address: HexAddress) -> bool:
        """
        Check if the index token is available in the signed prices API.

        :param index_token_address: Token address to check
        :type index_token_address: HexAddress
        :return: True if token is available, False otherwise
        :rtype: bool
        """
        try:
            prices = OraclePrices(chain=self.config.chain).get_recent_prices()

            if index_token_address == to_checksum_address("0x0000000000000000000000000000000000000000"):
                return True

            prices[index_token_address]
            return True
        except KeyError:
            return False

    def get_market_info(self, market_address: HexAddress) -> Optional[MarketInfo]:
        """
        Get detailed information for a specific market.

        :param market_address: Market contract address
        :type market_address: HexAddress
        :return: Market information or None if not found
        :rtype: Optional[MarketInfo]
        """
        try:
            if market_address in self.info:
                market_data = self.info[market_address]
                return MarketInfo(gmx_market_address=market_data["gmx_market_address"], market_symbol=market_data["market_symbol"], index_token_address=market_data["index_token_address"], market_metadata=market_data["market_metadata"], long_token_metadata=market_data["long_token_metadata"], long_token_address=market_data["long_token_address"], short_token_metadata=market_data["short_token_metadata"], short_token_address=market_data["short_token_address"])
            else:
                return None

        except Exception as e:
            self.log.error(f"Failed to get market info for {market_address}: {e}")
            return None

    def is_market_disabled(self, market_address: HexAddress) -> bool:
        """
        Check if a market is disabled.

        :param market_address: Market contract address
        :type market_address: HexAddress
        :return: True if market is disabled, False otherwise
        :rtype: bool
        """
        # For now, assume all markets in our processed list are enabled
        return market_address not in self.info
