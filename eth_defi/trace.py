"""Symbolic transaction tracing and Solidity stack traces.

- This code is very preliminary and has not been througly tested with different smart contracts,
  so patches welcome

- Internally use evm-trace from Ape: https://github.com/ApeWorX/evm-trace
"""

import logging
from typing import cast, Optional, Iterator

from eth_typing import ChecksumAddress
from eth_utils import to_checksum_address
from eth_defi.deploy import ContractRegistry
from hexbytes import HexBytes
from web3 import Web3

from evm_trace import TraceFrame, CallTreeNode
from evm_trace import CallType, get_calltree_from_geth_trace


logger = logging.getLogger(__name__)


class TraceNotEnabled(Exception):
    """Tracing is not enabled on the backend."""


def trace_evm_transaction(web3: Web3, tx_hash: HexBytes | str) -> CallTreeNode:
    """Trace a (failed) transaction.

    - See :py:func:`print_symbolic_trace` for usage

    - Extract an EVM transaction stack trace from a node, using GoEthereum compatible `debug_traceTransaction`

    - Currently only works with Anvil backend and if `steps_trace=True`
    """

    if type(tx_hash) == HexBytes:
        tx_hash = tx_hash.hex()

    struct_logs = web3.manager.request_blocking("debug_traceTransaction", [tx_hash], {"enableMemory": True})["structLogs"]

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

        if contract:
            # Set in deploy_contract()
            symbolic_name = getattr(contract, "name", None)

            function = None
            if function_selector != "0x":
                try:
                    function = contract.get_function_by_selector(function_selector)
                except ValueError as e:
                    function = None

            if function:
                symbolic_function = function.fn_name

        symbolic_name = symbolic_name or address
        symbolic_function = symbolic_function or function_selector.hex()

        cost = self.call.gas_cost
        call_path = symbolic_name if address else ""
        if self.call.calldata:
            call_path = f"{call_path}." if call_path else ""
            call_path = f"{call_path}<{symbolic_function}>"

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
