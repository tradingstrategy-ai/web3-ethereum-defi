"""
Tests for BaseOrder class with parametrized chain testing.

This test suite verifies the functionality of the updated BaseOrder class
when connected to different networks using Anvil forks. Tests include:
- Gas limits initialization
- Order types initialization
- Order creation with new parameters
- Price calculation with slippage
- Token approval checks
- Transaction building
- Price impact estimation
"""

import pytest
from decimal import Decimal
from eth_utils import to_checksum_address

from eth_defi.gmx.order.base_order import BaseOrder, OrderParams, OrderType, OrderResult, ETH_ZERO_ADDRESS, ZERO_REFERRAL_CODE
from eth_defi.gmx.contracts import NETWORK_TOKENS
from eth_defi.token import fetch_erc20_details


# ==================== Dataclass Tests ====================


def test_order_params_dataclass():
    """Test OrderParams dataclass with all fields."""
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
        execution_buffer=1.3,
        callback_gas_limit=100000,  # Updated: New field
        min_output_amount=500000,  # Updated: New field
        valid_from_time=0,  # Updated: New field
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
    # Updated: Test new fields
    assert params.callback_gas_limit == 100000
    assert params.min_output_amount == 500000
    assert params.valid_from_time == 0


def test_order_params_defaults():
    """Test OrderParams default values."""
    params = OrderParams(
        market_key="0x1234567890123456789012345678901234567890",
        collateral_address="0x2345678901234567890123456789012345678901",
        index_token_address="0x3456789012345678901234567890123456789012",
        is_long=True,
        size_delta=1000.0,
        initial_collateral_delta_amount="1000000000000000000",
    )

    assert params.slippage_percent == 0.005
    assert params.swap_path == []
    assert params.auto_cancel is False
    assert params.execution_buffer == 1.3
    # Updated: Test new field defaults
    assert params.callback_gas_limit == 0
    assert params.min_output_amount == 0
    assert params.valid_from_time == 0


def test_order_result_dataclass():
    """Test OrderResult dataclass with price impact."""
    result = OrderResult(
        transaction={"from": "0x123", "to": "0x456"},
        execution_fee=1000000,
        acceptable_price=2000000,
        mark_price=1900000.0,
        gas_limit=2500000,
        estimated_price_impact=-0.005,  # Updated: New field
    )

    assert result.transaction == {"from": "0x123", "to": "0x456"}
    assert result.execution_fee == 1000000
    assert result.acceptable_price == 2000000
    assert result.mark_price == 1900000.0
    assert result.gas_limit == 2500000
    # Updated: Test new field
    assert result.estimated_price_impact == -0.005


def test_order_type_enum():
    """Test OrderType enum values."""
    assert OrderType.SWAP.value == 0
    assert OrderType.SHIFT.value == 1
    assert OrderType.ATOMIC_WITHDRAWAL.value == 2
    assert OrderType.DEPOSIT.value == 3
    assert OrderType.WITHDRAWAL.value == 4
    assert OrderType.ATOMIC_SWAP.value == 5


def test_module_constants():
    """Test module-level constants."""
    assert ETH_ZERO_ADDRESS == "0x" + "0" * 40
    assert ZERO_REFERRAL_CODE == bytes.fromhex("0" * 64)
    assert len(ZERO_REFERRAL_CODE) == 32


# ==================== Initialization Tests ====================


def test_base_order_initialization(chain_name, gmx_config_fork):
    """Test that BaseOrder initializes correctly with all required attributes."""
    base_order = BaseOrder(gmx_config_fork)

    # Basic attributes
    assert base_order.config == gmx_config_fork
    assert base_order.chain.lower() == chain_name.lower()
    assert base_order.web3 is not None
    assert base_order.chain_id == gmx_config_fork.web3.eth.chain_id
    assert base_order.contract_addresses is not None
    assert base_order._exchange_router_contract is not None

    # Updated: Test new attributes
    assert hasattr(base_order, "_order_types")
    assert base_order._order_types is not None
    assert "market_increase" in base_order._order_types
    assert "market_decrease" in base_order._order_types
    assert "market_swap" in base_order._order_types

    # Updated: Test gas limits initialization
    assert hasattr(base_order, "_gas_limits")
    assert base_order._gas_limits is not None
    assert isinstance(base_order._gas_limits, dict)


