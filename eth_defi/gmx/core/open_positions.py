"""
GMX Open Positions Data Module.

This module provides access to open positions data for user addresses.
"""

import logging
from typing import Any, Optional

import numpy as np
from eth_utils import to_checksum_address

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.core.get_data import GetData
from eth_defi.gmx.core.oracle import OraclePrices
from eth_defi.gmx.contracts import (
    get_contract_addresses,
    get_tokens_metadata_dict,
    NETWORK_TOKENS_METADATA,
    TESTNET_TO_MAINNET_ORACLE_TOKENS,
    GMX_SUBSQUID_ENDPOINTS,
)
from eth_defi.gmx.types import MarketData

logger = logging.getLogger(__name__)


class GetOpenPositions(GetData):
    """Open positions data provider for GMX protocol.

    Retrieves all open trading positions for a specific user address.
    Provides detailed information about current trading positions,
    including position sizes, entry prices, current profit/loss, margin requirements,
    and liquidation thresholds.
    """

    def __init__(self, config: GMXConfig, filter_swap_markets: bool = True, use_graphql: bool = False):
        """Initialize open positions data provider.

        :param config: GMXConfig instance containing chain and network info
        :param filter_swap_markets: Whether to filter out swap markets from results
        :param use_graphql: Whether to use GraphQL (faster) instead of RPC calls (default: False - uses contract calls)
        """
        super().__init__(config, filter_swap_markets=filter_swap_markets)
        self.use_graphql = use_graphql

    def get_data(self, address: str) -> MarketData:
        """Get all open positions for a given address on the configured chain.

        Uses GraphQL (fast) by default, falls back to RPC if GraphQL fails or is disabled.

        :param address: User wallet address to query positions for
        :returns: A dictionary containing the open positions, where asset and direction are the keys
        :rtype: dict
        """
        # Convert address to checksum format
        checksum_address = to_checksum_address(address)

        # Try GraphQL first if enabled
        if self.use_graphql:
            try:
                return self._get_data_via_graphql(checksum_address)
            except Exception as e:
                logger.warning(f"GraphQL query failed, falling back to RPC: {e}")
                # Fall through to RPC method

        # RPC method (original implementation)
        # Normalize chain name to lowercase string
        chain_name = self.config.chain.lower() if isinstance(self.config.chain, str) else str(self.config.chain).lower()

        try:
            contract_addresses = get_contract_addresses(chain_name)
            datastore_address = contract_addresses.datastore

            # Get raw positions from reader contract
            try:
                raw_positions = self.reader_contract.functions.getAccountPositions(datastore_address, checksum_address, 0, 10).call()
            except Exception as decode_error:
                # Handle decoding errors - could be due to:
                # 1. Contract ABI mismatch
                # 2. No positions for this address
                # 3. Network/RPC issues
                error_msg = str(decode_error)
                logger.error(
                    f"Could not decode positions for address {checksum_address}: {error_msg}",
                )
                # Return empty dict for addresses with no valid positions
                raise decode_error

            if len(raw_positions) == 0:
                logger.info(
                    f'No positions open for address: "{checksum_address}" on {chain_name.title()}.',
                )
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

    def _get_data_via_graphql(self, address: str) -> MarketData:
        """Fetch open positions using GraphQL (faster than RPC).

        Queries GMX Subsquid endpoint for position data, which is significantly faster than
        calling the Reader contract via RPC.

        :param address: Checksum wallet address
        :returns: Dictionary of positions matching RPC format
        :rtype: dict
        """
        import requests

        # Get Subsquid GraphQL URL for current chain (normalize to lowercase)
        chain_name = self.config.chain.lower() if isinstance(self.config.chain, str) else str(self.config.chain).lower()
        subgraph_url = GMX_SUBSQUID_ENDPOINTS.get(chain_name)
        if not subgraph_url:
            raise ValueError(f"No GraphQL endpoint configured for chain: {chain_name}")

        # GraphQL query to fetch positions
        query = """
        query GetPositions($account: String!) {
          positions(where: {account_eq: $account, sizeInUsd_gt: "0"}, limit: 100) {
            id
            positionKey
            account
            market
            collateralToken
            isLong
            sizeInUsd
            sizeInTokens
            collateralAmount
            entryPrice
            leverage
            realizedPnl
            unrealizedPnl
            realizedFees
            unrealizedFees
            maxSize
            openedAt
          }
        }
        """

        # Subsquid stores addresses in checksummed format (case-sensitive)
        variables = {"account": address}

        try:
            response = requests.post(
                subgraph_url,
                json={"query": query, "variables": variables},
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            if "errors" in data:
                raise ValueError(f"GraphQL errors: {data['errors']}")

            positions_data = data.get("data", {}).get("positions", [])

            if not positions_data:
                logger.info(f'No positions found via GraphQL for address: "{address}" on {chain_name.title()}')
                return {}

            # Process GraphQL positions to match RPC format
            processed_positions = {}
            chain_tokens = self._get_tokens_address_dict()
            oracle_prices = OraclePrices(chain=chain_name)
            logger.info("Fetching oracle prices (may take a few seconds)...")
            prices = oracle_prices.get_recent_prices()

            for pos in positions_data:
                try:
                    # Convert GraphQL position to internal format
                    processed_position = self._process_graphql_position(pos, chain_tokens, prices)

                    # Build key using market symbol and direction
                    direction = "long" if processed_position["is_long"] else "short"
                    key = f"{processed_position['market_symbol']}_{direction}"
                    processed_positions[key] = processed_position

                except Exception as e:
                    logger.warning(f"Failed to process GraphQL position {pos.get('id')}: {e}")
                    continue

            return processed_positions

        except requests.RequestException as e:
            raise ValueError(f"GraphQL request failed: {e}")

    def _process_graphql_position(self, pos: dict, chain_tokens: dict, prices: dict) -> dict[str, Any]:
        """Convert GraphQL position data to internal format matching RPC response.

        .. warning::
            GraphQL provides faster queries but is missing real-time borrowing/funding fields.
            The following fields are set to 0 because they require on-chain computation:

            - ``borrowing_factor``: Requires current market state and time-based accumulation
            - ``funding_fee_amount_per_size``: Calculated from funding rate changes since position opened
            - ``long_token_claimable_funding_amount_per_size``: Real-time funding pool state
            - ``short_token_claimable_funding_amount_per_size``: Real-time funding pool state

            These fields are only available through the Reader contract (RPC method).
            For accurate borrowing/funding data, use ``use_graphql=False`` when initializing GetOpenPositions.

        :param pos: Position data from GraphQL
        :param chain_tokens: Token metadata dictionary
        :param prices: Oracle prices dictionary
        :returns: Processed position matching RPC format
        :rtype: dict
        """
        # Normalize chain name to lowercase string
        chain_name = self.config.chain.lower() if isinstance(self.config.chain, str) else str(self.config.chain).lower()

        # Get market info
        markets = self.markets.get_available_markets()
        market_address = to_checksum_address(pos["market"])
        market_info = None
        for key, market in markets.items():
            if market["gmx_market_address"].lower() == market_address.lower():
                market_info = market
                break

        if not market_info:
            raise ValueError(f"Market not found for address: {market_address}")

        # Get index token from market info
        index_token = to_checksum_address(market_info["index_token_address"])
        collateral_token = to_checksum_address(pos["collateralToken"])

        index_token_info = chain_tokens.get(index_token)
        collateral_token_info = chain_tokens.get(collateral_token)

        if not index_token_info or not collateral_token_info:
            raise ValueError(f"Token info not found for index={index_token} or collateral={collateral_token}")

        # Parse values from GraphQL
        position_size_usd = int(pos["sizeInUsd"]) / 10**30
        size_in_tokens = int(pos["sizeInTokens"])
        collateral_amount = int(pos["collateralAmount"])
        is_long = pos["isLong"]

        # GraphQL provides entry price and leverage directly (different decimal format than RPC)
        entry_price = int(pos["entryPrice"]) / 10 ** (30 - index_token_info["decimals"])
        leverage = int(pos["leverage"]) / 10**4

        # Calculate collateral value in USD
        collateral_decimals = collateral_token_info["decimals"]
        collateral_amount_tokens = collateral_amount / 10**collateral_decimals

        # Get mark price from oracle
        from eth_defi.gmx.utils import get_oracle_address

        index_decimals = index_token_info["decimals"]
        oracle_address = get_oracle_address(chain_name, index_token)
        mark_price = entry_price  # Default fallback
        if oracle_address in prices:
            price_data = prices[oracle_address]
            mark_price = np.median([float(price_data["maxPriceFull"]), float(price_data["minPriceFull"])]) / 10 ** (30 - index_decimals)

        # Get collateral price for USD value calculation
        collateral_oracle = get_oracle_address(chain_name, collateral_token)
        if collateral_oracle in prices:
            collateral_price_data = prices[collateral_oracle]
            collateral_price = np.median([float(collateral_price_data["maxPriceFull"]), float(collateral_price_data["minPriceFull"])]) / 10 ** (30 - collateral_decimals)
        else:
            collateral_price = 1.0  # Assume $1 for stablecoins

        collateral_amount_usd = collateral_amount_tokens * collateral_price

        # Calculate profit percentage using mark price vs entry price
        if entry_price > 0:
            if is_long:
                percent_profit = ((mark_price / entry_price) - 1) * leverage * 100
            else:
                percent_profit = (1 - (mark_price / entry_price)) * leverage * 100
        else:
            percent_profit = 0

        return {
            "account": to_checksum_address(pos["account"]),
            "market": market_address,
            "market_symbol": market_info["market_symbol"],
            "collateral_token": collateral_token_info["symbol"],
            "position_size": position_size_usd,
            "position_size_usd_raw": int(pos["sizeInUsd"]),
            "size_in_tokens": size_in_tokens,
            "entry_price": entry_price,
            "initial_collateral_amount": collateral_amount,
            "initial_collateral_amount_usd": collateral_amount_usd,
            "leverage": leverage,
            # These fields require real-time on-chain calculation and are NOT in GraphQL schema
            # Use RPC method (use_graphql=False) if you need accurate values
            "pending_impact_amount": 0,  # Requires Reader contract call
            "borrowing_factor": 0,  # Requires current market state + time accumulation
            "funding_fee_amount_per_size": 0,  # Requires funding rate history since position opened
            "long_token_claimable_funding_amount_per_size": 0,  # Requires real-time funding pool state
            "short_token_claimable_funding_amount_per_size": 0,  # Requires real-time funding pool state
            "increased_at_time": int(pos.get("openedAt", 0)),
            "decreased_at_time": 0,  # Static field not tracked in GraphQL
            "position_modified_at": "",
            "is_long": is_long,
            "percent_profit": percent_profit,
            "mark_price": mark_price,
        }

    def _get_tokens_address_dict(self) -> dict[str, Any]:
        """Get token metadata from GMX API.

        Fetches token data (symbol, decimals, synthetic flag) from GMX API,
        avoiding expensive smart contract calls for each token.

        Falls back to NETWORK_TOKENS_METADATA for tokens missing from API.

        :returns: Dictionary mapping token addresses to their metadata
        :rtype: dict
        """
        # Normalize chain name to lowercase string
        chain = self.config.chain.lower() if isinstance(self.config.chain, str) else str(self.config.chain).lower()

        try:
            # Get tokens metadata from GMX API (includes decimals - no contract calls needed!)
            chain_tokens = get_tokens_metadata_dict(chain)
            logging.debug(
                f"Fetched {len(chain_tokens)} tokens from GMX API for {chain}",
            )

            # Add missing tokens from NETWORK_TOKENS_METADATA if needed
            if chain in NETWORK_TOKENS_METADATA:
                network_tokens_meta = NETWORK_TOKENS_METADATA[chain]
                for address, metadata in network_tokens_meta.items():
                    if address not in chain_tokens:
                        chain_tokens[address] = metadata
                        logging.info(
                            f"Added token from NETWORK_TOKENS_METADATA: {metadata['symbol']} ({address})",
                        )

            return chain_tokens

        except Exception as e:
            logging.error(f"Failed to get token metadata: {e}")
            raise e

    def _get_data_processing(self, raw_position: tuple) -> dict[str, Any]:
        """Process raw position data from the reader contract query GetAccountPositions.

        :param raw_position: Raw information returned from the reader contract
        :type raw_position: tuple
        :returns: A processed dictionary containing info on the positions
        :rtype: dict
        """
        # Normalize chain name to lowercase string
        chain_name = self.config.chain.lower() if isinstance(self.config.chain, str) else str(self.config.chain).lower()

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

        # Get token decimals
        collateral_token_decimals = chain_tokens[collateral_token_address]["decimals"]

        # Get oracle prices with error handling
        try:
            logger.info("Fetching oracle prices (may take a few seconds)...")
            prices = OraclePrices(chain=chain_name).get_recent_prices()

            # Map testnet token addresses to mainnet for oracle lookups
            # Testnets don't have their own oracles, so we use mainnet prices
            oracle_index_token_address = TESTNET_TO_MAINNET_ORACLE_TOKENS.get(
                index_token_address,
                index_token_address,
            )
            oracle_collateral_token_address = TESTNET_TO_MAINNET_ORACLE_TOKENS.get(
                collateral_token_address,
                collateral_token_address,
            )

            # Helper function to find price data (case-insensitive)
            def find_price_data(token_address: str):
                """Find price data for a token address (case-insensitive)."""
                # Try exact match first
                if token_address in prices:
                    return prices[token_address]
                # Try case-insensitive match
                token_lower = token_address.lower()
                for addr, data in prices.items():
                    if addr.lower() == token_lower:
                        return data
                return None

            # Get index token price (mark price)
            index_price_data = find_price_data(oracle_index_token_address)

            if index_price_data and "maxPriceFull" in index_price_data and "minPriceFull" in index_price_data:
                # Calculate mark price from oracle data
                mark_price = np.median(
                    [
                        float(index_price_data["maxPriceFull"]),
                        float(index_price_data["minPriceFull"]),
                    ]
                ) / 10 ** (30 - index_token_decimals)
                logging.debug(f"Got oracle price for index token {index_token_address}: ${mark_price:.4f},")
            else:
                # Price not found in oracle, use entry price
                logging.debug(
                    f"Oracle price not found for index token {index_token_address} (oracle address: {oracle_index_token_address}), using entry price",
                )
                mark_price = entry_price

            # Get collateral token price for leverage calculation
            collateral_price_data = find_price_data(oracle_collateral_token_address)

            if collateral_price_data and "maxPriceFull" in collateral_price_data and "minPriceFull" in collateral_price_data:
                # Calculate collateral price from oracle data
                collateral_price = np.median(
                    [
                        float(collateral_price_data["maxPriceFull"]),
                        float(collateral_price_data["minPriceFull"]),
                    ]
                ) / 10 ** (30 - collateral_token_decimals)
                logging.debug(f"Got oracle price for collateral token {collateral_token_address}: ${collateral_price:.4f},")
            else:
                # Collateral price not found, assume $1 (for stablecoins) or use mark price (for same-asset collateral)
                if index_token_address.lower() == collateral_token_address.lower():
                    collateral_price = mark_price
                    logging.debug(f"Using mark price for collateral (same as index token): ${collateral_price:.4f},")
                else:
                    collateral_price = 1.0
                    logging.debug(
                        f"Oracle price not found for collateral token {collateral_token_address}, assuming $1 (stablecoin)",
                    )

        except (KeyError, TypeError, ValueError) as e:
            logging.warning(f"Could not get oracle prices: {e}")
            mark_price = entry_price  # Fallback to entry price
            # For collateral, assume $1 for stablecoins or use entry price for same-asset
            if index_token_address.lower() == collateral_token_address.lower():
                collateral_price = entry_price
            else:
                collateral_price = 1.0

        # Calculate leverage with correct formula: leverage = position_size_usd / collateral_usd
        # where collateral_usd = collateral_amount * collateral_price
        position_size_usd = raw_position[1][0] / 10**30
        collateral_amount_tokens = raw_position[1][2] / 10**collateral_token_decimals
        collateral_amount_usd = collateral_amount_tokens * collateral_price

        if collateral_amount_usd > 0:
            leverage = position_size_usd / collateral_amount_usd
        else:
            leverage = 0

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
            "position_size_usd_raw": raw_position[1][0],  # Raw value with 30 decimals - needed for exact position closing
            "size_in_tokens": raw_position[1][1],
            "entry_price": entry_price,
            "initial_collateral_amount": raw_position[1][2],
            "initial_collateral_amount_usd": collateral_amount_usd,  # in USD
            "leverage": leverage,
            "pending_impact_amount": raw_position[1][3],
            "borrowing_factor": raw_position[1][4],
            "funding_fee_amount_per_size": raw_position[1][5],
            "long_token_claimable_funding_amount_per_size": raw_position[1][6],
            "short_token_claimable_funding_amount_per_size": raw_position[1][7],
            "increased_at_time": raw_position[1][8],
            "decreased_at_time": raw_position[1][9],
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
