"""Symbolic transaction tracing and Solidity stack traces.

- This code is very preliminary and has not been througly tested with different smart contracts,
  so patches welcome

- Internally use evm-trace from Ape: https://github.com/ApeWorX/evm-trace
"""
import enum
import logging
from typing import cast, Optional, Iterator

from eth_typing import ChecksumAddress
from eth_utils import to_checksum_address

from eth_defi.abi import decode_function_args, humanise_decoded_arg_data
from eth_defi.deploy import ContractRegistry, get_or_create_contract_registry
from hexbytes import HexBytes
from web3 import Web3

from evm_trace import TraceFrame, CallTreeNode, ParityTraceList, get_calltree_from_parity_trace
from evm_trace import CallType, get_calltree_from_geth_trace

from eth_defi.revert_reason import fetch_transaction_revert_reason

logger = logging.getLogger(__name__)


class TraceNotEnabled(Exception):
    """Tracing is not enabled on the backend."""



class TraceMethod(enum.Enum):
    """What kind of transaction tracing method we use.

    See Anvil manual: https://book.getfoundry.sh/reference/anvil/
    """

    #: Use debug_traceTransaction
    geth = "geth"

    #: Use trace_transaction
    parity = "parity"


def trace_evm_transaction(
        web3: Web3,
        tx_hash: HexBytes | str,
        trace_method: TraceMethod = TraceMethod.parity,
) -> CallTreeNode:
    """Trace a (failed) transaction.

    - See :py:func:`print_symbolic_trace` for usage

    - Extract an EVM transaction stack trace from a node, using GoEthereum compatible `debug_traceTransaction`

    - Currently only works with Anvil backend and if `steps_trace=True`

    :param web3:
        Anvil connection

    :param tx_hash:
        Transaction to trace

    :param trace_method:
        How to trace.

        Choose between `debug_traceTransaction` and `trace_transaction` RPCs.
    """

    if type(tx_hash) == HexBytes:
        tx_hash = tx_hash.hex()

    match trace_method:
        case TraceMethod.geth:
            trace_dump = web3.manager.request_blocking("debug_traceTransaction", [tx_hash], {"enableMemory": True, "enableReturnData": True})
            struct_logs = trace_dump["structLogs"]

            if not struct_logs:
                raise TraceNotEnabled(f"Tracing not enabled on the backend {web3.provider}.\n" f"If you are using anvil make sure you start with --steps-trace")

            tx = web3.eth.get_transaction(tx_hash)

            # https://github.com/ApeWorX/ape/blob/f303e74addf601b09fe2cf0f23f6c51eb8a330e7/src/ape_geth/provider.py#L420
            root_node_kwargs = {
                "gas_cost": tx["gas"],
                "address": tx["to"],
                "calldata": tx.get("input", ""),
                "call_type": CallType.CALL,
            }

            if "value" in tx:
                root_node_kwargs["value"] = tx["value"]

            if len(struct_logs) == 0:
                raise RuntimeError("struct_logs empty")

            frames = [TraceFrame.parse_obj(item) for item in struct_logs]

            logger.debug("Tracing %d frames", len(frames))

            calltree = get_calltree_from_geth_trace(iter(frames), **root_node_kwargs)
        case TraceMethod.parity:
            trace_dump = web3.manager.request_blocking("trace_transaction", [tx_hash])
            trace_list = ParityTraceList.parse_obj(trace_dump)
            calltree = get_calltree_from_parity_trace(trace_list)
        case _:
            raise RuntimeError("Unsupported method")

    return calltree


