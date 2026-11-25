"""Check the block numbers selected for RPC reads near the chain tip."""

from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock

import pytest
from requests.exceptions import HTTPError
from web3 import HTTPProvider, Web3
from web3.exceptions import Web3RPCError

from eth_defi.provider.anvil import invalidate_anvil_detection
from eth_defi.provider.broken_provider import get_almost_latest_block_number, set_block_tip_latency
from eth_defi.provider.fallback import ExtraValueError, FallbackProvider
from eth_defi.provider.mev_blocker import MEVBlockerProvider


@pytest.mark.parametrize("provider_type", [HTTPProvider, FallbackProvider, MEVBlockerProvider])
def test_arbitrum_block_tip_latency(provider_type: type) -> None:
    """Use a 20-block buffer on Arbitrum regardless of provider wrapper."""
    web3 = Mock(
        spec=Web3,
        provider=Mock(spec=provider_type),
        eth=SimpleNamespace(chain_id=42161, block_number=100),
        client_version="nitro/v3",
    )

    assert get_almost_latest_block_number(web3) == 80

    set_block_tip_latency(web3, 0)
    assert get_almost_latest_block_number(web3) == 100


def test_arbitrum_anvil_fork_reads_its_head() -> None:
    """Anvil follows its head and reconnecting refreshes the cached delay."""
    web3 = Mock(
        spec=Web3,
        provider=HTTPProvider("http://localhost:8545"),
        eth=SimpleNamespace(chain_id=42161, block_number=100),
        client_version="anvil/v1",
    )

    assert get_almost_latest_block_number(web3) == 100

    web3.client_version = "nitro/v3"
    invalidate_anvil_detection(web3)
    assert get_almost_latest_block_number(web3) == 80


@pytest.mark.parametrize("error_type", [HTTPError, Web3RPCError, ExtraValueError])
def test_arbitrum_block_tip_latency_without_client_version(error_type: type[Exception]) -> None:
    """Cache the Arbitrum default when the optional Anvil probe is unavailable."""
    eth = Mock(block_number=100)
    chain_id = PropertyMock(return_value=42161)
    type(eth).chain_id = chain_id
    web3 = Mock(
        spec=Web3,
        provider=HTTPProvider("https://rpc.example"),
        eth=eth,
    )
    client_version = PropertyMock(side_effect=error_type("Client version is unavailable"))
    type(web3).client_version = client_version

    assert get_almost_latest_block_number(web3) == 80
    assert get_almost_latest_block_number(web3) == 80
    chain_id.assert_called_once_with()
    client_version.assert_called_once_with()


@pytest.mark.parametrize(("provider_type", "expected_block"), [(HTTPProvider, 100), (FallbackProvider, 96)])
def test_other_chain_keeps_provider_delay(provider_type: type, expected_block: int) -> None:
    """Use provider defaults without an Anvil probe on other chains."""
    web3 = Mock(
        spec=Web3,
        provider=Mock(spec=provider_type, endpoint_uri="https://rpc.example"),
        eth=SimpleNamespace(chain_id=1, block_number=100),
    )
    type(web3).client_version = PropertyMock(side_effect=AssertionError("Unexpected client-version probe"))

    assert get_almost_latest_block_number(web3) == expected_block
