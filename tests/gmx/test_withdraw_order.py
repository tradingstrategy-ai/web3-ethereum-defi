"""
Tests for WithdrawOrder class.
"""

import pytest
from eth_utils import to_checksum_address

from eth_defi.gmx.order.withdraw_order import WithdrawOrder
from eth_defi.gmx.liquidity_base.withdraw import WithdrawResult
from eth_defi.gmx.contracts import NETWORK_TOKENS
from eth_defi.token import fetch_erc20_details


@pytest.fixture
def gm_market(chain_name):
    """Get GM market address for the specified chain."""
    if chain_name == "avalanche":
        return "0xB7e69749E3d2EDd90ea59A4932EFEa2D41E245d7"  # GM AVAX/USDC
    elif chain_name == "arbitrum":
        return "0x70d95587d40A2caf56bd97485aB3Eec10Bee6336"  # GM ETH/USDC
    else:
        pytest.skip(f"GM market not configured for chain: {chain_name}")


def test_withdraw_order_initialization(chain_name, gmx_config_fork):
    """Test that WithdrawOrder initializes correctly with market and output token configuration."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    # Get first market
    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    # Create WithdrawOrder with long token as output
    withdraw_order = WithdrawOrder(
        gmx_config_fork,
        market_key=market_key,
        out_token=market_data["long_token_address"],
    )

    # Verify initialization
    assert withdraw_order.config == gmx_config_fork
    assert withdraw_order.chain.lower() == chain_name.lower()
    assert withdraw_order.market_key == to_checksum_address(market_key)
    assert withdraw_order.out_token == to_checksum_address(market_data["long_token_address"])
    assert withdraw_order.web3 is not None
    assert withdraw_order.markets is not None


def test_withdraw_order_create_to_long_token(gmx_config_fork, wallet_with_gm_tokens, gm_market):
    """Test creating a withdrawal order to receive long token."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_data = markets[gm_market]

    # Create WithdrawOrder with long token as output
    withdraw_order = WithdrawOrder(
        gmx_config_fork,
        market_key=gm_market,
        out_token=market_data["long_token_address"],
    )

    # Create withdrawal order
    result = withdraw_order.create_withdraw_order(
        gm_amount=1000000000000000000,  # 1 GM token
        execution_buffer=1.3,
    )

    # Verify result
    assert isinstance(result, WithdrawResult)
    assert hasattr(result, "transaction")
    assert isinstance(result.transaction, dict)
    assert result.execution_fee > 0
    assert result.gas_limit > 0


def test_withdraw_order_create_to_short_token(gmx_config_fork, wallet_with_gm_tokens, gm_market):
    """Test creating a withdrawal order to receive short token."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_data = markets[gm_market]

    # Create WithdrawOrder with short token as output
    withdraw_order = WithdrawOrder(
        gmx_config_fork,
        market_key=gm_market,
        out_token=market_data["short_token_address"],
    )

    # Create withdrawal order
    result = withdraw_order.create_withdraw_order(
        gm_amount=1000000000000000000,  # 1 GM token
        execution_buffer=1.3,
    )

    # Verify result
    assert isinstance(result, WithdrawResult)
    assert result.execution_fee > 0


def test_withdraw_order_create_with_native_token(chain_name, gmx_config_fork, wallet_with_gm_tokens, gm_market):
    """Test creating a withdrawal order to receive native token (WAVAX)."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_data = markets[gm_market]

    # For Avalanche, WAVAX is the native token (long token in this market)
    tokens = NETWORK_TOKENS[chain_name]
    native_token = tokens.get("WAVAX")

    # Create WithdrawOrder with native token (WAVAX) as output
    withdraw_order = WithdrawOrder(
        gmx_config_fork,
        market_key=gm_market,
        out_token=market_data["long_token_address"],  # WAVAX
    )

    # Create withdrawal order
    result = withdraw_order.create_withdraw_order(
        gm_amount=1000000000000000000,  # 1 GM token
        execution_buffer=1.3,
    )

    # Verify result
    assert isinstance(result, WithdrawResult)
    assert result.execution_fee > 0


def test_withdraw_order_transaction_structure(gmx_config_fork, wallet_with_gm_tokens, gm_market):
    """Test that withdrawal order creates valid unsigned transaction structure."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_data = markets[gm_market]

    # Create WithdrawOrder
    withdraw_order = WithdrawOrder(
        gmx_config_fork,
        market_key=gm_market,
        out_token=market_data["long_token_address"],
    )

    # Create withdrawal order
    result = withdraw_order.create_withdraw_order(
        gm_amount=1000000000000000000,  # 1 GM token
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
    assert len(tx["data"]) > 2  # Should be hex string like "0x..."

    print(f"Withdrawal order transaction created successfully:")
    print(f"  To: {tx['to']}")
    print(f"  Data length: {len(tx['data'])} bytes")
    print(f"  Value: {tx.get('value', 0)}")
    print(f"  Execution fee: {result.execution_fee}")


def test_withdraw_order_with_custom_execution_buffer(gmx_config_fork, wallet_with_gm_tokens, gm_market):
    """Test withdrawal order with custom execution buffer."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_data = markets[gm_market]

    # Create WithdrawOrder
    withdraw_order = WithdrawOrder(
        gmx_config_fork,
        market_key=gm_market,
        out_token=market_data["long_token_address"],
    )

    # Test with different execution buffers
    result_low = withdraw_order.create_withdraw_order(
        gm_amount=1000000000000000000,
        execution_buffer=1.1,  # Low buffer
    )

    result_high = withdraw_order.create_withdraw_order(
        gm_amount=1000000000000000000,
        execution_buffer=2.0,  # High buffer
    )

    # Higher buffer should result in higher execution fee
    assert result_high.execution_fee > result_low.execution_fee
    assert isinstance(result_low, WithdrawResult)
    assert isinstance(result_high, WithdrawResult)


