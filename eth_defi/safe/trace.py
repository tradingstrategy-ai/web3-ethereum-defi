"""Safe multisig transaction tranasction error handling."""

from hexbytes import HexBytes
from web3 import Web3

from safe_eth.eth.account_abstraction.constants import EXECUTION_FROM_MODULE_FAILURE_TOPIC, EXECUTION_FROM_MODULE_SUCCESS_TOPIC

from eth_defi.confirmation import wait_transactions_to_complete
from eth_defi.deploy import get_or_create_contract_registry
from eth_defi.trace import trace_evm_transaction, print_symbolic_trace, TraceMethod


def assert_execute_module_success(
    web3: Web3,
    tx_hash: HexBytes,
    verbose=True,
):
    """Assert that a Gnosis safe transaction succeeded.

    - Gnosis safe swallows any reverts

    - We need to extract Gnosis Safe logs from the tx receipt and check if they are successful

    :raise AssertionError:
        If the transaction failed
    """

    receipts = wait_transactions_to_complete(web3, [tx_hash])

    receipt = receipts[tx_hash]

    success = 0
    failure = 0

    for logs in receipt["logs"]:
        if logs["topics"][0] == EXECUTION_FROM_MODULE_SUCCESS_TOPIC:
            success += 1
        elif logs["topics"][0] == EXECUTION_FROM_MODULE_FAILURE_TOPIC:
            failure += 1

    if success == 0 and failure == 0:
        raise AssertionError(f"Does not look like a Gnosis Safe transaction, no ExecutionFromModuleSuccess or ExecutionFromModuleFailure events detected:\n{receipt}")
    elif success + failure > 1:
        raise AssertionError(f"Too many success and failures in tx. Some weird nested case?")
    elif failure == 1:
        if verbose:
            trace_data = trace_evm_transaction(web3, tx_hash, TraceMethod.parity)
            trace_output = print_symbolic_trace(get_or_create_contract_registry(web3), trace_data)
            raise AssertionError(f"Gnosis Safe multisig tx {tx_hash.hex()} failed.\nTrace output:\n{trace_output}\nYou might want to trace with JSON_RPC_TENDERLY method to get better diagnostics.")
        else:
            raise AssertionError(f"Gnosis Safe tx failed. Remember to check gas.")
    elif success == 1:
        return
    else:
        raise RuntimeError("Should not happen")
