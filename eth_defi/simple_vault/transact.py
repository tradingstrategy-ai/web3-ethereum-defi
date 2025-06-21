from typing import Tuple

from eth_typing import ChecksumAddress, HexStr
from web3._utils.contracts import encode_abi
from web3.contract.contract import ContractFunction
from web3.utils import get_abi_element_info


def encode_simple_vault_transaction(func: ContractFunction) -> Tuple[ChecksumAddress, HexStr]:
    """Simpler version using just function name instead of full signature"""
    assert isinstance(func, ContractFunction)

    w3 = func.w3
    contract_abi = func.contract_abi
    fn_abi = func.abi

    # SIMPLIFIED: Use fn_name instead of function_identifier
    fn_name = func.fn_name
    args = func.args

    if fn_abi:
        # If we already have the function ABI, get the selector
        fn_info = get_abi_element_info(
            contract_abi,
            fn_name,  # Use function name instead of full signature
            *args,
            abi_codec=w3.codec
        )
        fn_selector = fn_info["selector"]
        fn_arguments = args
    else:
        # Get full function info
        fn_info = get_abi_element_info(
            contract_abi,
            fn_name,  # Use function name instead of full signature
            *args,
            abi_codec=w3.codec
        )
        fn_abi = fn_info["abi"]
        fn_selector = fn_info["selector"]
        fn_arguments = fn_info["arguments"]

    encoded = encode_abi(w3, fn_abi, fn_arguments, fn_selector)
    return func.address, encoded
