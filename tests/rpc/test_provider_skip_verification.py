"""Test skipping startup chain ID verification for subprocess web3 factories.

The vault scanner fans out multicalls to many worker processes. Each worker
rebuilds its own Web3 via :py:class:`MultiProviderWeb3Factory`. If every worker
re-ran :py:meth:`FallbackProvider.verify_providers`, the ``eth_chainId`` probe
load on the primary provider would multiply and could itself trigger HTTP 429
rate limiting (the production "429 death loop"). ``skip_verification`` lets the
parent verify once and the workers skip re-verification while still inheriting
the verified chain ID for runtime switchover safety.
"""

from unittest.mock import MagicMock

import pytest

from eth_defi.provider import multi_provider as mp
from eth_defi.provider.fallback import FallbackProvider
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3

ARBITRUM_CHAIN_ID = 42161

#: Two distinct, never-contacted endpoints. With verification skipped and the
#: network touch-points patched out, no request is ever made to them.
FAKE_RPC_CONFIG = "https://fake-a.example/rpc https://fake-b.example/rpc"


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise the two network touch-points in create_multi_provider_web3.

    ``is_anvil()`` calls ``web3.client_version`` and
    ``install_chain_middleware()`` calls ``web3.eth.chain_id``; both would hit
    the network. We stub them so the test exercises only the verification
    branch without any real RPC traffic.
    """
    monkeypatch.setattr(mp, "is_anvil", lambda web3: False)
    monkeypatch.setattr(mp, "install_chain_middleware", lambda web3, hint=None: None)


def test_skip_verification_seeds_expected_chain_id(no_network, monkeypatch: pytest.MonkeyPatch):
    """skip_verification avoids the probe but still seeds expected_chain_id.

    1. Spy on FallbackProvider.verify_providers
    2. Build a Web3 with skip_verification=True and expected_chain_id set
    3. Assert verify_providers() was never called (no eth_chainId storm)
    4. Assert expected_chain_id was seeded so runtime switchover stays safe
    """
    # 1. Spy on the verification method
    verify_spy = MagicMock()
    monkeypatch.setattr(FallbackProvider, "verify_providers", verify_spy)

    # 2. Build with verification skipped, chain ID pre-seeded by the parent
    web3 = create_multi_provider_web3(
        FAKE_RPC_CONFIG,
        skip_verification=True,
        expected_chain_id=ARBITRUM_CHAIN_ID,
    )

    # 3. No startup verification probe was issued
    verify_spy.assert_not_called()

    # 4. Runtime switchover still has a chain-id baseline to reject wrong chains
    assert web3.get_fallback_provider().expected_chain_id == ARBITRUM_CHAIN_ID


def test_skip_verification_without_seed_is_rejected(no_network, monkeypatch: pytest.MonkeyPatch):
    """skip_verification without expected_chain_id must fail loudly.

    Skipping verification but leaving expected_chain_id unset would silently
    disable the runtime switchover chain-id safety guard, so the API rejects it.

    1. Spy on FallbackProvider.verify_providers (must not even be reached)
    2. Build a Web3 with skip_verification=True but no expected_chain_id
    3. Assert an AssertionError is raised
    4. Assert verification was not silently run as a fallback
    """
    # 1. Spy to prove we did not fall back to verifying
    verify_spy = MagicMock()
    monkeypatch.setattr(FallbackProvider, "verify_providers", verify_spy)

    # 2-3. Missing seed is a programming error, not a silent downgrade
    with pytest.raises(AssertionError, match="expected_chain_id"):
        create_multi_provider_web3(FAKE_RPC_CONFIG, skip_verification=True)

    # 4. We failed closed rather than verifying anyway
    verify_spy.assert_not_called()


def test_default_runs_verification(no_network, monkeypatch: pytest.MonkeyPatch):
    """Without skip_verification, verify_providers() is called as before.

    1. Spy on FallbackProvider.verify_providers
    2. Build a Web3 with default arguments
    3. Assert verify_providers() was called exactly once
    """
    # 1. Spy on the verification method
    verify_spy = MagicMock()
    monkeypatch.setattr(FallbackProvider, "verify_providers", verify_spy)

    # 2. Default construction
    create_multi_provider_web3(FAKE_RPC_CONFIG)

    # 3. Verification still happens on the normal (parent) path
    verify_spy.assert_called_once()


def test_factory_passes_skip_verification_through(no_network, monkeypatch: pytest.MonkeyPatch):
    """MultiProviderWeb3Factory forwards skip_verification and expected_chain_id.

    1. Spy on FallbackProvider.verify_providers
    2. Build a factory with skip_verification=True and expected_chain_id
    3. Invoke the factory as a subprocess worker would
    4. Assert no verification probe ran and the chain ID was seeded
    """
    # 1. Spy on the verification method
    verify_spy = MagicMock()
    monkeypatch.setattr(FallbackProvider, "verify_providers", verify_spy)

    # 2. Factory configured for subprocess fan-out
    factory = MultiProviderWeb3Factory(
        FAKE_RPC_CONFIG,
        skip_verification=True,
        expected_chain_id=ARBITRUM_CHAIN_ID,
    )
    assert factory.skip_verification is True
    assert factory.expected_chain_id == ARBITRUM_CHAIN_ID

    # 3. A worker process calls the factory to build its own Web3
    web3 = factory()

    # 4. Same guarantees as the direct call
    verify_spy.assert_not_called()
    assert web3.get_fallback_provider().expected_chain_id == ARBITRUM_CHAIN_ID
