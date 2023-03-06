                                       """Transaction debug tracing.

Internally use evm-trace from Ape: https://github.com/ApeWorX/evm-trace
"""
from hexbytes import HexBytes
from web3 import Web3

from evm_trace import TraceFrame
from evm_trace import CallType, get_calltree_from_geth_trace



class TraceNotEnabled(Exception):
    """Tracing is not enabled on the backend."""


def trace_evm_transaction(web3: Web3, tx_hash: HexBytes | str):
    """Trace a (failed) transaction.

    - Prints out an EVM transaction stack trace

    - Currently only works with Anvil backend if `steps_trace=True`

    See also :py:func:`eth_defi.anvil.launch_anvil`.
    """

    # See https://book.getfoundry.sh/reference/anvil/
    struct_logs = web3.manager.request_blocking("debug_traceTransaction", [tx_hash])["structLogs"]

    if not struct_logs:
        raise TraceNotEnabled(f"Tracing not enabled on the backend {web3.provider}.\n"
                              f"If you are using anvil make sure you start with --steps-trace")

    tx = web3.eth.get_transaction(tx_hash)

    root_node_kwargs = {

    }

    import ipdb ; ipdb.set_trace()
    calltree = get_calltree_from_geth_trace(trace, **root_node_kwargs)

    for item in struct_logs:
        frame = TraceFrame.parse_obj(item)