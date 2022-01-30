"""Deploy a mock Uniswap v2 like decentralised exchange.

Compatible exchanges include, but not limited to
* Uniswap v2
* Sushiswap v2
* Pancakeswap v2 and v3
* QuickSwap
* TraderJoe

Under the hood we are using `SushiSwap v2 contracts <github.com/sushiswap/sushiswap>`_ for the deployment.
"""
from web3 import Web3
from web3.contract import Contract

from smart_contracts_for_testing.abi import get_contract
from tradeexecutor.utils import dataclass


@dataclass
class UniswapV2Deployment:
    """Describe Uniswap v2 deployment."""

    #: Factory address
    factory: Contract

    #: Router address
    router: Contract

    #: WETH address
    weth: Contract


def deploy_uniswap_v2_like(web3: Web3, deployer: str) -> UniswapV2Deployment:
    """Deploy v2=

    `See this StackOverflow question for commentary <https://stackoverflow.com/q/70846489/315168>`_.

    :param web3:
    :param deployer:
    :return:
    """

    factory = get_contract(web3, "UniswapV2Factory.json")



def deploy_sample_pair(web3, deployre: str, deployment: UniswapV2Deployment):