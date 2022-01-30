"""ERC-20 token mocking."""

from typing import Type

from web3 import Web3
from web3.contract import Contract

from smart_contracts_for_testing.abi import get_contract


def create_token(web3: Web3, deployer: str, name: str, symbol: str, supply: int) -> Contract:
    """Create a new test token.

    Uses `ERC20Mock <https://github.com/sushiswap/sushiswap/blob/canary/contracts/mocks/ERC20Mock.sol>_` contract for the deployment.

    :param web3: Web3 instance
    :param deployer: Deployer account as 0x address
    :param name: Token name
    :param symbol: Token symbol
    :param supply: Token supply as raw units
    :return: Instance to a deployed Web3 contract
    """
    ERC20Mock = get_contract(web3, "ERC20Mock.json")
    # TODO: Figure out why this does not work when not passing gas
    # TypeError: estimate_gas() takes 2 positional arguments but 3 were given
    tx_hash = ERC20Mock.constructor(name, symbol, supply).transact({"from": deployer})
    tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    instance = ERC20Mock(
        address=tx_receipt.contractAddress,
    )
    return instance
