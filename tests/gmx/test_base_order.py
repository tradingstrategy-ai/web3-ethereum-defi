"""
Tests for BaseOrder class with parametrized chain testing.

This test suite verifies the functionality of the BaseOrder class
when connected to different networks using Anvil forks. Tests include
order creation, price calculation, approval checks, and transaction building.
"""

import pytest
from decimal import Decimal
from eth_utils import to_checksum_address

from eth_defi.gmx.order.base_order import BaseOrder, OrderParams, OrderType, OrderResult
from eth_defi.gmx.contracts import NETWORK_TOKENS
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation


def test_order_params_dataclass():
    """Test OrderParams dataclass functionality."""
    params = OrderParams(
        market_key="0x1234567890123456789012345678901234567890",
        collateral_address="0x2345678901234567890123456789012345678901",
        index_token_address="0x3456789012345678901234567890123456789012",
        is_long=True,
        size_delta=1000.0,
        initial_collateral_delta_amount="1000000000000000000",
        slippage_percent=0.005,
        swap_path=["0x4567890123456789012345678901234567890123"],
        auto_cancel=False,
        execution_buffer=1.3
    )

    assert params.market_key == "0x1234567890123456789012345678901234567890"
    assert params.collateral_address == "0x2345678901234567890123456789012345678901"
    assert params.index_token_address == "0x3456789012345678901234567890123456789012"
    assert params.is_long is True
    assert params.size_delta == 1000.0
    assert params.initial_collateral_delta_amount == "1000000000000000000"
    assert params.slippage_percent == 0.005
    assert params.swap_path == ["0x4567890123456789012345678901234567890123"]
    assert params.auto_cancel is False
    assert params.execution_buffer == 1.3


def test_base_order_initialization(chain_name, gmx_config_fork):
    """Test that BaseOrder initializes correctly with GMX configuration."""
    base_order = BaseOrder(gmx_config_fork)

    assert base_order.config == gmx_config_fork
    assert base_order.chain.lower() == chain_name.lower()
    assert base_order.web3 is not None
    assert base_order.markets is not None
    assert base_order.oracle_prices is not None
    assert base_order.contract_addresses is not None
    assert base_order._exchange_router_contract is not None


def test_get_prices_calculation(chain_name, base_order):
    """Test price calculation with slippage."""
    # Get market data for a known market
    markets = base_order.markets.get_available_markets()
    assert len(markets) > 0
    
    # Get the first available market
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]
    
    # Get price data
    prices = base_order.oracle_prices.get_recent_prices()
    index_token_address = market_data["index_token_address"]
    
    if index_token_address not in prices:
        pytest.skip(f"No price data for token {index_token_address}")

    # Test price calculation for opening a long position
    params = OrderParams(
        market_key=market_key,
        collateral_address=market_data["long_token_address"],
        index_token_address=index_token_address,
        is_long=True,
        size_delta=1000.0,
        initial_collateral_delta_amount="1000000000000000000",
        slippage_percent=0.005
    )

    decimals = market_data["market_metadata"]["decimals"]
    price, acceptable_price, acceptable_price_in_usd = base_order._get_prices(
        decimals, prices, params, is_open=True, is_close=False, is_swap=False
    )

    # For a long position, acceptable price should be higher than mark price
    assert price > 0
    assert acceptable_price > int(price)
    assert acceptable_price_in_usd > 0

    # Test price calculation for closing a long position
    price, acceptable_price, acceptable_price_in_usd = base_order._get_prices(
        decimals, prices, params, is_open=False, is_close=True, is_swap=False
    )

    # For a long position close, acceptable price should be lower than mark price
    if int(price) != 0:  # Only check if we have a real price
        assert acceptable_price < int(price)


def test_build_order_arguments(chain_name, base_order, gmx_config_fork):
    """Test building order arguments tuple."""
    # Get market data for a known market
    markets = base_order.markets.get_available_markets()
    assert len(markets) > 0
    
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]
    
    params = OrderParams(
        market_key=market_key,
        collateral_address=market_data["long_token_address"],
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=5000.0,
        initial_collateral_delta_amount="2000000000000000000",
        slippage_percent=0.005,
        swap_path=[market_data["long_token_address"]]
    )

    execution_fee = 1000000000000
    order_type = 1
    acceptable_price = 2000000000000000000
    mark_price = 1990000000000000000

    arguments = base_order._build_order_arguments(
        params, execution_fee, order_type, acceptable_price, mark_price
    )

    # Verify the structure of arguments (flattened tuple with 8 elements)
    assert len(arguments) == 8
    assert len(arguments[0]) == 7  # Addresses tuple
    assert len(arguments[1]) == 8  # Integers tuple
    assert arguments[2] == order_type  # Order type
    assert arguments[4] == params.is_long  # is_long flag
    
    # Verify addresses are checksummed
    addresses = arguments[0]
    assert addresses[0] == to_checksum_address(gmx_config_fork.get_wallet_address())  # receiver
    assert addresses[1] == to_checksum_address(gmx_config_fork.get_wallet_address())  # cancellationReceiver
    assert addresses[4] == to_checksum_address(market_key)
    assert addresses[5] == to_checksum_address(params.collateral_address)
    
    # Verify the size delta calculation
    size_delta_usd = arguments[1][0]
    expected_size = int(Decimal(str(params.size_delta)) * Decimal(10**30))
    assert size_delta_usd == expected_size
    
    # Verify collateral amount
    collateral_amount = arguments[1][1]
    assert collateral_amount == int(params.initial_collateral_delta_amount)


