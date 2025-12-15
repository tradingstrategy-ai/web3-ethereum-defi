"""
Tests for GMX Subsquid GraphQL client.
"""

import pytest
from decimal import Decimal

from eth_defi.gmx.graphql.client import GMXSubsquidClient


def test_client_initialization():
    """Test that the client initializes correctly with different chains."""
    # Test Arbitrum
    client_arb = GMXSubsquidClient(chain="arbitrum")
    assert client_arb.chain == "arbitrum"
    assert "arbitrum" in client_arb.endpoint

    # Test Avalanche
    client_avax = GMXSubsquidClient(chain="avalanche")
    assert client_avax.chain == "avalanche"
    assert "avalanche" in client_avax.endpoint

    # Test Arbitrum Sepolia
    client_arb_sepolia = GMXSubsquidClient(chain="arbitrum_sepolia")
    assert client_arb_sepolia.chain == "arbitrum_sepolia"
    assert "arb-sepolia" in client_arb_sepolia.endpoint

    # Test custom endpoint
    custom_endpoint = "https://custom-endpoint.example/graphql"
    client_custom = GMXSubsquidClient(custom_endpoint=custom_endpoint)
    assert client_custom.endpoint == custom_endpoint


def test_client_initialization_invalid_chain():
    """Test that the client raises error for unsupported chains."""
    with pytest.raises(ValueError, match="Unsupported chain"):
        GMXSubsquidClient(chain="ethereum")


def test_get_positions(graphql_client, account_with_positions):
    """Test fetching positions for an account."""
    positions = graphql_client.get_positions(
        account=account_with_positions,
        only_open=False,
        limit=10,
    )

    # Should return a list (may be empty or have positions)
    assert isinstance(positions, list)

    # If there are positions, check structure
    if len(positions) > 0:
        position = positions[0]
        assert isinstance(position, dict)

        # Check required fields
        assert "id" in position
        assert "positionKey" in position
        assert "account" in position
        assert "market" in position
        assert "collateralToken" in position
        assert "isLong" in position
        assert "sizeInUsd" in position
        assert "collateralAmount" in position
        assert "entryPrice" in position
        assert "leverage" in position


def test_get_positions_only_open(graphql_client, account_with_positions):
    """Test fetching only open positions."""
    positions = graphql_client.get_positions(
        account=account_with_positions,
        only_open=True,
        limit=10,
    )

    assert isinstance(positions, list)

    # All positions should have sizeInUsd > 0
    for position in positions:
        assert int(position["sizeInUsd"]) > 0


def test_get_position_by_key(graphql_client):
    """Test fetching a specific position by key."""
    # Use a dummy position key (may not exist)
    position_key = "0x0000000000000000000000000000000000000000000000000000000000000000"
    position = graphql_client.get_position_by_key(position_key)

    # Should return None if not found, or a dict if found
    assert position is None or isinstance(position, dict)


def test_get_pnl_summary(graphql_client, account_with_positions):
    """Test fetching PnL summary for an account."""
    pnl_summary = graphql_client.get_pnl_summary(account=account_with_positions)

    # Should return a list
    assert isinstance(pnl_summary, list)

    # If there's data, check structure
    if len(pnl_summary) > 0:
        period = pnl_summary[0]
        assert isinstance(period, dict)

        # Check required fields
        assert "bucketLabel" in period
        assert "pnlUsd" in period
        assert "volume" in period
        assert "wins" in period
        assert "losses" in period

        # Bucket labels should be valid
        valid_labels = ["today", "yesterday", "week", "month", "year", "all"]
        assert period["bucketLabel"] in valid_labels


def test_get_position_changes(graphql_client, account_with_positions):
    """Test fetching position change history."""
    changes = graphql_client.get_position_changes(
        account=account_with_positions,
        limit=10,
    )

    # Should return a list
    assert isinstance(changes, list)

    # If there are changes, check structure
    if len(changes) > 0:
        change = changes[0]
        assert isinstance(change, dict)

        # Check required fields
        assert "id" in change
        assert "account" in change
        assert "market" in change
        assert "sizeInUsd" in change


