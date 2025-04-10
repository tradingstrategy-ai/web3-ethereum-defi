import logging
import os
from typing import Generator, Any

import pytest
from web3 import Web3, HTTPProvider

from eth_defi.chain import install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.data import GMXMarketData
from eth_defi.provider.anvil import fork_network_anvil

mainnet_rpc = os.environ.get("ARBITRUM_CHAIN_JSON_RPC")

pytestmark = pytest.mark.skipif(not mainnet_rpc, reason="No ARBITRUM_CHAIN_JSON_RPC environment variable")


@pytest.fixture()
def anvil_arbitrum_chain_fork(request, large_eth_holder) -> Generator[str, Any, None]:
    # Create a testable fork of live arbitrum chain.
    launch = fork_network_anvil(mainnet_rpc, unlocked_addresses=[large_eth_holder])
    try:
        yield launch.json_rpc_url
    finally:
        # Wind down Anvil process after the test is complete
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3_arbitrum(anvil_arbitrum_chain_fork: str) -> Web3:
    # Set up a local unit testing blockchain
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    web3 = Web3(HTTPProvider(anvil_arbitrum_chain_fork))
    # Anvil needs POA middlware if parent chain needs POA middleware
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
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