def test_build_multicall_args_native_token(chain_name, base_order):
    """Test building multicall arguments with native token collateral."""
    # Get market data
    markets = base_order.markets.get_available_markets()
    assert len(markets) > 0
    
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]
    
    # Use the native token for this chain
    tokens = NETWORK_TOKENS[chain_name]
    if chain_name == "arbitrum":
        native_token = tokens.get("WETH")
    else:  # avalanche
        native_token = tokens.get("WAVAX")
    
    params = OrderParams(
        market_key=market_key,
        collateral_address=native_token,
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=1000.0,
        initial_collateral_delta_amount="1000000000000000000",
        slippage_percent=0.005
    )

    # Create proper arguments similar to _build_order_arguments
    execution_fee = 1000000000000
    order_type = 1
    acceptable_price = 2000000000000000000
    mark_price = 1990000000000000000

    arguments = base_order._build_order_arguments(
        params, execution_fee, order_type, acceptable_price, mark_price
    )

    multicall_args, value_amount = base_order._build_multicall_args(
        params, arguments, execution_fee, is_close=False
    )

    # For native token, value should include both execution fee and collateral
    expected_value = int(params.initial_collateral_delta_amount) + execution_fee
    assert value_amount == expected_value
    assert len(multicall_args) == 2  # sendWnt and createOrder
    assert len(multicall_args[0]) > 0  # sendWnt call data
    assert len(multicall_args[1]) > 0  # createOrder call data


def test_build_multicall_args_erc20_token(chain_name, base_order, usdc):
    """Test building multicall arguments with ERC20 token collateral."""
    # Get market data
    markets = base_order.markets.get_available_markets()
    assert len(markets) > 0
    
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    params = OrderParams(
        market_key=market_key,
        collateral_address=usdc.address,
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=1000.0,
        initial_collateral_delta_amount="1000000000",
        slippage_percent=0.005
    )

    # Create proper arguments similar to _build_order_arguments
    execution_fee = 1000000000000
    order_type = 1
    acceptable_price = 2000000000000000000
    mark_price = 1990000000000000000

    arguments = base_order._build_order_arguments(
        params, execution_fee, order_type, acceptable_price, mark_price
    )

    multicall_args, value_amount = base_order._build_multicall_args(
        params, arguments, execution_fee, is_close=False
    )

    # For ERC20 token, value should only include execution fee
    assert value_amount == execution_fee
    assert len(multicall_args) == 3  # sendWnt, sendTokens, and createOrder
    assert len(multicall_args[0]) > 0  # sendWnt call data (for execution fee)
    assert len(multicall_args[1]) > 0  # sendTokens call data
    assert len(multicall_args[2]) > 0  # createOrder call data


def test_check_for_approval_native_token(chain_name, base_order):
    """Test approval check for native token (should not require approval)."""
    # Get market data
    markets = base_order.markets.get_available_markets()
    assert len(markets) > 0
    
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]
    
    # Use the native token for this chain
    tokens = NETWORK_TOKENS[chain_name]
    if chain_name == "arbitrum":
        native_token = tokens.get("WETH")
    else:  # avalanche
        native_token = tokens.get("WAVAX")

    params = OrderParams(
        market_key=market_key,
        collateral_address=native_token,
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=1000.0,
        initial_collateral_delta_amount="1000000000000000000",
        slippage_percent=0.005
    )

    # This should not raise an exception for native token
    base_order._check_for_approval(params)


def test_check_for_approval_insufficient_erc20(chain_name, base_order, usdc):
    """Test approval check for ERC20 token with insufficient allowance."""
    # Get market data
    markets = base_order.markets.get_available_markets()
    assert len(markets) > 0
    
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    params = OrderParams(
        market_key=market_key,
        collateral_address=usdc.address,
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=1000.0,
        initial_collateral_delta_amount="1000000000000000000000",  # Large amount
        slippage_percent=0.005
    )

    # This should raise ValueError for insufficient approval
    with pytest.raises(ValueError, match="Insufficient token approval"):
        base_order._check_for_approval(params)


def test_check_if_approved_native_token(chain_name, base_order, test_wallet):
    """Test check_if_approved method with native token."""
    tokens = NETWORK_TOKENS[chain_name]
    if chain_name == "arbitrum":
        native_token = tokens.get("WETH")
    else:  # avalanche
        native_token = tokens.get("WAVAX")

    spender = base_order.contract_addresses.exchangerouter

    result = base_order.check_if_approved(
        spender=spender,
        token_to_approve=native_token,
        amount_of_tokens_to_spend=1000000000000000000,  # 1 ETH/AVAX worth
        approve=False,
        wallet=test_wallet
    )

    # Native tokens should always return approved=True
    assert result["approved"] is True
    assert result["needs_approval"] is False


