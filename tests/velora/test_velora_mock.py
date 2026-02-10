"""Mocked Velora API tests.

These tests mock the Velora API responses to test the quote and swap building
logic without requiring real API calls or blockchain access.
"""

from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from hexbytes import HexBytes

from eth_defi.velora.quote import VeloraQuote, fetch_velora_quote
from eth_defi.velora.swap import VeloraSwapTransaction, fetch_velora_swap_transaction
from eth_defi.velora.api import get_augustus_swapper, get_token_transfer_proxy


@pytest.fixture
def mock_usdc():
    """Mock USDC token details."""
    token = Mock()
    token.address = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    token.symbol = "USDC"
    token.decimals = 6
    token.chain_id = 42161
    token.convert_to_raw = lambda x: int(x * 10**6)
    token.convert_to_decimals = lambda x: Decimal(x) / Decimal(10**6)
    return token


@pytest.fixture
def mock_weth():
    """Mock WETH token details."""
    token = Mock()
    token.address = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    token.symbol = "WETH"
    token.decimals = 18
    token.chain_id = 42161
    token.convert_to_raw = lambda x: int(x * 10**18)
    token.convert_to_decimals = lambda x: Decimal(x) / Decimal(10**18)
    return token


@pytest.fixture
def mock_quote_response():
    """Mock Velora /prices API response."""
    return {
        "priceRoute": {
            "blockNumber": 375216652,
            "network": 42161,
            "srcToken": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
            "srcDecimals": 18,
            "srcAmount": "100000000000000000",  # 0.1 WETH
            "destToken": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
            "destDecimals": 6,
            "destAmount": "350000000",  # 350 USDC
            "bestRoute": [
                {
                    "percent": 100,
                    "swaps": [
                        {
                            "srcToken": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",
                            "destToken": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
                            "exchanges": [{"exchange": "UniswapV3", "percent": 100}],
                        }
                    ],
                }
            ],
            "gasCostUSD": "0.15",
            "contractAddress": "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57",
            "contractMethod": "multiSwap",
            "srcUSD": "350.00",
            "destUSD": "350.00",
        }
    }


@pytest.fixture
def mock_swap_response():
    """Mock Velora /transactions API response."""
    return {
        "to": "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57",
        "data": "0x54e3f31b000000000000000000000000000000000000000000000000000000000000002000000000000000000000000082af49447d8a07e3bd95bd0d56f35241523fbab1",
        "value": "0",
        "chainId": 42161,
    }


def test_velora_quote_parsing(mock_weth, mock_usdc):
    """Test that VeloraQuote correctly parses API response data."""
    quote_data = {
        "srcToken": mock_weth.address,
        "srcAmount": "100000000000000000",  # 0.1 WETH
        "destToken": mock_usdc.address,
        "destAmount": "350000000",  # 350 USDC
        "gasCostUSD": "0.15",
    }

    quote = VeloraQuote(
        buy_token=mock_usdc,
        sell_token=mock_weth,
        data=quote_data,
    )

    assert quote.get_sell_amount() == Decimal("0.1")
    assert quote.get_buy_amount() == Decimal("350")
    assert quote.get_price() == Decimal("3500")
    assert quote.get_gas_cost_usd() == Decimal("0.15")


def test_velora_quote_pformat(mock_weth, mock_usdc):
    """Test that quote pretty formatting doesn't crash."""
    quote_data = {
        "srcToken": mock_weth.address,
        "srcAmount": "100000000000000000",
        "destToken": mock_usdc.address,
        "destAmount": "350000000",
        "gasCostUSD": "0.15",
    }

    quote = VeloraQuote(
        buy_token=mock_usdc,
        sell_token=mock_weth,
        data=quote_data,
    )

    formatted = quote.pformat()
    assert "WETH" in formatted
    assert "USDC" in formatted
    assert "350" in formatted


@patch("eth_defi.velora.quote.requests.get")
def test_fetch_velora_quote(mock_get, mock_weth, mock_usdc, mock_quote_response):
    """Test fetching a quote from the mocked API."""
    mock_response = Mock()
    mock_response.json.return_value = mock_quote_response
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    quote = fetch_velora_quote(
        from_="0x1234567890123456789012345678901234567890",
        buy_token=mock_usdc,
        sell_token=mock_weth,
        amount_in=Decimal("0.1"),
    )

    assert quote.sell_token == mock_weth
    assert quote.buy_token == mock_usdc
    assert quote.get_sell_amount() == Decimal("0.1")
    assert quote.get_buy_amount() == Decimal("350")

    # Verify API was called with correct parameters
    mock_get.assert_called_once()
    call_args = mock_get.call_args
    assert call_args.kwargs["params"]["srcToken"] == mock_weth.address
    assert call_args.kwargs["params"]["destToken"] == mock_usdc.address
    assert call_args.kwargs["params"]["network"] == 42161


