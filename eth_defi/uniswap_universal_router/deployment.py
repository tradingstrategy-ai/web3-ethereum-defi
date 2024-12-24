"""Uniswap v3 and compatible DEX deployments.

Compatible exchanges include Uniswap v3 deployments on:

- Ethereum mainnet
- Avalanche
- Polygon
- Optimism
- Arbitrum
- Base

"""

from dataclasses import dataclass
from typing import Optional

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_abi_by_filename, get_contract, get_deployed_contract
from eth_defi.deploy import deploy_contract
from eth_defi.uniswap_v3.constants import (
    DEFAULT_FEES,
    FOREVER_DEADLINE,
    UNISWAP_V3_FACTORY_BYTECODE,
    UNISWAP_V3_FACTORY_DEPLOYMENT_DATA,
)


@dataclass(frozen=True, slots=True)
class UniswapUniversalRouterDeployment:
    """Describe Uniswap Universal Router deployment."""

    #: The Web3 instance for which all the contracts here are bound
    web3: Web3

    #: Uniswap Universal Router
    router: Contract

    #: Permit2 contract
    permit2: Contract

    def __repr__(self):
        return f"<Uniswap Universal Router on chain: {self.web3.eth.chain_id}, router: {self.router.address}>"


def fetch_uniswap_universal_router_deployment(
    web3: Web3,
    uniswap_universal_router_address: HexAddress | str,
    permit2_address: HexAddress | str,
) -> UniswapUniversalRouterDeployment:
    return UniswapUniversalRouterDeployment(
        web3=web3,
        router=get_deployed_contract(web3, "uniswap_universal_router/UniversalRouter.json", uniswap_universal_router_address),
        permit2=get_deployed_contract(web3, "uniswap_universal_router/Permit2.json", permit2_address),
    )
