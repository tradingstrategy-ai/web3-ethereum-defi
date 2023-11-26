"""Symbolic transaction tracing and human-readable Solidity stack traces.

- This code is very preliminary and has not been througly tested with different smart contracts,
  so patches welcome

- Internally use `evm-trace library from Ape <https://github.com/ApeWorX/evm-trace>`__

- Currently only works with Anvil (:py:mod:`eth_defi.anvil`) backend

"""
import enum
import logging
from typing import Any, Iterator, Optional, cast

from eth_typing import ChecksumAddress
from eth_utils import to_checksum_address
from evm_trace import (
    CallTreeNode,
    CallType,
    ParityTraceList,
    TraceFrame,
    get_calltree_from_geth_trace,
    get_calltree_from_parity_trace,
)
from hexbytes import HexBytes
from web3 import Web3
from web3.contract.contract import ContractFunction
from web3.types import TxParams, TxReceipt

from eth_defi.abi import decode_function_args, humanise_decoded_arg_data
from eth_defi.deploy import ContractRegistry, get_or_create_contract_registry
from eth_defi.revert_reason import fetch_transaction_revert_reason

logger = logging.getLogger(__name__)


class TraceNotEnabled(Exception):
    """Tracing is not enabled on the backend."""


class TransactionAssertionError(AssertionError):
    """Exception thrown when unit test transaction asset fails.

    See :py:func:`assert_transaction_success_with_explanation`.
    """

    def __init__(
        self,
        message,
        revert_reason: str = "",
        solidity_stack_trace: str = "",
    ):
        super().__init__(message)

        self.revert_reason = revert_reason
        self.solidity_stack_trace = solidity_stack_trace


class TraceMethod(enum.Enum):
    """What kind of transaction tracing method we use.

    - `See Anvil manual for more information <https://book.getfoundry.sh/reference/anvil/>`__

    - `GoEthereum method not supported <https://github.com/foundry-rs/foundry/discussions/4498>`__
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


def trace_evm_call(
    web3: Web3,
    tx: dict,
    trace_method: TraceMethod = TraceMethod.parity,
    block_reference="latest",
) -> CallTreeNode:
    """Trace a Solidity function call.

    - See :py:func:`print_symbolic_trace` for usage

    - Extract an EVM transaction stack trace from a node, using GoEthereum compatible `debug_traceTransaction`

    - Currently only works with Anvil backend and if `steps_trace=True`

    .. warning::

        Currently not implemented. Anvil does not support `trace_call` RPC yet.

    :param web3:
        Anvil connection

    :param tx:
        Transaction object for the call

    :param trace_method:
        How to trace.

        Choose between `debug_traceTransaction` and `trace_transaction` RPCs.
    """

    raise NotImplementedError("Anvil does not support yet")

    assert trace_method == TraceMethod.parity, f"Only Parity style traces supported"
    trace_call_resp = web3.manager.request_blocking("trace_call", [tx, ["trace"], block_reference])


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


    Example output:

    .. code-block:: text

        E           AssertionError: Transaction failed: AttributeDict({'hash': HexBytes('0xaa70b2f76ad9f32f7c722390535d5a806b4d815f3d8d460e5d18cdba3b1c8c2d'), 'nonce': 2, 'blockHash': HexBytes('0x1d2a1d36185bebb373639e1eb4ddbe9f7f3347fa6dd7bcbbe5e5905fe6a1f4ed'), 'blockNumber': 3, 'transactionIndex': 0, 'from': '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266', 'to': '0x5FbDB2315678afecb367f032d93F642f64180aa3', 'value': 0, 'gasPrice': 768647811, 'gas': 500000, 'input': '0x25ad8c83000000000000000000000000e7f1725e7734ce288f8367e1bb143e90bb3f0512', 'v': 1, 'r': HexBytes('0x43336f08be93aec7ecf456c724d3c29c6cebc589ab3fe6199ee783a627bbcda8'), 's': HexBytes('0x74002a6cdd84b81932e36ac0725591460b09eaaa6b0dd615c0c5d43171467c8a'), 'type': 2, 'accessList': [], 'maxPriorityFeePerGas': 0, 'maxFeePerGas': 1768647811, 'chainId': 31337})
        E           Revert reason: execution reverted: Big bada boom
        E           Solidity stack trace:
        E           CALL: RevertTest.revert2(second=0xe7f1725e7734ce288f8367e1bb143e90bb3f0512) [3284 gas]
        E           └── CALL: RevertTest2.boom() [230 gas]

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


def assert_transaction_success_with_explanation(
    web3: Web3,
    tx_hash: HexBytes,
) -> TxReceipt:
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

        E           AssertionError: Transaction failed: AttributeDict({'hash': HexBytes('0xaa70b2f76ad9f32f7c722390535d5a806b4d815f3d8d460e5d18cdba3b1c8c2d'), 'nonce': 2, 'blockHash': HexBytes('0x1d2a1d36185bebb373639e1eb4ddbe9f7f3347fa6dd7bcbbe5e5905fe6a1f4ed'), 'blockNumber': 3, 'transactionIndex': 0, 'from': '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266', 'to': '0x5FbDB2315678afecb367f032d93F642f64180aa3', 'value': 0, 'gasPrice': 768647811, 'gas': 500000, 'input': '0x25ad8c83000000000000000000000000e7f1725e7734ce288f8367e1bb143e90bb3f0512', 'v': 1, 'r': HexBytes('0x43336f08be93aec7ecf456c724d3c29c6cebc589ab3fe6199ee783a627bbcda8'), 's': HexBytes('0x74002a6cdd84b81932e36ac0725591460b09eaaa6b0dd615c0c5d43171467c8a'), 'type': 2, 'accessList': [], 'maxPriorityFeePerGas': 0, 'maxFeePerGas': 1768647811, 'chainId': 31337})
        E           Revert reason: execution reverted: Big bada boom
        E           Solidity stack trace:
        E           CALL: RevertTest.revert2(second=0xe7f1725e7734ce288f8367e1bb143e90bb3f0512) [3284 gas]
        E           └── CALL: RevertTest2.boom() [230 gas]

    See also :py:func:`print_symbolic_trace`.

    :param web3:
        Web3 instance

    :param tx_hash:
        A transaction (mined/not mined) we want to make sure has succeeded.

        Gas limit must have been set for this transaction.

    :raise TransactionAssertionError:
        Outputs a verbose AssertionError on what went wrong.

    :return tx_receipt:
        Output transaction receipt if no error is raised
    """

    receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    if receipt["status"] == 0:
        # Explain why the transaction failed
        tx_details = web3.eth.get_transaction(tx_hash)

        if web3.eth.chain_id == 31337:
            # Transaction tracing only enabled to anvil
            revert_reason = fetch_transaction_revert_reason(web3, tx_hash)
            trace_data = trace_evm_transaction(web3, tx_hash, TraceMethod.parity)
            trace_output = print_symbolic_trace(get_or_create_contract_registry(web3), trace_data)
            raise TransactionAssertionError(
                f"Transaction failed: {tx_details}\n" f"Revert reason: {revert_reason}\n" f"Solidity stack trace:\n" f"{trace_output}\n",
                revert_reason=revert_reason,
                solidity_stack_trace=trace_output,
            )
        else:
            raise RuntimeError(f"Transaction failed: {tx_details} - tracing disabled")

    return receipt


