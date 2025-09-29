"""
Tests for BaseOrder class with parametrized chain testing.

This test suite verifies the functionality of the BaseOrder class
when connected to different networks using Anvil forks. Tests focus
on creating unsigned transactions that can be signed and executed.
"""

import pytest
from decimal import Decimal

from eth_defi.gmx.order.base_order import OrderParams, OrderType
from eth_defi.gmx.contracts import NETWORK_TOKENS
from eth_defi.trace import assert_transaction_success_with_explanation


def test_base_order_initialization(chain_name, base_order):
    """Test that BaseOrder initializes correctly with chain-specific config."""

    assert base_order.config is not None
    assert base_order.chain.lower() == chain_name.lower()
    assert base_order.web3 is not None
    assert base_order.markets is not None
    assert base_order.oracle_prices is not None
    assert base_order.contract_addresses is not None
    assert base_order._exchange_router_contract is not None


def test_create_market_buy_order_validation(chain_name,base_order):
    """Test parameter validation for market buy orders."""

    # Test with invalid symbol
    with pytest.raises(ValueError, match="Invalid market symbol"):
        base_order.create_market_buy_order(
            symbol="INVALID/USD",
            amount=100
        )

    # Test with zero amount
    with pytest.raises(ValueError, match="Amount must be positive"):
        base_order.create_market_buy_order(
            symbol="ETH/USD" if chain_name == "arbitrum" else "AVAX/USD",
            amount=0
        )


def test_create_market_buy_order_success(chain_name, base_order, wallet_with_all_tokens):
    """Test successful creation of market buy order transaction."""

    # Select appropriate market based on chain
    symbol = "ETH/USD" if chain_name == "arbitrum" else "AVAX/USD"

    # Create market buy order
    result = base_order.create_market_buy_order(
        symbol=symbol,
        amount=100,  # $100 position size
        params={"slippage_percent": 0.01}  # 1% slippage
    )

    # Verify result structure
    assert isinstance(result, TransactionResult)
    assert result.order_type == OrderType.MARKET_INCREASE
    assert result.side == OrderSide.BUY
    assert result.amount == 100
    assert result.symbol == symbol
    assert result.estimated_execution_fee > 0
    assert result.gas_estimates["total"] > 0

    # Verify transaction structure
    assert "to" in result.transaction
    assert "data" in result.transaction
    assert "value" in result.transaction
    assert "gas" in result.transaction
    assert "chainId" in result.transaction

    # Transaction should be properly formatted for signing
    assert result.transaction["chainId"] == base_order.web3.eth.chain_id
    assert result.transaction["to"] == base_order.contract_addresses.exchangerouter


def test_create_market_sell_order_success(chain_name, base_order, wallet_with_all_tokens):
    """Test successful creation of market sell order transaction."""

    # Select appropriate market based on chain
    symbol = "ETH/USD" if chain_name == "arbitrum" else "AVAX/USD"

    # Create market sell order
    result = base_order.create_market_sell_order(
        symbol=symbol,
        amount=50,  # $50 position size to close
        params={"slippage_percent": 0.005}  # 0.5% slippage
    )

    # Verify result structure
    assert isinstance(result, TransactionResult)
    assert result.order_type == OrderType.MARKET_DECREASE
    assert result.side == OrderSide.SELL
    assert result.amount == 50
    assert result.symbol == symbol
    assert result.estimated_execution_fee > 0


def test_create_limit_order_success(chain_name, base_order, wallet_with_all_tokens):
    """Test successful creation of limit order transactions."""

    # Select appropriate market based on chain
    symbol = "ETH/USD" if chain_name == "arbitrum" else "AVAX/USD"
    limit_price = 2000.0 if chain_name == "arbitrum" else 25.0

    # Create limit buy order
    buy_result = base_order.create_limit_buy_order(
        symbol=symbol,
        amount=200,  # $200 position size
        price=limit_price,
        params={"slippage_percent": 0.003}  # 0.3% slippage
    )

    # Verify buy order
    assert isinstance(buy_result, TransactionResult)
    assert buy_result.order_type == OrderType.LIMIT
    assert buy_result.side == OrderSide.BUY
    assert buy_result.amount == 200

    # Create limit sell order
    sell_result = base_order.create_limit_sell_order(
        symbol=symbol,
        amount=100,  # $100 position size to close
        price=limit_price * 1.1,  # 10% higher
        params={"slippage_percent": 0.003}
    )

    # Verify sell order
    assert isinstance(sell_result, TransactionResult)
    assert sell_result.order_type == OrderType.LIMIT
    assert sell_result.side == OrderSide.SELL
    assert sell_result.amount == 100


