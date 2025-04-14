import logging
import os
from typing import Generator, Any

import pytest
from web3 import Web3, HTTPProvider

from eth_defi.chain import install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.gmx.api import GMXAPI
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.data import GMXMarketData
from eth_defi.gmx.liquidity import GMXLiquidityManager
from eth_defi.provider.anvil import fork_network_anvil

mainnet_rpc = os.environ.get("ARBITRUM_JSON_RPC_URL")

pytestmark = pytest.mark.skipif(not mainnet_rpc, reason="No ARBITRUM_JSON_RPC_URL environment variable")


@pytest.fixture()
def anvil_arbitrum_chain_fork(request, large_eth_holder) -> Generator[str, Any, None]:
    # Create a testable fork of live arbitrum chain.
    launch = fork_network_anvil(mainnet_rpc, unlocked_addresses=[large_eth_holder])
    try:
        yield launch.json_rpc_url
    finally:
        # Wind down Anvil process after the test is complete
        launch.close(log_level=logging.ERROR)


# forking is giving error
@pytest.fixture()
def web3_arbitrum_fork(anvil_arbitrum_chain_fork: str) -> Web3:
    # Set up a local unit testing blockchain
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    web3 = Web3(HTTPProvider(anvil_arbitrum_chain_fork))
    # Anvil needs POA middlware if parent chain needs POA middleware
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    return web3


@pytest.fixture()
def web3_arbitrum():
    """Set up a Web3 connection to ARBITRUM.

    This fixture creates a Web3 connection to an ARBITRUM RPC endpoint.
    It uses the ARBITRUM_JSON_RPC_URL environment variable if available,
    or falls back to a public RPC endpoint.
    """
    # Try to get RPC URL from environment variable, or use a public endpoint
    ARBITRUM_RPC_URL = os.environ.get("ARBITRUM_JSON_RPC_URL", None)

    # Create the Web3 connection
    web3 = Web3(HTTPProvider(ARBITRUM_RPC_URL))

    # Clear middleware and add chain-specific middleware
    web3.middleware_onion.clear()
    install_chain_middleware(web3)

    # Skip tests if we can't connect
    if not web3.is_connected():
        pytest.skip(f"Could not connect to ARBITRUM RPC at {ARBITRUM_RPC_URL}")

    # Verify we're actually connected to ARBITRUM
    chain_id = web3.eth.chain_id
    if chain_id != 42161:
        pytest.skip(f"Connected to chain ID {chain_id}, but expected ARBITRUM (42161)")

    return web3


@pytest.fixture()
def gmx_config_arbitrum(web3_arbitrum: Web3) -> GMXConfig:
    """
    Create a GMX configuration for Arbitrum.

    This creates a configuration object that can be reused across tests,
    avoiding repeated initialization.
    """
    return GMXConfig(web3_arbitrum, chain="arbitrum")


@pytest.fixture()
def market_data_arbitrum(gmx_config_arbitrum: GMXConfig) -> GMXMarketData:
    """
    Create a GMXMarketData instance for Arbitrum.

    This instance will be reused across all tests to improve performance.
    """
    return GMXMarketData(gmx_config_arbitrum)


@pytest.fixture()
def api_arbitrum(gmx_config_arbitrum):
    """
    Create a GMXAPI instance for Arbitrum.
    """
    return GMXAPI(gmx_config_arbitrum)

@pytest.fixture()
def gmx_config_arbitrum_fork(web3_arbitrum_fork: Web3) -> GMXConfig:
    """
    Create a GMX configuration for Arbitrum.

    This creates a configuration object that can be reused across tests,
    avoiding repeated initialization.
    """
    anvil_private_key: str = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    address: str = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    return GMXConfig(web3_arbitrum_fork, chain="arbitrum", private_key=anvil_private_key, user_wallet_address=address)


@pytest.fixture()
def liquidity_manager_arbitrum(gmx_config_arbitrum_fork):
    """
    Create a GMXLiquidityManager instance for Arbitrum.
    """
    return GMXLiquidityManager(gmx_config_arbitrum_fork)