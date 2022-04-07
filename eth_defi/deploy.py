"""Deploy any precompiled contract.

`See Github for available contracts <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/eth_defi/abi>`_.
"""

from typing import Union

from web3 import Web3
from web3.contract import Contract

from eth_defi.abi import get_contract


def deploy_contract(web3: Web3, contract: Union[str, Contract], deployer: str, *constructor_args) -> Contract:
    """Deploys a new contract from ABI file.

    A generic helper function to deploy any contract.

    Example:

    .. code-block:: python

        token = deploy_contract(web3, deployer, "ERC20Mock.json", name, symbol, supply)
        print(f"Deployed ERC-20 token at {token.address}")

    :param web3: Web3 instance
    :param contract: Contract file path as string or contract proxy class
    :param deployer: Deployer account
    :param constructor_args: Other arguments to pass to the contract's constructor
    :return: Contract instance

    """
    if isinstance(contract, str):
        Contract = get_contract(web3, contract)
    else:
        Contract = contract
    tx_hash = Contract.constructor(*constructor_args).transact({"from": deployer})
    tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    instance = Contract(
        address=tx_receipt.contractAddress,
    )
    return instance