def test_order_execution_with_native_token(chain_name, base_order, wallet_with_all_tokens):
    """Test order creation when using native tokens as collateral."""

    # Use native token as collateral
    if chain_name == "arbitrum":
        symbol = "ETH/USD"
        # WETH as collateral - should adjust value amount for native token
    else:
        symbol = "AVAX/USD"
        # WAVAX as collateral

    # Create order with native token collateral
    result = base_order.create_market_buy_order(
        symbol=symbol,
        amount=50,  # $50 position
        params={
            "slippage_percent": 0.01,
            "collateral_address": NETWORK_TOKENS[chain_name]["WETH" if chain_name == "arbitrum" else "WAVAX"]
        }
    )

    # When using native tokens, value should include both amount and execution fee
    assert result.transaction["value"] > result.estimated_execution_fee
    assert isinstance(result, TransactionResult)


def test_order_execution_with_non_native_token(chain_name, base_order, wallet_with_all_tokens):
    """Test order creation when using non-native tokens as collateral."""

    symbol = "ETH/USD" if chain_name == "arbitrum" else "AVAX/USD"

    # Create order with USDC collateral (non-native)
    result = base_order.create_market_buy_order(
        symbol=symbol,
        amount=100,  # $100 position
        params={
            "slippage_percent": 0.01,
            "collateral_address": NETWORK_TOKENS[chain_name]["USDC"]
        }
    )

    # When using non-native tokens, value should only be execution fee
    assert result.transaction["value"] == result.estimated_execution_fee
    assert isinstance(result, TransactionResult)


def test_sign_and_execute_transaction(chain_name, base_order, test_wallet, wallet_with_all_tokens, web3_fork):
    """Test signing and executing the unsigned transaction returned by BaseOrder."""
    web3 = web3_fork
    wallet_address = test_wallet.address

    # Get initial balance
    initial_balance = web3.eth.get_balance(wallet_address)

    symbol = "ETH/USD" if chain_name == "arbitrum" else "AVAX/USD"

    # Find the market
    markets = base_order.markets.get_available_markets()
    market_data = None
    for addr, data in markets.items():
        if data.get("market_symbol", "") + "/USD" == symbol:
            market_data = data
            market_key = addr
            break

    if not market_data:
        pytest.skip(f"Market {symbol} not found")

    tokens = NETWORK_TOKENS[chain_name]

    # Approve USDC
    usdc_approval = base_order.check_if_approved(
        spender=base_order.contract_addresses.exchangerouter,
        token_to_approve=tokens["USDC"],
        amount_of_tokens_to_spend=50 * 10 ** 6,
        approve=True,
        wallet=test_wallet
    )

    if usdc_approval.get("needs_approval") and "approval_transaction" in usdc_approval:
        approval_hash = web3.eth.send_raw_transaction(usdc_approval["approval_transaction"].rawTransaction)
        web3.eth.wait_for_transaction_receipt(approval_hash, timeout=60)

    # Create order using OrderParams (migrated pattern)
    params = OrderParams(
        market_key=market_key,
        collateral_address=tokens["USDC"],
        index_token_address=market_data["index_token_address"],
        is_long=True,
        size_delta=10.0,  # $10 position size
        initial_collateral_delta_amount=str(10 * 10 ** 6),  # 10 USDC collateral (6 decimals)
        slippage_percent=0.02,
        swap_path=[],
    )

    # Create the order (is_open=True for opening position)
    result = base_order.create_order(params, is_open=True)

    # Sign and execute
    signed_txn = test_wallet.sign_transaction_with_new_nonce(result.transaction)
    tx_hash = web3.eth.send_raw_transaction(signed_txn.rawTransaction)
    tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

    # Verify
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert tx_receipt.status == 1


