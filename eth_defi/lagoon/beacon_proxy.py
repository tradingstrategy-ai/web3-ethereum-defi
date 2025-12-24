"""OpenZeppelin beacon proxy contract deployments."""

import logging

from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress

from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import deploy_contract


logger = logging.getLogger(__name__)


def deploy_beacon_proxy(
    web3: Web3,
    deployer: HexAddress | LocalAccount,
    beacon_address: HexAddress | str,
    implementation_contract_abi: str,
    payload: bytes = b"",
    gas=500_000,
) -> Contract:
    """Deploy a new proxy contract from the beacon master contract.

    - Uses [OpenZeppelin beacon proxy contract deployment pattern](https://github.com/OpenZeppelin/openzeppelin-foundry-upgrades/)

    See https://github.com/OpenZeppelin/openzeppelin-foundry-upgrades/blob/3a4bd0d10e945e82472b306776eb5ec272571945/src/Upgrades.sol#L295

    Example:

    .. code-block:: python

        # Deploy a new vault contract using a beacon proxy contract pattern
        vault = deploy_beacon_proxy(
            web3,
            deployer=deployer,
            beacon_address=beacon_address,
            implementation_contract_abi="lagoon/Vault.json",
        )

    :param web3:
        Web3 instance

    :parma beacon_address:
        The master copy beacon address

    :param implementation_contract_abi:
        The name of the JSON ABI file for the implementation.

    :param payload:
        Encoded constructor args.

    :param gas:
        Gas limit for the deployment.

    :return:
        Proxied contract interface
    """

    assert isinstance(web3, Web3)
    assert isinstance(beacon_address, str)
    assert isinstance(implementation_contract_abi, str)

    logger.info(
        "Deploying beacon proxy for %s using beacon at %s",
        implementation_contract_abi,
        beacon_address,
    )

    beacon_proxy = deploy_contract(
        web3,
        "lagoon/BeaconProxy.json",
        deployer,
        beacon_address,
        payload,
        gas=gas,
    )

    return get_deployed_contract(
        web3,
        implementation_contract_abi,
        beacon_proxy.address,
    )
