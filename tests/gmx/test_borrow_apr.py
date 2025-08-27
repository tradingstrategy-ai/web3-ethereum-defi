"""
Tests for GMX Borrow APR functionality.
"""

from eth_defi.gmx.core.borrow_apr import GetBorrowAPR


def test_initialization(get_borrow_apr):
    """Test GetBorrowAPR initialization."""
    assert get_borrow_apr.config is not None
    assert get_borrow_apr.filter_swap_markets is True


def test_initialization_with_custom_filter(gmx_config):
    """Test GetBorrowAPR initialization with custom filter setting."""
    borrow_apr = GetBorrowAPR(gmx_config, filter_swap_markets=False)
    assert borrow_apr.filter_swap_markets is False


def test_get_data_processing(get_borrow_apr):
    """Test _get_data_processing method."""
    result = get_borrow_apr._get_data_processing()

    # Verify the result structure
    assert "parameter" in result
    assert result["parameter"] == "borrow_apr"
    assert "long" in result
    assert "short" in result

    # Check that we have data
    assert isinstance(result["long"], dict)
    assert isinstance(result["short"], dict)


def test_get_data_processing_empty_markets(get_borrow_apr):
    """Test _get_data_processing with empty markets."""
    # Temporarily replace markets cache with empty dict
    original_markets_cache = get_borrow_apr.markets._markets_cache
    get_borrow_apr.markets._markets_cache = {}

    result = get_borrow_apr._get_data_processing()

    # Should still return proper structure
    assert "parameter" in result
    assert result["parameter"] == "borrow_apr"
    assert "long" in result
    assert "short" in result

    # Restore original markets
    get_borrow_apr.markets._markets_cache = original_markets_cache


def test_output_format(get_borrow_apr):
    """Test that the output format matches expected structure."""
    result = get_borrow_apr._get_data_processing()

    # Check basic structure
    assert isinstance(result, dict)
    assert "parameter" in result
    assert result["parameter"] == "borrow_apr"
    assert "long" in result
    assert "short" in result


def test_inheritance_from_get_data(get_borrow_apr):
    """Test that GetBorrowAPR properly inherits from GetData."""
    # Test that it has the expected methods from GetData
    assert hasattr(get_borrow_apr, "get_data")
    assert hasattr(get_borrow_apr, "config")

    # Test that it's callable
    assert callable(get_borrow_apr.get_data)