def test_fetch_markets(chain_name, base_order):
    """Test fetching available markets in CCXT format."""

    markets = base_order.fetch_markets()

    # Verify markets structure
    assert isinstance(markets, dict)
    assert len(markets) > 0

    # Check structure of market data
    for symbol, market_data in markets.items():
        assert isinstance(symbol, str)
        assert "/USD" in symbol  # All GMX markets are quoted in USD
        assert "id" in market_data
        assert "symbol" in market_data
        assert "base" in market_data
        assert "quote" in market_data
        assert "active" in market_data
        assert "type" in market_data
        assert "info" in market_data

        assert market_data["quote"] == "USD"
        assert market_data["active"] is True
        assert market_data["type"] == "perpetual"


def test_fetch_ticker(chain_name, base_order):
    """Test fetching ticker data for a specific symbol."""

    symbol = "ETH/USD" if chain_name == "arbitrum" else "AVAX/USD"

    ticker = base_order.fetch_ticker(symbol)

    # Verify ticker structure
    assert isinstance(ticker, dict)
    assert "symbol" in ticker
    assert "high" in ticker
    assert "low" in ticker
    assert "bid" in ticker
    assert "ask" in ticker
    assert "last" in ticker
    assert "info" in ticker

    assert ticker["symbol"] == symbol
    assert isinstance(ticker["high"], float)
    assert isinstance(ticker["low"], float)
    assert isinstance(ticker["bid"], float)
    assert isinstance(ticker["ask"], float)
    assert isinstance(ticker["last"], float)

    # Price should be reasonable
    assert ticker["high"] > 0
    assert ticker["low"] > 0
    assert ticker["bid"] > 0
    assert ticker["ask"] > 0
    assert ticker["last"] > 0


def test_fetch_ticker_invalid_symbol(chain_name,base_order):
    """Test fetching ticker for invalid symbol raises error."""

    with pytest.raises(ValueError, match="Market .* not found"):
        base_order.fetch_ticker("INVALID/USD")


def test_order_params_dataclass():
    """Test OrderParams dataclass functionality."""
    # Test with minimal required parameters
    params = OrderParams(
        symbol="ETH/USD",
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        amount=100
    )

    assert params.symbol == "ETH/USD"
    assert params.type == OrderType.MARKET
    assert params.side == OrderSide.BUY
    assert params.amount == 100
    assert params.slippage_percent == 0.005  # Default value
    assert params.is_long is True  # Default value
    assert params.auto_cancel is False  # Default value

    # Test with all parameters
    params_full = OrderParams(
        symbol="AVAX/USD",
        type=OrderType.LIMIT,
        side=OrderSide.SELL,
        amount=Decimal("200.5"),
        price=25.0,
        market_key="0x123...",
        collateral_address="0x456...",
        index_token_address="0x789...",
        is_long=False,
        slippage_percent=0.01,
        swap_path=["0xabc...", "0xdef..."],
        execution_fee_buffer=1.5,
        auto_cancel=True,
        min_output_amount=1000,
        client_order_id="test_order_123",
        metadata={"source": "test"}
    )

    assert params_full.price == 25.0
    assert params_full.is_long is False
    assert params_full.slippage_percent == 0.01
    assert params_full.execution_fee_buffer == 1.5
    assert params_full.auto_cancel is True
    assert params_full.client_order_id == "test_order_123"
    assert params_full.metadata["source"] == "test"


def test_transaction_result_dataclass():
    """Test TransactionResult dataclass functionality."""
    mock_transaction = {
        "to": "0x123...",
        "data": "0xabc...",
        "value": 1000,
        "gas": 2500000,
        "chainId": 42161
    }

    result = TransactionResult(
        transaction=mock_transaction,
        order_type=OrderType.MARKET_INCREASE,
        symbol="ETH/USD",
        side=OrderSide.BUY,
        amount=100,
        estimated_execution_fee=5000,
        market_info={"test": "data"},
        gas_estimates={"total": 2500000, "execution": 2300000},
        acceptable_price=2000,
        mark_price=1950.0,
        slippage_percent=0.005
    )

    assert result.transaction == mock_transaction
    assert result.order_type == OrderType.MARKET_INCREASE
    assert result.symbol == "ETH/USD"
    assert result.side == OrderSide.BUY
    assert result.amount == 100
    assert result.estimated_execution_fee == 5000
    assert result.acceptable_price == 2000
    assert result.mark_price == 1950.0
    assert result.slippage_percent == 0.005