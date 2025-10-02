"""
Tests for Withdraw class with parametrized chain testing.

This test suite verifies the functionality of the Withdraw base class
when connected to different networks using Anvil forks. Tests include:
- Withdraw initialization
- Gas limits initialization
- Withdrawal creation with different output tokens
- Swap path determination
- Transaction building
- GM token approval checks
"""

import pytest
from decimal import Decimal
from eth_utils import to_checksum_address

from eth_defi.gmx.liquidity_base import Withdraw, WithdrawParams, WithdrawResult
from eth_defi.gmx.contracts import NETWORK_TOKENS
from eth_defi.token import fetch_erc20_details


# ==================== Initialization Tests ====================


def test_withdraw_initialization(chain_name, gmx_config_fork):
    """Test that Withdraw class initializes correctly."""
    withdraw = Withdraw(gmx_config_fork)

    assert withdraw.config == gmx_config_fork
    assert withdraw.chain == chain_name
    assert withdraw.web3 is not None
    assert withdraw.chain_id == gmx_config_fork.web3.eth.chain_id
    assert withdraw.contract_addresses is not None
    assert withdraw._exchange_router_contract is not None
    assert withdraw.markets is not None

    # Test gas limits initialization
    assert hasattr(withdraw, "_gas_limits")
    assert withdraw._gas_limits is not None
    assert isinstance(withdraw._gas_limits, dict)


def test_gas_limits_initialization(chain_name, gmx_config_fork):
    """Test that gas limits are properly loaded from datastore."""
    withdraw = Withdraw(gmx_config_fork)

    assert "withdraw" in withdraw._gas_limits
    assert "multicall_base" in withdraw._gas_limits

    # Verify they are actual integer values
    assert isinstance(withdraw._gas_limits["withdraw"], int)
    assert isinstance(withdraw._gas_limits["multicall_base"], int)

    # Verify reasonable gas limit values
    assert withdraw._gas_limits["withdraw"] > 0
    assert withdraw._gas_limits["multicall_base"] > 0


# ==================== Swap Path Tests ====================


def test_determine_swap_paths_to_long_token(chain_name, gmx_config_fork):
    """Test swap path determination when withdrawing to long token."""
    withdraw = Withdraw(gmx_config_fork)
    markets = withdraw.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    # Withdraw to long token (no swap needed for long)
    params = WithdrawParams(
        market_key=market_key,
        gm_amount=1000000000000000000,  # 1 GM token
        out_token=market_data["long_token_address"],
    )

    long_swap_path, short_swap_path = withdraw._determine_swap_paths(params, market_data)

    # No swap needed for long token
    assert long_swap_path == []
    # Swap needed for short token (to convert to long)
    assert len(short_swap_path) > 0


def test_determine_swap_paths_to_short_token(chain_name, gmx_config_fork):
    """Test swap path determination when withdrawing to short token."""
    withdraw = Withdraw(gmx_config_fork)
    markets = withdraw.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    # Withdraw to short token (no swap needed for short)
    params = WithdrawParams(
        market_key=market_key,
        gm_amount=1000000000000000000,
        out_token=market_data["short_token_address"],
    )

    long_swap_path, short_swap_path = withdraw._determine_swap_paths(params, market_data)

    # Swap needed for long token (to convert to short)
    assert len(long_swap_path) > 0
    # No swap needed for short token
    assert short_swap_path == []


def test_determine_swap_paths_to_other_token(chain_name, gmx_config_fork):
    """Test swap path determination when withdrawing to non-market token."""
    withdraw = Withdraw(gmx_config_fork)
    markets = withdraw.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    tokens = NETWORK_TOKENS[chain_name]
    usdc_address = tokens.get("USDC")

    # Withdraw to USDC (likely needs swap for both)
    params = WithdrawParams(
        market_key=market_key,
        gm_amount=1000000000000000000,
        out_token=usdc_address,
    )

    long_swap_path, short_swap_path = withdraw._determine_swap_paths(params, market_data)

    # Both should have swap paths if USDC is not a market token
    assert isinstance(long_swap_path, list)
    assert isinstance(short_swap_path, list)


# ==================== Argument Building Tests ====================


