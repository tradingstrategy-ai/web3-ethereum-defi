"""
Tests for GMX Oracle Price Data Module.

This test suite verifies the functionality of the OraclePrices class
when fetching oracle price data from GMX APIs.
"""

import pytest
import requests

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


def test_make_query_invalid_url():
    """Test _make_query behavior with invalid URL."""
    oracle = OraclePrices("arbitrum")

    # Temporarily modify URLs to invalid endpoint
    original_url = oracle.oracle_url
    original_backup_url = oracle.backup_oracle_url
    oracle.oracle_url = "https://invalid-api-endpoint.nonexistent/hehehe"
    oracle.backup_oracle_url = None  # Disable backup to ensure exception is raised

    try:
        with pytest.raises(requests.exceptions.RequestException):
            oracle._make_query(max_retries=2, initial_backoff=0.1)
    finally:
        # Restore original URLs
        oracle.oracle_url = original_url
        oracle.backup_oracle_url = original_backup_url
