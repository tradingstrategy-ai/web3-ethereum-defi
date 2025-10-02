"""
Tests for the DepositOrder class with parametrised chain testing.

This test suite verifies the functionality of the DepositOrder wrapper class
"""

import pytest
from eth_utils import to_checksum_address

from eth_defi.gmx.order.deposit_order import DepositOrder
from eth_defi.gmx.liquidity_base.deposit import DepositResult
from eth_defi.gmx.contracts import NETWORK_TOKENS



def test_deposit_order_initialization(chain_name, gmx_config_fork):
    """Test that DepositOrder initializes correctly with market and token configuration."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    # Get first market
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    # Create DepositOrder
    deposit_order = DepositOrder(
        gmx_config_fork,
        market_key=market_key,
        initial_long_token=market_data["long_token_address"],
        initial_short_token=market_data["short_token_address"],
    )

    # Verify initialization
    assert deposit_order.config == gmx_config_fork
    assert deposit_order.chain.lower() == chain_name.lower()
    assert deposit_order.market_key == to_checksum_address(market_key)
    assert deposit_order.initial_long_token == to_checksum_address(market_data["long_token_address"])
    assert deposit_order.initial_short_token == to_checksum_address(market_data["short_token_address"])
    assert deposit_order.web3 is not None
    assert deposit_order.markets is not None


def test_deposit_order_create_with_both_tokens(chain_name, gmx_config_fork):
    """Test creating a deposit order with both long and short tokens."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    # Find a market with USDC
    tokens = NETWORK_TOKENS[chain_name]
    usdc_address = tokens.get("USDC")

    market_key = None
    market_data = None
    for key, data in markets.items():
        if data["long_token_address"].lower() == usdc_address.lower() or \
           data["short_token_address"].lower() == usdc_address.lower():
            market_key = key
            market_data = data
            break

    if not market_key:
        pytest.skip("No suitable market found with USDC")

    # Create DepositOrder
    deposit_order = DepositOrder(
        gmx_config_fork,
        market_key=market_key,
        initial_long_token=market_data["long_token_address"],
        initial_short_token=market_data["short_token_address"],
    )

    # Create deposit order
    result = deposit_order.create_deposit_order(
        long_token_amount=1000000,  # 1 USDC (if USDC is long token)
        short_token_amount=1000000,  # 1 USDC (if USDC is short token)
        execution_buffer=1.3,
    )

    # Verify result
    assert isinstance(result, DepositResult)
    assert hasattr(result, "transaction")
    assert isinstance(result.transaction, dict)
    assert result.execution_fee > 0
    assert result.gas_limit > 0


def test_deposit_order_create_with_native_token(chain_name, gmx_config_fork):
    """Test creating a deposit order with native token (WETH/WAVAX)."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    # Find a market with native token (WETH or WAVAX)
    tokens = NETWORK_TOKENS[chain_name]
    if chain_name == "arbitrum":
        native_token = tokens.get("WETH")
    else:
        native_token = tokens.get("WAVAX")

    market_key = None
    market_data = None
    for key, data in markets.items():
        if data["long_token_address"].lower() == native_token.lower() or \
           data["short_token_address"].lower() == native_token.lower():
            market_key = key
            market_data = data
            break

    if not market_key:
        pytest.skip(f"No suitable market found with native token")

    # Create DepositOrder
    deposit_order = DepositOrder(
        gmx_config_fork,
        market_key=market_key,
        initial_long_token=market_data["long_token_address"],
        initial_short_token=market_data["short_token_address"],
    )

    # Create deposit order with native token
    result = deposit_order.create_deposit_order(
        long_token_amount=1000000000000000000,  # 1 token
        short_token_amount=0,
        execution_buffer=1.3,
    )

    # Verify result
    assert isinstance(result, DepositResult)
    assert result.execution_fee > 0


def test_deposit_order_transaction_structure(chain_name, gmx_config_fork):
    """Test that deposit order creates a valid unsigned transaction structure."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    # Create DepositOrder
    deposit_order = DepositOrder(
        gmx_config_fork,
        market_key=market_key,
        initial_long_token=market_data["long_token_address"],
        initial_short_token=market_data["short_token_address"],
    )

    # Create deposit order
    result = deposit_order.create_deposit_order(
        long_token_amount=1000000,
        short_token_amount=1000000,
    )

    # Validate unsigned transaction structure
    tx = result.transaction
    assert isinstance(tx, dict), "Transaction must be a dict"
    assert "from" in tx
    assert "to" in tx
    assert "data" in tx
    assert "value" in tx
    assert "gas" in tx
    assert "chainId" in tx
    assert "nonce" in tx
    assert tx["to"] is not None
    assert len(tx["data"]) > 2  # Should be a hex string like "0x..."



