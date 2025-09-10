"""
GMX Open Positions Data Module.

This module provides access to open positions data for user addresses.
"""

import logging

logger = logging.getLogger(__name__)
from typing import Any

import numpy as np
from cchecksum import to_checksum_address

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.get_data import GetData
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.contracts import get_contract_addresses, get_tokens_address_dict, NETWORK_TOKENS
from eth_defi.gmx.types import MarketData


class GetOpenPositions(GetData):
    """Open positions data provider for GMX protocol.

    Retrieves all open trading positions for a specific user address.
    Provides detailed information about current trading positions,
    including position sizes, entry prices, current profit/loss, margin requirements,
    and liquidation thresholds.
    """

    def __init__(self, config: GMXConfig, filter_swap_markets: bool = True):
        """Initialize open positions data provider.

        :param config: GMXConfig instance containing chain and network info
        :param filter_swap_markets: Whether to filter out swap markets from results
        """
        super().__init__(config, filter_swap_markets=filter_swap_markets)

    def get_data(self, address: str) -> MarketData:
        """Get all open positions for a given address on the configured chain.

        :param address: User wallet address to query positions for
        :returns: A dictionary containing the open positions, where asset and direction are the keys
        :rtype: dict
        """
        # Convert address to checksum format
        checksum_address = to_checksum_address(address)

        try:
            contract_addresses = get_contract_addresses(self.config.chain)
            datastore_address = contract_addresses.datastore

            # Get raw positions from reader contract
            raw_positions = self.reader_contract.functions.getAccountPositions(datastore_address, checksum_address, 0, 10).call()

            if len(raw_positions) == 0:
                logging.info(f'No positions open for address: "{checksum_address}" on {self.config.chain.title()}.')
                return {}

            processed_positions = {}

            for raw_position in raw_positions:
                try:
                    processed_position = self._get_data_processing(raw_position)

                    # Build a better key using market symbol and direction
                    if processed_position["is_long"]:
                        direction = "long"
                    else:
                        direction = "short"

                    key = "{}_{}".format(processed_position["market_symbol"], direction)
                    processed_positions[key] = processed_position
                except KeyError as e:
                    logging.error(f"Incompatible market: {e}")
                    # Continue processing other positions instead of failing completely
                    continue
                except Exception as e:
                    logging.error(f"Error processing position: {e}")
                    continue

            return processed_positions

        except Exception as e:
            logger.error(f"Failed to fetch open positions data: {e}")
            raise e

    def _get_tokens_address_dict(self) -> dict[str, Any]:
        """Enhanced version of get_tokens_address_dict with fallback token data.

        This method calls the original GMX API and supplements it with token data
        from NETWORK_TOKENS for commonly used tokens that might be missing from
        the API response.

        :returns: Dictionary mapping token addresses to their information, enhanced
                  with fallback data from NETWORK_TOKENS
        :rtype: dict
        """
        try:
            # Get tokens from GMX API using the original function
            chain_tokens = get_tokens_address_dict(self.config.chain)

            # Convert to address -> metadata format if needed
            if chain_tokens and isinstance(list(chain_tokens.values())[0], str):
                # If it's symbol -> address format, we need the reverse lookup from API
                # Let's get the full token data from API directly
                pass

            # Create fallback token metadata from NETWORK_TOKENS
            chain = self.config.chain
            if chain in NETWORK_TOKENS:
                network_tokens = NETWORK_TOKENS[chain]

                # Define token decimals for NETWORK_TOKENS (standard decimals)
                token_decimals = {"WETH": 18, "ETH": 18, "WBTC": 8, "WBTC.b": 8, "USDC": 6, "USDT": 6, "ARB": 18, "LINK": 18, "wstETH": 18, "WAVAX": 18, "AVAX": 18}

                for symbol, address in network_tokens.items():
                    if address not in chain_tokens:
                        # Add missing token with metadata
                        chain_tokens[address] = {
                            "symbol": symbol,
                            "address": address,
                            "decimals": token_decimals.get(symbol, 18),  # Default to 18 decimals
                        }
                        logging.info(f"Added fallback token data for {symbol} ({address})")

            return chain_tokens

        except Exception as e:
            logging.error(f"Failed to get enhanced tokens: {e}")

            # If everything fails, create basic token data from NETWORK_TOKENS
            chain = self.config.chain
            if chain in NETWORK_TOKENS:
                logging.warning(f"Using only NETWORK_TOKENS fallback data for {chain}")
                network_tokens = NETWORK_TOKENS[chain]

                fallback_tokens = {}
                token_decimals = {"WETH": 18, "ETH": 18, "WBTC": 8, "WBTC.b": 8, "USDC": 6, "USDT": 6, "ARB": 18, "LINK": 18, "wstETH": 18, "WAVAX": 18, "AVAX": 18}

                for symbol, address in network_tokens.items():
                    fallback_tokens[address] = {"symbol": symbol, "address": address, "decimals": token_decimals.get(symbol, 18)}

                return fallback_tokens
            else:
                raise Exception(f"No token data available for chain {chain}")

    def _get_data_processing(self, raw_position: tuple) -> dict[str, Any]:
        """Process raw position data from the reader contract query GetAccountPositions.

        :param raw_position: Raw information returned from the reader contract
        :type raw_position: tuple
        :returns: A processed dictionary containing info on the positions
        :rtype: dict
        """
        # Get market information
        available_markets = self.markets.get_available_markets()
        market_info = available_markets[raw_position[0][1]]

        # Use enhanced token dictionary with NETWORK_TOKENS fallbacks
        chain_tokens = self._get_tokens_address_dict()

        # Get token addresses
        index_token_address = market_info["index_token_address"]
        collateral_token_address = raw_position[0][2]

        # Validate required tokens exist (this prevents the original KeyError)
        if index_token_address not in chain_tokens:
            raise KeyError(f"Index token {index_token_address} not found in token data")

        if collateral_token_address not in chain_tokens:
            raise KeyError(f"Collateral token {collateral_token_address} not found in token data")

        # Calculate entry price - this was line causing KeyError
        index_token_decimals = chain_tokens[index_token_address]["decimals"]
        entry_price = (raw_position[1][0] / raw_position[1][1]) / 10 ** (30 - index_token_decimals)

        # Calculate leverage
        collateral_token_decimals = chain_tokens[collateral_token_address]["decimals"]
        leverage = (raw_position[1][0] / 10**30) / (raw_position[1][2] / 10**collateral_token_decimals)

        # Get oracle prices with error handling
        try:
            prices = OraclePrices(chain=self.config.chain).get_recent_prices()

            # Calculate mark price
            mark_price = np.median(
                [
                    float(prices[index_token_address]["maxPriceFull"]),
                    float(prices[index_token_address]["minPriceFull"]),
                ]
            ) / 10 ** (30 - index_token_decimals)
        except (KeyError, TypeError) as e:
            logging.warning(f"Could not get oracle price for {index_token_address}: {e}")
            mark_price = entry_price  # Fallback to entry price

        # Calculate profit percentage with proper long/short logic
        if entry_price > 0:
            if raw_position[2][0]:  # is_long
                percent_profit = ((mark_price / entry_price) - 1) * leverage * 100
            else:  # is_short
                percent_profit = (1 - (mark_price / entry_price)) * leverage * 100
        else:
            percent_profit = 0

        return {
            "account": raw_position[0][0],
            "market": raw_position[0][1],
            "market_symbol": market_info["market_symbol"],
            "collateral_token": chain_tokens[collateral_token_address]["symbol"],
            "position_size": raw_position[1][0] / 10**30,
            "size_in_tokens": raw_position[1][1],
            "entry_price": entry_price,
            "initial_collateral_amount": raw_position[1][2],
            "initial_collateral_amount_usd": raw_position[1][2] / 10**collateral_token_decimals,
            "leverage": leverage,
            "borrowing_factor": raw_position[1][3],
            "funding_fee_amount_per_size": raw_position[1][4],
            "long_token_claimable_funding_amount_per_size": raw_position[1][5],
            "short_token_claimable_funding_amount_per_size": raw_position[1][6],
            "position_modified_at": "",
            "is_long": raw_position[2][0],
            "percent_profit": percent_profit,
            "mark_price": mark_price,
        }

    def get_chain_tokens(self) -> dict[str, Any]:
        """Get chain token information - for backward compatibility with tests.

        :returns: Dictionary mapping token addresses to their information
        :rtype: dict
        """
        return self._get_tokens_address_dict()
