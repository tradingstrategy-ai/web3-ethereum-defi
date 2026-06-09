"""Tests for the HyperEVM goldsky eRPC consensus failover helpers.

These cover the workaround for goldsky's "not enough agreement among responses"
consensus failure on HyperEVM (chain 999), where we pin multicall retries to the
Alchemy single node instead of cycling back onto the consensus endpoint. See
``docs/README-hyperevm-goldsky-failure.md`` for the full failure analysis.

The helpers are pure (no real network): the tests use mock providers whose
``eth_chainId`` responses are stubbed, so the verified provider switch can run
without a live RPC.
"""

from unittest.mock import MagicMock

import pytest

from eth_defi.event_reader.multicall_batcher import (
    ERPC_CONSENSUS_DISAGREEMENT_CLUE,
    HYPEREVM_CHAIN_ID,
    pin_fallback_provider_by_host,
    resolve_hyperevm_consensus_failover,
)
from eth_defi.provider.fallback import FallbackProvider
from eth_defi.provider.named import get_provider_name


def _make_mock_provider(url: str, chain_id: int) -> MagicMock:
    """Create a mock provider returning a given chain ID for ``eth_chainId``."""
    provider = MagicMock()
    provider.endpoint_uri = url
    provider.middlewares = ()
    provider.exception_retry_configuration = None
    provider.make_request.return_value = {"jsonrpc": "2.0", "id": 1, "result": hex(chain_id)}
    return provider


def _goldsky_alchemy_mix(alchemy_chain_id: int = HYPEREVM_CHAIN_ID) -> FallbackProvider:
    """HyperEVM-style fallback mix: goldsky (index 0), Alchemy (1), dRPC (2)."""
    providers = [
        _make_mock_provider("https://edge.goldsky.com/standard/evm/999?secret=x", HYPEREVM_CHAIN_ID),
        _make_mock_provider("https://hyperliquid-mainnet.g.alchemy.com/v2/key", alchemy_chain_id),
        _make_mock_provider("https://lb.drpc.live/ogrpc?network=hyperliquid&dkey=x", HYPEREVM_CHAIN_ID),
    ]
    return FallbackProvider(providers, sleep=0, backoff=1)


@pytest.fixture()
def goldsky_alchemy_fallback() -> FallbackProvider:
    return _goldsky_alchemy_mix()


def test_hyperevm_consensus_failover_happy_path(goldsky_alchemy_fallback: FallbackProvider):
    """The goldsky consensus error on HyperEVM fails over to Alchemy.

    1. A consensus disagreement error on chain 999 with a goldsky+Alchemy mix is detected.
    2. The detection returns the ``"alchemy"`` host substring to pin to.
    3. Pinning (chain-id verified) selects the Alchemy provider as the active one.
    """
    exception = Exception("{'code': -32603, 'message': '" + ERPC_CONSENSUS_DISAGREEMENT_CLUE + "'}")

    # 1. + 2. Detected as the HyperEVM goldsky consensus failure, returns Alchemy host
    host = resolve_hyperevm_consensus_failover(HYPEREVM_CHAIN_ID, goldsky_alchemy_fallback, exception)
    assert host == "alchemy"

    # 3. Pinning makes Alchemy the active provider
    assert pin_fallback_provider_by_host(goldsky_alchemy_fallback, host) is True
    assert "alchemy" in get_provider_name(goldsky_alchemy_fallback.get_active_provider()).lower()


def test_hyperevm_consensus_failover_chain_id_rollback():
    """A mis-routing Alchemy endpoint is not silently selected; the pin rolls back.

    1. Build a mix where Alchemy starts mis-routing at runtime (reports chain ID 1,
       not 999), after the expected chain ID (999) was already captured.
    2. Attempt to pin to Alchemy.
    3. Pinning returns False (chain-id verification failed and rolled back).
    4. The active provider is still goldsky, not the bad Alchemy endpoint.
    """
    # 1. Alchemy mock returns chain ID 1 instead of 999. The expected chain ID was
    #    captured earlier (e.g. at startup) while Alchemy was still healthy.
    fallback = _goldsky_alchemy_mix(alchemy_chain_id=1)
    fallback.expected_chain_id = HYPEREVM_CHAIN_ID

    # 2. + 3. Pinning detects the mismatch, rolls back, and reports failure
    assert pin_fallback_provider_by_host(fallback, "alchemy") is False

    # 4. We did not silently switch to the wrong-chain Alchemy provider
    assert "goldsky" in get_provider_name(fallback.get_active_provider()).lower()


def test_hyperevm_consensus_failover_negatives(goldsky_alchemy_fallback: FallbackProvider):
    """The failover stays inert outside its exact triggering conditions.

    1. A different chain id is not eligible (chain 1).
    2. A non-consensus error is not eligible (a generic 429) — this is what lets a
       later Alchemy-specific failure fall back to normal provider switching.
    3. A non-FallbackProvider is not eligible.
    4. A mix without an Alchemy endpoint is not eligible (cannot fail over).
    """
    consensus_exc = Exception("{'message': '" + ERPC_CONSENSUS_DISAGREEMENT_CLUE + "'}")

    # 1. Wrong chain id
    assert resolve_hyperevm_consensus_failover(1, goldsky_alchemy_fallback, consensus_exc) is None

    # 2. Wrong error type (e.g. Alchemy throttling) — caller resumes normal switching
    assert resolve_hyperevm_consensus_failover(HYPEREVM_CHAIN_ID, goldsky_alchemy_fallback, Exception("HTTP 429 too many requests")) is None

    # 3. Not a FallbackProvider (single provider, nothing to fail over to)
    single = _make_mock_provider("https://edge.goldsky.com/x", HYPEREVM_CHAIN_ID)
    assert resolve_hyperevm_consensus_failover(HYPEREVM_CHAIN_ID, single, consensus_exc) is None

    # 4. Mix without Alchemy — goldsky + dRPC only
    no_alchemy = FallbackProvider(
        [
            _make_mock_provider("https://edge.goldsky.com/x", HYPEREVM_CHAIN_ID),
            _make_mock_provider("https://lb.drpc.live/x", HYPEREVM_CHAIN_ID),
        ],
        sleep=0,
        backoff=1,
    )
    assert resolve_hyperevm_consensus_failover(HYPEREVM_CHAIN_ID, no_alchemy, consensus_exc) is None


def test_pin_fallback_provider_no_match(goldsky_alchemy_fallback: FallbackProvider):
    """Pinning to a host not present in the mix reports failure and changes nothing.

    1. Attempt to pin to a host substring that does not exist in the mix.
    2. The call returns False so the caller can fall back to the normal switch.
    """
    # 1. + 2. No provider matches "infura", so pinning fails
    assert pin_fallback_provider_by_host(goldsky_alchemy_fallback, "infura") is False
