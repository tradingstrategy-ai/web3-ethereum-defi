from web3 import HTTPProvider, Web3
import pytest
from eth_defi.chain import install_chain_middleware
import os

from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.data import GMXMarketData


@pytest.fixture()
def web3_avalanche():
    """Set up a Web3 connection to Avalanche.

    This fixture creates a Web3 connection to an Avalanche RPC endpoint.
    It uses the AVALANCHE_JSON_RPC_URL environment variable if available,
    or falls back to a public RPC endpoint.
    """
    # Try to get RPC URL from environment variable, or use a public endpoint
    avalanche_rpc_url = os.environ.get("AVALANCHE_JSON_RPC_URL", "https://api.avax.network/ext/bc/C/rpc")  # Public Avalanche C-Chain RPC

    # Create the Web3 connection
    web3 = Web3(HTTPProvider(avalanche_rpc_url))

    # Clear middleware and add chain-specific middleware
    web3.middleware_onion.clear()
    install_chain_middleware(web3)

    # Skip tests if we can't connect
    if not web3.is_connected():
        pytest.skip(f"Could not connect to Avalanche RPC at {avalanche_rpc_url}")

    # Verify we're actually connected to Avalanche
    chain_id = web3.eth.chain_id
    if chain_id != 43114:
        pytest.skip(f"Connected to chain ID {chain_id}, but expected Avalanche (43114)")

    return web3


@pytest.fixture()
def gmx_config_avalanche(web3_avalanche):
    """
    Create a GMX configuration for Avalanche that persists throughout the test session.
    """
    return GMXConfig(web3_avalanche, chain="avalanche")


@pytest.fixture()
def market_data_avalanche(gmx_config_avalanche):
    """
    Create a GMXMarketData instance for Avalanche that persists throughout the test session.
    """
    return GMXMarketData(gmx_config_avalanche)
