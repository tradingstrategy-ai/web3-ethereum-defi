"""Solidity linking tests."""
import pytest

from web3 import Web3, EthereumTesterProvider

from eth_defi.aave_v3.deployer import get_aave_hardhard_export
from eth_defi.abi import get_contract, get_linked_contract
from eth_defi.chain import install_chain_middleware

#
# @pytest.fixture()
# def web3() -> Web3:
#     """Set up the Anvil Web3 connection.
#     Also perform the Anvil state reset for each test.
#     """
#     web3 = Web3(EthereumTesterProvider())
#     return web3
#


@pytest.fixture()
def web3() -> Web3:
    """Set up the Anvil Web3 connection.
    Also perform the Anvil state reset for each test.
    """
    web3 = Web3(EthereumTesterProvider())
    web3.middleware_onion.clear()
    install_chain_middleware(web3)
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
    # Check that the code runs (we are not checking actual bytecode result yet)
    get_linked_contract(web3, "aave_v3/Pool.json", hardhat_export_data=export)
