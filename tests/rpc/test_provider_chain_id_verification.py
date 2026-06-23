"""Test startup chain ID verification for fallback providers.

Ensures that misconfigured RPC endpoints (e.g. an Ethereum mainnet
endpoint mixed into an Arbitrum provider list) are caught at startup
rather than at runtime when a provider switch occurs.
"""

import re
from unittest.mock import MagicMock

import pytest

from eth_defi.provider.fallback import ChainIdMismatch, FallbackProvider, ProviderNotAvailable

ARBITRUM_CHAIN_ID = 42161
ETHEREUM_CHAIN_ID = 1
PROVIDER_C_INDEX = 2


def _make_mock_provider(name: str, chain_id: int) -> MagicMock:
    """Create a mock provider that returns a given chain ID for eth_chainId."""
    provider = MagicMock()
    provider.endpoint_uri = f"https://{name}/rpc"
    provider.middlewares = ()
    provider.exception_retry_configuration = None
    provider.make_request.return_value = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": hex(chain_id),
    }
    return provider


def test_verify_providers_same_chain():
    """All providers on the same chain should pass verification.

    1. Create two mock providers both returning Arbitrum chain ID
    2. Call verify_providers()
    3. Assert no exception is raised and expected_chain_id is set
    """
    # 1. Create mock providers on the same chain
    p1 = _make_mock_provider("provider-a.com", ARBITRUM_CHAIN_ID)
    p2 = _make_mock_provider("provider-b.com", ARBITRUM_CHAIN_ID)

    fallback = FallbackProvider([p1, p2], sleep=0, backoff=1)

    # 2. Verify - should not raise
    fallback.verify_providers()

    # 3. expected_chain_id should be populated
    assert fallback.expected_chain_id == ARBITRUM_CHAIN_ID


def test_verify_providers_chain_mismatch():
    """A provider returning a different chain ID should fail verification.

    1. Create one provider returning Arbitrum chain ID
    2. Create another returning Ethereum mainnet chain ID
    3. Call verify_providers()
    4. Assert ChainIdMismatch is raised with both chain IDs in the message
    """
    # 1-2. Create providers on different chains
    p1 = _make_mock_provider("arbitrum-rpc.com", ARBITRUM_CHAIN_ID)
    p2 = _make_mock_provider("mainnet-rpc.com", ETHEREUM_CHAIN_ID)

    fallback = FallbackProvider([p1, p2], sleep=0, backoff=1)

    # 3-4. Should raise with informative message
    with pytest.raises(ChainIdMismatch, match="different chains"):
        fallback.verify_providers()


def test_verify_providers_unreachable():
    """A provider that fails to respond should not fail startup if others work.

    1. Create one working provider and one that raises on eth_chainId
    2. Call verify_providers()
    3. Assert the working provider populates expected_chain_id
    """
    # 1. Create one working and one broken provider
    p1 = _make_mock_provider("good-rpc.com", ARBITRUM_CHAIN_ID)
    p2 = MagicMock()
    p2.endpoint_uri = "https://broken-rpc.com/rpc"
    p2.middlewares = ()
    p2.exception_retry_configuration = None
    p2.make_request.side_effect = ConnectionError("Connection refused")

    fallback = FallbackProvider([p1, p2], sleep=0, backoff=1)

    # 2. Should not raise, because at least one provider proves the chain id
    fallback.verify_providers()

    # 3. Runtime switchover verifies the broken provider before selecting it
    assert fallback.expected_chain_id == ARBITRUM_CHAIN_ID


def test_verify_providers_all_unreachable():
    """Startup verification fails if no provider can prove the chain ID.

    1. Create two providers that fail on eth_chainId
    2. Call verify_providers()
    3. Assert ChainIdMismatch is raised because no expected chain ID can be set
    """
    # 1. All providers are broken
    p1 = MagicMock()
    p1.endpoint_uri = "https://broken-rpc-1.com/rpc"
    p1.middlewares = ()
    p1.exception_retry_configuration = None
    p1.make_request.side_effect = ConnectionError("Connection refused")

    p2 = MagicMock()
    p2.endpoint_uri = "https://broken-rpc-2.com/rpc"
    p2.middlewares = ()
    p2.exception_retry_configuration = None
    p2.make_request.side_effect = ConnectionError("Connection refused")

    fallback = FallbackProvider([p1, p2], sleep=0, backoff=1)

    # 2-3. There is no safe chain ID baseline
    with pytest.raises(ChainIdMismatch, match="No RPC providers responded"):
        fallback.verify_providers()


def test_switch_provider_skips_unavailable_provider():
    """Runtime switchover skips providers that cannot answer eth_chainId.

    1. Create active provider A, unavailable provider B, and working provider C
    2. Seed expected_chain_id as startup verification would do
    3. Switch provider
    4. Assert provider C is selected and provider B was not made active
    """
    # 1. Provider B is between the active provider and the next usable provider
    p1 = _make_mock_provider("provider-a.com", ARBITRUM_CHAIN_ID)
    p2 = MagicMock()
    p2.endpoint_uri = "https://broken-rpc.com/rpc"
    p2.middlewares = ()
    p2.exception_retry_configuration = None
    p2.make_request.side_effect = ConnectionError("Connection refused")
    p3 = _make_mock_provider("provider-c.com", ARBITRUM_CHAIN_ID)

    fallback = FallbackProvider([p1, p2, p3], sleep=0, backoff=1)
    fallback.expected_chain_id = ARBITRUM_CHAIN_ID

    # 2-3. Switchover tries B, skips it, and selects C
    fallback.switch_provider()

    # 4. Broken provider was probed but not selected
    assert fallback.currently_active_provider == PROVIDER_C_INDEX
    p2.make_request.assert_called_once_with("eth_chainId", [])


def test_switch_to_provider_index_unavailable_rolls_back():
    """Direct provider pinning rejects an unavailable provider.

    1. Create an active provider and a broken provider
    2. Try to pin directly to the broken provider
    3. Assert ProviderNotAvailable is raised and the active provider is unchanged
    """
    # 1. Broken provider cannot be chain-id verified
    p1 = _make_mock_provider("provider-a.com", ARBITRUM_CHAIN_ID)
    p2 = MagicMock()
    p2.endpoint_uri = "https://broken-rpc.com/rpc"
    p2.middlewares = ()
    p2.exception_retry_configuration = None
    p2.make_request.side_effect = ConnectionError("Connection refused")

    fallback = FallbackProvider([p1, p2], sleep=0, backoff=1)
    fallback.expected_chain_id = ARBITRUM_CHAIN_ID

    # 2-3. Direct pinning should fail closed
    with pytest.raises(ProviderNotAvailable, match=re.escape("broken-rpc.com")):
        fallback.switch_to_provider_index(1)

    assert fallback.currently_active_provider == 0


def test_verify_providers_single_provider():
    """A single provider should skip verification without error.

    1. Create a single mock provider
    2. Call verify_providers()
    3. Assert no exception and no RPC call is made
    """
    # 1. Single provider
    p1 = _make_mock_provider("solo-rpc.com", ARBITRUM_CHAIN_ID)

    fallback = FallbackProvider([p1], sleep=0, backoff=1)

    # 2. Should return immediately
    fallback.verify_providers()

    # 3. No eth_chainId call should have been made
    p1.make_request.assert_not_called()
