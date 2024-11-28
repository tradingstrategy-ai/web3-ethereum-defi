"""Safe multisig transaction tranasction error handling."""

from hexbytes import HexBytes
from web3 import Web3

from safe_eth.eth.account_abstraction.constants import EXECUTION_FROM_MODULE_FAILURE_TOPIC, EXECUTION_FROM_MODULE_SUCCESS_TOPIC


def assert_safe_success(web3: Web3, tx_hash: HexBytes):
    """Assert that a Gnosis safe transaction succeeded.

    - Gnosis safe swallows any reverts

    - We need to extract Gnosis Safe logs from the tx receipt and check if they are successful

    :raise AssertionError:
        If the transaction failed
    """
    
    receipt = web3.eth.get_transaction_receipt(tx_hash)

    success = 0
    failure = 0

    for logs in receipt["logs"]:
        if logs["topics"][0] == EXECUTION_FROM_MODULE_SUCCESS_TOPIC:
            success += 1
        elif logs["topics"][0] == EXECUTION_FROM_MODULE_FAILURE_TOPIC:
            failure += 1

    if success == 0 and failure == 0:
        raise AssertionError(f"Does not look like a Gnosis Safe transction")
    elif success + failure > 1:
        raise AssertionError(f"Too many success and failures in tx. Some weird nested case?")
    elif failure == 1:
        raise AssertionError(f"Gnosis Safe tx failed")
    elif success == 1:
        return
    else:
        raise RuntimeError("Should not happen")