def test_gas_limits_initialization(chain_name, base_order):
    """Test that gas limits are properly loaded from datastore."""
    # Updated: Test gas limits are integers (not contract functions)
    assert "swap_order" in base_order._gas_limits
    assert "increase_order" in base_order._gas_limits
    assert "decrease_order" in base_order._gas_limits
    assert "deposit" in base_order._gas_limits
    assert "withdraw" in base_order._gas_limits
    assert "multicall_base" in base_order._gas_limits

    # Updated: Verify they are actual integer values
    assert isinstance(base_order._gas_limits["swap_order"], int)
    assert isinstance(base_order._gas_limits["increase_order"], int)
    assert isinstance(base_order._gas_limits["decrease_order"], int)

    # Updated: Verify reasonable gas limit values
    assert base_order._gas_limits["swap_order"] > 0
    assert base_order._gas_limits["increase_order"] > 0
    assert base_order._gas_limits["decrease_order"] > 0


def test_order_types_initialization(base_order):
    """Test that order types are properly initialized."""
    # Updated: Test all order types are available
    assert base_order._order_types["market_swap"] == 0
    assert base_order._order_types["limit_swap"] == 1
    assert base_order._order_types["market_increase"] == 2
    assert base_order._order_types["limit_increase"] == 3
    assert base_order._order_types["market_decrease"] == 4
    assert base_order._order_types["limit_decrease"] == 5
    assert base_order._order_types["stop_loss_decrease"] == 6
    assert base_order._order_types["liquidation"] == 7


def test_markets_property(base_order):
    """Test that markets property is properly initialized."""
    markets = base_order.markets
    assert markets is not None
    assert hasattr(markets, "get_available_markets")

    # Get markets
    available_markets = markets.get_available_markets()
    assert isinstance(available_markets, dict)
    assert len(available_markets) > 0


def test_oracle_prices_property(base_order):
    """Test that oracle_prices property is properly initialized."""
    oracle_prices = base_order.oracle_prices
    assert oracle_prices is not None
    assert hasattr(oracle_prices, "get_recent_prices")

    # Get prices
    prices = oracle_prices.get_recent_prices()
    assert isinstance(prices, dict)
    assert len(prices) > 0


# ==================== Gas Limit Determination Tests ====================


def test_determine_gas_limits_increase(base_order):
    """Test gas limit determination for increase orders."""
    gas_limits = base_order._determine_gas_limits(is_open=True, is_close=False, is_swap=False)

    assert "execution" in gas_limits
    assert "total" in gas_limits
    assert gas_limits["total"] > gas_limits["execution"]
    assert gas_limits["total"] == gas_limits["execution"] + base_order._gas_limits["multicall_base"]


def test_determine_gas_limits_decrease(base_order):
    """Test gas limit determination for decrease orders."""
    gas_limits = base_order._determine_gas_limits(is_open=False, is_close=True, is_swap=False)

    assert "execution" in gas_limits
    assert "total" in gas_limits
    assert gas_limits["total"] > gas_limits["execution"]


def test_determine_gas_limits_swap(base_order):
    """Test gas limit determination for swap orders."""
    gas_limits = base_order._determine_gas_limits(is_open=False, is_close=False, is_swap=True)

    assert "execution" in gas_limits
    assert "total" in gas_limits
    assert gas_limits["total"] > gas_limits["execution"]


# ==================== Price Calculation Tests ====================


def test_get_prices_long_open(chain_name, base_order):
    """Test price calculation for opening a long position."""
    markets = base_order.markets.get_available_markets()
    assert len(markets) > 0

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    prices = base_order.oracle_prices.get_recent_prices()
    index_token_address = market_data["index_token_address"]

    if index_token_address not in prices:
        pytest.skip(f"No price data for token {index_token_address}")

    params = OrderParams(
        market_key=market_key,
        collateral_address=market_data["long_token_address"],
        index_token_address=index_token_address,
        is_long=True,
        size_delta=1000.0,
        initial_collateral_delta_amount="1000000000000000000",
        slippage_percent=0.005,
    )

    decimals = market_data["market_metadata"]["decimals"]
    price, acceptable_price, acceptable_price_in_usd = base_order._get_prices(
        decimals,
        prices,
        params,
        is_open=True,
        is_close=False,
        is_swap=False,
    )

    # For opening a long, acceptable price should be higher than mark price (allow slippage up)
    assert price > 0
    assert acceptable_price > int(price)
    assert acceptable_price_in_usd > 0


