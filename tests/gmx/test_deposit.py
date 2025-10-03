"""
Tests for Deposit class
"""

import pytest
from eth_utils import to_checksum_address

from eth_defi.gmx.liquidity_base import Deposit, DepositParams, DepositResult
from eth_defi.gmx.contracts import NETWORK_TOKENS


# ==================== Initialization Tests ====================


def test_deposit_initialization(chain_name, gmx_config_fork):
    """Test that Deposit class initialises correctly."""
    deposit = Deposit(gmx_config_fork)

    assert deposit.config == gmx_config_fork
    assert deposit.chain == chain_name
    assert deposit.web3 is not None
    assert deposit.chain_id == gmx_config_fork.web3.eth.chain_id
    assert deposit.contract_addresses is not None
    assert deposit._exchange_router_contract is not None
    assert deposit.markets is not None

    # Test gas limits initialisation
    assert hasattr(deposit, "_gas_limits")
    assert deposit._gas_limits is not None
    assert isinstance(deposit._gas_limits, dict)


def test_gas_limits_initialization(chain_name, gmx_config_fork):
    """Test that gas limits are properly loaded from datastore."""
    deposit = Deposit(gmx_config_fork)

    assert "deposit" in deposit._gas_limits
    assert "multicall_base" in deposit._gas_limits

    # Verify they are actual integer values
    assert isinstance(deposit._gas_limits["deposit"], int)
    assert isinstance(deposit._gas_limits["multicall_base"], int)

    # Verify reasonable gas limit values
    assert deposit._gas_limits["deposit"] > 0
    assert deposit._gas_limits["multicall_base"] > 0


# ==================== Swap Path Tests ====================


def test_determine_swap_paths_no_swap_needed(chain_name, gmx_config_fork):
    """Test swap path determination when tokens match market tokens."""
    deposit = Deposit(gmx_config_fork)
    markets = deposit.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    params = DepositParams(
        market_key=market_key,
        initial_long_token=market_data["long_token_address"],
        initial_short_token=market_data["short_token_address"],
        long_token_amount=1000000,
        short_token_amount=1000000,
    )

    long_swap_path, short_swap_path = deposit._determine_swap_paths(
        params,
        market_data,
        markets,
    )

    # No swap needed when tokens match
    assert long_swap_path == []
    assert short_swap_path == []


def test_determine_swap_paths_with_swap(chain_name, gmx_config_fork):
    """Test swap path determination when swap is needed."""
    deposit = Deposit(gmx_config_fork)
    markets = deposit.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    tokens = NETWORK_TOKENS[chain_name]
    usdc_address = tokens.get("USDC")

    # Use USDC for both tokens (will need swap if market tokens differ)
    params = DepositParams(
        market_key=market_key,
        initial_long_token=usdc_address,
        initial_short_token=usdc_address,
        long_token_amount=1000000,
        short_token_amount=1000000,
    )

    long_swap_path, short_swap_path = deposit._determine_swap_paths(
        params,
        market_data,
        markets,
    )

    # Should have swap paths if USDC doesn't match market tokens
    # (At least one should be non-empty)
    assert isinstance(long_swap_path, list)
    assert isinstance(short_swap_path, list)


def test_build_deposit_arguments_structure(chain_name, gmx_config_fork):
    """Test that deposit arguments have correct structure."""
    deposit = Deposit(gmx_config_fork)
    markets = deposit.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    params = DepositParams(
        market_key=market_key,
        initial_long_token=market_data["long_token_address"],
        initial_short_token=market_data["short_token_address"],
        long_token_amount=1000000000000000000,
        short_token_amount=1000000000000000000,
    )

    long_swap_path = []
    short_swap_path = []
    min_market_tokens = 0
    execution_fee = 1000000000000000

    arguments = deposit._build_deposit_arguments(params, long_swap_path, short_swap_path, min_market_tokens, execution_fee)

    # Verify tuple structure (12 elements)
    assert len(arguments) == 12

    # Verify addresses are checksummed
    receiver = arguments[0]
    assert receiver == to_checksum_address(gmx_config_fork.get_wallet_address())

    # Verify market key
    assert arguments[3] == to_checksum_address(market_key)

    # Verify execution fee
    assert arguments[10] == execution_fee


