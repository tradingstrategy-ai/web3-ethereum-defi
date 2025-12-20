"""
GMX Swap Order Implementation

Specialized order class for handling token swaps on GMX protocol.
Extends BaseOrder to provide swap-specific functionality and returning unsigned transactions.
"""

import logging
from typing import Optional, Any

from eth_utils import to_checksum_address
from eth_typing import ChecksumAddress

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.order.base_order import BaseOrder, OrderParams, OrderResult
from eth_defi.gmx.constants import PRECISION, ETH_ZERO_ADDRESS
from eth_defi.gmx.contracts import get_contract_addresses, get_reader_contract
from eth_defi.gmx.utils import determine_swap_route, get_oracle_address
from eth_defi.token import fetch_erc20_details


logger = logging.getLogger(__name__)


class SwapOrder(BaseOrder):
    """GMX Swap Order class for token-to-token swaps.

    Handles creation of swap transactions on GMX protocol, providing
    estimation capabilities and unsigned transaction generation for
    external signing.

    Example:
        >TODO: Add example usage
    """

    def __init__(self, config: GMXConfig, start_token: ChecksumAddress, out_token: ChecksumAddress):
        """Initialise swap order with token addresses.

        :param config: GMX configuration
        :type config: GMXConfig
        :param start_token: Input token address (hex)
        :type start_token: ChecksumAddress
        :param out_token: Output token address (hex)
        :type out_token: ChecksumAddress
        """
        super().__init__(config)

        # Convert to checksum addresses
        self.start_token = to_checksum_address(start_token)
        self.out_token = to_checksum_address(out_token)

        logger.debug("Initialized swap order: %s -> %s", self.start_token, self.out_token)

    def create_swap_order(
        self,
        amount_in: int | float,
        slippage_percent: float = 0.005,
        min_output_amount: int = 0,
        execution_buffer: float = 2.2,
        auto_cancel: bool = False,
    ) -> OrderResult:
        """Create a swap order transaction.

        Creates an unsigned transaction for swapping tokens on GMX. The transaction
        needs to be signed and sent by the user.

        :param amount_in: Amount of input tokens to swap (in token's smallest unit, e.g., wei)
        :type amount_in: int | float
        :param slippage_percent: Maximum acceptable slippage (default 0.5%)
        :type slippage_percent: float
        :param min_output_amount: Minimum output amount (0 for auto-calculation)
        :type min_output_amount: int
        :param execution_buffer: Gas execution buffer multiplier (default 2.2)
        :type execution_buffer: float
        :param auto_cancel: Whether to auto-cancel if execution fails
        :type auto_cancel: bool
        :return: Transaction result with unsigned transaction
        :rtype: OrderResult
        """
        # Validate amount
        if amount_in <= 0:
            raise ValueError("Amount must be positive")

        # Determine swap route and market
        markets = self.markets.get_available_markets()
        swap_route, is_multi_swap = determine_swap_route(markets, self.start_token, self.out_token, self.chain)

        if not swap_route:
            raise ValueError(f"No swap route found from {self.start_token} to {self.out_token}")

        logger.debug("Swap route determined: %d market(s)", len(swap_route))
        if is_multi_swap:
            logger.debug("Multi-market swap required")

        # For swaps, we need any market to get a valid index token, but the actual market in order will be zero address
        first_market_key = swap_route[0]
        market_data = markets.get(first_market_key)
        if not market_data:
            raise ValueError(f"Market {first_market_key} not found")

        # Convert amount_in to string (in token's smallest unit)
        amount_str = str(int(amount_in))

        # Calculate min_output_amount if not provided (auto-calculation)
        if min_output_amount == 0:
            try:
                # Estimate swap output to calculate minimum
                estimation = self.estimate_swap_output(int(amount_in), market_key=first_market_key)
                estimated_output = estimation["out_token_amount"]

                # Apply slippage to get minimum acceptable output
                # For swaps, we reduce expected output by slippage percentage
                min_output_amount = int(estimated_output * (1 - slippage_percent))

                logger.debug(
                    "Auto-calculated min_output_amount: %d (from estimated %d with %.2f%% slippage)",
                    min_output_amount,
                    estimated_output,
                    slippage_percent * 100,
                )
            except Exception as e:
                logger.warning("Could not estimate swap output, using min_output_amount=0: %s", e)
                min_output_amount = 0

        # Create order parameters for swap
        # Note: For swaps, market_key is zero address
        order_params = OrderParams(
            market_key=ETH_ZERO_ADDRESS,  # Zero address for swaps (not a real market)
            collateral_address=self.start_token,
            index_token_address=market_data["index_token_address"],
            is_long=False,  # Not relevant for swaps
            size_delta=0.0,  # No position size for swaps
            initial_collateral_delta_amount=amount_str,
            slippage_percent=slippage_percent,
            swap_path=swap_route,
            execution_buffer=execution_buffer,
            auto_cancel=auto_cancel,
            min_output_amount=min_output_amount,
        )

        # Build and return unsigned transaction
        return self.order_builder(order_params, is_swap=True)

    def estimate_swap_output(self, amount_in: int, market_key: Optional[str] = None) -> dict[str, Any]:
        """Estimate the output amount and price impact for a swap.

        Queries the GMX Reader contract to estimate swap output without
        executing the transaction.

        :param amount_in: Amount of input tokens (in token's smallest unit)
        :type amount_in: int
        :param market_key: Specific market to use (auto-detected if None)
        :type market_key: Optional[str]
        :return: Dictionary with estimated output and price impact
        :rtype: dict[str, Any]

        Example return value:
            {
                "out_token_amount": 950000000,  # Output amount in the smallest unit
                "price_impact_usd": -0.0025,  # Price impact in USD
                "estimated_output_formatted": 950.0  # Formatted output amount
            }
        """
        markets = self.markets.get_available_markets()

        # Determine market to use
        if market_key is None:
            swap_route, is_multi_swap = determine_swap_route(markets, self.start_token, self.out_token, self.chain)
            if not swap_route:
                raise ValueError(f"No swap route found from {self.start_token} to {self.out_token}")

            # Use the first market in route for estimation
            market_key = swap_route[0]

        market_data = markets.get(market_key)
        if not market_data:
            raise ValueError(f"Market {market_key} not found")

        # Get current oracle prices
        prices = self.oracle_prices.get_recent_prices()
        contract_addresses = get_contract_addresses(self.chain)

        # Get oracle addresses for price lookup
        index_oracle = get_oracle_address(self.chain, market_data["index_token_address"])
        long_oracle = get_oracle_address(self.chain, market_data["long_token_address"])
        short_oracle = get_oracle_address(self.chain, market_data["short_token_address"])

        # Build parameters for swap estimation
        estimation_params = {
            "data_store_address": contract_addresses.datastore,
            "market_addresses": [
                market_data["gmx_market_address"],
                market_data["index_token_address"],
                market_data["long_token_address"],
                market_data["short_token_address"],
            ],
            "token_prices_tuple": [
                [
                    int(prices[index_oracle]["maxPriceFull"]),
                    int(prices[index_oracle]["minPriceFull"]),
                ],
                [
                    int(prices[long_oracle]["maxPriceFull"]),
                    int(prices[long_oracle]["minPriceFull"]),
                ],
                [
                    int(prices[short_oracle]["maxPriceFull"]),
                    int(prices[short_oracle]["minPriceFull"]),
                ],
            ],
            "token_in": self.start_token,
            "token_amount_in": int(amount_in),
            "ui_fee_receiver": ETH_ZERO_ADDRESS,
        }

        # Call the reader contract for estimation
        reader_contract = get_reader_contract(self.web3, self.chain)

        try:
            result = reader_contract.functions.getSwapAmountOut(
                estimation_params["data_store_address"],
                estimation_params["market_addresses"],
                estimation_params["token_prices_tuple"],
                estimation_params["token_in"],
                estimation_params["token_amount_in"],
                estimation_params["ui_fee_receiver"],
            ).call()

            # Get output token decimals for formatting
            out_token_details = fetch_erc20_details(self.web3, self.out_token, chain_id=self.chain_id)

            return {
                "out_token_amount": result[0],
                "price_impact_usd": result[1] / (10**PRECISION),
                "estimated_output_formatted": result[0] / (10**out_token_details.decimals),
            }

        except Exception as e:
            logger.error("Failed to estimate swap output: %s", e)
            raise ValueError(f"Could not estimate swap output: {e}")

    # CCXT-compatible convenience methods
    def create_market_swap(
        self,
        amount_in: int | float,
        slippage_percent: float = 0.005,
        execution_buffer: float = 2.2,
    ) -> OrderResult:
        """Create a market swap order (CCXT-style method).

        Convenience method that matches CCXT trading interface patterns.

        :param amount_in: Amount of input tokens
        :type amount_in: int | float
        :param slippage_percent: Slippage tolerance
        :type slippage_percent: float
        :param execution_buffer: Gas execution buffer multiplier
        :type execution_buffer: float
        :return: Transaction result
        :rtype: OrderResult
        """
        return self.create_swap_order(
            amount_in=amount_in,
            slippage_percent=slippage_percent,
            execution_buffer=execution_buffer,
        )
