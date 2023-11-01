"""Deploy any precompiled contract.

`See Github for available contracts <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/eth_defi/abi>`_.
"""

from typing import Dict, TypeAlias, Union

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_contract

#: Manage internal registry of deployed contracts
#:
#: Lower case address -> Contract mapping.
ContractRegistry: TypeAlias = Dict[str, Contract]


class ContractDeploymentFailed(Exception):
    """Did not get successful tx receipt from a deployment."""

    def __init__(self, tx_hash, msg):
        super().__init__(msg)
        self.tx_hash = tx_hash


def deploy_contract(
    web3: Web3,
    contract: Union[str, Contract],
    deployer: str,
    *constructor_args,
    register_for_tracing=True,
) -> Contract:
    """Deploys a new contract from ABI file.

    A generic helper function to deploy any contract.

    Example:

    .. code-block:: python

        token = deploy_contract(web3, deployer, "ERC20Mock.json", name, symbol, supply)
        print(f"Deployed ERC-20 token at {token.address}")

    :param web3:
        Web3 instance

    :param contract:
        Contract file path as string or contract proxy class

    :param deployer:
        Deployer account

    :param constructor_args:
        Other arguments to pass to the contract's constructor

    :param register_for_tracing:
        Make the symbolic contract information available on web3 instance.

        See :py:func:`get_contract_registry`

    :raise ContractDeploymentFailed:
        In the case we could not deploy the contract.

    :return:
        Contract proxy instance

    """
    if isinstance(contract, str):
        Contract = get_contract(web3, contract)

        # Used in trace.py
        contract_name = contract.replace(".json", "")

    else:
        Contract = contract
        contract_name = None

    tx_hash = Contract.constructor(*constructor_args).transact({"from": deployer})

    tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    if tx_receipt["status"] != 1:
        raise ContractDeploymentFailed(tx_hash, f"Contract {contract_name} deployment failed with args {constructor_args}, tx hash is {tx_hash.hex()}")

    instance = Contract(address=tx_receipt["contractAddress"])

    if register_for_tracing:
        instance.name = contract_name
        register_contract(web3, tx_receipt["contractAddress"], instance)

    return instance


def get_or_create_contract_registry(web3: Web3) -> ContractRegistry:
    """Get a contract registry associated with a Web3 connection.

    - Only relevant for test sessions

    - Assumes one web3 instance per test

    - Useful to make traces symbolic in :py:mod:`eth_defi.trace`

    :param web3:
        Web3 test session

    :return:
        Mapping of address -> deployed contract instance
    """
    if not hasattr(web3, "contract_registry"):
        web3.contract_registry = {}

    return web3.contract_registry


def register_contract(web3, address: HexAddress, instance: Contract):
    """Register a contract for tracing.

    See :py:func:`deploy_contract`.
    """
    assert type(address) == str, f"address is {type(address)}, expected str"
    registry = get_or_create_contract_registry(web3)
    registry[address.lower()] = instance


def get_registered_contract(web3, address: str) -> Contract:
    """Get a contract that was deployed with the registry.

    - Resolve a symbolic contract information based on the contract address and our contract registry

    - See :py:func:`eth_defi.deploy.deploy_contract` how to deploy a registered contract

    Example:

    .. code-block:: python

         from eth_defi.deploy import get_registered_contract

         contract = get_registered_contract(web3, "0x1613beb3b2c4f22ee086b2b38c1476a3ce7f78e8")
         assert contract.name == "VaultSpecificGenericAdapter"

    :param address:
        Contract address as a hex string

    :return:
        The known Contract instance at the registry or `None` if the contract was not registered/deployed through registry mechanism.
    """
    assert type(address) == str
    registry = get_or_create_contract_registry(web3)
    return registry.get(address.lower())
