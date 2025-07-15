from typing import Tuple

from eth_typing import ChecksumAddress, HexStr
from web3._utils.contracts import encode_abi
from eth_defi.compat import get_function_info, WEB3_PY_V7
from web3.contract.contract import ContractFunction


def encode_simple_vault_transaction(func: ContractFunction) -> Tuple[ChecksumAddress, HexStr]:
    """Encode a bound web3 function call as a simple vault transaction.

    :param call:
        Bound function prepared for a call.

    :return:
        Address, call data tuple.
    """
    assert isinstance(func, ContractFunction)

    w3 = func.w3
    contract_abi = func.contract_abi
    fn_abi = func.abi
    if WEB3_PY_V7:
        fn_identifier = func.abi_element_identifier
    else:
        fn_identifier = func.function_identifier
    args = func.args
    fn_abi, fn_selector, fn_arguments = get_function_info(
        # type ignored b/c fn_id here is always str b/c FallbackFn is handled above
        fn_identifier,  # type: ignore
        w3.codec,
        contract_abi,
        fn_abi,
        args,
    )
    encoded = encode_abi(w3, fn_abi, fn_arguments, fn_selector)
    return func.address, encoded
