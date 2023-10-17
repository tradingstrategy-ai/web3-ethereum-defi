"""Aave v3 deployments."""
from dataclasses import dataclass
from typing import NamedTuple

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.aave_v3.deployment import AaveV3Deployment
from eth_defi.abi import get_abi_by_filename, get_contract, get_deployed_contract
from eth_defi.deploy import deploy_contract
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment
from eth_defi.uniswap_v3.deployment import UniswapV3Deployment


@dataclass(frozen=True)
class OneDeltaDeployment:
    """Describe 1delta deployment."""

    #: The Web3 instance for which all the contracts here are bound
    web3: Web3

    #: FlashAggregator contract proxy
    flash_aggregator: Contract

    # ManagementModule contract proxy
    manager: Contract

    # DeltaBrokerProxy contract proxy
    broker_proxy: Contract

    # Aave v3 deployment
    aave_v3: AaveV3Deployment


def deploy_1delta(
    web3: Web3,
    deployer: HexAddress,
    uniswap_v2: UniswapV2Deployment,
    uniswap_v3: UniswapV3Deployment,
    aave_v3: AaveV3Deployment,
    weth: Contract | None = None,
) -> OneDeltaDeployment:
    """Deploy 1delta

    Example:

    .. code-block:: python

        TODO

    :param web3: Web3 instance
    :param deployer: Deployer account
    :param weth: WETH contract instance
    :param give_weth:
        Automatically give some Wrapped ETH to the deployer.
        Express as ETH units.
    :return: Deployment details
    """

    module_config = deploy_contract(
        web3,
        "1delta/ConfigModule.json",
        deployer,
    )

    broker_proxy = deploy_contract(
        web3,
        "1delta/DeltaBrokerProxy.json",
        deployer,
        deployer,
        module_config.address,
    )

    manager = deploy_contract(
        web3,
        "1delta/ManagementModule.json",
        deployer,
        # proxy_address=proxy.address,
    )

    # module_config.functions.configureModules(
    #     [{
    #         moduleAddress: manager.address,
    #         action: 0,
    #         functionSelectors: getSelectors(managerModule)
    #     }]
    # ).transact({"from": deployer})

    flash_aggregator = deploy_contract(
        web3,
        "1delta/FlashAggregator.json",
        deployer,
        uniswap_v2.factory.address,
        uniswap_v3.factory.address,
        aave_v3.pool.address,
        weth.address,
        # proxy_address=proxy.address,
    )

    return OneDeltaDeployment(
        web3=web3,
        aave_v3=aave_v3,
        flash_aggregator=flash_aggregator,
        manager=manager,
        broker_proxy=broker_proxy,
    )


def fetch_deployment(
    web3: Web3,
    aave_v3: AaveV3Deployment,
    flash_aggregator_address: HexAddress | str,
    manager_address: HexAddress | str,
    broker_proxy_address: HexAddress | str,
) -> AaveV3Deployment:
    """Construct 1delta deployment based on on-chain data.

    :return:
        Data class representing 1delta deployment
    """
    # flash_aggregator = get_deployed_contract(
    #     web3,
    #     "1delta/FlashAggregator.json",
    #     flash_aggregator_address,
    #     register_for_tracing=True,
    # )
    # manager = get_deployed_contract(web3, "1delta/ManagementModule.json", manager_address)
    # broker_proxy = get_deployed_contract(web3, "1delta/DeltaBrokerProxy.json", broker_proxy_address)

    flash_aggregator = get_deployed_contract(
        web3,
        "1delta/modules/deploy/polygon/FlashAggregator.sol/DeltaFlashAggregator.json",
        flash_aggregator_address,
        register_for_tracing=True,
    )
    manager = get_deployed_contract(
        web3,
        "1delta/modules/aave/ManagementModule.sol/ManagementModule.json",
        manager_address,
    )
    broker_proxy = get_deployed_contract(
        web3,
        "1delta/proxy/DeltaBroker.sol/DeltaBrokerProxy.json",
        broker_proxy_address,
    )

    return OneDeltaDeployment(
        web3=web3,
        aave_v3=aave_v3,
        flash_aggregator=flash_aggregator,
        manager=manager,
        broker_proxy=broker_proxy,
    )
