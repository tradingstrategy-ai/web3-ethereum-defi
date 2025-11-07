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
from eth_defi.gmx.contracts import (
    get_contract_addresses,
    get_tokens_address_dict,
    NETWORK_TOKENS,
    TESTNET_TO_MAINNET_ORACLE_TOKENS,
)
from eth_defi.gmx.types import MarketData
from eth_defi.token import fetch_erc20_details


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
            try:
                raw_positions = self.reader_contract.functions.getAccountPositions(datastore_address, checksum_address, 0, 10).call()
            except Exception as decode_error:
                # Handle decoding errors gracefully - could be due to:
                # 1. Contract ABI mismatch
                # 2. No positions for this address
                # 3. Network/RPC issues
                error_msg = str(decode_error)
                if "Could not decode" in error_msg or "InsufficientDataBytes" in error_msg:
                    print(f"Could not decode positions for address {checksum_address}: {decode_error}")
                    # Return empty dict for addresses with no valid positions
                    return {}
                else:
                    # Re-raise other errors
                    raise

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

    def _fetch_token_details_from_chain(self, token_address: str) -> dict[str, Any]:
        """Fetch token details directly from the blockchain.

        :param token_address: Token contract address
        :returns: Dictionary with token symbol, address, and decimals
        :rtype: dict
        """
        try:
            token_details = fetch_erc20_details(self.config.web3, token_address)
            return {
                "symbol": token_details.symbol,
                "address": token_address,
                "decimals": token_details.decimals,
            }
        except Exception as e:
            logging.warning(f"Failed to fetch token details for {token_address}: {e}")
            # Return default assuming 18 decimals
            return {
                "symbol": "UNKNOWN",
                "address": token_address,
                "decimals": 18,
            }

    def _get_tokens_address_dict(self) -> dict[str, Any]:
        """Enhanced version of get_tokens_address_dict that fetches token data from blockchain.

        This method fetches token details (including decimals) directly from the blockchain
        contracts, ensuring accurate data for all tokens without maintaining hardcoded lists.

        :returns: Dictionary mapping token addresses to their information
        :rtype: dict
        """
        try:
            # Get tokens from GMX API using the original function
            chain_tokens = get_tokens_address_dict(self.config.chain)

            # If chain_tokens is symbol -> address mapping, we need to fetch details
            if chain_tokens and isinstance(list(chain_tokens.values())[0], str):
                # Fetch token details from blockchain for each address
                enhanced_tokens = {}
                for symbol, address in chain_tokens.items():
                    token_details = self._fetch_token_details_from_chain(address)
                    enhanced_tokens[address] = token_details
                    logging.debug(f"Fetched token details for {symbol}: {token_details['decimals']} decimals")

                return enhanced_tokens

            # If it's already address -> metadata format, verify/enhance with blockchain data
            enhanced_tokens = {}
            for address, token_info in chain_tokens.items():
                if isinstance(token_info, dict):
                    # Already has metadata, but we can verify decimals from chain if needed
                    enhanced_tokens[address] = token_info
                else:
                    # Fetch from chain
                    enhanced_tokens[address] = self._fetch_token_details_from_chain(address)

            # Add missing tokens from NETWORK_TOKENS if needed
            chain = self.config.chain
            if chain in NETWORK_TOKENS:
                network_tokens = NETWORK_TOKENS[chain]
                for symbol, address in network_tokens.items():
                    if address not in enhanced_tokens:
                        token_details = self._fetch_token_details_from_chain(address)
                        enhanced_tokens[address] = token_details
                        logging.info(f"Added token from NETWORK_TOKENS: {symbol} ({address}) with {token_details['decimals']} decimals")

            return enhanced_tokens

        except Exception as e:
            logging.error(f"Failed to get enhanced tokens: {e}")
            raise e

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

            # Map testnet token addresses to mainnet for oracle lookups
            # Testnets don't have their own oracles, so we use mainnet prices
            oracle_token_address = TESTNET_TO_MAINNET_ORACLE_TOKENS.get(index_token_address, index_token_address)

            # Try to find the price
            price_data = None

            # Try exact match first
            if oracle_token_address in prices:
                price_data = prices[oracle_token_address]
            else:
                # Try case-insensitive match
                oracle_token_lower = oracle_token_address.lower()
                for addr, data in prices.items():
                    if addr.lower() == oracle_token_lower:
                        price_data = data
                        break

            if price_data and "maxPriceFull" in price_data and "minPriceFull" in price_data:
                # Calculate mark price from oracle data
                mark_price = np.median(
                    [
                        float(price_data["maxPriceFull"]),
                        float(price_data["minPriceFull"]),
                    ]
                ) / 10 ** (30 - index_token_decimals)
                logging.debug(f"Got oracle price for {index_token_address}: ${mark_price:.4f}")
            else:
                # Price not found in oracle, use entry price
                logging.debug(f"Oracle price not found for {index_token_address} (oracle address: {oracle_token_address}), using entry price")
                mark_price = entry_price

        except (KeyError, TypeError, ValueError) as e:
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

        # Position struct indices (GMX v2.2):
        # raw_position[0] = Addresses (account, market, collateralToken)
        # raw_position[1] = Numbers:
        #   [0] sizeInUsd
        #   [1] sizeInTokens
        #   [2] collateralAmount
        #   [3] pendingImpactAmount (NEW in v2.2)
        #   [4] borrowingFactor
        #   [5] fundingFeeAmountPerSize
        #   [6] longTokenClaimableFundingAmountPerSize
        #   [7] shortTokenClaimableFundingAmountPerSize
        #   [8] increasedAtTime
        #   [9] decreasedAtTime
        # raw_position[2] = Flags (isLong)

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
            "pending_impact_amount": raw_position[1][3],  # NEW in v2.2
            "borrowing_factor": raw_position[1][4],  # Shifted from [3] to [4]
            "funding_fee_amount_per_size": raw_position[1][5],  # Shifted from [4] to [5]
            "long_token_claimable_funding_amount_per_size": raw_position[1][6],  # Shifted from [5] to [6]
            "short_token_claimable_funding_amount_per_size": raw_position[1][7],  # Shifted from [6] to [7]
            "increased_at_time": raw_position[1][8],  # NEW in v2.2
            "decreased_at_time": raw_position[1][9],  # NEW in v2.2
            "position_modified_at": "",  # Deprecated, keeping for backward compatibility
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