def test_get_prices_long_close(chain_name, base_order):
    """Test price calculation for closing a long position."""
    markets = base_order.markets.get_available_markets()
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    prices = base_order.oracle_prices.get_recent_prices()
    index_token_address = market_data["index_token_address"]

    if index_token_address not in prices:
        pytest.skip(f"No price data for token {index_token_address}")

    params = OrderParams(market_key=market_key, collateral_address=market_data["long_token_address"], index_token_address=index_token_address, is_long=True, size_delta=1000.0, initial_collateral_delta_amount="1000000000000000000", slippage_percent=0.005)

    decimals = market_data["market_metadata"]["decimals"]
    price, acceptable_price, acceptable_price_in_usd = base_order._get_prices(
        decimals,
        prices,
        params,
        is_open=False,
        is_close=True,
        is_swap=False,
    )

    # For closing a long, acceptable price should be lower than mark price (allow slippage down)
    if int(price) != 0:
        assert acceptable_price < int(price)


def test_get_prices_swap(chain_name, base_order):
    """Test price calculation for swap orders."""
    markets = base_order.markets.get_available_markets()
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    prices = base_order.oracle_prices.get_recent_prices()
    index_token_address = market_data["index_token_address"]

    if index_token_address not in prices:
        pytest.skip(f"No price data for token {index_token_address}")

    params = OrderParams(market_key=market_key, collateral_address=market_data["long_token_address"], index_token_address=index_token_address, is_long=False, size_delta=0.0, initial_collateral_delta_amount="1000000000000000000", slippage_percent=0.005)

    decimals = market_data["market_metadata"]["decimals"]
    price, acceptable_price, acceptable_price_in_usd = base_order._get_prices(
        decimals,
        prices,
        params,
        is_open=False,
        is_close=False,
        is_swap=True,
    )

    # For swaps, acceptable price should be 0
    assert price > 0
    assert acceptable_price == 0
    assert acceptable_price_in_usd == 0


# ==================== Order Arguments Tests ====================


def test_build_order_arguments_structure(chain_name, base_order, gmx_config_fork):
    """Test the structure of built order arguments."""
    markets = base_order.markets.get_available_markets()
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
        swap_path=[market_data["long_token_address"]],
        # Updated: Test new parameters
        callback_gas_limit=100000,
        min_output_amount=500000,
        valid_from_time=1234567890,
    )

    execution_fee = 1000000000000
    order_type = 2
    acceptable_price = 2000000000000000000
    mark_price = 1990000000000000000

    arguments = base_order._build_order_arguments(params, execution_fee, order_type, acceptable_price, mark_price)

    # Verify tuple structure (8 elements)
    assert len(arguments) == 8
    assert len(arguments[0]) == 7  # Addresses tuple
    assert len(arguments[1]) == 8  # Numbers tuple
    assert arguments[2] == order_type  # Order type
    assert arguments[4] == params.is_long  # is_long flag

    # Verify addresses are checksummed
    addresses = arguments[0]
    assert addresses[0] == to_checksum_address(gmx_config_fork.get_wallet_address())
    assert addresses[4] == to_checksum_address(market_key)
    assert addresses[5] == to_checksum_address(params.collateral_address)

    # Verify numbers
    numbers = arguments[1]
    size_delta_usd = numbers[0]
    expected_size = int(Decimal(str(params.size_delta)) * Decimal(10**30))
    assert size_delta_usd == expected_size
    assert numbers[1] == int(params.initial_collateral_delta_amount)
    assert numbers[4] == execution_fee

    # Updated: Verify new parameters are included
    assert numbers[5] == params.callback_gas_limit
    assert numbers[6] == params.min_output_amount
    assert numbers[7] == params.valid_from_time


def test_build_order_arguments_uses_constants(base_order, gmx_config_fork):
    """Test that order arguments use module constants."""
    markets = base_order.markets.get_available_markets()
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    params = OrderParams(
        market_key=market_key,
        collateral_address=market_data["long_token_address"],
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=1000.0,
        initial_collateral_delta_amount="1000000000000000000",
    )

    arguments = base_order._build_order_arguments(params, 1000000, 2, 2000000, 1990000)

    addresses = arguments[0]
    # Updated: Verify zero addresses use constant
    assert addresses[2] == ETH_ZERO_ADDRESS  # callbackContract
    assert addresses[3] == ETH_ZERO_ADDRESS  # uiFeeReceiver

    # Updated: Verify referral code uses constant
    assert arguments[7] == ZERO_REFERRAL_CODE


# ==================== Multicall Args Tests ====================


