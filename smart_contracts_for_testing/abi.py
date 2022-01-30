"""Methods for loading contracts and ABI files from the precompiled bundle.

`See Github for available contracts <https://github.com/tradingstrategy-ai/smart-contracts-for-testing/tree/master/smart_contracts_for_testing/abi>`_.
"""

import os
import os.path
import json
from typing import Type

from web3 import Web3
from web3.contract import Contract


def get_abi_by_filename(fname: str) -> dict:
    """Reads a embedded ABI file and returns it.

    Example::

        abi = get_abi_by_filename("ERC20Mock.json")

    :return: Full contract interface, including bytecode.
    """
    here = os.path.dirname(__file__)
    abi_path = os.path.join(here, "abi", fname)
    abi = json.load(open(abi_path, "rt"))
    return abi


def get_contract(web3: Web3, fname: str) -> Type[Contract]:
    """Load contract from an ABI file with bytecode enabled.

    `See Web3.py documentation on Contract instances <https://web3py.readthedocs.io/en/stable/contracts.html#contract-deployment-example>`_.
    """
    contract_interface = get_abi_by_filename(fname)
    abi = contract_interface["abi"]
    bytecode = contract_interface["bytecode"]
    contract = web3.eth.contract(abi=abi, bytecode=bytecode)
    return contract

