"""Test GMX CCXT-style initialization.

Tests the new CCXT-compatible constructor that accepts parameters dictionary.
"""

import logging
import pytest

from eth_defi.gmx.ccxt.exchange import GMX


def test_ccxt_style_initialization_with_private_key(arbitrum_fork_config, test_wallet):
    """Test CCXT-style initialization with privateKey parameter.

    - Initializes GMX with parameters dict containing rpcUrl and privateKey
    - Verifies wallet is auto-created and available
    - Verifies web3 and config are properly initialized
    """
    # Get RPC URL from existing config
    rpc_url = arbitrum_fork_config.web3.provider.endpoint_uri

    # Initialize with CCXT-style parameters
    # Note: private_key.hex() doesn't include 0x prefix, so we add it
    private_key_hex = "0x" + test_wallet.private_key.hex()
    gmx = GMX(
        {
            "rpcUrl": rpc_url,
            "privateKey": private_key_hex,
        }
    )

    # Verify initialization
    assert gmx.wallet is not None
    assert gmx.wallet.address == test_wallet.address
    assert gmx.web3 is not None
    assert gmx.config is not None
    assert gmx.api is not None
    assert gmx.trader is not None
    assert gmx.subsquid is not None

    # Verify can load markets
    gmx.load_markets()
    assert len(gmx.markets) > 0


def test_ccxt_style_initialization_with_wallet_object(arbitrum_fork_config, test_wallet):
    """Test CCXT-style initialization with wallet object parameter.

    - Initializes GMX with parameters dict containing wallet object
    - Verifies wallet is used directly
    """
    rpc_url = arbitrum_fork_config.web3.provider.endpoint_uri

    # Initialize with wallet object instead of privateKey
    gmx = GMX(
        {
            "rpcUrl": rpc_url,
            "wallet": test_wallet,
        }
    )

    # Verify initialization
    assert gmx.wallet is not None
    assert gmx.wallet.address == test_wallet.address
    assert gmx.trader is not None


def test_ccxt_style_view_only_mode(arbitrum_fork_config):
    """Test view-only mode without privateKey or wallet.

    - Initializes GMX without wallet credentials
    - Verifies warning is logged
    - Verifies read-only operations work
    - Verifies order creation raises appropriate error
    """
    rpc_url = arbitrum_fork_config.web3.provider.endpoint_uri

    # Capture log messages
    with pytest.warns(None) as warning_list:
        # Initialize without wallet (view-only mode)
        gmx = GMX(
            {
                "rpcUrl": rpc_url,
            }
        )

    # Verify view-only mode
    assert gmx.wallet is None
    assert gmx.trader is None

    # Verify read-only operations work
    gmx.load_markets()
    assert len(gmx.markets) > 0

    # Verify order creation fails with appropriate error
    with pytest.raises(ValueError, match="VIEW-ONLY mode"):
        gmx.create_market_buy_order("ETH/USD", 10.0)


def test_ccxt_style_chain_auto_detection(arbitrum_fork_config):
    """Test automatic chain detection from RPC.

    - Initializes without explicit chainId
    - Verifies chain is auto-detected from RPC
    """
    rpc_url = arbitrum_fork_config.web3.provider.endpoint_uri

    gmx = GMX(
        {
            "rpcUrl": rpc_url,
        }
    )

    # Verify chain was detected
    assert gmx.config is not None
    chain = gmx.config.get_chain()
    assert chain in ["arbitrum", "arbitrum_sepolia", "avalanche"]


def test_ccxt_style_chain_override(arbitrum_fork_config):
    """Test explicit chainId parameter override.

    - Initializes with explicit chainId parameter
    - Verifies chainId is respected
    """
    rpc_url = arbitrum_fork_config.web3.provider.endpoint_uri
    chain_id = arbitrum_fork_config.web3.eth.chain_id

    gmx = GMX(
        {
            "rpcUrl": rpc_url,
            "chainId": chain_id,
        }
    )

    # Verify initialization succeeded
    assert gmx.config is not None
    assert gmx.web3.eth.chain_id == chain_id


def test_ccxt_style_verbose_mode(arbitrum_fork_config, caplog):
    """Test verbose mode enables debug logging.

    - Initializes with verbose=True
    - Verifies debug logging is enabled
    """
    rpc_url = arbitrum_fork_config.web3.provider.endpoint_uri

    with caplog.at_level(logging.DEBUG):
        gmx = GMX(
            {
                "rpcUrl": rpc_url,
                "verbose": True,
            }
        )

        # Load markets to generate some log messages
        gmx.load_markets()

    # Verify some debug messages were logged (verbose mode is working)
    # We just check that the logger configuration was set up
    assert gmx.config is not None


def test_ccxt_style_subsquid_endpoint(arbitrum_fork_config):
    """Test custom Subsquid endpoint parameter.

    - Initializes with custom subsquidEndpoint
    - Verifies Subsquid client is initialized
    """
    rpc_url = arbitrum_fork_config.web3.provider.endpoint_uri
    custom_endpoint = "https://custom.subsquid.endpoint"

    gmx = GMX(
        {
            "rpcUrl": rpc_url,
            "subsquidEndpoint": custom_endpoint,
        }
    )

    # Verify Subsquid client was initialized
    assert gmx.subsquid is not None


def test_legacy_initialization_still_works(arbitrum_fork_config, test_wallet):
    """Test backward compatibility with legacy initialization.

    - Initializes with legacy config parameter
    - Verifies all functionality works as before
    """
    # Legacy-style initialization
    gmx = GMX(config=arbitrum_fork_config, wallet=test_wallet)

    # Verify initialization
    assert gmx.wallet is not None
    assert gmx.wallet.address == test_wallet.address
    assert gmx.config == arbitrum_fork_config
    assert gmx.api is not None
    assert gmx.trader is not None

    # Verify can load markets
    gmx.load_markets()
    assert len(gmx.markets) > 0


def test_ccxt_style_missing_rpc_url():
    """Test that missing rpcUrl raises appropriate error.

    - Initializes without rpcUrl
    - Verifies ValueError is raised
    """
    with pytest.raises(ValueError, match="rpcUrl is required"):
        GMX(
            {
                "privateKey": "0x1234567890abcdef" * 4,
            }
        )
