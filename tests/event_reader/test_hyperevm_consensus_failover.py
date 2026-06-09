"""Tests for the HyperEVM goldsky eRPC consensus failover helpers.

These cover the workaround for goldsky's "not enough agreement among responses"
consensus failure on HyperEVM (chain 999), where we pin multicall retries to the
Alchemy single node instead of cycling back onto the consensus endpoint. See
``docs/README-hyperevm-goldsky-failure.md`` for the full failure analysis.

The helpers are pure (no network), so the tests construct ``FallbackProvider``
instances from fake RPC URLs and assert detection + pinning behaviour.
"""

import pytest
from web3 import HTTPProvider

from eth_defi.compat import create_http_provider
from eth_defi.event_reader.multicall_batcher import (
    ERPC_CONSENSUS_DISAGREEMENT_CLUE,
    HYPEREVM_CHAIN_ID,
    pin_fallback_provider_by_host,
    resolve_hyperevm_consensus_failover,
)
from eth_defi.provider.fallback import FallbackProvider
from eth_defi.provider.named import get_provider_name


@pytest.fixture()
def goldsky_alchemy_fallback() -> FallbackProvider:
    """A HyperEVM-style fallback mix containing goldsky, Alchemy and dRPC."""
    providers = [
        create_http_provider("https://edge.goldsky.com/standard/evm/999?secret=x", exception_retry_configuration=None),
        create_http_provider("https://hyperliquid-mainnet.g.alchemy.com/v2/key", exception_retry_configuration=None),
        create_http_provider("https://lb.drpc.live/ogrpc?network=hyperliquid&dkey=x", exception_retry_configuration=None),
    ]
    return FallbackProvider(providers)


def test_hyperevm_consensus_failover_happy_path(goldsky_alchemy_fallback: FallbackProvider):
    """The goldsky consensus error on HyperEVM fails over to Alchemy.

    1. A consensus disagreement error on chain 999 with a goldsky+Alchemy mix is detected.
    2. The detection returns the ``"alchemy"`` host substring to pin to.
    3. Pinning selects the Alchemy provider as the active one.
    """
    exception = Exception("{'code': -32603, 'message': '" + ERPC_CONSENSUS_DISAGREEMENT_CLUE + "'}")

    # 1. + 2. Detected as the HyperEVM goldsky consensus failure, returns Alchemy host
    host = resolve_hyperevm_consensus_failover(HYPEREVM_CHAIN_ID, goldsky_alchemy_fallback, exception)
    assert host == "alchemy"

    # 3. Pinning makes Alchemy the active provider
    assert pin_fallback_provider_by_host(goldsky_alchemy_fallback, host) is True
    assert "alchemy" in get_provider_name(goldsky_alchemy_fallback.get_active_provider()).lower()


def test_hyperevm_consensus_failover_negatives(goldsky_alchemy_fallback: FallbackProvider):
    """The failover stays inert outside its exact triggering conditions.

    1. A different chain id is not eligible (chain 1).
    2. A non-consensus error is not eligible (a generic 429).
    3. A non-FallbackProvider is not eligible.
    4. A mix without an Alchemy endpoint is not eligible (cannot fail over).
    """
    consensus_exc = Exception("{'message': '" + ERPC_CONSENSUS_DISAGREEMENT_CLUE + "'}")

    # 1. Wrong chain id
    assert resolve_hyperevm_consensus_failover(1, goldsky_alchemy_fallback, consensus_exc) is None

    # 2. Wrong error type
    assert resolve_hyperevm_consensus_failover(HYPEREVM_CHAIN_ID, goldsky_alchemy_fallback, Exception("HTTP 429 too many requests")) is None

    # 3. Not a FallbackProvider (single HTTPProvider, nothing to fail over to)
    single = HTTPProvider("https://edge.goldsky.com/x")
    assert resolve_hyperevm_consensus_failover(HYPEREVM_CHAIN_ID, single, consensus_exc) is None

    # 4. Mix without Alchemy — goldsky + dRPC only
    no_alchemy = FallbackProvider(
        [
            create_http_provider("https://edge.goldsky.com/x", exception_retry_configuration=None),
            create_http_provider("https://lb.drpc.live/x", exception_retry_configuration=None),
        ]
    )
    assert resolve_hyperevm_consensus_failover(HYPEREVM_CHAIN_ID, no_alchemy, consensus_exc) is None


def test_pin_fallback_provider_no_match(goldsky_alchemy_fallback: FallbackProvider):
    """Pinning to a host not present in the mix reports failure and changes nothing.

    1. Attempt to pin to a host substring that does not exist in the mix.
    2. The call returns False so the caller can fall back to the normal switch.
    """
    # 1. + 2. No provider matches "infura", so pinning fails
    assert pin_fallback_provider_by_host(goldsky_alchemy_fallback, "infura") is False
