"""
GMX Markets Data Module

This module provides access to GMX protocol market information and trading pairs.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)
from typing import Optional, Any

from eth_typing import HexAddress

from cchecksum import to_checksum_address

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_reader_contract, get_tokens_address_dict
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.types import MarketSymbol, MarketData


@dataclass(slots=True)
class MarketInfo:
    """Information about a GMX market."""

    #: GMX market contract address
    gmx_market_address: HexAddress
    #: Symbol identifier for the market
    market_symbol: MarketSymbol
    #: Address of the index token
    index_token_address: HexAddress
    #: Metadata dictionary for the market token
    market_metadata: dict[str, Any]
    #: Metadata dictionary for the long token
    long_token_metadata: dict[str, Any]
    #: Address of the long token
    long_token_address: HexAddress
    #: Metadata dictionary for the short token
    short_token_metadata: dict[str, Any]
    #: Address of the short token
    short_token_address: HexAddress


class Markets:
    """
    GMX markets data provider with optimized performance.

    This class retrieves information about all trading markets available on GMX,
    replacing the gmx_python_sdk Markets class functionality with improved
    performance through efficient caching and streamlined processing.
    """

    def __init__(self, config: GMXConfig):
        """
        Initialize markets data provider.

        :param config: GMXConfig instance containing chain and network info
        """
        self.config = config
        self._markets_cache = None
        self._token_metadata_dict = None
        self._oracle_prices = None
        self._processed_markets = False
        self._special_wsteth_address = to_checksum_address("0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5")

    def _ensure_markets_processed(self):
        """Ensure markets are processed and cache is populated."""
        if not self._processed_markets:
            self._process_markets()
            self._processed_markets = True

    def _get_token_metadata_dict(self) -> dict[HexAddress, dict]:
        """Get or create token metadata dictionary."""
        if self._token_metadata_dict is None:
            # Get token address mapping
            token_address_dict = get_tokens_address_dict(self.config.chain)

            # Create reverse lookup: address -> metadata
            self._token_metadata_dict = {}
            for symbol, address in token_address_dict.items():
                # Add synthetic flag to metadata
                self._token_metadata_dict[address] = {
                    "symbol": symbol,
                    "decimals": 18,  # Default, will be updated if we have more info
                    "synthetic": False,  # Default value
                }

        return self._token_metadata_dict

    def _get_oracle_prices(self) -> dict[str, dict]:
        """Get or fetch oracle prices with caching."""
        if self._oracle_prices is None:
            try:
                self._oracle_prices = OraclePrices(chain=self.config.chain).get_recent_prices()
            except Exception as e:
                logger.debug(f"Failed to fetch oracle prices: {e}")
                self._oracle_prices = {}

        return self._oracle_prices

    def get_available_markets(self) -> MarketData:
        """
        Get the available markets on a given chain.

        :return: Dictionary of the available markets
        :rtype: dict
        """
        self._ensure_markets_processed()
        return self._markets_cache

    def get_index_token_address(self, market_key: str) -> HexAddress:
        """
        Get index token address for a market.

        :param market_key: Market contract address
        :type market_key: str
        :return: Index token address
        :rtype: HexAddress
        """
        self._ensure_markets_processed()
        return self._markets_cache.get(market_key, {}).get("index_token_address", None)

    def get_long_token_address(self, market_key: str) -> HexAddress:
        """
        Get long token address for a market.

        :param market_key: Market contract address
        :type market_key: str
        :return: Long token address
        :rtype: HexAddress
        """
        self._ensure_markets_processed()
        return self._markets_cache.get(market_key, {}).get("long_token_address", None)

    def get_short_token_address(self, market_key: str) -> HexAddress:
        """
        Get short token address for a market.

        :param market_key: Market contract address
        :type market_key: str
        :return: Short token address
        :rtype: HexAddress
        """
        self._ensure_markets_processed()
        return self._markets_cache.get(market_key, {}).get("short_token_address", None)

    def get_market_symbol(self, market_key: str) -> str:
        """
        Get market symbol for a market.

        :param market_key: Market contract address
        :type market_key: str
        :return: Market symbol
        :rtype: str
        """
        self._ensure_markets_processed()
        return self._markets_cache.get(market_key, {}).get("market_symbol", None)

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
        self._ensure_markets_processed()
        if long:
            return self._markets_cache[market_key]["long_token_metadata"]["decimals"]
        elif short:
            return self._markets_cache[market_key]["short_token_metadata"]["decimals"]
        else:
            return self._markets_cache[market_key]["market_metadata"]["decimals"]

    def is_synthetic(self, market_key: str) -> bool:
        """
        Check if a market is synthetic.

        :param market_key: Market contract address
        :type market_key: str
        :return: True if market is synthetic, False otherwise
        :rtype: bool
        """
        self._ensure_markets_processed()
        return self._markets_cache[market_key]["market_metadata"].get("synthetic", False)

    def get_market_info(self, market_address: HexAddress) -> Optional[MarketInfo]:
        """
        Get detailed information for a specific market.

        :param market_address: Market contract address
        :type market_address: HexAddress
        :return: Market information or None if not found
        :rtype: Optional[MarketInfo]
        """
        self._ensure_markets_processed()
        if market_address in self._markets_cache:
            market_data = self._markets_cache[market_address]
            return MarketInfo(gmx_market_address=market_data["gmx_market_address"], market_symbol=market_data["market_symbol"], index_token_address=market_data["index_token_address"], market_metadata=market_data["market_metadata"], long_token_metadata=market_data["long_token_metadata"], long_token_address=market_data["long_token_address"], short_token_metadata=market_data["short_token_metadata"], short_token_address=market_data["short_token_address"])
        else:
            return None

    def is_market_disabled(self, market_address: HexAddress) -> bool:
        """
        Check if a market is disabled.

        :param market_address: Market contract address
        :type market_address: HexAddress
        :return: True if market is disabled, False otherwise
        :rtype: bool
        """
        self._ensure_markets_processed()
        # For now, assume all markets in our processed list are enabled
        return market_address not in self._markets_cache

    def _get_available_markets_raw(self) -> list[tuple]:
        """
        Get the available markets from the reader contract.

        :return: List of raw output from the reader contract
        :rtype: List[tuple]
        """
        reader_contract = get_reader_contract(self.config.web3, self.config.chain)
        contract_addresses = get_contract_addresses(self.config.chain)
        data_store_contract_address = contract_addresses.datastore

        return reader_contract.functions.getMarkets(data_store_contract_address, 0, 50).call()

    def _process_markets(self) -> None:
        """
        Process the raw market data and populate the cache.
        """
        logger.debug("Processing GMX markets data...")

        # Pre-load necessary data
        token_metadata_dict = self._get_token_metadata_dict()
        oracle_prices = self._get_oracle_prices()

        # Get raw market data
        raw_markets = self._get_available_markets_raw()
        logger.debug(f"Retrieved {len(raw_markets)} raw markets from contract")

        # Process markets in bulk
        processed_markets = {}

        for raw_market in raw_markets:
            try:
                # Checksum all addresses
                market_address = to_checksum_address(raw_market[0])
                index_token_address = to_checksum_address(raw_market[1])
                long_token_address = to_checksum_address(raw_market[2])
                short_token_address = to_checksum_address(raw_market[3])

                # Skip markets with zero index token address (except for special case)
                if index_token_address == "0x0000000000000000000000000000000000000000":
                    # Special case for wstETH market
                    if market_address == self._special_wsteth_address:
                        index_token_address = to_checksum_address("0x5979D7b546E38E414F7E9822514be443A4800529")
                    else:
                        logger.debug(f"Skipping market {market_address} with zero index token address")
                        continue

                # Check if index token is available in oracle prices
                if index_token_address not in oracle_prices:
                    # Special case for wstETH market
                    if market_address == self._special_wsteth_address:
                        pass  # Continue processing
                    else:
                        logger.debug(f"Skipping market {market_address}: index token {index_token_address} not in oracle prices")
                        continue

                # Get metadata for all tokens
                index_token_meta = token_metadata_dict.get(index_token_address)
                long_token_meta = token_metadata_dict.get(long_token_address)
                short_token_meta = token_metadata_dict.get(short_token_address)

                # Handle swap markets (when index token metadata is missing)
                if not index_token_meta:
                    print(f"{index_token_meta=}")
                    # For swap markets, create a custom symbol
                    long_symbol = long_token_meta["symbol"] if long_token_meta else "UNKNOWN"
                    short_symbol = short_token_meta["symbol"] if short_token_meta else "UNKNOWN"
                    market_symbol = f"SWAP {long_symbol}-{short_symbol}"

                    # Create synthetic metadata for swap markets
                    index_token_meta = {"symbol": market_symbol, "decimals": 18, "synthetic": True}
                else:
                    # Determine market symbol
                    market_symbol = index_token_meta["symbol"]
                    if long_token_address == short_token_address:
                        market_symbol = f"{market_symbol}2"

                    # Set synthetic flag for BTC2/ETH2 markets
                    index_token_meta["synthetic"] = long_token_address == short_token_address

                # Special case for wstETH market
                if market_address == self._special_wsteth_address:
                    market_symbol = "wstETH"
                    index_token_address = to_checksum_address("0x5979D7b546E38E414F7E9822514be443A4800529")
                    index_token_meta = token_metadata_dict.get(index_token_address, {"symbol": "wstETH", "decimals": 18, "synthetic": False})

                # Ensure metadata exists for all tokens
                if not long_token_meta:
                    long_token_meta = {"symbol": "UNKNOWN", "decimals": 18, "synthetic": False}
                if not short_token_meta:
                    short_token_meta = {"symbol": "UNKNOWN", "decimals": 18, "synthetic": False}

                # Store processed market
                processed_markets[market_address] = {
                    "gmx_market_address": market_address,
                    "market_symbol": market_symbol,
                    "index_token_address": index_token_address,
                    "market_metadata": index_token_meta,
                    "long_token_metadata": long_token_meta,
                    "long_token_address": long_token_address,
                    "short_token_metadata": short_token_meta,
                    "short_token_address": short_token_address,
                }

            except Exception as e:
                logger.debug(f"Skipping market {raw_market[0]}: {e}")
                continue

        self._markets_cache = processed_markets
        logger.debug(f"Processed {len(processed_markets)} markets successfully")
