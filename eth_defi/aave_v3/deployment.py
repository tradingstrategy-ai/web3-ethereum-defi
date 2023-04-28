"""Aave v3 deployments."""

from dataclasses import dataclass

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_contract
from eth_defi.deploy import deploy_contract
from eth_defi.trace import assert_transaction_success_with_explanation


@dataclass(frozen=True)
class AaveV3Deployment:
    """Describe Aave v3 deployment."""

    #: The Web3 instance for which all the contracts here are bound
    web3: Web3

    #: Aave v3 pool contract
    pool: Contract


def deploy_aave_v3(
    web3: Web3,
    deployer: HexAddress,
) -> AaveV3Deployment:
    """Deploy Aave v3

    Example:

    .. code-block:: python

        deployment = deploy_aave_v3(web3, deployer)
        pool = deployment.pool
        print(f"Aave v3 pool is {pool.address}")

    :param web3: Web3 instance
    :param deployer: Deployer account
    :return: Deployment details
    """

    # deploy PoolAddressesProvider first as it's required by Pool's constructor
    pool_addresses_provider = deploy_contract(
        web3,
        "aave_v3/PoolAddressesProvider.json",
        deployer,
        "Aave Ethereum Market",  # FIXME: not hardcode
        deployer,
    )

    # now deploy pool
    Pool = get_contract(web3, "aave_v3/Pool.json")
    tx_hash = Pool.constructor(pool_addresses_provider.address).transact(
        {
            "from": deployer,
            "gas": 10_000_000,
        }
    )

    tx_receipt = assert_transaction_success_with_explanation(web3, tx_hash)

    pool = Pool(
        address=tx_receipt["contractAddress"],
    )

    # pool = deploy_contract(
    #     web3,
    #     "aave_v3/Pool.json",
    #     deployer,
    #     pool_addresses_provider.address,
    # )

    return AaveV3Deployment(
        web3=web3,
        pool=pool,
    )