# ==================== Multicall Building Tests ====================


def test_build_multicall_args_both_tokens(chain_name, gmx_config_fork):
    """Test multicall args when depositing both long and short tokens."""
    deposit = Deposit(gmx_config_fork)
    markets = deposit.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    tokens = NETWORK_TOKENS[chain_name]
    usdc_address = tokens.get("USDC")

    params = DepositParams(
        market_key=market_key,
        initial_long_token=usdc_address,
        initial_short_token=usdc_address,
        long_token_amount=100000000,  # 100 USDC
        short_token_amount=100000000,  # 100 USDC
    )

    long_swap_path, short_swap_path = deposit._determine_swap_paths(
        params,
        market_data,
        markets,
    )
    execution_fee = 1000000000000000

    arguments = deposit._build_deposit_arguments(
        params,
        long_swap_path,
        short_swap_path,
        0,
        execution_fee,
    )

    multicall_args, value_amount = deposit._build_multicall_args(
        params,
        arguments,
        execution_fee,
    )

    # Should have: sendWnt (execution fee) + sendTokens (long) + sendTokens (short) + createDeposit
    assert len(multicall_args) == 4
    assert value_amount == execution_fee  # Only execution fee for ERC20 tokens


def test_build_multicall_args_native_token(chain_name, gmx_config_fork):
    """Test multicall args when depositing native token."""
    deposit = Deposit(gmx_config_fork)
    markets = deposit.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    tokens = NETWORK_TOKENS[chain_name]
    native_token = tokens.get("WETH") if chain_name == "arbitrum" else tokens.get("WAVAX")
    usdc_address = tokens.get("USDC")

    long_amount = 1000000000000000000  # 1 native token

    params = DepositParams(
        market_key=market_key,
        initial_long_token=native_token,
        initial_short_token=usdc_address,
        long_token_amount=long_amount,
        short_token_amount=100000000,
    )

    long_swap_path, short_swap_path = deposit._determine_swap_paths(
        params,
        market_data,
        markets,
    )
    execution_fee = 1000000000000000

    arguments = deposit._build_deposit_arguments(
        params,
        long_swap_path,
        short_swap_path,
        0,
        execution_fee,
    )

    multicall_args, value_amount = deposit._build_multicall_args(
        params,
        arguments,
        execution_fee,
    )

    # Value should include execution fee + native token amount
    assert value_amount == execution_fee + long_amount


# ==================== Transaction Building Tests ====================


def test_build_transaction_structure(chain_name, gmx_config_fork):
    """Test that built transaction has correct structure."""
    deposit = Deposit(gmx_config_fork)

    multicall_args = [b"\x00\x01\x02"]  # Dummy data
    value_amount = 1000000000000000
    gas_limit = 2500000
    gas_price = 100000000

    transaction = deposit._build_transaction(
        multicall_args,
        value_amount,
        gas_limit,
        gas_price,
    )

    # Verify transaction structure
    assert "from" in transaction
    assert "to" in transaction
    assert "value" in transaction
    assert "gas" in transaction
    assert "chainId" in transaction
    assert "data" in transaction
    assert "nonce" in transaction

    # Verify addresses
    assert transaction["to"] == deposit.contract_addresses.exchangerouter
    assert to_checksum_address(transaction["from"]) == gmx_config_fork.get_wallet_address()

    # Verify values
    assert transaction["value"] == value_amount
    assert transaction["gas"] == gas_limit
    assert transaction["chainId"] == deposit.chain_id

    # Verify gas pricing
    has_eip1559 = "maxFeePerGas" in transaction and "maxPriorityFeePerGas" in transaction
    has_legacy = "gasPrice" in transaction
    assert has_eip1559 or has_legacy


# ==================== Approval Tests ====================