def test_build_withdraw_arguments_structure(chain_name, gmx_config_fork):
    """Test that withdraw arguments have correct structure."""
    withdraw = Withdraw(gmx_config_fork)
    markets = withdraw.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    params = WithdrawParams(
        market_key=market_key,
        gm_amount=1000000000000000000,
        out_token=market_data["long_token_address"],
    )

    long_swap_path = []
    short_swap_path = [market_key]
    min_long_token_amount = 0
    min_short_token_amount = 0
    execution_fee = 1000000000000000

    arguments = withdraw._build_withdraw_arguments(
        params,
        long_swap_path,
        short_swap_path,
        min_long_token_amount,
        min_short_token_amount,
        execution_fee,
    )

    # Verify tuple structure (11 elements)
    assert len(arguments) == 11

    # Verify addresses are checksummed
    receiver = arguments[0]
    assert receiver == to_checksum_address(gmx_config_fork.get_wallet_address())

    # Verify market key
    assert arguments[3] == to_checksum_address(market_key)

    # Verify execution fee
    assert arguments[9] == execution_fee

    # Verify swap paths
    assert arguments[4] == long_swap_path
    assert arguments[5] == [to_checksum_address(addr) for addr in short_swap_path]


# ==================== Multicall Building Tests ====================


def test_build_multicall_args_withdrawal(chain_name, gmx_config_fork):
    """Test multicall args for withdrawal."""
    withdraw = Withdraw(gmx_config_fork)
    markets = withdraw.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    gm_amount = 1000000000000000000  # 1 GM token

    params = WithdrawParams(
        market_key=market_key,
        gm_amount=gm_amount,
        out_token=market_data["long_token_address"],
    )

    long_swap_path, short_swap_path = withdraw._determine_swap_paths(params, market_data)
    execution_fee = 1000000000000000

    arguments = withdraw._build_withdraw_arguments(params, long_swap_path, short_swap_path, 0, 0, execution_fee)

    multicall_args, value_amount = withdraw._build_multicall_args(params, arguments, execution_fee)

    # Should have: sendWnt (execution fee) + sendTokens (GM tokens) + createWithdrawal
    assert len(multicall_args) == 3
    # Value is just execution fee
    assert value_amount == execution_fee


# ==================== Transaction Building Tests ====================


def test_build_transaction_structure(chain_name, gmx_config_fork):
    """Test that built transaction has correct structure."""
    withdraw = Withdraw(gmx_config_fork)

    multicall_args = [b"\x00\x01\x02"]  # Dummy data
    value_amount = 1000000000000000
    gas_limit = 2000000
    gas_price = 100000000

    transaction = withdraw._build_transaction(multicall_args, value_amount, gas_limit, gas_price)

    # Verify transaction structure
    assert "from" in transaction
    assert "to" in transaction
    assert "value" in transaction
    assert "gas" in transaction
    assert "chainId" in transaction
    assert "data" in transaction
    assert "nonce" in transaction

    # Verify addresses
    assert transaction["to"] == withdraw.contract_addresses.exchangerouter
    assert to_checksum_address(transaction["from"]) == gmx_config_fork.get_wallet_address()

    # Verify values
    assert transaction["value"] == value_amount
    assert transaction["gas"] == gas_limit
    assert transaction["chainId"] == withdraw.chain_id

    # Verify gas pricing
    has_eip1559 = "maxFeePerGas" in transaction and "maxPriorityFeePerGas" in transaction
    has_legacy = "gasPrice" in transaction
    assert has_eip1559 or has_legacy


# ==================== Approval Tests ====================


def test_check_for_approval_zero_amount(chain_name, gmx_config_fork):
    """Test that zero amount doesn't require approval check."""
    withdraw = Withdraw(gmx_config_fork)
    markets = withdraw.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))

    # Should not raise exception for zero amount
    withdraw._check_for_approval(market_key, 0)


def test_check_for_approval_insufficient_gm(chain_name, gmx_config_fork):
    """Test approval check fails with insufficient GM token allowance."""
    withdraw = Withdraw(gmx_config_fork)
    markets = withdraw.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))

    # Should raise ValueError for insufficient allowance (test wallet has no approval)
    with pytest.raises(ValueError, match="Insufficient GM token allowance"):
        withdraw._check_for_approval(market_key, 999999999999999999)


# ==================== Full Withdrawal Creation Tests ====================