def test_deposit_order_with_custom_execution_buffer(chain_name, gmx_config_fork):
    """Test deposit order with a custom execution buffer."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    # Create DepositOrder
    deposit_order = DepositOrder(
        gmx_config_fork,
        market_key=market_key,
        initial_long_token=market_data["long_token_address"],
        initial_short_token=market_data["short_token_address"],
    )

    # Test with different execution buffers
    result_low = deposit_order.create_deposit_order(
        long_token_amount=1000000,
        short_token_amount=1000000,
        execution_buffer=1.1,  # Low buffer
    )

    result_high = deposit_order.create_deposit_order(
        long_token_amount=1000000,
        short_token_amount=1000000,
        execution_buffer=2.0,  # High buffer
    )

    # Higher buffer should result in a higher execution fee
    assert result_high.execution_fee > result_low.execution_fee
    assert isinstance(result_low, DepositResult)
    assert isinstance(result_high, DepositResult)


def test_deposit_order_with_zero_amounts(chain_name, gmx_config_fork):
    """Test deposit order with zero token amounts (should still work)."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    # Find a market with USDC (which we have funded and approved)
    tokens = NETWORK_TOKENS[chain_name]
    usdc_address = tokens.get("USDC")

    market_key = None
    market_data = None
    for key, data in markets.items():
        if data.get("long_token_address", "").lower() == usdc_address.lower() or \
           data.get("short_token_address", "").lower() == usdc_address.lower():
            market_key = key
            market_data = data
            break

    if not market_key:
        pytest.skip("No USDC market found")

    # Create DepositOrder
    deposit_order = DepositOrder(
        gmx_config_fork,
        market_key=market_key,
        initial_long_token=market_data["long_token_address"],
        initial_short_token=market_data["short_token_address"],
    )

    # Create a deposit order with only long token (zero short token)
    result = deposit_order.create_deposit_order(
        long_token_amount=1000000,
        short_token_amount=0,
    )

    assert isinstance(result, DepositResult)


def test_deposit_order_with_invalid_market(chain_name, gmx_config_fork):
    """Test deposit order with invalid market address raises error."""
    invalid_market = "0x0000000000000000000000000000000000000001"

    # Create DepositOrder with invalid market
    deposit_order = DepositOrder(
        gmx_config_fork,
        market_key=invalid_market,
        initial_long_token="0x0000000000000000000000000000000000000002",
        initial_short_token="0x0000000000000000000000000000000000000003",
    )

    # Should raise ValueError when trying to create order
    with pytest.raises(ValueError, match="Market.*not found"):
        deposit_order.create_deposit_order(
            long_token_amount=1000000,
            short_token_amount=1000000,
        )


def test_deposit_order_with_custom_gas_price(chain_name, gmx_config_fork):
    """Test deposit order with a custom max fee per gas."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    # Create DepositOrder
    deposit_order = DepositOrder(
        gmx_config_fork,
        market_key=market_key,
        initial_long_token=market_data["long_token_address"],
        initial_short_token=market_data["short_token_address"],
    )

    # Create with a custom gas price
    custom_gas_price = 50000000000  # 50 gwei
    result = deposit_order.create_deposit_order(
        long_token_amount=1000000,
        short_token_amount=1000000,
        max_fee_per_gas=custom_gas_price,
    )

    assert isinstance(result, DepositResult)
    # The execution fee calculation should use the custom gas price


def test_deposit_order_attributes_accessible(chain_name, gmx_config_fork):
    """Test that all DepositOrder attributes are accessible."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    # Create DepositOrder
    deposit_order = DepositOrder(
        gmx_config_fork,
        market_key=market_key,
        initial_long_token=market_data["long_token_address"],
        initial_short_token=market_data["short_token_address"],
    )

    # Test all inherited attributes from the Deposit base class
    assert hasattr(deposit_order, "config")
    assert hasattr(deposit_order, "chain")
    assert hasattr(deposit_order, "web3")
    assert hasattr(deposit_order, "markets")
    assert hasattr(deposit_order, "market_key")
    assert hasattr(deposit_order, "initial_long_token")
    assert hasattr(deposit_order, "initial_short_token")
    assert hasattr(deposit_order, "logger")

    # Verify they're not None
    assert deposit_order.config is not None
    assert deposit_order.chain is not None
    assert deposit_order.web3 is not None
    assert deposit_order.markets is not None
