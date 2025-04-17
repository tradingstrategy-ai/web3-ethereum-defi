import logging
from typing import Generator, Any

from web3 import HTTPProvider, Web3
import pytest

from eth_defi.balances import fetch_erc20_balances_multicall
from eth_defi.chain import install_chain_middleware
import os

from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.gmx.api import GMXAPI
from eth_defi.gmx.config import GMXConfig
from eth_defi.gmx.data import GMXMarketData
from eth_defi.gmx.liquidity import GMXLiquidityManager
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.token import TokenDetails, fetch_erc20_details

mainnet_rpc = os.environ.get("AVALANCHE_JSON_RPC_URL")

pytestmark = pytest.mark.skipif(not mainnet_rpc, reason="No AVALANCHE_JSON_RPC_URL environment variable")

# https://betterstack.com/community/questions/how-to-disable-logging-when-running-tests-in-python/
original_log_handlers = logging.getLogger().handlers[:]
# Remove all existing log handlers bcz of anvil is dumping the logs which is not desirable in the workflows
for handler in original_log_handlers:
    logging.getLogger().removeHandler(handler)


@pytest.fixture()
def anvil_avalanche_chain_fork(request, large_eth_holder, large_wbtc_holder) -> Generator[str, Any, None]:
    # Create a testable fork of live avalanche chain.
    launch = fork_network_anvil(mainnet_rpc, unlocked_addresses=[large_eth_holder, large_wbtc_holder], test_request_timeout=30)
    try:
        yield launch.json_rpc_url
    finally:
        # Wind down Anvil process after the test is complete
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3_avalanche_fork(anvil_avalanche_chain_fork: str) -> Web3:
    # Set up a local unit testing blockchain
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    web3 = Web3(HTTPProvider(anvil_avalanche_chain_fork))
    # Anvil needs POA middlware if parent chain needs POA middleware
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    return web3


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


@pytest.fixture()
def api_avalanche(gmx_config_avalanche):
    """
    Create a GMXAPI instance for Avalanche
    """
    return GMXAPI(gmx_config_avalanche)


@pytest.fixture()
def gmx_config_avalanche_fork(web3_avalanche_fork: Web3) -> GMXConfig:
    """
    Create a GMX configuration for avalanche.

    This creates a configuration object that can be reused across tests,
    avoiding repeated initialization.
    """
    anvil_private_key: str = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    address: str = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    return GMXConfig(web3_avalanche_fork, chain="avalanche", private_key=anvil_private_key, user_wallet_address=address)


@pytest.fixture()
def liquidity_manager_avalanche(gmx_config_avalanche_fork):
    """
    Create a GMXLiquidityManager instance for avalanche.
    """
    return GMXLiquidityManager(gmx_config_avalanche_fork)


@pytest.fixture()
def wbtc_avalanche(web3_avalanche_fork) -> TokenDetails:
    """WBTC token."""
    return fetch_erc20_details(web3_avalanche_fork, "0x50b7545627a5162F82A992c33b87aDc75187B218")


@pytest.fixture()
def wavax(web3_avalanche_fork) -> TokenDetails:
    """WAVAX token"""
    return fetch_erc20_details(web3_avalanche_fork, "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7", contract_name="./WAVAX.json")


@pytest.fixture()
def wallet_with_avax(web3_avalanche_fork, wavax) -> None:
    """
    Setup the anvil wallet[0] with avax for testing
    """
    address: str = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    # print(f"{wavax.fetch_balance_of(address, 32579341)=}")

    wavax.contract.functions.deposit().transact({"from": address, "value": 100 * 10**18})

    # block_number = get_almost_latest_block_number(web3_avalanche_fork)
    # balance = fetch_erc20_balances_multicall(web3_avalanche_fork, address, ["0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7"], block_number)
    # print(f"{balance=}")
