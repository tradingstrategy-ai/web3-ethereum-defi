"""Test GMX integration with MultiProviderWeb3."""

import os

import pytest

from eth_defi.gmx.config import (
    GMXConfig,
    create_gmx_config_with_fallback,
    get_fallback_provider_from_gmx_config,
)
from eth_defi.provider.multi_provider import MultiProviderWeb3
from eth_defi.provider.fallback import FallbackProvider


# Use the Alchemy RPC URL provided, combined with public endpoint for fallback
ALCHEMY_RPC = "https://arb-mainnet.g.alchemy.com/v2/sQkYwBCUdfNfC0AabiD7E"
PUBLIC_RPC = "https://arb1.arbitrum.io/rpc"


def test_create_gmx_config_with_fallback_single_provider():
    """Test the factory function creates proper config with single provider."""
    config = create_gmx_config_with_fallback(
        rpc_configuration=ALCHEMY_RPC,
        user_wallet_address="0x1234567890123456789012345678901234567890",
    )

    assert isinstance(config, GMXConfig)
    assert isinstance(config.web3, MultiProviderWeb3)
    assert config.get_chain() == "arbitrum"
    assert config.has_write_capability()


def test_create_gmx_config_with_fallback_multiple_providers():
    """Test the factory function with multiple providers."""
    rpc_config = f"{ALCHEMY_RPC} {PUBLIC_RPC}"

    config = create_gmx_config_with_fallback(
        rpc_configuration=rpc_config,
        user_wallet_address="0x1234567890123456789012345678901234567890",
        require_multiple_providers=True,
    )

    assert isinstance(config, GMXConfig)
    assert isinstance(config.web3, MultiProviderWeb3)

    fallback = get_fallback_provider_from_gmx_config(config)
    assert fallback is not None
    assert len(fallback.providers) == 2


def test_get_fallback_provider_from_gmx_config():
    """Test extracting FallbackProvider from config."""
    config = create_gmx_config_with_fallback(ALCHEMY_RPC)

    fallback = get_fallback_provider_from_gmx_config(config)

    assert isinstance(fallback, FallbackProvider)
    assert len(fallback.providers) >= 1


def test_create_gmx_config_require_multiple_raises():
    """Test that require_multiple_providers raises with single provider."""
    with pytest.raises(ValueError, match="at least 2 providers"):
        create_gmx_config_with_fallback(
            rpc_configuration=ALCHEMY_RPC,
            require_multiple_providers=True,
        )


def test_gmx_config_can_fetch_block_number():
    """Test that the config can actually make RPC calls."""
    config = create_gmx_config_with_fallback(ALCHEMY_RPC)

    block_number = config.web3.eth.block_number
    assert block_number > 0


def test_ccxt_gmx_with_require_multiple_providers():
    """Test CCXT GMX exchange with requireMultipleProviders parameter."""
    from eth_defi.gmx.ccxt.exchange import GMX

    rpc_config = f"{ALCHEMY_RPC} {PUBLIC_RPC}"

    gmx = GMX(
        params={
            "rpcUrl": rpc_config,
            "requireMultipleProviders": True,
        }
    )

    assert isinstance(gmx.web3, MultiProviderWeb3)
    fallback = gmx.web3.get_fallback_provider()
    assert len(fallback.providers) == 2

    # Verify we can make actual API calls
    block_number = gmx.web3.eth.block_number
    assert block_number > 0


def test_ccxt_gmx_require_multiple_providers_raises():
    """Test CCXT GMX exchange raises with single provider when required."""
    from eth_defi.gmx.ccxt.exchange import GMX

    with pytest.raises(ValueError, match="at least 2 providers"):
        GMX(
            params={
                "rpcUrl": ALCHEMY_RPC,
                "requireMultipleProviders": True,
            }
        )