def test_create_withdrawal_to_long_token(chain_name, gmx_config_fork):
    """Test creating a withdrawal to long token."""
    withdraw = Withdraw(gmx_config_fork)
    markets = withdraw.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    gm_amount = 1000000000000000000  # 1 GM token

    params = WithdrawParams(
        market_key=market_key,
        gm_amount=gm_amount,
        out_token=market_data["long_token_address"],
    )

    # This will fail on approval, but we're testing the flow
    try:
        result = withdraw.create_withdrawal(params)

        # If we get here, verify the result
        assert isinstance(result, WithdrawResult)
        assert result.transaction is not None
        assert result.execution_fee > 0
        assert result.gas_limit > 0
        assert result.min_long_token_amount >= 0
        assert result.min_short_token_amount >= 0
    except ValueError as e:
        # Expected to fail on approval
        if "Insufficient GM token allowance" not in str(e):
            raise


def test_create_withdrawal_to_short_token(chain_name, gmx_config_fork):
    """Test creating a withdrawal to short token."""
    withdraw = Withdraw(gmx_config_fork)
    markets = withdraw.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    gm_amount = 1000000000000000000  # 1 GM token

    params = WithdrawParams(
        market_key=market_key,
        gm_amount=gm_amount,
        out_token=market_data["short_token_address"],
    )

    # This will fail on approval, but we're testing the flow
    try:
        result = withdraw.create_withdrawal(params)

        assert isinstance(result, WithdrawResult)
        assert result.transaction is not None
        assert result.execution_fee > 0
        assert result.gas_limit > 0
    except ValueError as e:
        if "Insufficient GM token allowance" not in str(e):
            raise


def test_create_withdrawal_invalid_market(gmx_config_fork):
    """Test that withdrawal creation fails with invalid market."""
    withdraw = Withdraw(gmx_config_fork)

    params = WithdrawParams(
        market_key="0x0000000000000000000000000000000000000000",
        gm_amount=1000000000000000000,
        out_token="0x1111111111111111111111111111111111111111",
    )

    with pytest.raises(ValueError, match="Market .* not found"):
        withdraw.create_withdrawal(params)


# ==================== Encoding Tests ====================


def test_create_withdrawal_encoding(chain_name, gmx_config_fork):
    """Test that createWithdrawal function is encoded correctly."""
    withdraw = Withdraw(gmx_config_fork)
    markets = withdraw.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    params = WithdrawParams(
        market_key=market_key,
        gm_amount=1000000000000000000,
        out_token=market_data["long_token_address"],
    )

    arguments = withdraw._build_withdraw_arguments(params, [], [], 0, 0, 1000000)
    encoded = withdraw._create_withdrawal(arguments)

    assert isinstance(encoded, bytes)
    assert len(encoded) > 0


def test_send_gm_tokens_encoding(chain_name, gmx_config_fork):
    """Test that sendTokens function for GM tokens is encoded correctly."""
    withdraw = Withdraw(gmx_config_fork)
    markets = withdraw.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))

    encoded = withdraw._send_gm_tokens(market_key, 1000000000000000000)

    assert isinstance(encoded, bytes)
    assert len(encoded) > 0


def test_send_wnt_encoding(chain_name, gmx_config_fork):
    """Test that sendWnt function is encoded correctly."""
    withdraw = Withdraw(gmx_config_fork)

    encoded = withdraw._send_wnt(1000000000000000)

    assert isinstance(encoded, bytes)
    assert len(encoded) > 0


# ==================== Parameter Validation Tests ====================


def test_withdraw_params_validation(chain_name, gmx_config_fork):
    """Test that WithdrawParams validates correctly."""
    markets_instance = gmx_config_fork
    withdraw = Withdraw(markets_instance)

    markets = withdraw.markets.get_available_markets()
    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    # Valid params should work
    params = WithdrawParams(
        market_key=market_key,
        gm_amount=1000000000000000000,
        out_token=market_data["long_token_address"],
        execution_buffer=1.5,
    )

    assert params.market_key == market_key
    assert params.gm_amount == 1000000000000000000
    assert params.execution_buffer == 1.5


def test_withdraw_with_custom_gas_price(chain_name, gmx_config_fork):
    """Test withdrawal with custom max_fee_per_gas."""
    withdraw = Withdraw(gmx_config_fork)
    markets = withdraw.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    custom_gas_price = 50000000000  # 50 gwei

    params = WithdrawParams(
        market_key=market_key,
        gm_amount=1000000000000000000,
        out_token=market_data["long_token_address"],
        max_fee_per_gas=custom_gas_price,
    )

    assert params.max_fee_per_gas == custom_gas_price

    # The create_withdrawal would use this gas price
    # (We can't test full flow without approval, but params are validated)
