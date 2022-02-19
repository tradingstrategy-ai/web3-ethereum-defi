"""Deploy any precompiled contract.

`See Github for available contracts <https://github.com/tradingstrategy-ai/eth-hentai/tree/master/eth_hentai/abi>`_.
"""

from web3 import Web3
from web3.contract import Contract

from eth_hentai.abi import get_contract


def deploy_contract(web3: Web3, fname: str, deployer: str, *constructor_args) -> Contract:
    """Deploys a new contract from ABI file.

    A generic helper function to deploy any contract.

    Example:

    .. code-block:: python

        token = deploy_contract(web3, deployer, "ERC20Mock.json", name, symbol, supply)
        print(f"Deployed ERC-20 token at {token.address}")

    """
    Contract = get_contract(web3, fname)
    tx_hash = Contract.constructor(*constructor_args).transact({"from": deployer})
    tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    instance = Contract(
        address=tx_receipt.contractAddress,
    )
    return instance
