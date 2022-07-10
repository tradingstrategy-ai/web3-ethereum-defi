"""ABI loading from the precompiled bundle.

`See Github for available contracts <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/eth_defi/abi>`_.
"""
import json
from pathlib import Path
from typing import Optional, Type, Union

from eth_typing import HexAddress
from web3 import Web3
from web3.contract import Contract

# Cache loaded ABI files in-process memory for speedup
from web3.datastructures import AttributeDict

_cache = {}


def get_abi_by_filename(fname: str) -> dict:
    """Reads a embedded ABI file and returns it.

    Example::

        abi = get_abi_by_filename("ERC20Mock.json")

    You are most likely interested in the keys `abi` and `bytecode` of the JSON file.

    Loaded ABI files are cache in in-process memory to speed up future loading.

    :param web3: Web3 instance
    :param fname: `JSON filename from supported contract lists <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/eth_defi/abi>`_.
    :return: Full contract interface, including `bytecode`.
    """

    if fname in _cache:
        return _cache[fname]

    here = Path(__file__).resolve().parent
    abi_path = here / "abi" / Path(fname)
    with open(abi_path, "rt", encoding="utf-8") as f:
        abi = json.load(f)
    _cache[fname] = abi

    return abi


def get_contract(web3: Web3, fname: str, bytecode: Optional[str] = None) -> Type[Contract]:
    """Create a Contract proxy class from our bundled contracts.

    `See Web3.py documentation on Contract instances <https://web3py.readthedocs.io/en/stable/contracts.html#contract-deployment-example>`_.

    :param web3: Web3 instance
    :param bytecode: Override bytecode payload for the contract
    :param fname: `JSON filename from supported contract lists <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/eth_defi/abi>`_.
    :return: Python class
    """
    contract_interface = get_abi_by_filename(fname)
    abi = contract_interface["abi"]
    bytecode = bytecode if bytecode is not None else contract_interface["bytecode"]
    Contract = web3.eth.contract(abi=abi, bytecode=bytecode)
    return Contract


def get_deployed_contract(
    web3: Web3,
    fname: str,
    address: Union[HexAddress, str],
) -> Contract:
    """Get a Contract proxy objec for a contract deployed at a specific address.

    `See Web3.py documentation on Contract instances <https://web3py.readthedocs.io/en/stable/contracts.html#contract-deployment-example>`_.

    :param web3: Web3 instance
    :param fname: `JSON filename from supported contract lists <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/eth_defi/abi>`_.
    :param address: Ethereum address of the deployed contract
    :return: `web3.contract.Contract` subclass
    """
    assert address
    Contract = get_contract(web3, fname)
    return Contract(address)


def get_transaction_data_field(tx: AttributeDict) -> str:
    """Get the "Data" payload of a transaction.

    Ethereum Tester has this in tx.data while Ganache has this in tx.input.
    Yes, it is madness.

    Example:

    .. code-block::

        tx = web3.eth.get_transaction(tx_hash)
        function, input_args = router.decode_function_input(get_transaction_data_field(tx))
        print("Transaction {tx_hash} called function {function}")

    """
    if "data" in tx:
        return tx["data"]
    else:
        return tx["input"]