def test_withdraw_order_with_small_amount(gmx_config_fork, wallet_with_gm_tokens, gm_market):
    """Test withdrawal order with very small GM token amount."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_data = markets[gm_market]

    # Create WithdrawOrder
    withdraw_order = WithdrawOrder(
        gmx_config_fork,
        market_key=gm_market,
        out_token=market_data["long_token_address"],
    )

    # Create withdrawal order with small amount
    result = withdraw_order.create_withdraw_order(
        gm_amount=1000000,  # 0.000001 GM token
    )

    assert isinstance(result, WithdrawResult)


def test_withdraw_order_with_invalid_market(chain_name, gmx_config_fork):
    """Test withdrawal order with invalid market address raises error."""
    invalid_market = "0x0000000000000000000000000000000000000001"

    # Create WithdrawOrder with invalid market
    withdraw_order = WithdrawOrder(
        gmx_config_fork,
        market_key=invalid_market,
        out_token="0x0000000000000000000000000000000000000002",
    )

    # Should raise ValueError when trying to create order
    with pytest.raises(ValueError, match="Market.*not found"):
        withdraw_order.create_withdraw_order(
            gm_amount=1000000000000000000,
        )


def test_withdraw_order_with_custom_gas_price(gmx_config_fork, wallet_with_gm_tokens, gm_market):
    """Test withdrawal order with custom max fee per gas."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_data = markets[gm_market]

    # Create WithdrawOrder
    withdraw_order = WithdrawOrder(
        gmx_config_fork,
        market_key=gm_market,
        out_token=market_data["long_token_address"],
    )

    # Create with custom gas price
    custom_gas_price = 50000000000  # 50 gwei
    result = withdraw_order.create_withdraw_order(
        gm_amount=1000000000000000000,
        max_fee_per_gas=custom_gas_price,
    )

    assert isinstance(result, WithdrawResult)
    # The execution fee calculation should use the custom gas price


def test_withdraw_order_attributes_accessible(chain_name, gmx_config_fork):
    """Test that all WithdrawOrder attributes are accessible."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_key = next(iter(markets.keys()))
    market_data = markets[market_key]

    # Create WithdrawOrder
    withdraw_order = WithdrawOrder(
        gmx_config_fork,
        market_key=market_key,
        out_token=market_data["long_token_address"],
    )

    # Test all inherited attributes from Withdraw base class
    assert hasattr(withdraw_order, "config")
    assert hasattr(withdraw_order, "chain")
    assert hasattr(withdraw_order, "web3")
    assert hasattr(withdraw_order, "markets")
    assert hasattr(withdraw_order, "market_key")
    assert hasattr(withdraw_order, "out_token")
    assert hasattr(withdraw_order, "logger")

    # Verify they're not None
    assert withdraw_order.config is not None
    assert withdraw_order.chain is not None
    assert withdraw_order.web3 is not None
    assert withdraw_order.markets is not None


def test_withdraw_order_different_output_tokens(gmx_config_fork, wallet_with_gm_tokens, gm_market):
    """Test withdrawal orders with different output tokens for the same market."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_data = markets[gm_market]

    # Create WithdrawOrder for long token
    withdraw_to_long = WithdrawOrder(
        gmx_config_fork,
        market_key=gm_market,
        out_token=market_data["long_token_address"],
    )

    # Create WithdrawOrder for short token
    withdraw_to_short = WithdrawOrder(
        gmx_config_fork,
        market_key=gm_market,
        out_token=market_data["short_token_address"],
    )

    # Create both orders
    result_long = withdraw_to_long.create_withdraw_order(gm_amount=1000000000000000000)
    result_short = withdraw_to_short.create_withdraw_order(gm_amount=1000000000000000000)

    # Both should succeed
    assert isinstance(result_long, WithdrawResult)
    assert isinstance(result_short, WithdrawResult)

    # Verify different output tokens
    assert withdraw_to_long.out_token != withdraw_to_short.out_token


def test_withdraw_order_to_usdc(chain_name, gmx_config_fork, wallet_with_gm_tokens, gm_market):
    """Test withdrawal order to USDC specifically."""
    # Get available markets
    from eth_defi.gmx.core.markets import Markets

    markets_obj = Markets(gmx_config_fork)
    markets = markets_obj.get_available_markets()

    if not markets:
        pytest.skip("No markets available")

    market_data = markets[gm_market]

    # Get USDC address
    tokens = NETWORK_TOKENS[chain_name]
    usdc_address = tokens.get("USDC")

    # Create WithdrawOrder to USDC (short token)
    withdraw_order = WithdrawOrder(
        gmx_config_fork,
        market_key=gm_market,
        out_token=market_data["short_token_address"],  # USDC
    )

    # Create withdrawal order
    result = withdraw_order.create_withdraw_order(
        gm_amount=1000000000000000000,  # 1 GM token
    )

    assert isinstance(result, WithdrawResult)
    assert withdraw_order.out_token.lower() == usdc_address.lower()