def test_check_if_approved_erc20_insufficient(chain_name, base_order, usdc, test_wallet):
    """Test check_if_approved method with ERC20 token and insufficient balance."""
    spender = base_order.contract_addresses.exchangerouter
    # Use an account with low USDC balance to trigger the insufficient balance error
    large_amount = 10**25  # Very large amount
    
    # Since the test wallet has limited USDC, this should throw an insufficient balance error
    with pytest.raises(ValueError, match="Insufficient balance"):
        base_order.check_if_approved(
            spender=spender,
            token_to_approve=usdc.address,
            amount_of_tokens_to_spend=large_amount,
            approve=False,
            wallet=test_wallet
        )


def test_create_order_market_increase(chain_name, base_order):
    """Test creating a market increase order."""
    # Get market data for a known market
    markets = base_order.markets.get_available_markets()
    if not markets:
        pytest.skip("No markets available for testing")
    
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]
    
    # Use the native token to avoid approval issues
    tokens = NETWORK_TOKENS[chain_name]
    if chain_name == "arbitrum":
        native_token = tokens.get("WETH")
    else:  # avalanche
        native_token = tokens.get("WAVAX")

    params = OrderParams(
        market_key=market_key,
        collateral_address=native_token,
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=500.0,
        initial_collateral_delta_amount="500000000000000000",  # 0.5 native tokens
        slippage_percent=0.005
    )

    # Create order - this should work for opening a position with native token
    result = base_order.create_order(params, is_open=True)

    # Verify result structure
    assert isinstance(result, OrderResult)
    assert "from" in result.transaction
    assert "to" in result.transaction
    assert "data" in result.transaction
    assert result.execution_fee > 0
    assert result.gas_limit > 0
    assert isinstance(result.acceptable_price, int)

    # Verify transaction structure
    assert result.transaction["to"] == base_order.contract_addresses.exchangerouter
    assert result.transaction["value"] >= result.execution_fee  # Value includes execution fee


def test_create_order_market_decrease(chain_name, base_order):
    """Test creating a market decrease order."""
    # Get market data for a known market
    markets = base_order.markets.get_available_markets()
    if not markets:
        pytest.skip("No markets available for testing")
    
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]
    
    # Use a token that's not the native token to avoid approval issues
    collateral_address = market_data.get("long_token_address", market_data.get("short_token_address"))
    if not collateral_address:
        pytest.skip("No collateral token available")

    params = OrderParams(
        market_key=market_key,
        collateral_address=collateral_address,
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=300.0,
        initial_collateral_delta_amount="300000000",
        slippage_percent=0.005
    )

    # Create order - this should work for closing a position (no approval check for close)
    result = base_order.create_order(params, is_close=True)

    # Verify result structure
    assert isinstance(result, OrderResult)
    assert "from" in result.transaction
    assert "to" in result.transaction
    assert "data" in result.transaction
    assert result.execution_fee > 0
    assert isinstance(result.acceptable_price, int)

    # For close orders, value should only include execution fee
    assert result.transaction["value"] == result.execution_fee


def test_order_type_enum():
    """Test OrderType enum values."""
    assert OrderType.MARKET_INCREASE.value == 2
    assert OrderType.MARKET_DECREASE.value == 4
    assert OrderType.LIMIT_INCREASE.value == 3
    assert OrderType.LIMIT_DECREASE.value == 5
    assert OrderType.MARKET_SWAP.value == 0
    assert OrderType.STOP_LOSS_DECREASE.value == 6
    assert OrderType.LIMIT_SWAP.value == 1
    assert OrderType.LIQUIDATION.value == 7


def test_build_transaction_structure(chain_name, base_order):
    """Test that the built transaction has the correct structure."""
    # Get market data
    markets = base_order.markets.get_available_markets()
    if not markets:
        pytest.skip("No markets available for testing")
    
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]
    
    # Use the native token to avoid approval issues
    tokens = NETWORK_TOKENS[chain_name]
    if chain_name == "arbitrum":
        native_token = tokens.get("WETH")
    else:  # avalanche
        native_token = tokens.get("WAVAX")

    params = OrderParams(
        market_key=market_key,
        collateral_address=native_token,
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=100.0,
        initial_collateral_delta_amount="100000000000000000",  # 0.1 native tokens
        slippage_percent=0.005
    )

    # Create order to get a transaction
    result = base_order.create_order(params, is_open=True)

    transaction = result.transaction

    # Verify required transaction fields
    assert "from" in transaction
    assert "to" in transaction
    assert "data" in transaction
    assert "value" in transaction
    assert "gas" in transaction
    assert "chainId" in transaction
    assert "nonce" in transaction

    # Verify address formatting
    assert transaction["to"] == base_order.contract_addresses.exchangerouter
    assert to_checksum_address(transaction["from"]) == base_order.config.get_wallet_address()

    # Verify gas pricing (EIP-1559 or legacy)
    has_eip1559 = "maxFeePerGas" in transaction and "maxPriorityFeePerGas" in transaction
    has_legacy = "gasPrice" in transaction
    assert has_eip1559 or has_legacy