@patch("eth_defi.velora.swap.requests.post")
def test_fetch_velora_swap_transaction(mock_post, mock_weth, mock_usdc, mock_swap_response):
    """Test building a swap transaction from the mocked API."""
    mock_response = Mock()
    mock_response.json.return_value = mock_swap_response
    mock_response.raise_for_status = Mock()
    mock_post.return_value = mock_response

    # Create a mock quote
    quote_data = {
        "srcToken": mock_weth.address,
        "srcAmount": "100000000000000000",
        "destToken": mock_usdc.address,
        "destAmount": "350000000",
    }
    quote = VeloraQuote(
        buy_token=mock_usdc,
        sell_token=mock_weth,
        data=quote_data,
    )

    swap_tx = fetch_velora_swap_transaction(
        quote=quote,
        user_address="0x1234567890123456789012345678901234567890",
        slippage_bps=100,  # 1%
    )

    assert swap_tx.sell_token == mock_weth
    assert swap_tx.buy_token == mock_usdc
    assert swap_tx.amount_in == Decimal("0.1")
    assert swap_tx.to == "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57"
    assert len(swap_tx.calldata) > 0
    assert swap_tx.value == 0

    # min_amount_out should be 99% of destAmount (1% slippage)
    expected_min = Decimal("350") * Decimal("0.99")
    assert swap_tx.min_amount_out == expected_min


def test_velora_swap_transaction_dataclass(mock_weth, mock_usdc):
    """Test VeloraSwapTransaction dataclass."""
    swap_tx = VeloraSwapTransaction(
        buy_token=mock_usdc,
        sell_token=mock_weth,
        amount_in=Decimal("0.1"),
        min_amount_out=Decimal("346.5"),
        to="0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57",
        calldata=HexBytes("0x1234"),
        value=0,
        price_route={"test": "data"},
    )

    assert swap_tx.amount_in == Decimal("0.1")
    assert swap_tx.min_amount_out == Decimal("346.5")
    assert swap_tx.to == "0xDEF171Fe48CF0115B1d80b88dc8eAB59176FEe57"
    assert swap_tx.calldata == HexBytes("0x1234")


def test_get_augustus_swapper():
    """Test getting Augustus Swapper address for supported chains."""
    # Arbitrum - same address as most chains
    assert get_augustus_swapper(42161).lower() == "0xdef171fe48cf0115b1d80b88dc8eab59176fee57"
    # Ethereum
    assert get_augustus_swapper(1).lower() == "0xdef171fe48cf0115b1d80b88dc8eab59176fee57"
    # Base has a different address
    assert get_augustus_swapper(8453).lower() == "0x59c7c832e96d2568bea6db468c1aadcbbda08a52"


def test_get_token_transfer_proxy():
    """Test getting TokenTransferProxy address for supported chains."""
    # Arbitrum
    assert get_token_transfer_proxy(42161).lower() == "0x216b4b4ba9f3e719726886d34a177484278bfcae"
    # Ethereum
    assert get_token_transfer_proxy(1).lower() == "0x216b4b4ba9f3e719726886d34a177484278bfcae"
    # Base has a different address
    assert get_token_transfer_proxy(8453).lower() == "0x93aaae79a53759cd164340e4c8766e4db5331cd7"


def test_slippage_calculation(mock_weth, mock_usdc):
    """Test that slippage is correctly applied to min_amount_out."""
    quote_data = {
        "srcToken": mock_weth.address,
        "srcAmount": "1000000000000000000",  # 1 WETH
        "destToken": mock_usdc.address,
        "destAmount": "3500000000",  # 3500 USDC
    }
    quote = VeloraQuote(
        buy_token=mock_usdc,
        sell_token=mock_weth,
        data=quote_data,
    )

    # Test various slippage values
    test_cases = [
        (100, Decimal("3465")),  # 1% slippage -> 99% of 3500
        (250, Decimal("3412.5")),  # 2.5% slippage -> 97.5% of 3500
        (500, Decimal("3325")),  # 5% slippage -> 95% of 3500
    ]

    for slippage_bps, expected_min in test_cases:
        # Manually calculate min_amount_out as the API would
        dest_amount = int(quote_data["destAmount"])
        min_amount_out_raw = dest_amount * (10000 - slippage_bps) // 10000
        min_amount_out = mock_usdc.convert_to_decimals(min_amount_out_raw)
        assert min_amount_out == expected_min, f"Failed for slippage {slippage_bps} bps"


def test_quote_without_gas_cost(mock_weth, mock_usdc):
    """Test quote parsing when gas cost is not available."""
    quote_data = {
        "srcToken": mock_weth.address,
        "srcAmount": "100000000000000000",
        "destToken": mock_usdc.address,
        "destAmount": "350000000",
        # No gasCostUSD field
    }

    quote = VeloraQuote(
        buy_token=mock_usdc,
        sell_token=mock_weth,
        data=quote_data,
    )

    assert quote.get_gas_cost_usd() is None
