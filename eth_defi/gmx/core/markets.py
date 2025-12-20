"""
GMX Markets Data Module

This module provides access to GMX protocol market information and trading pairs.
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

from eth_typing import HexAddress
from eth_utils import to_checksum_address

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.contracts import get_contract_addresses, get_reader_contract, get_tokens_address_dict, get_tokens_metadata_dict
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.types import MarketData, MarketSymbol

logger = logging.getLogger(__name__)


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
    GMX markets data provider.

    This class retrieves information about all trading markets available on GMX,
    replacing the gmx_python_sdk Markets class functionality.
    """

    def __init__(self, config: GMXConfig):
        """
        Initialise markets data provider.

        :param config: GMXConfig instance containing chain and network info
        """
        self.config = config
        self._special_wsteth_address = to_checksum_address("0x0Cf1fb4d1FF67A3D8Ca92c9d6643F8F9be8e03E5")
        self._markets_cache: Optional[dict] = None  # Cache for processed markets

    def _get_token_metadata_dict(self) -> dict[HexAddress, dict]:
        """Get token metadata dictionary with correct decimals from GMX API.

        Uses get_tokens_metadata_dict which fetches decimals from the GMX API,
        ensuring correct price conversions for all tokens (e.g., BTC=8, ETH=18).
        """
        return get_tokens_metadata_dict(self.config.chain)

    def _get_oracle_prices(self) -> dict[str, dict]:
        """Get or fetch oracle prices."""
        try:
            oracle_prices = OraclePrices(chain=self.config.chain).get_recent_prices()
        except Exception as e:
            logger.debug(f"Failed to fetch oracle prices: {e}")
            oracle_prices = {}

        return oracle_prices

    def get_available_markets(self) -> MarketData:
        """
        Get the available markets on a given chain.

        :return: Dictionary of the available markets
        :rtype: dict
        """
        return self._process_markets()

    def get_index_token_address(self, market_key: str) -> HexAddress:
        """
        Get index token address for a market.

        :param market_key: Market contract address
        :type market_key: str
        :return: Index token address
        :rtype: HexAddress
        """
        markets = self._process_markets()
        return markets.get(market_key, {}).get("index_token_address", None)

    def get_long_token_address(self, market_key: str) -> HexAddress:
        """
        Get long token address for a market.

        :param market_key: Market contract address
        :type market_key: str
        :return: Long token address
        :rtype: HexAddress
        """
        markets = self._process_markets()
        return markets.get(market_key, {}).get("long_token_address", None)

    def get_short_token_address(self, market_key: str) -> HexAddress:
        """
        Get short token address for a market.

        :param market_key: Market contract address
        :type market_key: str
        :return: Short token address
        :rtype: HexAddress
        """
        markets = self._process_markets()
        return markets.get(market_key, {}).get("short_token_address", None)

    def get_market_symbol(self, market_key: str) -> str:
        """
        Get market symbol for a market.

        :param market_key: Market contract address
        :type market_key: str
        :return: Market symbol
        :rtype: str
        """
        markets = self._process_markets()
        return markets.get(market_key, {}).get("market_symbol", None)

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
        markets = self._process_markets()
        if long:
            return markets[market_key]["long_token_metadata"]["decimals"]
        elif short:
            return markets[market_key]["short_token_metadata"]["decimals"]
        else:
            return markets[market_key]["market_metadata"]["decimals"]

    def is_synthetic(self, market_key: str) -> bool:
        """
        Check if a market is synthetic.

        :param market_key: Market contract address
        :type market_key: str
        :return: True if market is synthetic, False otherwise
        :rtype: bool
        """
        markets = self._process_markets()
        return markets[market_key]["market_metadata"].get("synthetic", False)

    def get_market_info(self, market_address: HexAddress) -> Optional[MarketInfo]:
        """
        Get detailed information for a specific market.

        :param market_address: Market contract address
        :type market_address: HexAddress
        :return: Market information or None if not found
        :rtype: Optional[MarketInfo]
        """
        markets = self._process_markets()
        if market_address in markets:
            market_data = markets[market_address]
            return MarketInfo(
                gmx_market_address=market_data["gmx_market_address"],
                market_symbol=market_data["market_symbol"],
                index_token_address=market_data["index_token_address"],
                market_metadata=market_data["market_metadata"],
                long_token_metadata=market_data["long_token_metadata"],
                long_token_address=market_data["long_token_address"],
                short_token_metadata=market_data["short_token_metadata"],
                short_token_address=market_data["short_token_address"],
            )
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
        # For now, assume all markets in our processed list are enabled
        return market_address not in self._process_markets()

    def _get_available_markets_raw(self) -> list[tuple]:
        """
        Get the available markets from the reader contract.

        :return: List of raw output from the reader contract
        :rtype: List[tuple]
        """
        reader_contract = get_reader_contract(self.config.web3, self.config.chain)
        contract_addresses = get_contract_addresses(self.config.chain)
        data_store_contract_address = contract_addresses.datastore

        return reader_contract.functions.getMarkets(
            data_store_contract_address,
            0,
            115,
        ).call()

    def _process_markets(self) -> dict:
        """
        Process the raw market data and return the results.

        :return: Dictionary of processed markets
        :rtype: dict
        """
        # Return cached data if available
        if self._markets_cache is not None:
            logger.debug("Returning cached markets data")
            return self._markets_cache

        logger.debug("Processing GMX markets data...")

        # Pre-load necessary data
        token_metadata_dict = self._get_token_metadata_dict()
        oracle_prices = self._get_oracle_prices()

        # Testnets use different token addresses, so skip oracle validation for them
        is_testnet = self.config.chain in ["arbitrum_sepolia", "avalanche_fuji"]

        # Use token metadata for testnets from contracts module
        if self.config.chain == "arbitrum_sepolia":
            # Get token metadata from contracts module
            arbitrum_sepolia_token_metadata = get_tokens_metadata_dict(self.config.chain)
            token_metadata_dict.update(arbitrum_sepolia_token_metadata)

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

                # Check if index token is available in oracle prices (skip for testnets due to address mismatch)
                if not is_testnet and oracle_prices and index_token_address not in oracle_prices:
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
                    # Skip swap markets - they don't have price data we can safely convert
                    logger.debug(f"Skipping market {market_address}: no index token metadata (likely a swap market)")
                    continue

                # Verify index token has decimals
                if "decimals" not in index_token_meta:
                    raise ValueError(f"Index token {index_token_address} missing decimals in GMX API response. Cannot safely process market {market_address}.")

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
                    index_token_meta = token_metadata_dict.get(index_token_address)
                    if not index_token_meta or "decimals" not in index_token_meta:
                        raise ValueError(f"wstETH token {index_token_address} not found in GMX API or missing decimals.")

                # Ensure metadata exists for all tokens (long/short tokens need decimals for collateral)
                if not long_token_meta or "decimals" not in long_token_meta:
                    raise ValueError(f"Long token {long_token_address} missing metadata or decimals for market {market_address}.")
                if not short_token_meta or "decimals" not in short_token_meta:
                    raise ValueError(f"Short token {short_token_address} missing metadata or decimals for market {market_address}.")

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

        logger.debug(f"Processed {len(processed_markets)} markets successfully")

        # Cache the results for future calls
        self._markets_cache = processed_markets

        return processed_markets