def test_get_account_stats(graphql_client, account_with_positions):
    """Test fetching account statistics."""
    stats = graphql_client.get_account_stats(account=account_with_positions)

    # Should return a dict or None
    assert stats is None or isinstance(stats, dict)

    # If stats exist, check structure
    if stats:
        # Check required fields
        assert "id" in stats
        assert "volume" in stats
        assert "closedCount" in stats
        assert "wins" in stats
        assert "losses" in stats
        assert "realizedPnl" in stats
        assert "maxCapital" in stats
        assert "netCapital" in stats


def test_is_large_account(graphql_client, account_with_positions):
    """Test large account detection."""
    is_large = graphql_client.is_large_account(account=account_with_positions)

    # Should return a boolean
    assert isinstance(is_large, bool)


def test_from_fixed_point():
    """Test BigInt parsing with different decimal precisions."""
    # Test with 30 decimals (USD values)
    value_30 = "8625000000000000000000000000000"
    result_30 = GMXSubsquidClient.from_fixed_point(value_30, decimals=30)
    assert result_30 == Decimal("8.625")

    # Test with 18 decimals (entry price)
    value_18 = "3941148315941020859138"
    result_18 = GMXSubsquidClient.from_fixed_point(value_18, decimals=18)
    assert abs(result_18 - Decimal("3941.148315941020859138")) < Decimal("0.000001")

    # Test with 4 decimals (leverage)
    value_4 = "72480"
    result_4 = GMXSubsquidClient.from_fixed_point(value_4, decimals=4)
    assert result_4 == Decimal("7.248")

    # Test with 6 decimals (USDC)
    value_6 = "1000000"
    result_6 = GMXSubsquidClient.from_fixed_point(value_6, decimals=6)
    assert result_6 == Decimal("1")


def test_get_token_decimals(graphql_client):
    """Test token decimal detection from GMX API."""
    # USDC on Arbitrum (6 decimals)
    usdc_address = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
    assert graphql_client.get_token_decimals(usdc_address) == 6

    # WETH on Arbitrum (18 decimals)
    weth_address = "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
    assert graphql_client.get_token_decimals(weth_address) == 18

    # WBTC on Arbitrum (8 decimals)
    wbtc_address = "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"
    assert graphql_client.get_token_decimals(wbtc_address) == 8

    # Unknown token (defaults to 18)
    unknown_address = "0x0000000000000000000000000000000000000000"
    assert graphql_client.get_token_decimals(unknown_address) == 18


def test_format_position(graphql_client):
    """Test position formatting with correct decimal handling."""
    # Create a mock position with raw BigInt values
    raw_position = {
        "id": "test-position",
        "positionKey": "0x1234",
        "account": "0x5678",
        "market": "0xabcd",
        "collateralToken": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC (6 decimals)
        "isLong": True,
        "collateralAmount": "1000000",  # 1 USDC (6 decimals)
        "sizeInUsd": "10000000000000000000000000000000",  # 10 USD (30 decimals)
        "sizeInTokens": "1000000000000000000000000000000",  # 1 token (30 decimals)
        "entryPrice": "3000000000000000000000",  # 3000 USD (18 decimals)
        "realizedPnl": "500000000000000000000000000000",  # 0.5 USD (30 decimals)
        "unrealizedPnl": "-200000000000000000000000000000",  # -0.2 USD (30 decimals)
        "realizedFees": "100000000000000000000000000000",  # 0.1 USD (30 decimals)
        "unrealizedFees": "50000000000000000000000000000",  # 0.05 USD (30 decimals)
        "leverage": "25000",  # 2.5x (4 decimals: 10000 = 1x)
        "openedAt": 1234567890,
    }

    formatted = graphql_client.format_position(raw_position)

    # Check formatted values
    assert formatted["id"] == "test-position"
    assert formatted["is_long"] is True
    assert formatted["collateral_amount"] == 1.0  # 1 USDC
    assert formatted["size_usd"] == 10.0  # 10 USD
    assert formatted["size_tokens"] == 1.0  # 1 token
    assert formatted["entry_price"] == 3000.0  # 3000 USD
    assert formatted["realized_pnl"] == 0.5  # 0.5 USD
    assert formatted["unrealized_pnl"] == -0.2  # -0.2 USD
    assert formatted["realized_fees"] == 0.1  # 0.1 USD
    assert formatted["unrealized_fees"] == 0.05  # 0.05 USD
    assert formatted["leverage"] == 2.5  # 2.5x
    assert formatted["opened_at"] == 1234567890


