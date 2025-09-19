"""
Tests for GMX Oracle Price Data Module.

This test suite verifies the functionality of the OraclePrices class
when fetching oracle price data from GMX APIs.
"""

import logging
import pytest
import requests
import os

# Suppress all logging before imports to prevent startup noise
# TODO: Bcz of conftest deps of gmx-python-sdk-ng we are still getting loggings
os.environ["PYTEST_RUNNING"] = "1"
# logging.disable(logging.CRITICAL)

from eth_defi.gmx.core.oracle import OraclePrices


def test_oracle_initialization(chain_name):
    """Test OraclePrices initialization with supported chains."""
    oracle = OraclePrices(chain_name)
    assert oracle.chain == chain_name
    assert "/signed_prices/latest" in oracle.oracle_url

    if chain_name == "arbitrum":
        assert "arbitrum-api.gmxinfra.io" in oracle.oracle_url
    elif chain_name == "avalanche":
        assert "avalanche-api.gmxinfra.io" in oracle.oracle_url


@pytest.mark.parametrize("invalid_chain", ["ethereum", "polygon", "bsc", ""])
def test_invalid_chain_initialization(invalid_chain):
    """Test initialization with unsupported chains."""
    with pytest.raises(ValueError, match=f"Unsupported chain: {invalid_chain}"):
        OraclePrices(invalid_chain)


def test_backup_url_setup(chain_name):
    """Test that backup URLs are correctly configured."""
    oracle = OraclePrices(chain_name)

    # All supported chains should have backup URLs
    assert oracle.backup_oracle_url is not None
    assert "/signed_prices/latest" in oracle.backup_oracle_url

    # Check specific backup URLs
    if chain_name == "arbitrum":
        assert "arbitrum-api.gmxinfra2.io" in oracle.backup_oracle_url
    elif chain_name == "avalanche":
        assert "avalanche-api.gmxinfra2.io" in oracle.backup_oracle_url


def test_get_recent_prices_success(chain_name):
    """Test successful price data retrieval from real API."""
    oracle = OraclePrices(chain_name)

    try:
        prices = oracle.get_recent_prices()

        # Verify basic response structure
        assert isinstance(prices, dict)
        assert len(prices) > 0

        # Check structure of first price entry
        first_token_address = next(iter(prices.keys()))
        first_price_data = prices[first_token_address]

        # Verify required fields exist in response structure
        required_fields = ["tokenAddress", "minPrice", "maxPrice"]
        for field in required_fields:
            assert field in first_price_data, f"Missing field: {field}"

        # Check that we have some time-related field (could be timestamp or createdAt)
        time_fields = ["timestamp", "createdAt", "minBlockTimestamp"]
        assert any(field in first_price_data for field in time_fields), "Missing time-related field"

        # Verify tokenAddress matches the key
        assert first_price_data["tokenAddress"] == first_token_address

        # Find a token with valid price data (some may have None values)
        valid_token_data = None
        for token_addr, token_data in prices.items():
            if token_data.get("minPrice") is not None and token_data.get("maxPrice") is not None and isinstance(token_data.get("minPrice"), str) and isinstance(token_data.get("maxPrice"), str):
                valid_token_data = token_data
                break

        # If we found valid price data, verify it
        if valid_token_data:
            assert valid_token_data["minPrice"].isdigit()
            assert valid_token_data["maxPrice"].isdigit()
            # Verify prices are reasonable
            min_price = int(valid_token_data["minPrice"])
            max_price = int(valid_token_data["maxPrice"])
            assert min_price > 0
            assert max_price > 0
            assert max_price >= min_price

    except requests.exceptions.RequestException as e:
        pytest.skip(f"API request failed: {e}")


