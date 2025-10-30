"""
Tests for GMX Open Positions functionality based on real API structure.
"""

from eth_defi.gmx.core.open_positions import GetOpenPositions
from tests.gmx.conftest import get_open_positions


def test_initialization_and_basic_functionality(get_open_positions, gmx_config):
    """Test GetOpenPositions initialization and basic functionality."""
    # Test basic initialization
    assert get_open_positions.config is not None
    assert get_open_positions.filter_swap_markets is True

    # Test initialization with custom filter setting
    open_positions_custom = GetOpenPositions(gmx_config, filter_swap_markets=False)
    assert open_positions_custom.filter_swap_markets is False

    # Test inheritance from GetData
    assert hasattr(get_open_positions, "get_data")
    assert callable(get_open_positions.get_data)

    # Test config dependency
    assert hasattr(get_open_positions.config, "web3")
    assert hasattr(get_open_positions.config, "chain")


def test_open_positions_1(gmx_open_positions):
    """Test data processing structure and method availability."""
    import pytest

    address_with_open_positions = "0x91666112b851E33D894288A95846d14781e86cad"

    open_positions = gmx_open_positions.get_data(address_with_open_positions)

    # Note: Address may not have positions at test time (positions can be closed)
    # This is not a code bug - positions are dynamic
    if len(open_positions) == 0:
        pytest.skip(f"Address {address_with_open_positions} has no open positions at test time. This is expected as positions are dynamic.")

    # Verify structure if positions exist
    # Just check the first position to verify structure
    first_position_key = list(open_positions.keys())[0]
    first_position = open_positions[first_position_key]

    assert first_position["account"] == address_with_open_positions
    assert "market_symbol" in first_position
    assert "collateral_token" in first_position
    assert isinstance(first_position["position_size"], float)


def test_open_positions_2(gmx_open_positions):
    """Test data processing structure and method availability."""
    import pytest

    address_with_open_positions = "0xe2823659bE02E0F48a4660e4Da008b5E1aBFdF29"

    open_positions = gmx_open_positions.get_data(address_with_open_positions)

    # Note: Address may not have positions at test time (positions can be closed)
    # This is not a code bug - positions are dynamic
    if len(open_positions) == 0:
        pytest.skip(f"Address {address_with_open_positions} has no open positions at test time. This is expected as positions are dynamic.")

    # Verify structure if positions exist
    # Just check the first position to verify structure
    first_position_key = list(open_positions.keys())[0]
    first_position = open_positions[first_position_key]

    assert first_position["account"] == address_with_open_positions
    assert "market_symbol" in first_position
    assert "collateral_token" in first_position
    assert isinstance(first_position["position_size"], float)
