"""
Tests for GMXOrderManager on Arbitrum network.

This test suite tests the functionality of the GMXOrderManager class
when connected to the Arbitrum network using an Anvil fork.
"""
import pytest
from gmx_python_sdk.scripts.v2.order.create_decrease_order import DecreaseOrder
from eth_defi.gmx.order import GMXOrderManager


def test_initialization(gmx_config_arbitrum):
    """
    Test that the order manager initializes correctly with Arbitrum config.
    """
    order_manager = GMXOrderManager(gmx_config_arbitrum)
    assert order_manager.config == gmx_config_arbitrum
    assert order_manager.config.get_chain().lower() == "arbitrum"


def test_get_open_positions_with_known_address(gmx_config_arbitrum, account_with_positions_arbitrum):
    """
    Test fetching open positions from a known address with positions.

    This test uses a real address known to have GMX positions to verify
    that we can retrieve position data.
    """
    # Create order manager with our config
    order_manager = GMXOrderManager(gmx_config_arbitrum)

    # Get positions for the known address
    positions = order_manager.get_open_positions(address=account_with_positions_arbitrum)

    # Verify we got position data
    assert positions is not None
    assert isinstance(positions, dict)

    # Check the structure of returned data
    # Note: This might fail if the address has no positions at test time
    if positions:
        for key, position in positions.items():
            # Keys should be in format "MARKET_DIRECTION"
            assert "_" in key
            parts = key.split("_")
            assert len(parts) == 2
            assert parts[1].lower() in ["long", "short"]

            # Position should have basic data structure
            assert "position_size" in position
            assert "entry_price" in position
            assert "funding_fee_amount_per_size" in position
            assert "short_token_claimable_funding_amount_per_size" in position
            assert isinstance(position["entry_price"], float)

            print(f"Found position {key} with size {position['position_size']}")


def test_fail_close_position_parameter_validation(order_manager_arbitrum):
    """
    Test validation logic when providing incomplete parameters.
    """
    # Missing required parameters
    invalid_params = {
        "chain": "arbitrum",
        "index_token_symbol": "ETH",
        # Missing collateral_token_symbol
        "is_long": True,
        # Missing size_delta_usd
    }

    # Should raise ValueError for missing parameters
    with pytest.raises(ValueError):
        order_manager_arbitrum.close_position(parameters=invalid_params, debug_mode=True)


def test_close_position_creates_valid_order(order_manager_arbitrum):
    """
    Test that close_position creates a valid decrease order.

    This test verifies order creation with debug_mode=True.
    """
    # Valid parameters for closing an ETH position
    valid_params = {"chain": "arbitrum", "index_token_symbol": "ETH", "collateral_token_symbol": "ETH", "start_token_symbol": "ETH", "is_long": True, "size_delta_usd": 1000, "initial_collateral_delta": 0.1, "slippage_percent": 0.05}  # Close $1000 worth  # Remove 0.1 ETH  # 5% slippage

    # Create order in debug mode
    order = order_manager_arbitrum.close_position(parameters=valid_params, debug_mode=False)

    # Verify order was created with correct type
    assert isinstance(order, DecreaseOrder)

    # Verify order properties
    assert order.debug_mode is False
    assert order.is_long is True  # Should be a long position

    # Verify slippage was set correctly
    assert order.slippage_percent == 0.05

    # Verify key parameters were passed correctly
    assert hasattr(order, "market_key")
    assert hasattr(order, "collateral_address")
    assert hasattr(order, "index_token_address")
    assert hasattr(order, "size_delta")
    assert hasattr(order, "initial_collateral_delta_amount")


def test_fail_close_position_by_key_raises_for_invalid_key(order_manager_arbitrum):
    """
    Test that close_position_by_key raises an error for invalid position keys.
    """
    # Try to close a non-existent position
    with pytest.raises(ValueError, match="Position with key .* not found"):
        order_manager_arbitrum.close_position_by_key(position_key="NON_EXISTENT_KEY", out_token_symbol="ETH", debug_mode=False)


