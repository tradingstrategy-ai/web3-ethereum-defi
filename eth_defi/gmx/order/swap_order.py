# """
# GMX Swap Order Implementation
#
# Specialized order class for handling token swaps on GMX protocol.
# Extends BaseOrder to provide swap-specific functionality and returning unsigned transactions.
# """
#
# import logging
# from typing import Optional, Any
#
# from cchecksum import to_checksum_address
# from eth_typing import ChecksumAddress
#
# from eth_defi.gmx.order.base_order import BaseOrder, OrderParams, OrderType
# from eth_defi.gmx.constants import PRECISION
# from eth_defi.gmx.contracts import get_contract_addresses
# from eth_defi.gmx.utils import determine_swap_route
#
#
# class SwapOrder(BaseOrder):
#     """GMX Swap Order class for token-to-token swaps.
#
#     Handles creation of swap transactions on GMX protocol, providing
#     estimation capabilities and unsigned transaction generation for
#     external signing.
#     """
#
#     def __init__(self, config, start_token: ChecksumAddress, out_token: ChecksumAddress):
#         """Initialize swap order with token addresses.
#
#         :param config: GMX configuration
#         :type config: GMXConfig
#         :param start_token: Input token address (hex)
#         :type start_token: ChecksumAddress
#         :param out_token: Output token address (hex)
#         :type out_token: ChecksumAddress
#         """
#         super().__init__(config)
#
#         # Convert to checksum addresses
#         self.start_token = to_checksum_address(start_token)
#         self.out_token = to_checksum_address(out_token)
#
#         self.logger = logging.getLogger(f"{self.__class__.__name__}")
#         self.logger.info(f"Initialised swap order: {self.start_token} -> {self.out_token}")
#
#     def create_swap_order(
#         self,
#         amount_in: int | float,
#         slippage_percent: float = 0.005,
#         min_output_amount: int = 0,
#         params: Optional[dict] = None,
#     ) -> TransactionResult:
#         """Create a swap order transaction.
#
#         :param amount_in: Amount of input tokens to swap
#         :type amount_in: int | float
#         :param slippage_percent: Maximum acceptable slippage (default 0.5%)
#         :type slippage_percent: float
#         :param min_output_amount: Minimum output amount (0 for auto-calculation)
#         :type min_output_amount: int
#         :param params: Additional parameters
#         :type params: Optional[dict]
#         :return: Transaction result with unsigned transaction
#         :rtype: TransactionResult
#         """
#         # Determine swap route and market
#         markets = self.markets.get_available_markets()
#         swap_route, is_multi_swap = determine_swap_route(markets, self.start_token, self.out_token, self.chain)
#
#         if not swap_route:
#             raise ValueError(f"No swap route found from {self.start_token} to {self.out_token}")
#
#         # Use the first market in the route for symbol determination
#         market_data = markets[swap_route[0]]
#         symbol = f"{market_data.get('market_symbol', 'UNKNOWN')}/USD"
#
#         # Create order parameters for swap
#         order_params = OrderParams(
#             symbol=symbol,
#             type=OrderType.MARKET_SWAP,
#             side=OrderSide.BUY,  # Conceptually buying the output token
#             amount=amount_in,
#             market_key=swap_route[0],
#             collateral_address=self.start_token,
#             index_token_address=market_data.get("index_token_address"),
#             is_long=True,  # Not relevant for swaps
#             slippage_percent=slippage_percent,
#             swap_path=swap_route,
#             min_output_amount=min_output_amount,
#             **(params or {}),
#         )
#
#         return self.create_order(order_params)
#
#     def estimate_swap_output(self, amount_in: int, market_data: Optional[dict] = None) -> dict[str, Any]:
#         """Estimate the output amount and price impact for a swap.
#
#         :param amount_in: Amount of input tokens
#         :type amount_in: int
#         :param market_data: Specific market data (auto-detected if None)
#         :type market_data: Optional[dict]
#         :return: Dictionary with estimated output and price impact
#         :rtype: dict[str, Any]
#         """
#         markets = self.markets.get_available_markets()
#
#         if market_data is None:
#             swap_route, is_multi_swap = determine_swap_route(markets, self.start_token, self.out_token, self.chain)
#             if not swap_route:
#                 raise ValueError(f"No swap route found from {self.start_token} to {self.out_token}")
#             market_data = markets[swap_route[0]]
#
#         prices = self.oracle_prices.get_recent_prices()
#         contract_addresses = get_contract_addresses(self.chain)
#
#         # Build parameters for swap estimation
#         estimation_params = {
#             "data_store_address": contract_addresses.datastore,
#             "market_addresses": [
#                 market_data["gmx_market_address"],
#                 market_data["index_token_address"],
#                 market_data["long_token_address"],
#                 market_data["short_token_address"],
#             ],
#             "token_prices_tuple": [
#                 [
#                     int(prices[market_data["index_token_address"]]["maxPriceFull"]),
#                     int(prices[market_data["index_token_address"]]["minPriceFull"]),
#                 ],
#                 [
#                     int(prices[market_data["long_token_address"]]["maxPriceFull"]),
#                     int(prices[market_data["long_token_address"]]["minPriceFull"]),
#                 ],
#                 [
#                     int(prices[market_data["short_token_address"]]["maxPriceFull"]),
#                     int(prices[market_data["short_token_address"]]["minPriceFull"]),
#                 ],
#             ],
#             "token_in": self.start_token,
#             "token_amount_in": amount_in,
#             "ui_fee_receiver": "0x0000000000000000000000000000000000000000",
#         }
#
#         # Call the reader contract for estimation
#         reader_contract = self._get_reader_contract()
#
#         try:
#             result = reader_contract.functions.getSwapAmountOut(
#                 estimation_params["data_store_address"],
#                 estimation_params["market_addresses"],
#                 estimation_params["token_prices_tuple"],
#                 estimation_params["token_in"],
#                 estimation_params["token_amount_in"],
#                 estimation_params["ui_fee_receiver"],
#             ).call()
#
#             # Get output token decimals for formatting
#             from eth_defi.token import fetch_erc20_details
#             out_token_details = fetch_erc20_details(self.web3, self.out_token)
#
#             return {
#                 "out_token_amount": result[0],
#                 "price_impact_usd": result[1] / (10**PRECISION),
#                 "estimated_output_formatted": result[0] / (10**out_token_details.decimals),
#             }
#
#         except Exception as e:
#             self.logger.error(f"Failed to estimate swap output: {e}")
#             raise ValueError(f"Could not estimate swap output: {e}")
#
#     def _get_reader_contract(self):
#         """Get the reader contract for swap estimations.
#
#         :return: Reader contract instance
#         """
#         from eth_defi.gmx.contracts import get_reader_contract
#
#         return get_reader_contract(self.web3, self.chain)
#
#     # CCXT-compatible convenience methods
#     def create_market_swap(
#         self,
#         amount_in: int | float,
#         slippage_percent: float = 0.005,
#         params: Optional[dict] = None,
#     ) -> TransactionResult:
#         """Create a market swap order (CCXT-style method).
#
#         :param amount_in: Amount of input tokens
#         :type amount_in: int | float
#         :param slippage_percent: Slippage tolerance
#         :type slippage_percent: float
#         :param params: Additional parameters
#         :type params: Optional[dict]
#         :return: Transaction result
#         :rtype: TransactionResult
#         """
#         return self.create_swap_order(
#             amount_in=amount_in,
#             slippage_percent=slippage_percent,
#             params=params,
#         )