def test_build_multicall_args_native_token(chain_name, base_order):
    """Test building multicall arguments with native token (WETH/WAVAX)."""
    markets = base_order.markets.get_available_markets()
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    tokens = NETWORK_TOKENS[chain_name]
    native_token = tokens.get("WETH") if chain_name == "arbitrum" else tokens.get("WAVAX")

    params = OrderParams(
        market_key=market_key,
        collateral_address=native_token,
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=1000.0,
        initial_collateral_delta_amount="1000000000000000000",
    )

    execution_fee = 1000000000000
    arguments = base_order._build_order_arguments(params, execution_fee, 2, 2000000, 1990000)

    multicall_args, value_amount = base_order._build_multicall_args(params, arguments, execution_fee, is_close=False)

    # For native token, value should include both execution fee and collateral
    expected_value = int(params.initial_collateral_delta_amount) + execution_fee
    assert value_amount == expected_value
    assert len(multicall_args) == 2  # sendWnt and createOrder
    assert len(multicall_args[0]) > 0
    assert len(multicall_args[1]) > 0


def test_build_multicall_args_erc20_token(chain_name, base_order, usdc):
    """Test building multicall arguments with ERC20 token."""
    markets = base_order.markets.get_available_markets()
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    params = OrderParams(
        market_key=market_key,
        collateral_address=usdc.address,
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=1000.0,
        initial_collateral_delta_amount="1000000000",  # 1000 USDC
    )

    execution_fee = 1000000000000
    arguments = base_order._build_order_arguments(params, execution_fee, 2, 2000000, 1990000)

    multicall_args, value_amount = base_order._build_multicall_args(params, arguments, execution_fee, is_close=False)

    # For ERC20, value should only include execution fee
    assert value_amount == execution_fee
    assert len(multicall_args) == 3  # sendWnt, sendTokens, createOrder
    assert len(multicall_args[0]) > 0
    assert len(multicall_args[1]) > 0
    assert len(multicall_args[2]) > 0


def test_build_multicall_args_close_position(chain_name, base_order):
    """Test building multicall arguments for closing a position."""
    markets = base_order.markets.get_available_markets()
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    tokens = NETWORK_TOKENS[chain_name]
    native_token = tokens.get("WETH") if chain_name == "arbitrum" else tokens.get("WAVAX")

    params = OrderParams(
        market_key=market_key,
        collateral_address=native_token,
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=1000.0,
        initial_collateral_delta_amount="1000000000000000000",
    )

    execution_fee = 1000000000000
    arguments = base_order._build_order_arguments(params, execution_fee, 4, 2000000, 0)

    multicall_args, value_amount = base_order._build_multicall_args(params, arguments, execution_fee, is_close=True)

    # For close, value should only include execution fee
    assert value_amount == execution_fee
    assert len(multicall_args) == 2  # sendWnt and createOrder
    assert len(multicall_args[0]) > 0
    assert len(multicall_args[1]) > 0


# ==================== Transaction Building Tests ====================


def test_build_transaction_structure(chain_name, base_order):
    """Test that built transaction has correct structure."""
    markets = base_order.markets.get_available_markets()
    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    tokens = NETWORK_TOKENS[chain_name]
    native_token = tokens.get("WETH") if chain_name == "arbitrum" else tokens.get("WAVAX")

    params = OrderParams(
        market_key=market_key,
        collateral_address=native_token,
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=100.0,
        initial_collateral_delta_amount="100000000000000000",
    )

    result = base_order.create_order(params, is_open=True)
    transaction = result.transaction

    # Verify required fields
    assert "from" in transaction
    assert "to" in transaction
    assert "data" in transaction
    assert "value" in transaction
    assert "gas" in transaction
    assert "chainId" in transaction
    assert "nonce" in transaction

    # Verify addresses
    assert transaction["to"] == base_order.contract_addresses.exchangerouter
    assert to_checksum_address(transaction["from"]) == base_order.config.get_wallet_address()

    # Verify gas pricing
    has_eip1559 = "maxFeePerGas" in transaction and "maxPriorityFeePerGas" in transaction
    has_legacy = "gasPrice" in transaction
    assert has_eip1559 or has_legacy


# ==================== Token Approval Tests ====================


def test_check_for_approval_native_token(chain_name, base_order):
    """Test that native tokens don't require approval."""
    markets = base_order.markets.get_available_markets()
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    tokens = NETWORK_TOKENS[chain_name]
    native_token = tokens.get("WETH") if chain_name == "arbitrum" else tokens.get("WAVAX")

    params = OrderParams(
        market_key=market_key,
        collateral_address=native_token,
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=1000.0,
        initial_collateral_delta_amount="1000000000000000000",
    )

    # Should not raise exception for native token
    base_order._check_for_approval(params)