def test_case_sensitive_addresses(graphql_client):
    """Test that the client handles checksummed addresses correctly.

    The Subsquid GraphQL endpoint is case-sensitive for addresses.
    """
    # Test with checksummed address
    checksummed = "0x1640e916e10610Ba39aAC5Cd8a08acF3cCae1A4c"
    positions_checksummed = graphql_client.get_positions(account=checksummed, only_open=False, limit=1)

    # Test with lowercase address (may return different results)
    lowercase = checksummed.lower()
    positions_lowercase = graphql_client.get_positions(account=lowercase, only_open=False, limit=1)

    # Both should return lists (but may have different content)
    assert isinstance(positions_checksummed, list)
    assert isinstance(positions_lowercase, list)


def test_format_position_btc_real_data(graphql_client):
    """Test position formatting with real BTC/USD position data from GraphQL.

    This tests the decimal decoding with actual data from GMX Subsquid,
    ensuring proper handling of token-specific decimal places.
    """
    # Real BTC/USD position data from GMX GraphQL
    # Market: BTC/USD (0x47c031236e19d024b42f8AE6780E44A573170703 on Arbitrum)
    # Index token: BTC (8 decimals)
    # Collateral token: USDC (6 decimals)
    raw_position = {
        "id": "0x9a9fc3e047a4b8ca7f3fe2cc2d4812e019ba08b41cc135fa36ae271edcac45e8",
        "positionKey": "0x9a9fc3e047a4b8ca7f3fe2cc2d4812e019ba08b41cc135fa36ae271edcac45e8",
        "account": "0xB065f2BE6A488735148C06109c5e0b12E832f3D4",
        "market": "0x47c031236e19d024b42f8AE6780E44A573170703",
        "collateralToken": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",  # USDC
        "isLong": True,
        "maxSize": "10515274652799372837048675205120",
        "collateralAmount": "10512973",  # 10.512973 USDC (6 decimals)
        "entryPrice": "904695401600221357399008449",  # ~$90,469.54 (30-index_decimals = 22 decimals for BTC)
        "leverage": "10003",  # ~1.0003x (4 decimals)
        "sizeInTokens": "11623",  # 0.00011623 BTC (30 decimals)
        "sizeInUsd": "10515274652799372837048675205120",  # $10.52 (30 decimals)
        "realizedFees": "3785466987990000000000000000",  # ~$0.00379 (30 decimals)
        "realizedPnl": "0",
        "unrealizedPnl": "0",
        "unrealizedFees": "0",
        "realizedPriceImpact": "0",
        "unrealizedPriceImpact": "0",
        "openedAt": 1765617888,
    }

    formatted = graphql_client.format_position(raw_position)

    # Expected values from GMX UI:
    # - Size: $10.52
    # - Net value: $10.51
    # - Collateral: $10.51 (USDC)
    # - Entry price: $90,469.54
    # - Leverage: ~1.0003x
    # - PnL: -0.03%

    # Test collateral amount (USDC has 6 decimals)
    assert abs(formatted["collateral_amount"] - 10.512973) < 0.000001

    # Test size in USD (30 decimals)
    assert abs(formatted["size_usd"] - 10.52) < 0.01

    # Test entry price (BTC has 8 decimals, so entryPrice uses 30-8=22 decimals)
    # 904695401600221357399008449 / 10^22 = 90469.54...
    assert abs(formatted["entry_price"] - 90469.54) < 1.0

    # Test leverage (4 decimals: 10000 = 1x)
    assert abs(formatted["leverage"] - 1.0003) < 0.0001

    # Test size in tokens (30 decimals)
    assert abs(formatted["size_tokens"] - 0.00011623) < 0.00000001
