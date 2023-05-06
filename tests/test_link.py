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
def anvil(request: FixtureRequest) -> AnvilLaunch:
    """Launch Anvil for the test backend."""

    anvil = launch_anvil(
        gas_limit=15_000_000,
        port=8545,  # Must be localhost:8545 for Aave deployment
    )
    try:
        yield anvil
    finally:
        anvil.close()


@pytest.fixture()
def web3(anvil: AnvilLaunch) -> Web3:
    """Set up the Anvil Web3 connection.
    Also perform the Anvil state reset for each test.
    """
    web3 = Web3(HTTPProvider(anvil.json_rpc_url))
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
    Pool = get_linked_contract(web3, "aave_v3/Pool.json", export=export)

    # # Deploy linked contract
    # tx_hash = Pool.constructor(ZERO_ADDRESS_STR).transact({"from": deployer, "gas": 15_000_000})
    # assert_transaction_success_with_explanation(web3, tx_hash)
    #
    # # Check deployed contract works
    # revision = pool.functions.getRevision().call()
    # assert revision == 1
    #
