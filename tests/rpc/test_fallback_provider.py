"""Test JSON-RPC provider fallback mechanism."""
from unittest.mock import patch, DEFAULT

import pytest
import requests
from web3 import HTTPProvider, Web3

from eth_defi.anvil import launch_anvil, AnvilLaunch
from eth_defi.provider.fallback import FallbackProvider


@pytest.fixture(scope="module")
def anvil() -> AnvilLaunch:
    """Launch Anvil for the test backend."""
    anvil = launch_anvil(port=20005)
    try:
        yield anvil
    finally:
        anvil.close()


@pytest.fixture()
def provider_1(anvil):
    provider = HTTPProvider(anvil.json_rpc_url)
    provider.middlewares.clear()
    return provider


@pytest.fixture()
def provider_2(anvil):
    provider = HTTPProvider(anvil.json_rpc_url)
    provider.middlewares.clear()
    return provider


@pytest.fixture()
def fallback_provider(provider_1, provider_2) -> FallbackProvider:
    provider = FallbackProvider([provider_1, provider_2], sleep=0.1, backoff=1)
    return provider


def test_fallback_no_issue(anvil: AnvilLaunch, fallback_provider: FallbackProvider):
    """Callback goes through the first provider"""
    web3 = Web3(fallback_provider)
    assert fallback_provider.api_call_counts[0]["eth_blockNumber"] == 0
    assert fallback_provider.api_call_counts[1]["eth_blockNumber"] == 0
    assert fallback_provider.currently_active_provider == 0
    assert fallback_provider.endpoint_uri == anvil.json_rpc_url
    web3.eth.block_number
    assert fallback_provider.api_call_counts[0]["eth_blockNumber"] == 1
    assert fallback_provider.api_call_counts[1]["eth_blockNumber"] == 0
    assert fallback_provider.currently_active_provider == 0


def test_fallback_single_fault(fallback_provider: FallbackProvider, provider_1):
    """Fallback goes through the second provider when first fails"""

    web3 = Web3(fallback_provider)

    with patch.object(provider_1, "make_request", side_effect=requests.exceptions.ConnectionError):
        web3.eth.block_number

    assert fallback_provider.api_call_counts[0]["eth_blockNumber"] == 0
    assert fallback_provider.api_call_counts[1]["eth_blockNumber"] == 1
    assert fallback_provider.currently_active_provider == 1


def test_fallback_double_fault(fallback_provider: FallbackProvider, provider_1, provider_2):
    """Fallback fails on both providers."""

    web3 = Web3(fallback_provider)

    with patch.object(provider_1, "make_request", side_effect=requests.exceptions.ConnectionError), patch.object(provider_2, "make_request", side_effect=requests.exceptions.ConnectionError):
        with pytest.raises(requests.exceptions.ConnectionError):
            web3.eth.block_number

    assert fallback_provider.retry_count == 5


def test_fallback_double_fault_recovery(fallback_provider: FallbackProvider, provider_1, provider_2):
    """Fallback fails on both providers, but then recover."""

    web3 = Web3(fallback_provider)

    count = 0

    def borg_start(*args, **kwargs):
        nonlocal count
        count += 1
        if count <= 2:
            raise requests.exceptions.ConnectionError()
        return DEFAULT

    with patch.object(provider_1, "make_request", side_effect=borg_start), patch.object(provider_2, "make_request", side_effect=borg_start):
        web3.eth.block_number

    assert fallback_provider.api_call_counts[0]["eth_blockNumber"] == 1
    assert fallback_provider.api_call_counts[1]["eth_blockNumber"] == 0
    assert fallback_provider.retry_count == 2
    assert fallback_provider.currently_active_provider == 0


def test_fallback_unhandled_exception(fallback_provider: FallbackProvider, provider_1):
    """Exception fallback provider cannot handle"""

    web3 = Web3(fallback_provider)

    with patch.object(provider_1, "make_request", side_effect=RuntimeError):
        with pytest.raises(RuntimeError):
            web3.eth.block_number
