from typing import Tuple

from eth_typing import ChecksumAddress, HexStr
from web3._utils.contracts import encode_abi
from web3.contract.contract import ContractFunction
from web3.utils import get_abi_element_info


def encode_simple_vault_transaction(func: ContractFunction) -> Tuple[ChecksumAddress, HexStr]:
    """Encode a bound web3 function call as a simple vault transaction.

    :param func:
        Bound function prepared for a call.

    :return:
        Address, call data tuple.
    """
    assert isinstance(func, ContractFunction)

    w3 = func.w3
    contract_abi = func.contract_abi
    fn_abi = func.abi
    fn_identifier = func.function_identifier
    args = func.args

    if fn_abi:
        # If we already have the function ABI, get the selector
        fn_info = get_abi_element_info(
            contract_abi,
            fn_identifier,
            *args,
            abi_codec=w3.codec
        )
        fn_selector = fn_info["selector"]
        fn_arguments = args
    else:
        # Get full function info
        fn_info = get_abi_element_info(
            contract_abi,
            fn_identifier,
            *args,
            abi_codec=w3.codec
        )
        fn_abi = fn_info["abi"]
        fn_selector = fn_info["selector"]
        fn_arguments = fn_info["arguments"]

    encoded = encode_abi(w3, fn_abi, fn_arguments, fn_selector)
    return func.address, encoded