def assert_call_success_with_explanation(
    func: ContractFunction,
    transaction: Optional[TxParams] = None,
) -> Any:
    """Make a Web3.call and if it fails get the Solidity stack trace.

    - We do `debug_traceCall` first to see if the call fails

    - If it does not fail we do the actual `eth_call`

    .. note ::

        Because Anvil does not support `trace_call` yet,
        we just do this as sending the transaction.
        We assume the call does not change any state.
        See notes in :py:func:`trace_evm_call`.

    If not gas given, assume 1,000,000 gas units.

    :param func:
        Prepared :py:class:`ContractFunction` call.

    :param transaction:
        Transactional parameters for the call, like gas limit and sender.

    :raise TransactionAssertionError:
        Outputs a verbose AssertionError on what went wrong.

    :return:
        Same results as you would have with `func.call(transaction)`
    """

    if transaction is None:
        transaction = {}

    if not "gas" in transaction:
        transaction["gas"] = 1_000_000

    tx_hash = func.transact(transaction)
    assert_transaction_success_with_explanation(func.w3, tx_hash)
    return func.call(transaction)


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
        try:
            call_type = self.call.call_type.value
        except AttributeError:
            # Python 3.12+
            # AST module changes?
            call_type = str(self.call.call_type)

        address_hex_str = self.call.address.hex() if self.call.address else None

        try:
            address = to_checksum_address(address_hex_str) if address_hex_str else None
        except (ImportError, ValueError):
            # Ignore checksumming if user does not have eth-hash backend installed.
            address = cast(ChecksumAddress, address_hex_str)

        contract = self.contract_registry.get(address.lower())

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

        if symbolic_name:
            # We know the contract at this address by its ABI
            symbolic_name = f"{symbolic_name}({address})"
        else:
            # No idea of ABI what is deployed at this address
            symbolic_name = address
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
