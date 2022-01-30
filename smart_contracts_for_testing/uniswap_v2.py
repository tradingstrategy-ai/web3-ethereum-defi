"""Deploy a mock Uniswap v2 like decentralised exchange.

Compatible exchanges include, but not limited to
* Uniswap v2
* Sushiswap v2
* Pancakeswap v2 and v3
* QuickSwap
* TraderJoe

Under the hood we are using `SushiSwap v2 contracts <github.com/sushiswap/sushiswap>`_ for the deployment.
"""
from dataclasses import dataclass

from web3 import Web3
from web3.contract import Contract

from smart_contracts_for_testing.deploy import deploy_contract



@dataclass
class UniswapV2Deployment:
    """Describe Uniswap v2 deployment."""

    #: Factory address
    factory: Contract

    #: WETH9Mock address.
    #: https://github.com/sushiswap/sushiswap/blob/4fdfeb7dafe852e738c56f11a6cae855e2fc0046/contracts/mocks/WETH9Mock.sol
    weth: Contract

    #: Router address.
    #: https://github.com/sushiswap/sushiswap/blob/4fdfeb7dafe852e738c56f11a6cae855e2fc0046/contracts/uniswapv2/UniswapV2Router02.sol
    router: Contract


def deploy_uniswap_v2_like(web3: Web3, deployer: str) -> UniswapV2Deployment:
    """Deploy v2=

    `See this StackOverflow question for commentary <https://stackoverflow.com/q/70846489/315168>`_.

    Example:

    .. code-block:: python

        deployment = deploy_uniswap_v2_like(web3, deployer)
        factory = deployment.factory
        print(f"Uniswap factory is {factory.address}")

    :param web3: Web3 instance
    :param deployer: Deployer account
    :return: Deployment details
    """

    # Factory takes feeSetter as an argument
    factory = deploy_contract(web3, "UniswapV2Factory.json", deployer, deployer)
    weth = deploy_contract(web3, "WETH9Mock.json", deployer)
    router = deploy_contract(web3, "UniswapV2Router02.json", deployer, factory.address, weth.address)
    return UniswapV2Deployment(factory, weth, router)


def deploy_trading_pair(
        web3: Web3,
        deployer: str,
        deployment: UniswapV2Deployment,
        token_a: Contract,
        token_b: Contract,
        liquidity_a: int,
        liquidity_b: int) -> str:
    """Deploy a new trading pair on Uniswap v2.

    Assumes `deployer` has enough token balance to add the initial liquidity.

    `See UniswapV2Factory.createPair() for details <https://github.com/sushiswap/sushiswap/blob/4fdfeb7dafe852e738c56f11a6cae855e2fc0046/contracts/uniswapv2/UniswapV2Factory.sol#L30>`_.

    :param web3:
    :param deployer:
    :param deployment:
    :return: Pair contract address
    """
    factory = deployment.factory
    tx = factory.functions.createPair(token_a.address, token_b.address).transact({"from": deployer})

