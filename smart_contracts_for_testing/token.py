"""ERC-20 token mocks.

Deploy ERC-20 tokens to be used within your test suite.
"""

from web3 import Web3
from web3.contract import Contract

from smart_contracts_for_testing.abi import get_contract
from smart_contracts_for_testing.deploy import deploy_contract


def create_token(web3: Web3, deployer: str, name: str, symbol: str, supply: int) -> Contract:
    """Deploys a new test token.

    Uses `ERC20Mock <https://github.com/sushiswap/sushiswap/blob/canary/contracts/mocks/ERC20Mock.sol>`_ contract for the deployment.

    `See Web3.py documentation on Contract instances <https://web3py.readthedocs.io/en/stable/contracts.html#contract-deployment-example>`_.

    Token decimal units is hardcoded to 18.

    Example:

    .. code-block::

        # Deploys an ERC-20 token where 100,000 tokens are allocated ato the deployer address
        token = create_token(web3, deployer, "Hentai books token", "HENTAI", 100_000 * 10**18)
        print(f"Deployed token contract address is {token.address}")
        print(f"Deployer account {deployer} has {token.functions.balanceOf(user_1).call() / 10**18} tokens")

    :param web3: Web3 instance
    :param deployer: Deployer account as 0x address
    :param name: Token name
    :param symbol: Token symbol
    :param supply: Token supply as raw units
    :return: Instance to a deployed Web3 contract.
    """
    return deploy_contract(web3, "ERC20Mock.json", deployer, name, symbol, supply)