def print_symbolic_trace(
    contract_registry: ContractRegistry,
    calltree: CallTreeNode,
):
    """Print a symbolic trace of an Ethereum transaction.

    - Contracts by name

    - Functions by name

    Notes about tracing:

    - Currently only works with Anvil backend and if `steps_trace=True`

    - Transaction must have its `gas` parameter set, otherwise transaction is never broadcasted
      because it fails in estimate gas phase

    See also

    - :py:func:`eth_defi.anvil.launch_anvil`.

    Usage example:

    .. code-block:: python

        reverter = deploy_contract(web3, "RevertTest.json", deployer)

        tx_hash = reverter.functions.revert1().transact({"from": deployer, "gas": 500_000})
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
        assert receipt["status"] == 0  # Tx failed

        # Get the debug trace from the node and transform it to a list of call items
        trace_data = trace_evm_transaction(web3, tx_hash)

        # Transform the list of call items to a human-readable output,
        # use ABI data from deployed contracts to enrich the output
        trace_output = print_symbolic_trace(get_or_create_contract_registry(web3), trace_data)

        assert trace_output == 'CALL: [reverted] RevertTest.<revert1> [500000 gas]'

    :param contract_registry:
        The registered contracts for which we have symbolic information available.

        See :py:func:`eth_defi.deploy.deploy_contract` for registering.
        All contracts deployed using this function should be registered by default.

    :param calltree:
        Call tree output.

        From :py:func:`trace_evm_transaction`.

    :return:
        Unicode print output

    """
    return SymbolicTreeRepresentation.get_tree_display(contract_registry, calltree)


class SymbolicTreeRepresentation:
    """A EVM trace tree that can resolve contract names and functions.

    Lifted from `eth_trace.display` module.

    See :py:func:`print_symbolic_trace` for more information.
    """

    # See https://github.com/ApeWorX/evm-trace/blob/main/evm_trace/display.py#L14 for sources

    FILE_MIDDLE_PREFIX = "├──"
    FILE_LAST_PREFIX = "└──"
    PARENT_PREFIX_MIDDLE = "    "
    PARENT_PREFIX_LAST = "│   "

    def __init__(
        self,
        contract_registry: ContractRegistry,
        call: "CallTreeNode",
        parent: Optional["SymbolicTreeRepresentation"] = None,
        is_last: bool = False,
    ):
        self.call = call
        self.contract_registry = contract_registry
        self.parent = parent
        self.is_last = is_last

    @property
    def depth(self) -> int:
        return self.call.depth

    @property
    def title(self) -> str:
        call_type = self.call.call_type.value
        address_hex_str = self.call.address.hex() if self.call.address else None

        try:
            address = to_checksum_address(address_hex_str) if address_hex_str else None
        except (ImportError, ValueError):
            # Ignore checksumming if user does not have eth-hash backend installed.
            address = cast(ChecksumAddress, address_hex_str)

        contract = self.contract_registry.get(address)

        function_selector = self.call.calldata[:4]

        symbolic_name = None
        symbolic_function = None
        symbolic_args = "<unknown>"

        if contract:
            # Set in deploy_contract()
            symbolic_name = getattr(contract, "name", None)

            function = None
            if function_selector != "0x":
                try:
                    function = contract.get_function_by_selector(function_selector)
                except ValueError as e:
                    function = None

            if function is not None:
                symbolic_function = function.fn_name
                arg_payload = self.call.calldata[4:]

                args = decode_function_args(function, arg_payload)
                human_args = humanise_decoded_arg_data(args)
                symbolic_args = ", ".join([f"{k}={v}" for k, v in human_args.items()])

        symbolic_name = symbolic_name or address
        symbolic_function = symbolic_function or function_selector.hex()

        cost = self.call.gas_cost
        call_path = symbolic_name if address else ""
        if self.call.calldata:
            call_path = f"{call_path}" if call_path else ""
            call_path = f"{call_path}.{symbolic_function}({symbolic_args})"

        call_path = f"[reverted] {call_path}" if self.call.failed and self.parent is None else call_path
        call_path = call_path.strip()
        node_title = f"{call_type}: {call_path}" if call_path else call_type
        if cost is not None:
            node_title = f"{node_title} [{cost} gas]"

        return node_title

    @classmethod
    def make_tree(
        cls,
        contract_registry: ContractRegistry,
        root: "CallTreeNode",
        parent: Optional["SymbolicTreeRepresentation"] = None,
        is_last: bool = False,
    ) -> Iterator["SymbolicTreeRepresentation"]:
        displayable_root = cls(contract_registry, root, parent=parent, is_last=is_last)
        yield displayable_root

        count = 1
        for child_node in root.calls:
            is_last = count == len(root.calls)
            if child_node.calls:
                yield from cls.make_tree(contract_registry, child_node, parent=displayable_root, is_last=is_last)
            else:
                yield cls(contract_registry, child_node, parent=displayable_root, is_last=is_last)

            count += 1

    def __str__(self) -> str:
        if self.parent is None:
            return self.title

        filename_prefix = self.FILE_LAST_PREFIX if self.is_last else self.FILE_MIDDLE_PREFIX

        parts = [f"{filename_prefix} {self.title}"]
        parent = self.parent
        while parent and parent.parent is not None:
            parts.append(self.PARENT_PREFIX_MIDDLE if parent.is_last else self.PARENT_PREFIX_LAST)
            parent = parent.parent

        return "".join(reversed(parts))

    @staticmethod
    def get_tree_display(contract_registry: ContractRegistry, call: "CallTreeNode") -> str:
        return "\n".join([str(t) for t in SymbolicTreeRepresentation.make_tree(contract_registry, call)])


