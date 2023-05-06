"""Solidity linking tests."""
import pytest
from _pytest.fixtures import FixtureRequest
from eth.constants import ZERO_ADDRESS

from web3 import Web3, EthereumTesterProvider, HTTPProvider

from eth_defi.aave_v3.deployer import get_aave_hardhard_export
from eth_defi.abi import get_contract, get_linked_contract
from eth_defi.anvil import AnvilLaunch, launch_anvil
from eth_defi.chain import install_chain_middleware
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.utils import ZERO_ADDRESS_STR


@pytest.fixture()
def web3() -> Web3:
    """Set up the Anvil Web3 connection.
    Also perform the Anvil state reset for each test.
    """
    web3 = Web3(EthereumTesterProvider())
    return web3


@pytest.fixture()
def deployer(web3) -> str:
    """Deploy account"""
    return web3.eth.accounts[0]


def test_link_aave(
        web3,
        deployer,
):
    """Test Hardhat linking by deploying Aave pool contract."""
    export = get_aave_hardhard_export()

    # Link bytecode
    Pool = get_linked_contract(web3, "aave_v3/Pool.json", export=export)

    # TODO: Deployment fails