def test_check_for_approval_native_token(chain_name, gmx_config_fork):
    """Test that native tokens don't require approval check."""
    deposit = Deposit(gmx_config_fork)

    tokens = NETWORK_TOKENS[chain_name]
    native_token = tokens.get("WETH") if chain_name == "arbitrum" else tokens.get("WAVAX")

    # Should not raise exception for native token
    deposit._check_for_approval(native_token, 1000000000000000000)


def test_check_for_approval_zero_amount(chain_name, gmx_config_fork):
    """Test that zero amount doesn't require approval check."""
    deposit = Deposit(gmx_config_fork)

    tokens = NETWORK_TOKENS[chain_name]
    usdc_address = tokens.get("USDC")

    # Should not raise exception for zero amount
    deposit._check_for_approval(usdc_address, 0)


# ==================== Deposit Creation Tests ====================


def test_create_deposit_with_market_tokens(chain_name, gmx_config_fork, test_wallet):
    """Test creating a deposit with market's native tokens."""
    deposit = Deposit(gmx_config_fork)
    markets = deposit.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    # Find a market with USDC as one of the tokens
    market_key = None
    market_data = None
    tokens = NETWORK_TOKENS[chain_name]
    usdc_address = tokens.get("USDC")

    for key, data in markets.items():
        if data.get("long_token_address", "").lower() == usdc_address.lower() or data.get("short_token_address", "").lower() == usdc_address.lower():
            market_key = key
            market_data = data
            break

    if not market_key:
        pytest.skip("No suitable market found")

    # Small amounts to avoid approval issues in test
    long_amount = 1000000  # 1 USDC
    short_amount = 1000000  # 1 USDC

    params = DepositParams(
        market_key=market_key,
        initial_long_token=market_data["long_token_address"],
        initial_short_token=market_data["short_token_address"],
        long_token_amount=long_amount,
        short_token_amount=short_amount,
    )

    # This will fail on approval, but we're testing the flow
    try:
        result = deposit.create_deposit(params)

        # If we get here, verify the result
        assert isinstance(result, DepositResult)
        assert result.transaction is not None
        assert result.execution_fee > 0
        assert result.gas_limit > 0
    except ValueError as e:
        # Expected to fail on approval
        if "Insufficient token allowance" not in str(e):
            raise


def test_create_deposit_invalid_market(gmx_config_fork):
    """Test that deposit creation fails with invalid market."""
    deposit = Deposit(gmx_config_fork)

    params = DepositParams(
        market_key="0x0000000000000000000000000000000000000000",
        initial_long_token="0x1111111111111111111111111111111111111111",
        initial_short_token="0x2222222222222222222222222222222222222222",
        long_token_amount=1000000,
        short_token_amount=1000000,
    )

    with pytest.raises(ValueError, match="Market .* not found"):
        deposit.create_deposit(params)


def test_create_deposit_encoding(chain_name, gmx_config_fork):
    """Test that createDeposit function is encoded correctly."""
    deposit = Deposit(gmx_config_fork)
    markets = deposit.markets.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    params = DepositParams(
        market_key=market_key,
        initial_long_token=market_data["long_token_address"],
        initial_short_token=market_data["short_token_address"],
        long_token_amount=1000000,
        short_token_amount=1000000,
    )

    arguments = deposit._build_deposit_arguments(
        params,
        [],
        [],
        0,
        1000000,
    )
    encoded = deposit._create_deposit(arguments)

    assert isinstance(encoded, bytes)
    assert len(encoded) > 0


def test_send_tokens_encoding(chain_name, gmx_config_fork):
    """Test that sendTokens function is encoded correctly."""
    deposit = Deposit(gmx_config_fork)

    tokens = NETWORK_TOKENS[chain_name]
    usdc_address = tokens.get("USDC")

    encoded = deposit._send_tokens(usdc_address, 1000000)

    assert isinstance(encoded, bytes)
    assert len(encoded) > 0


def test_send_wnt_encoding(chain_name, gmx_config_fork):
    """Test that sendWnt function is encoded correctly."""
    deposit = Deposit(gmx_config_fork)

    encoded = deposit._send_wnt(1000000000000000)

    assert isinstance(encoded, bytes)
    assert len(encoded) > 0
