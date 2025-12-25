# """
# Tests for GMXOrderManager with parametrized chain testing.
#
# This test suite tests the functionality of the GMXOrderManager class
# when connected to different networks using Anvil forks.
# """
#
# import pytest
#
# from gmx_python_sdk.scripts.v2.order.create_decrease_order import DecreaseOrder
# from eth_defi.gmx.order import GMXOrderManager
#
#
# def test_initialization(chain_name, gmx_config):
#     """
#     Test that the order manager initializes correctly with chain-specific config.
#     """
#     order_manager = GMXOrderManager(gmx_config)
#     assert order_manager.config == gmx_config
#     assert order_manager.config.get_chain().lower() == chain_name.lower()
#
#
# def test_get_open_positions_with_known_address(chain_name, gmx_config, account_with_positions):
#     """
#     Test fetching open positions from a known address with positions.
#
#     This test uses a real address known to have GMX positions to verify
#     that we can retrieve position data.
#     """
#     # Create order manager with our config
#     order_manager = GMXOrderManager(gmx_config)
#
#     # Get positions for the known address
#     positions = order_manager.get_open_positions(address=account_with_positions)
#
#     # Verify we got position data
#     assert positions is not None
#     assert isinstance(positions, dict)
#
#     # Check the structure of returned data
#     # Note: This might fail if the address has no positions at test time
#     if positions:
#         for key, position in positions.items():
#             # Keys should be in format "MARKET_DIRECTION"
#             assert "_" in key
#             parts = key.split("_")
#             assert len(parts) == 2
#             assert parts[1].lower() in ["long", "short"]
#
#             # Position should have basic data structure
#             assert "position_size" in position
#             assert "entry_price" in position
#             assert "funding_fee_amount_per_size" in position
#             assert "short_token_claimable_funding_amount_per_size" in position
#             assert isinstance(position["entry_price"], float)
#
#             print(f"Found position {key} with size {position['position_size']}")
#
#
# def test_fail_close_position_parameter_validation(chain_name, order_manager):
#     """
#     Test validation logic when providing incomplete parameters.
#     """
#     # Select appropriate index token based on the chain
#     index_token = "ETH" if chain_name == "arbitrum" else "AVAX"
#
#     # Missing required parameters
#     invalid_params = {
#         "chain": chain_name,
#         "index_token_symbol": index_token,
#         # Missing collateral_token_symbol
#         "is_long": True,
#         # Missing size_delta_usd
#     }
#
#     # Should raise ValueError for missing parameters
#     with pytest.raises(ValueError):
#         order_manager.close_position(parameters=invalid_params, debug_mode=True)
#
#
# def test_close_position_creates_valid_order(chain_name, order_manager):
#     """
#     Test that close_position creates a valid decrease order.
#
#     This test verifies order creation with debug_mode=False.
#     """
#     # Select appropriate parameters based on the chain
#     if chain_name == "arbitrum":
#         index_token = "ETH"
#         collateral_token = "ETH"
#         size_delta = 1000  # $1000 for ETH on Arbitrum
#         collateral_delta = 0.1  # 0.1 ETH
#     else:  # avalanche
#         index_token = "AVAX"
#         collateral_token = "AVAX"
#         size_delta = 10  # $10 for AVAX on Avalanche
#         collateral_delta = 2  # 2 AVAX
#
#     # Valid parameters for closing a position
#     valid_params = {
#         "chain": chain_name,
#         "index_token_symbol": index_token,
#         "collateral_token_symbol": collateral_token,
#         "start_token_symbol": collateral_token,
#         "is_long": True,
#         "size_delta_usd": size_delta,
#         "initial_collateral_delta": collateral_delta,
#         "slippage_percent": 0.05,
#     }  # 5% slippage
#
#     # Create order in debug mode
#     order = order_manager.close_position(parameters=valid_params, debug_mode=False)
#
#     # Verify order was created with correct type
#     assert isinstance(order, DecreaseOrder)
#
#     # Verify order properties
#     assert order.debug_mode is False
#     assert order.is_long is True  # Should be a long position
#
#     # Verify slippage was set correctly
#     assert order.slippage_percent == 0.05
#
#     # Verify key parameters were passed correctly
#     assert hasattr(order, "market_key")
#     assert hasattr(order, "collateral_address")
#     assert hasattr(order, "index_token_address")
#     assert hasattr(order, "size_delta")
#     assert hasattr(order, "initial_collateral_delta_amount")
#
#
# def test_fail_close_position_by_key_raises_for_invalid_key(order_manager):
#     """
#     Test that close_position_by_key raises an error for invalid position keys.
#     """
#     # Try to close a non-existent position
#     with pytest.raises(ValueError, match="Position with key .* not found"):
#         order_manager.close_position_by_key(position_key="NON_EXISTENT_KEY", out_token_symbol="ETH", debug_mode=False)
#
#
# def test_fail_close_position_by_key_invalid_format(order_manager):
#     """
#     Test that close_position_by_key validates key format.
#     """
#     # Invalid key format (no underscore)
#     with pytest.raises(ValueError, match="Position with key .* not found"):
#         order_manager.close_position_by_key(
#             position_key="INVALID_FORMAT_WITH_NO_PROPER_STRUCTURE",
#             out_token_symbol="ETH",
#             debug_mode=False,
#         )
#
#
# def test_close_position_chain_default(chain_name, order_manager):
#     """
#     Test that the chain parameter defaults to the config chain.
#     """
#     # Select appropriate parameters based on the chain
#     if chain_name == "arbitrum":
#         index_token = "ETH"
#         collateral_token = "ETH"
#         size_delta = 1000
#         collateral_delta = 0.1
#     else:  # avalanche
#         index_token = "AVAX"
#         collateral_token = "AVAX"
#         size_delta = 10
#         collateral_delta = 2
#
#     # Valid parameters but without chain specification
#     params = {
#         "index_token_symbol": index_token,
#         "collateral_token_symbol": collateral_token,
#         "start_token_symbol": collateral_token,
#         "is_long": True,
#         "size_delta_usd": size_delta,
#         "initial_collateral_delta": collateral_delta,
#         "slippage_percent": 0.05,
#     }
#
#     # Create order
#     order = order_manager.close_position(parameters=params, debug_mode=False)
#
#     # Verify order was created
#     assert isinstance(order, DecreaseOrder)
#
#     # The fact that the order was created means the chain defaulted correctly
#
#
# def test_close_position_partial_amount(chain_name, order_manager):
#     """
#     Test creating an order to close a partial amount of a position.
#     """
#     # Select appropriate parameters based on the chain
#     if chain_name == "arbitrum":
#         index_token = "ETH"
#         collateral_token = "ETH"
#         size_delta = 100  # Only close $100 worth
#         collateral_delta = 0.01  # Remove only 0.01 ETH
#     else:  # avalanche
#         index_token = "AVAX"
#         collateral_token = "AVAX"
#         size_delta = 20  # Only close $20 worth
#         collateral_delta = 2  # Remove only 2 AVAX
#
#     # Parameters for closing a small amount
#     params = {
#         "chain": chain_name,
#         "index_token_symbol": index_token,
#         "collateral_token_symbol": collateral_token,
#         "start_token_symbol": collateral_token,
#         "is_long": True,
#         "size_delta_usd": size_delta,
#         "initial_collateral_delta": collateral_delta,
#         "slippage_percent": 0.03,
#     }  # 3% slippage
#
#     # Create order
#     order = order_manager.close_position(parameters=params, debug_mode=False)
#
#     # Verify order was created with correct type
#     assert isinstance(order, DecreaseOrder)
#
#     # Verify size delta was set correctly
#     # But we can verify it's non-zero
#     assert order.size_delta > 0
#
#     # Verify collateral delta was set
#     assert int(order.initial_collateral_delta_amount) > 0
#
#
# def test_full_workflow_with_existing_positions(chain_name, order_manager, account_with_positions):
#     """
#     Test a more complete workflow using existing positions from a known account.
#
#     This test:
#     1. Gets positions from a known account
#     2. If positions exist, attempts to create a close order for the first one
#     """
#     # Get positions from a known account
#     positions = order_manager.get_open_positions(address=account_with_positions)
#
#     # Skip if no positions found
#     if not positions:
#         pytest.skip("No positions found on the test account")
#
#     # Get the first position key
#     position_key = next(iter(positions.keys()))
#
#     # Get details for constructing parameters
#     position = positions[position_key]
#     market_symbol = position["market_symbol"][0]
#     is_long = position["is_long"]
#
#     # Create parameters for closing a tiny amount of the position
#     size_delta = min(5 if chain_name == "avalanche" else 10, position["position_size"] * 0.01)  # Close 1% or $5/$10
#
#     params = {
#         "chain": chain_name,
#         "index_token_symbol": market_symbol,
#         "collateral_token_symbol": market_symbol,
#         "start_token_symbol": market_symbol,
#         "is_long": is_long,
#         "size_delta_usd": size_delta,
#         "initial_collateral_delta": position["inital_collateral_amount"] * 0.01,
#         "slippage_percent": 0.05,
#     }  # Remove 1% of collateral  # 5% slippage
#
#     # Create order
#     order = order_manager.close_position(parameters=params, debug_mode=False)
#
#     # Verify order was created
#     assert isinstance(order, DecreaseOrder)
#     assert order.is_long == is_long
#
#     # Get positions again (to make sure we have the latest data)
#     positions = order_manager.get_open_positions(address=account_with_positions)
#
#     # Skip if we can't find the position anymore
#     if position_key not in positions:
#         pytest.skip(f"Position {position_key} no longer exists")
#
#     out_token_symbol = positions[position_key]["collateral_token"]
#
#     # Now try using close_position_by_key with the same position
#     order2 = order_manager.close_position_by_key(
#         position_key=position_key,
#         out_token_symbol=out_token_symbol,
#         amount_of_position_to_close=0.01,
#         amount_of_collateral_to_remove=0.01,
#         slippage_percent=0.05,
#         debug_mode=False,
#         address=account_with_positions,
#     )  # Close 1%  # Remove 1% of collateral
#
#     # Verify the second order was created
#     assert isinstance(order2, DecreaseOrder)
#     assert order2.is_long == is_long