def test_check_for_approval_insufficient_erc20(chain_name, base_order, usdc, test_address):
    """Test approval check fails with insufficient allowance."""
    markets = base_order.markets.get_available_markets()
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    # Set a low allowance (less than what we'll try to use)
    low_allowance = 100  # Very small allowance
    usdc.contract.functions.approve(base_order.contract_addresses.syntheticsrouter, low_allowance).transact({"from": test_address})

    params = OrderParams(
        market_key=market_key,
        collateral_address=usdc.address,
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=1000.0,
        initial_collateral_delta_amount="999999999999999",  # Very large amount
    )

    # Should raise ValueError for insufficient allowance
    with pytest.raises(ValueError, match="Insufficient token allowance"):
        base_order._check_for_approval(params)


# ==================== Full Order Creation Tests ====================


def test_create_order_increase(chain_name, base_order):
    """Test creating an increase order end-to-end."""
    markets = base_order.markets.get_available_markets()
    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    tokens = NETWORK_TOKENS[chain_name]
    native_token = tokens.get("WETH") if chain_name == "arbitrum" else tokens.get("WAVAX")

    params = OrderParams(
        market_key=market_key,
        collateral_address=native_token,
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=300.0,
        initial_collateral_delta_amount="300000000000000000",
    )

    result = base_order.create_order(params, is_open=True)

    assert isinstance(result, OrderResult)
    assert result.transaction is not None
    assert result.execution_fee > 0
    assert result.acceptable_price > 0
    assert result.mark_price > 0
    assert result.gas_limit > 0


def test_create_order_decrease(chain_name, base_order):
    """Test creating a decrease order end-to-end."""
    markets = base_order.markets.get_available_markets()
    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    tokens = NETWORK_TOKENS[chain_name]
    native_token = tokens.get("WETH") if chain_name == "arbitrum" else tokens.get("WAVAX")

    params = OrderParams(
        market_key=market_key,
        collateral_address=native_token,
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=100.0,
        initial_collateral_delta_amount="100000000000000000",
    )

    result = base_order.create_order(params, is_close=True)

    assert isinstance(result, OrderResult)
    assert result.transaction is not None
    assert result.execution_fee > 0
    # For close orders, acceptable_price can be 0 for market orders
    assert result.mark_price >= 0
    assert result.gas_limit > 0


def test_create_order_swap(chain_name, base_order):
    """Test creating a swap order end-to-end."""
    markets = base_order.markets.get_available_markets()
    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    tokens = NETWORK_TOKENS[chain_name]
    native_token = tokens.get("WETH") if chain_name == "arbitrum" else tokens.get("WAVAX")

    params = OrderParams(
        market_key=market_key,
        collateral_address=native_token,
        index_token_address=market_data["index_token_address"],
        is_long=False,
        size_delta=0.0,
        initial_collateral_delta_amount="100000000000000000",
        swap_path=[market_key],
    )

    result = base_order.create_order(params, is_swap=True)

    assert isinstance(result, OrderResult)
    assert result.transaction is not None
    assert result.execution_fee > 0
    assert result.acceptable_price == 0  # Swaps have 0 acceptable price
    assert result.mark_price > 0
    assert result.gas_limit > 0


# ==================== Price Impact Tests ====================


def test_estimate_price_impact_returns_value_or_none(chain_name, base_order):
    """Test that price impact estimation returns a value or None gracefully."""
    markets = base_order.markets.get_available_markets()
    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    tokens = NETWORK_TOKENS[chain_name]
    native_token = tokens.get("WETH") if chain_name == "arbitrum" else tokens.get("WAVAX")

    params = OrderParams(
        market_key=market_key,
        collateral_address=native_token,
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=1000.0,
        initial_collateral_delta_amount="1000000000000000000",
    )

    # Updated: Test price impact estimation
    price_impact = base_order._estimate_price_impact(
        params,
        market_data,
        is_open=True,
        is_close=False,
        is_swap=False,
    )

    # Should return float or None
    assert price_impact is None or isinstance(price_impact, float)


# ==================== Error Handling Tests ====================


def test_order_creation_invalid_market(base_order):
    """Test order creation with invalid market fails properly."""
    params = OrderParams(
        market_key="0x0000000000000000000000000000000000000000",
        collateral_address="0x1111111111111111111111111111111111111111",
        index_token_address="0x2222222222222222222222222222222222222222",
        is_long=True,
        size_delta=1000.0,
        initial_collateral_delta_amount="1000000000000000000",
    )

    with pytest.raises(ValueError, match="Market .* not found"):
        base_order.create_order(params, is_open=True)