def assert_transaction_success_with_explanation(
        web3: Web3,
        tx_hash: HexBytes,
):
    """Checks if a transaction succeeds and give a verbose explanation why not..

    Designed to  be used on Anvil backend based tests.

    If it's a failure then print

    - The revert reason string

    - Solidity stack trace where the transaction reverted

    Example usage:

    .. code-block:: python

        tx_hash = contract.functions.myFunction().transact({"from": fund_owner, "gas": 1_000_000})
        assert_transaction_success_with_explaination(web3, tx_hash)

    Example output:

    .. code-block:: text

            E           AssertionError: Transaction failed: AttributeDict({'hash': HexBytes('0x1c29912e0821dc8c0dc744e40c9918ad4ed8e69015e1b22523b93abd2d706e26'), 'nonce': 0, 'blockHash': HexBytes('0x3c66ec2159e93277856df58252e08293222750149c44fc71b105b16a5346b914'), 'blockNumber': 44, 'transactionIndex': 0, 'from': '0x70997970C51812dc3A010C7d01b50e0d17dc79C8', 'to': '0x2b961E3959b79326A8e7F64Ef0d2d825707669b5', 'value': 0, 'gasPrice': 7512046, 'gas': 1000000, 'input': '0x39bf70d10000000000000000000000004ed7c70f96b99c776995fb64377f0d4ab3b0e1c10000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000006000000000000000000000000000000000000000000000000000000000000004c000000000000000000000000095401dc811bb5740090279ba06cfa8fcf6113778b7fe1a11000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000060000000000000000000000000000000000000000000000000000000000000044000000000000000000000000000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000000000000e00000000000000000000000000000000000000000000000000000000000000120000000000000000000000000000000000000000000000000000000000000016000000000000000000000000000000000000000000000000000000000000001a00000000000000000000000000000000000000000000000000000000000000001000000000000000000000000e7f1725e7734ce288f8367e1bb143e90bb3f05120000000000000000000000000000000000000000000000000000000000000001000000000000000000000000000000000000000000000000014bd1d08e6fe01800000000000000000000000000000000000000000000000000000000000000010000000000000000000000005fc8d32690cc91d4c39d9d3abcbd16989f87570700000000000000000000000000000000000000000000000000000000000000010000000000000000000000000000000000000000000000000000000008f0d1800000000000000000000000000000000000000000000000000000000000000280000000000000000000000000000000000000000000000000000000000000004000000000000000000000000000000000000000000000000000000000000000a00000000000000000000000000000000000000000000000000000000000000002000000000000000000000000e7f1725e7734ce288f8367e1bb143e90bb3f05120000000000000000000000009fe46736679d2d9a65f0992f2272de9f3c7fa6e00000000000000000000000000000000000000000000000000000000000000002000000000000000000000000000000000000000000000000000000000000004000000000000000000000000000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000000000000400000000000000000000000009fe46736679d2d9a65f0992f2272de9f3c7fa6e00000000000000000000000000000000000000000000000000000000008f0d18000000000000000000000000000000000000000000000000000000000000001000000000000000000000000000000000000000000000000000000000008f0d180000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000000000000000000000000000000a000000000000000000000000095401dc811bb5740090279ba06cfa8fcf6113778000000000000000000000000000000000000000000000000800000000000000000000000000000000000000000000000000000000000000000000000000000020000000000000000000000005fc8d32690cc91d4c39d9d3abcbd16989f875707000000000000000000000000e7f1725e7734ce288f8367e1bb143e90bb3f0512', 'v': 0, 'r': HexBytes('0x48e86bc7c5b224201033b7aecbd13d68e2cd17b5622032cb4c761e769487abb4'), 's': HexBytes('0x6b7dabedca1ed8f6d21e1719f2a18b0867755eb45f4c5abaae83ce75121ec3e6'), 'type': 2, 'accessList': [], 'maxPriorityFeePerGas': 0, 'maxFeePerGas': 1007512046, 'chainId': 31337})
            E           Revert reason: execution reverted:�y�
            E           Solidity stack trace: CALL: [reverted] 0x2b961E3959b79326A8e7F64Ef0d2d825707669b5.<0x39bf70d1> [1000000 gas]
            E           └── DELEGATECALL: enzyme/ComptrollerLib
            E               ├── STATICCALL: enzyme/GasRelayPaymasterFactory
            E               ├── STATICCALL: enzyme/GasRelayPaymasterLib
            E               └── CALL: enzyme/IntegrationManager
            E                   ├── STATICCALL: 0x6F1216D1BFe15c98520CA1434FC1d9D57AC95321
            E                   │   └── DELEGATECALL: enzyme/VaultLib
            E                   ├── STATICCALL: 0x6F1216D1BFe15c98520CA1434FC1d9D57AC95321
            E                   │   └── DELEGATECALL: enzyme/VaultLib
            E                   ├── STATICCALL: enzyme/GenericAdapter
            E                   ├── STATICCALL: enzyme/ValueInterpreter
            E                   ├── STATICCALL: WETH9Mock
            E                   ├── STATICCALL: ERC20MockDecimals
            E                   ├── CALL: 0x2b961E3959b79326A8e7F64Ef0d2d825707669b5
            E                   │   └── DELEGATECALL: enzyme/ComptrollerLib
            E                   │       └── CALL: 0x6F1216D1BFe15c98520CA1434FC1d9D57AC95321
            E                   │           └── DELEGATECALL: enzyme/VaultLib
            E                   │               └── CALL: ERC20MockDecimals
            E                   └── CALL: enzyme/GenericAdapter
            E                       └── CALL: WETH9Mock

    See also :py:func:`print_symbolic_trace`.

    :param web3:
        Web3 instance

    :param tx_hash:
        A transaction (mined/not mined) we want to make sure has succeeded.

        Gas limit must have been set for this transaction.

    :raise AssertionError:
        Outputs a verbose AssertionError on what went wrong.
    """

    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt["status"] == 0:
        # Explain why the transaction failed
        tx_details = web3.eth.get_transaction(tx_hash)
        revert_reason = fetch_transaction_revert_reason(web3, tx_hash)
        trace_data = trace_evm_transaction(web3, tx_hash, TraceMethod.parity)
        trace_output = print_symbolic_trace(get_or_create_contract_registry(web3), trace_data)
        raise AssertionError(
            f"Transaction failed: {tx_details}\n"
            f"Revert reason: {revert_reason}\n"
            f"Solidity stack trace: {trace_output}\n"
        )

