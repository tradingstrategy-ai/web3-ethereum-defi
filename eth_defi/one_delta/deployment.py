"""1delta deployments."""

from dataclasses import dataclass

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.aave_v3.deployment import AaveV3Deployment
from eth_defi.abi import get_deployed_contract


@dataclass(frozen=True)
class OneDeltaDeployment:
    """Describe 1delta deployment."""

    #: The Web3 instance for which all the contracts here are bound
    web3: Web3

    #: FlashAggregator contract proxy
    flash_aggregator: Contract

    # DeltaBrokerProxy contract proxy
    broker_proxy: Contract

    # Aave v3 deployment
    aave_v3: AaveV3Deployment


def fetch_deployment(
    web3: Web3,
    aave_v3: AaveV3Deployment,
    flash_aggregator_address: HexAddress | str,
    broker_proxy_address: HexAddress | str,
) -> AaveV3Deployment:
    """Construct 1delta deployment based on on-chain data.

    :return:
        Data class representing 1delta deployment
    """
    flash_aggregator = get_deployed_contract(
        web3,
        "1delta/FlashAggregator.json",
        flash_aggregator_address,
    )
    broker_proxy = get_deployed_contract(
        web3,
        "1delta/DeltaBrokerProxy.json",
        broker_proxy_address,
    )

    return OneDeltaDeployment(
        web3=web3,
        aave_v3=aave_v3,
        flash_aggregator=flash_aggregator,
        broker_proxy=broker_proxy,
    )
