"""ABI loading from the precompiled bundle.

Provides functions to load ABI files and construct :py:class:`web3.contract.Contract` types.
The results are cached for the speedup.

We also provide some helper functions to deal with ABI encode/decode.

`See Github for available contracts ABI files <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/eth_defi/abi>`_.
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence, Type, Union, Any

import eth_abi
from eth_abi import decode
from eth_typing import HexAddress, HexStr
from eth_utils import encode_hex, function_abi_to_4byte_selector
from web3._utils.contracts import encode_abi
from eth_utils.abi import event_abi_to_log_topic
from eth_defi.compat import abi_to_signature, get_function_info, WEB3_PY_V7
from hexbytes import HexBytes
from web3 import Web3
from web3._utils.abi import get_abi_input_names, get_abi_input_types
from web3.contract.contract import Contract, ContractFunction, ContractEvent

# Cache loaded ABI files in-process memory for speedup
from web3.datastructures import AttributeDict

# How big are our ABI and contract caches
_CACHE_SIZE = 512


#: Ethereum 0x0000000000000000000000000000000000000000 address as a string.
#:
#: Legacy. Use one below.
#:
ZERO_ADDRESS_STR = "0x0000000000000000000000000000000000000000"

#: Ethereum 0x0000000000000000000000000000000000000000 address
ZERO_ADDRESS = ZERO_ADDRESS_STR

#: Used by Gnosis Safe in linked lists
#: https://github.com/safe-global/safe-smart-account/pull/993
ONE_ADDRESS_STR = "0x0000000000000000000000000000000000000001"


@lru_cache(maxsize=_CACHE_SIZE)
def get_abi_by_filename(fname: str) -> dict:
    """Reads a embedded ABI file and returns it.

    Example::

        abi = get_abi_by_filename("ERC20Mock.json")

    You are most likely interested in the keys `abi` and `bytecode` of the JSON file.

    Loaded ABI files are cache in in-process memory to speed up future loading.

    Any results are cached.

    :param web3: Web3 instance
    :param fname: `JSON filename from supported contract lists <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/eth_defi/abi>`_.
    :return: Full contract interface, including `bytecode`.
    """

    here = Path(__file__).resolve().parent
    abi_path = here / "abi" / Path(fname)
    with open(abi_path, "rt", encoding="utf-8") as f:
        abi = json.load(f)
    return abi


@lru_cache(maxsize=_CACHE_SIZE)
def get_contract(
    web3: Web3,
    fname: str | Path,
    bytecode: Optional[str] = None,
) -> Type[Contract]:
    """Get Contract proxy class from ABI JSON file.

    Read ABI file from
    - Our bundled contracts in the Python package
    - Filesystem using absolute path
    - ABI file can be a solc compiling artifact or Etherscan copy-pasted ABI.

    `See Web3.py documentation on Contract instances <https://web3py.readthedocs.io/en/stable/contracts.html#contract-deployment-example>`_.

    Any results are cached. Web3 connection is part of the cache key.

    Example:

    .. code-block:: python

        IERC20 = get_contract(web3, "sushi/IERC20.json")

    :param web3:
        Web3 instance

    :param bytecode:
        Override bytecode payload for the contract

    :param fname:
        Solidity compiler artifact.

        Use slash prefixed path for absolute lookups.

        `JSON filename from supported contract lists <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/eth_defi/abi>`_.

    :return:
        Contract proxy class
    """

    contract_interface = get_abi_by_filename(fname)

    if type(contract_interface) == list:
        # Etherscan
        abi = contract_interface
        bytecode = None
    else:
        # Solc output
        abi = contract_interface["abi"]

        if bytecode is None:
            bytecode = contract_interface.get("bytecode")

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


def get_linked_contract(
    web3: Web3,
    fname: str | Path,
    hardhat_export_data: Optional[dict] = None,
) -> Type[Contract]:
    """Create a Contract proxy class from our bundled contracts or filesystem and links it Solidity bytecode.

    Needed when contracts contain references to libraries. The contract bytecode
    must be processed and placeholders must be replaced by the on-chain addresses
    of the deployed library contracts.

    Example:

    .. code-block:: python

        path = self.path.joinpath("artifacts/@aave/core-v3/contracts/mocks/tokens/MintableERC20.sol/MintableERC20.json")
        return get_linked_contract(web3, path, get_aave_hardhard_export())

    .. note ::

        If you do not need linking use :py:func:`get_contract` which is faster.

    :param web3:
        Web3 instance

    :param fname:
        Solidity compiler artifact.

        Use slash prefixed path for absolute lookups.

        `JSON filename from supported contract lists <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/eth_defi/abi>`_.

    :param hardhat_export_data:
        Hardhat deployment export data to link bytecode.

        A JSON file generated by `hardhat deploy --export` command.

    :return:
        Contract proxy class
    """

    contract_interface = get_abi_by_filename(fname)
    abi = contract_interface["abi"]
    bytecode = contract_interface["deployedBytecode"]

    link_references = contract_interface["linkReferences"]
    bytecode = link_libraries_hardhat(bytecode, link_references, hardhat_export_data)

    Contract = web3.eth.contract(abi=abi, bytecode=bytecode)
    return Contract


def get_deployed_contract(
    web3: Web3,
    fname: str | Path,
    address: Union[HexAddress, str],
    register_for_tracing: bool = True,
) -> Contract:
    """Get a Contract proxy objec for a contract deployed at a specific address.

    `See Web3.py documentation on Contract instances <https://web3py.readthedocs.io/en/stable/contracts.html#contract-deployment-example>`_.

    :param web3:
        Web3 instance

    :param fname:
        `JSON filename from supported contract lists <https://github.com/tradingstrategy-ai/web3-ethereum-defi/tree/master/eth_defi/abi>`_.

    :param address:
        Ethereum address of the deployed contract

    :param register_for_tracing:
        Add the contract to the deployment registry if not already there.

    :return:
        `web3.contract.Contract` proxy
    """
    assert isinstance(web3, Web3), f"Got {type(web3)} instead of Web3"
    assert address, f"get_deployed_contract() address was None"

    address = Web3.to_checksum_address(address)

    Contract = get_contract(web3, fname)
    contract = Contract(address)

    if register_for_tracing:
        # TODO: Currently hack around circular imports, move functoins
        from eth_defi.deploy import get_registered_contract, register_contract

        registered_contract = get_registered_contract(web3, address)
        if registered_contract is None:
            register_contract(web3, address, contract)

    return contract


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


def encode_with_signature(function_signature: str, args: Sequence) -> bytes:
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
    selector_text = function_signature[function_signature.find("(") + 1 : function_signature.rfind(")")]
    arg_types = selector_text.split(",")
    encoded_args = eth_abi.encode(arg_types, args)
    return function_selector + encoded_args


def decode_function_output(func: ContractFunction, data: bytes) -> Any:
    """Decode raw return value of Solidity function using Contract proxy object.

    Uses `web3.Contract.functions` prepared function as the ABI source.

    :param func:
        Function which arguments we are going to encode.

        Must be bound.

    :param result:
        Raw encoded Solidity bytes.
    """
    assert isinstance(func, ContractFunction)

    web3 = func.w3

    fn_abi, fn_selector, aligned_fn_arguments = get_function_info(
        func.fn_name,
        web3.codec,
        func.contract_abi,
        args=func.args,
    )
    arg_types = [t["type"] for t in fn_abi["outputs"]]
    decoded_out = eth_abi.decode(arg_types, data)
    return decoded_out


def encode_function_call(
    func: ContractFunction,
    args: Sequence | None = None,
) -> HexBytes:
    """Encode function selector + its arguments as data payload.

    Uses `web3.Contract.functions` prepared function as the ABI source.

    See also :py:func:`encode_function_args`.

    Example:


    :param func:
        Function which arguments we are going to encode.

    :param args:
        Argument values to be encoded.

        If not given, take bound args from the function.

    :return:
        Solidity's function selector + argument payload.

    """
    w3 = func.w3
    contract_abi = func.contract_abi
    fn_abi = func.abi

    if args is None:
        args = func.args
        assert args is not None, f"Function {func.fn_name} has no bound arguments, please provide args explicitly or bind them to ContractFunction object"

    if WEB3_PY_V7:
        fn_identifier = func.abi_element_identifier
    else:
        fn_identifier = func.function_identifier

    fn_abi, fn_selector, fn_arguments = get_function_info(
        # type ignored b/c fn_id here is always str b/c FallbackFn is handled above
        fn_identifier,  # type: ignore
        w3.codec,
        contract_abi,
        fn_abi,
        args,
    )
    try:
        encoded = encode_abi(w3, fn_abi, fn_arguments, fn_selector)
    except Exception as e:
        raise RuntimeError(f"Could not encode ABI: {fn_abi}, args: {fn_arguments}") from e
    return HexBytes(encoded)


def decode_function_args(
    func: ContractFunction,
    data: bytes | HexBytes,
) -> dict:
    """Decode binary CALL or CALLDATA to a Solidity function,

    Uses `web3.Contract.functions` prepared function as the ABI source.

    :param func:
        Function which arguments we are going to encode.

    :param data:
        Extracted from a transaction data field or EVM memoryo trace.

    :return:
        Ordered dict of the decoded arguments
    """
    assert isinstance(func, ContractFunction)
    fn_abi = func.abi

    arg_names = get_abi_input_names(fn_abi)
    arg_types = get_abi_input_types(fn_abi)
    arg_tuple = decode(arg_types, data)

    return dict(zip(arg_names, arg_tuple))


def humanise_decoded_arg_data(args: dict) -> dict:
    """Make decoded arguments more human readable.

    - All arguments are converted to good text types

    See :py:func:`decode_function_args`

    :return:
        Ordered dict of decoded arguments, easier to read
    """

    def _humanize(v):
        if type(v) == bytes:
            return v.hex()
        return v

    return {k: _humanize(v) for k, v in args.items()}


def link_libraries_hardhat(bytecode: str, link_references: dict, hardhat_export: dict):
    """Link Solidity libraries based on Hardhat deployment.

    .. warning ::

        Preliminary implementation

    See :py:func:`get_linked_contract` for details.

    :param bytecode:
        Raw bytecode of a Solidity contract.

        Get from ABI file.

        Bytecode must be a in string format, because placeholders are not parseable hex.

    :param link_references:
        List of binary sequences we need to replaced by a contract filename.

        Get from ABI file.

    :param hardhat_export:
        Hardhat's export format.

        You get with `hardhat deploy --export`.

    :return:
        Linked bytecode
    """

    assert type(bytecode) == str, f"Got {type(bytecode)}"

    assert bytecode.startswith("0x")

    hex_blob = bytecode[2:]

    # Remove placeholders and replace them with zeroes,
    # so that we can convert the bytecode to binary
    # https://stackoverflow.com/a/16160048/315168
    zeroes = str(ZERO_ADDRESS_STR)[2:]
    fixed_hex_blob = re.sub(r"__\$(.*?)\$__", zeroes, hex_blob, flags=re.DOTALL)

    data = bytearray.fromhex(fixed_hex_blob)

    def _get_contract_address(name: str):
        contracts = hardhat_export["contracts"]
        contract = contracts.get(name)
        assert contract, f"Hardhat export does not contain a contract entry for {name}"
        return contract["address"]

    for ref_file, ref_data in link_references.items():
        # 'contracts/protocol/libraries/logic/BorrowLogic.sol': {'BorrowLogic': [{'length': 20, 'start': 4405}, {'length': 20, 'start': 5081}, {'length': 20, 'start': 7441}, {'length': 20, 'start': 7604}, {'length': 20, 'start': 10111}, {'length': 20, 'start': 12083}]},
        for contract_name, ref_array in ref_data.items():
            address = _get_contract_address(contract_name)
            byte_address = bytes.fromhex(address[2:])
            for ref in ref_array:
                start = ref["start"]
                length = ref["length"]
                data[start : start + length] = byte_address
                # print(f"Linking {contract_name} {start} {address}")

    return data


def get_function_selector(func: ContractFunction) -> bytes:
    """Get Solidity function selector.

    Does not support multiple Solidity functions with the same name, but
    different arguments. On multiple functions
    use one first declared in ABI.

    Example:

    .. code-block:: python

        selector = get_function_selector(uniswap_v2.router.functions.swapExactTokensForTokens)
        assert selector.hex() == 38ed1739

    :param func:
        Unbound or bound contract function proxy

    :return:
        Solidity function selector.

        First 32-bit (4 bytes) keccak hash.
    """

    contract_abi = func.contract_abi
    # https://stackoverflow.com/a/8534381/315168
    fn_abi = next((a for a in contract_abi if a.get("name") == func.fn_name), None)
    assert fn_abi, f"Could not find function {func.fn_name} in Contract ABI"

    # In v7 the function_abi_to_4byte_selector method is updated to call abi_to_signature before processing the signature
    # for some reason this is not required maybe
    # if WEB3_PY_V7:
    #     function_signature = fn_abi
    # else:
    #     function_signature = abi_to_signature(fn_abi)
    # print(f"{fn_abi=}")
    fn_selector = function_abi_to_4byte_selector(fn_abi)  # type: ignore
    return fn_selector


def get_topic_signature_from_event(event: Type[ContractEvent]) -> HexStr:
    """Get topic signature for an Event class.

    :return:
        0x prefixed hex string
    """
    abi = event._get_event_abi()
    event_topic = encode_hex(event_abi_to_log_topic(abi))  # type: ignore
    return event_topic


def get_function_abi_by_name(contract: Contract, function_name: str) -> dict | None:
    """Get function ABI by its name."""
    for item in contract.abi:
        if item.get("type") == "function" and item.get("name") == function_name:
            return item
    return None  # Return None if function is not found


def _hexify(s: Any):
    if type(s) in (list, tuple):
        return str([_hexify(x) for x in s])
    if isinstance(s, str):
        return s
    elif isinstance(s, bytes):
        return "0x" + s.hex()
    return str(s)


def present_solidity_args(a: list | tuple | Any) -> str:
    """Try make Solidity call args human readable.

    Make sure we display bytes as hex.

    Example:

    .. code-block:: python

        contract_address = func_call.address
        data_payload = encode_function_call(func_call, func_call.arguments)
        logger.info(
            "Lagoon: Wrapping call to TradingStrategyModuleV0. Target: %s, function: %s (0x%s), args: %s, payload is %d bytes",
            contract_address,
            func_call.fn_name,
            get_function_selector(func_call).hex(),
            present_solidity_args(func_call.arguments),
            len(data_payload),
        )

    Output::

        Lagoon: Wrapping call to TradingStrategyModuleV0. Target: 0x5788F91Aa320e0610122fb88B39Ab8f35e50040b, function: exactInput (c04b8d59), args: ["['0x833589fcd6edb6e08f4c7c32d4f71b54bda029130001f442000000000000000000000000000000000000060027106921b130d297cc43754afba22e5eac0fbf8db75b', '0xEBee4d3fE83DD4755761C65b772f6a4f900A118b', '9223372036854775808', '10000000', '25455184317467649376256']"], payload is 324 bytes

    """
    return _hexify(a)


def format_debug_instructions(bound_call: ContractFunction, block_identifier="latest") -> str:
    """Print curl command line syntax to repeat a failed contract call.

    - Useful for sending information about broken nodes to dRPC team
    """

    if type(block_identifier) == int:
        block_identifier = hex(block_identifier)

    contract_address = bound_call.address
    data = encode_function_call(bound_call, bound_call.arguments)
    debug_template = f"""curl -X POST -H "Content-Type: application/json" \\
    --data '{{
      "jsonrpc": "2.0",
      "method": "eth_call",
      "params": [
        {{
          "to": "{contract_address}",
          "data": "{data.hex()}"
        }},
        "{block_identifier}"
      ],
      "id": 1
    }}' \\
    $JSON_RPC_URL"""
    return debug_template


def encode_multicalls(funcs: list[ContractFunction]) -> list[bytes]:
    """Encode multiple contract function calls into a single multicall payload for contract built-in multicall functionality.

    `See Uniswap V3 multicall documentation <hhttps://docs.uniswap.org/contracts/v3/reference/periphery/base/Multicall>`__.
    """
    return [encode_function_call(func) for func in funcs]