def test_process_output_structure(chain_name):
    """Test that _process_output methods is correctly structures API response."""
    oracle = OraclePrices(chain_name)

    # Sample API response structure
    sample_response = {"signedPrices": [{"tokenAddress": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "minPrice": "2500000000000000000000", "maxPrice": "2550000000000000000000", "createdAt": "2025-08-24T18:21:38.680Z"}, {"tokenAddress": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", "minPrice": "45000000000000000000000", "maxPrice": "45500000000000000000000", "createdAt": "2025-08-24T18:21:38.680Z"}]}

    processed = oracle._process_output(sample_response)

    # Should convert list to dict with tokenAddress as keys
    assert isinstance(processed, dict)
    assert len(processed) == 2

    # Verify token addresses are keys
    expected_addresses = ["0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f"]

    for addr in expected_addresses:
        assert addr in processed
        assert processed[addr]["tokenAddress"] == addr


def test_process_output_empty_response():
    """Test _process_output with empty signedPrices array."""
    oracle = OraclePrices("arbitrum")
    empty_response = {"signedPrices": []}

    processed = oracle._process_output(empty_response)

    assert isinstance(processed, dict)
    assert len(processed) == 0


def test_make_query_successful_request(chain_name):
    """Test _make_query makes successful requests to real API."""
    oracle = OraclePrices(chain_name)

    try:
        response = oracle._make_query()

        # Verify response object
        assert hasattr(response, "json")
        assert hasattr(response, "status_code")
        assert response.status_code == 200

        # Verify response contains expected data structure
        data = response.json()
        assert "signedPrices" in data
        assert isinstance(data["signedPrices"], list)

    except requests.exceptions.RequestException as e:
        pytest.skip(f"API request failed: {e}")


@pytest.mark.parametrize("max_retries", [1, 3, 5])
def test_make_query_retry_parameters(max_retries):
    """Test _make_query with different retry parameters."""
    oracle = OraclePrices("arbitrum")

    try:
        response = oracle._make_query(max_retries=max_retries, initial_backoff=0.1, max_backoff=1)
        assert response.status_code == 200

    except requests.exceptions.RequestException as e:
        pytest.skip(f"API request failed: {e}")


def test_make_query_invalid_url():
    """Test _make_query behavior with invalid URL."""
    oracle = OraclePrices("arbitrum")

    # Temporarily modify URL to invalid endpoint
    original_url = oracle.oracle_url
    oracle.oracle_url = "https://invalid-api-endpoint.nonexistent/hehehe"

    try:
        with pytest.raises(requests.exceptions.RequestException):
            oracle._make_query(max_retries=2, initial_backoff=0.1)
    finally:
        # Restore original URL
        oracle.oracle_url = original_url


def test_full_integration_flow(chain_name):
    """Test complete flow from initialization to price retrieval."""
    oracle = OraclePrices(chain_name)

    try:
        # Test full flow
        prices = oracle.get_recent_prices()

        # Verify we got meaningful data
        assert isinstance(prices, dict)
        assert len(prices) > 0

        # Verify each price entry has correct structure
        valid_entries_count = 0
        for token_address, price_data in prices.items():
            # Check token address format (should be checksummed)
            assert token_address.startswith("0x")
            assert len(token_address) == 42

            # Verify price data structure
            assert isinstance(price_data, dict)
            assert "tokenAddress" in price_data
            assert "minPrice" in price_data
            assert "maxPrice" in price_data

            # Verify consistency
            assert price_data["tokenAddress"] == token_address

            # Verify we have time information (skip timestamp validation as format varies)
            time_fields = ["timestamp", "createdAt", "minBlockTimestamp"]
            has_time_field = any(field in price_data for field in time_fields)
            assert has_time_field, "Price data should have time information"

            # Only validate price values if they're not None (some tokens may not have active prices)
            if price_data.get("minPrice") is not None and price_data.get("maxPrice") is not None:
                try:
                    min_price = int(price_data["minPrice"])
                    max_price = int(price_data["maxPrice"])
                    assert min_price > 0
                    assert max_price > 0
                    assert max_price >= min_price
                    valid_entries_count += 1
                except (ValueError, TypeError):
                    # Skip entries with invalid price data
                    continue

            # Stop after checking a reasonable sample
            if valid_entries_count >= 5:
                break

        # Test that we have some known tokens for the chain
        token_addresses = list(prices.keys())
        if chain_name == "arbitrum":
            # Should have ETH on Arbitrum (address: 0x82aF49447D8a07e3bd95BD0d56f35241523fBab1)
            eth_found = any("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1" in addr for addr in token_addresses)
            assert eth_found, f"Expected to find ETH token in Arbitrum prices. Available tokens: {[prices[addr].get('tokenSymbol', '??') for addr in token_addresses[:10]]}"
        elif chain_name == "avalanche":
            # Should have AVAX on Avalanche
            avax_found = any("B31f66AA3C1e785363F0875A1B74E27b85FD66c7" in addr.upper() for addr in token_addresses)
            if not avax_found:
                # Try looking for any AVAX-like symbol
                avax_symbols = [data.get("tokenSymbol", "") for data in prices.values() if "AVAX" in data.get("tokenSymbol", "").upper()]
                assert len(avax_symbols) > 0, f"Expected to find AVAX-related token. Available symbols: {[prices[addr].get('tokenSymbol', '??') for addr in token_addresses[:10]]}"

    except requests.exceptions.RequestException as e:
        pytest.skip(f"API request failed: {e}")


def test_error_handling_malformed_response():
    """Test handling of malformed API responses."""
    oracle = OraclePrices("arbitrum")

    # Test missing signedPrices key
    with pytest.raises(KeyError):
        oracle._process_output({"data": []})

    # Test None response
    with pytest.raises((TypeError, AttributeError)):
        oracle._process_output(None)

    # Test response with wrong data type
    with pytest.raises((KeyError, TypeError)):
        oracle._process_output("invalid response")


def test_api_consistency_between_chains():
    """Test that both supported chains return consistent data structures."""
    arbitrum_oracle = OraclePrices("arbitrum")
    avalanche_oracle = OraclePrices("avalanche")

    try:
        arb_prices = arbitrum_oracle.get_recent_prices()
        avax_prices = avalanche_oracle.get_recent_prices()

        # Both should return dictionaries
        assert isinstance(arb_prices, dict)
        assert isinstance(avax_prices, dict)

        # Both should have data
        assert len(arb_prices) > 0
        assert len(avax_prices) > 0

        # Verify structure consistency
        if arb_prices and avax_prices:
            arb_sample = next(iter(arb_prices.values()))
            avax_sample = next(iter(avax_prices.values()))

            # Same keys should exist
            assert set(arb_sample.keys()) == set(avax_sample.keys())

            # Same data types
            for key in arb_sample.keys():
                assert type(arb_sample[key]) == type(avax_sample[key])

    except requests.exceptions.RequestException as e:
        pytest.skip(f"API request failed: {e}")
