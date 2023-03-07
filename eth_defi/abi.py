"""ABI loading from the precompiled bundle.

`See Github for available contracts <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/eth_defi/abi>`_.
"""
import json
from pathlib import Path
from typing import Optional, Type, Union, Collection, Any

import eth_abi
from eth_typing import HexAddress
from web3 import Web3
from web3._utils.contracts import get_function_info
from web3.contract.contract import Contract, ContractFunction

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

    if bytecode is None:
        # Pick up bytecode from ABI description
        bytecode = contract_interface["bytecode"]
        if type(bytecode) == dict:
            # Sol 0.8 / Forge?
            # Contains keys object, sourceMap, linkReferences
            bytecode = bytecode["object"]
        else:
            # Sol 0.6 / legacy
            # Bytecode hex is directly in the key.
            pass

    Contract = web3.eth.contract(abi=abi, bytecode=bytecode)
    return Contract


def get_deployed_contract(
    web3: Web3,
    fname: str,
    address: Union[HexAddress, str],
) -> Contract:
    """Get a Contract proxy objec for a contract deployed at a specific address.

    `See Web3.py documentation on Contract instances <https://web3py.readthedocs.io/en/stable/contracts.html#contract-deployment-example>`_.

    :param web3:
        Web3 instance

    :param fname:
        `JSON filename from supported contract lists <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/eth_defi/abi>`_.

    :param address:
        Ethereum address of the deployed contract

    :return:
        `web3.contract.Contract` proxy
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


def encode_with_signature(function_signature: str, args: Collection[Any]) -> bytes:
    """Mimic Solidity's abi.encodeWithSignature() in Python.

    This is a Python equivalent for `abi.encodeWithSignature()`.

    Example:

    .. code-block:: python

            payload = encode_with_signature("init(address)", [my_address])
            assert type(payload) == bytes

    :param function_signature:
        Solidity function signature that can be hashed to a selector.

        ABI fill be extractd from this signature.

    :param args:
        Argument values to be encoded.
    """

    assert type(args) in (tuple, list)

    function_selector = Web3.keccak(text=function_signature)
    selector_text = function_signature[function_signature.find("(")+1:function_signature.rfind(")")]
    arg_types = selector_text.split(",")
    encoded_args = eth_abi.encode(arg_types, args)
    return function_selector + encoded_args


def encode_function_args(
        func: ContractFunction,
        args: Collection[any]
) -> bytes:
    """Mimic Solidity's abi.encodeWithSignature() in Python.

    Uses `web3.Contract.functions` prepared function as the ABI source.

    :param func:
        Function which arguments we are going to encode.

    :param args:
        Argument values to be encoded.
    """
    assert isinstance(func, ContractFunction)

    web3 = func.w3

    fn_abi, fn_selector, aligned_fn_arguments = get_function_info(
        func.fn_name,
        web3.codec,
        func.contract_abi,
        args=args,
    )
    arg_types = [t["type"] for t in fn_abi["inputs"]]
    encoded_args = eth_abi.encode(arg_types, args)
    return encoded_args