def test_fail_close_position_by_key_invalid_format(order_manager_arbitrum):
    """
    Test that close_position_by_key validates key format.
    """
    # Invalid key format (no underscore)
    with pytest.raises(ValueError, match="Position with key .* not found"):
        order_manager_arbitrum.close_position_by_key(position_key="INVALID_FORMAT_WITH_NO_PROPER_STRUCTURE", out_token_symbol="ETH", debug_mode=False)


def test_close_position_chain_default(order_manager_arbitrum):
    """
    Test that the chain parameter defaults to the config chain.
    """
    # Valid parameters but without chain specification
    params = {"index_token_symbol": "ETH", "collateral_token_symbol": "ETH", "start_token_symbol": "ETH", "is_long": True, "size_delta_usd": 1000, "initial_collateral_delta": 0.1, "slippage_percent": 0.05}

    # Create order in debug mode
    order = order_manager_arbitrum.close_position(parameters=params, debug_mode=False)

    # Verify order was created
    assert isinstance(order, DecreaseOrder)

    # The fact that the order was created means the chain defaulted correctly


def test_close_position_partial_amount(order_manager_arbitrum):
    """
    Test creating an order to close a partial amount of a position.
    """
    # Parameters for closing a small amount
    params = {"chain": "arbitrum", "index_token_symbol": "ETH", "collateral_token_symbol": "ETH", "start_token_symbol": "ETH", "is_long": True, "size_delta_usd": 100, "initial_collateral_delta": 0.01, "slippage_percent": 0.03}  # Only close $100 worth  # Remove only 0.01 ETH  # 3% slippage

    # Create order in debug mode
    order = order_manager_arbitrum.close_position(parameters=params, debug_mode=False)

    # Verify order was created with correct type
    assert isinstance(order, DecreaseOrder)

    # Verify size delta was set correctly
    # Note: The exact value might be different due to conversion in the OrderArgumentParser
    # but we can verify it's non-zero
    assert order.size_delta > 0

    # Verify collateral delta was set
    assert order.initial_collateral_delta_amount > 0


def test_full_workflow_with_existing_positions(order_manager_arbitrum, account_with_positions_arbitrum):
    """
    Test a more complete workflow using existing positions from a known account.

    This test:
    1. Gets positions from a known account
    2. If positions exist, attempts to create a close order for the first one
    """
    # Get positions from a known account
    positions = order_manager_arbitrum.get_open_positions(address=account_with_positions_arbitrum)

    # Skip if no positions found
    if not positions:
        pytest.skip("No positions found on the test account")

    # Get the first position key
    position_key = next(iter(positions.keys()))
    # key_iter = iter(positions)
    # next(key_iter)
    # # get the 2nd key
    # position_key = next(key_iter)
    # print(f"{position_key=}")

    # Get details for constructing parameters
    position = positions[position_key]
    market_symbol = position["market_symbol"][0]
    is_long = position["is_long"]

    # Create parameters for closing a tiny amount of the position
    params = {"chain": "arbitrum", "index_token_symbol": market_symbol, "collateral_token_symbol": market_symbol, "start_token_symbol": market_symbol, "is_long": is_long, "size_delta_usd": min(10, position["position_size"] * 0.01), "initial_collateral_delta": position["inital_collateral_amount"] * 0.01, "slippage_percent": 0.05}  # Close 1% or $10, whichever is smaller  # Remove 1% of collateral  # 5% slippage

    # Create order in debug mode
    order = order_manager_arbitrum.close_position(parameters=params, debug_mode=False)

    # Verify order was created
    assert isinstance(order, DecreaseOrder)
    assert order.is_long == is_long

    positions = order_manager_arbitrum.get_open_positions(address=account_with_positions_arbitrum)

    # Now try using close_position_by_key with the same position
    order2 = order_manager_arbitrum.close_position_by_key(position_key=position_key, out_token_symbol=market_symbol, amount_of_position_to_close=0.01, amount_of_collateral_to_remove=0.01, slippage_percent=0.05, debug_mode=False, address=account_with_positions_arbitrum)# Close 1%  # Remove 1% of collateral

    # Verify the second order was created
    assert isinstance(order2, DecreaseOrder)
    assert order2.is_long == is_long
