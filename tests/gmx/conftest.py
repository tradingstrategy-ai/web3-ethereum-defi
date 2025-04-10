import os
from typing import Any, Generator

import pytest
import logging

from eth_typing import HexAddress, HexStr
from web3 import HTTPProvider, Web3

from eth_defi.chain import install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.provider.anvil import fork_network_anvil



@pytest.fixture()
def large_eth_holder() -> HexAddress:
    """A random account picked from Arbitrum Smart chain that holds a lot of ETH.

    This account is unlocked on Ganache, so you have access to good ETH stash.

    `To find large holder accounts, use bscscan <https://arbiscan.io/accounts>`_.
    """
    # Binance Hot Wallet 20
    return HexAddress(HexStr("0xF977814e90dA44bFA03b6295A0616a897441aceC"))

@pytest.fixture()
def anvil_arbitrum_chain_fork(request, large_eth_holder) -> Generator[str, Any, None]:
    # Create a testable fork of live arbitrum chain.
    mainnet_rpc = os.environ["ARBITRUM_CHAIN_JSON_RPC"]
    launch = fork_network_anvil(mainnet_rpc, unlocked_addresses=[large_eth_holder])
    try:
        yield launch.json_rpc_url
    finally:
        # Wind down Anvil process after the test is complete
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3(anvil_arbitrum_chain_fork: str):
    # Set up a local unit testing blockchain
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    web3 =  Web3(HTTPProvider(anvil_arbitrum_chain_fork))
    # Anvil needs POA middlware if parent chain needs POA middleware
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    return web3


@pytest.fixture(scope="session")
def web3_avalanche():
    """Set up a Web3 connection to Avalanche.

    This fixture creates a Web3 connection to an Avalanche RPC endpoint.
    It uses the AVALANCHE_JSON_RPC_URL environment variable if available,
    or falls back to a public RPC endpoint.
    """
    # Try to get RPC URL from environment variable, or use a public endpoint
    avalanche_rpc_url = os.environ.get(
        "AVALANCHE_JSON_RPC_URL",
        "https://api.avax.network/ext/bc/C/rpc"  # Public Avalanche C-Chain RPC
    )

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