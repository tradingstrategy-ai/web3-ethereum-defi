"""Test startup chain ID verification for fallback providers.

Ensures that misconfigured RPC endpoints (e.g. an Ethereum mainnet
endpoint mixed into an Arbitrum provider list) are caught at startup
rather than at runtime when a provider switch occurs.
"""

from unittest.mock import MagicMock

import pytest

from eth_defi.provider.fallback import ChainIdMismatch, FallbackProvider


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

    1. Create two mock providers both returning chain ID 42161 (Arbitrum)
    2. Call verify_providers()
    3. Assert no exception is raised and expected_chain_id is set
    """
    # 1. Create mock providers on the same chain
    p1 = _make_mock_provider("provider-a.com", 42161)
    p2 = _make_mock_provider("provider-b.com", 42161)

    fallback = FallbackProvider([p1, p2], sleep=0, backoff=1)

    # 2. Verify — should not raise
    fallback.verify_providers()

    # 3. expected_chain_id should be populated
    assert fallback.expected_chain_id == 42161


def test_verify_providers_chain_mismatch():
    """A provider returning a different chain ID should fail verification.

    1. Create one provider returning chain ID 42161 (Arbitrum)
    2. Create another returning chain ID 1 (Ethereum mainnet)
    3. Call verify_providers()
    4. Assert ChainIdMismatch is raised with both chain IDs in the message
    """
    # 1-2. Create providers on different chains
    p1 = _make_mock_provider("arbitrum-rpc.com", 42161)
    p2 = _make_mock_provider("mainnet-rpc.com", 1)

    fallback = FallbackProvider([p1, p2], sleep=0, backoff=1)

    # 3-4. Should raise with informative message
    with pytest.raises(ChainIdMismatch, match="different chains"):
        fallback.verify_providers()


def test_verify_providers_unreachable():
    """A provider that fails to respond should raise at startup.

    1. Create one working provider and one that raises on eth_chainId
    2. Call verify_providers()
    3. Assert ChainIdMismatch is raised mentioning the failing provider
    """
    # 1. Create one working and one broken provider
    p1 = _make_mock_provider("good-rpc.com", 42161)
    p2 = MagicMock()
    p2.endpoint_uri = "https://broken-rpc.com/rpc"
    p2.middlewares = ()
    p2.exception_retry_configuration = None
    p2.make_request.side_effect = ConnectionError("Connection refused")

    fallback = FallbackProvider([p1, p2], sleep=0, backoff=1)

    # 2-3. Should raise mentioning the broken provider
    with pytest.raises(ChainIdMismatch, match="broken-rpc.com"):
        fallback.verify_providers()


def test_verify_providers_single_provider():
    """A single provider should skip verification without error.

    1. Create a single mock provider
    2. Call verify_providers()
    3. Assert no exception and no RPC call is made
    """
    # 1. Single provider
    p1 = _make_mock_provider("solo-rpc.com", 42161)

    fallback = FallbackProvider([p1], sleep=0, backoff=1)

    # 2. Should return immediately
    fallback.verify_providers()

    # 3. No eth_chainId call should have been made
    p1.make_request.assert_not_called()
