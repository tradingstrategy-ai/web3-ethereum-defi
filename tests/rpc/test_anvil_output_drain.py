"""Anvil must keep answering after it has logged more than a pipe buffer of output."""

from web3 import Web3

from eth_defi.provider.anvil import ANVIL_OUTPUT_TAIL_LINES, launch_anvil

#: Enough transactions to overflow a 64 KiB pipe with Anvil's per-transaction log lines;
#: before stdout and stderr were drained, Anvil stopped answering after about 150.
TRANSACTION_COUNT = 400


def test_anvil_keeps_answering_after_large_output() -> None:
    """Send many transactions, then check Anvil still answers and close() returns a bounded tail.

    1. Launch a local Anvil with captured output.
    2. Mine more transactions than its stdout pipe could buffer.
    3. Verify the node still answers and shutdown returns at most the retained tail.
    """
    # 1. Launch a local Anvil with captured output.
    launch = launch_anvil()
    try:
        web3 = Web3(Web3.HTTPProvider(launch.json_rpc_url, request_kwargs={"timeout": 15}))
        sender, receiver = web3.eth.accounts[0], web3.eth.accounts[1]

        # 2. Mine more transactions than its stdout pipe could buffer.
        for _ in range(TRANSACTION_COUNT):
            tx_hash = web3.eth.send_transaction({"from": sender, "to": receiver, "value": 1})
            assert web3.eth.wait_for_transaction_receipt(tx_hash, timeout=15)["status"] == 1

        # 3. Verify the node still answers and shutdown returns at most the retained tail.
        assert web3.eth.block_number == TRANSACTION_COUNT
    finally:
        stdout, _stderr = launch.close()
    assert stdout
    assert len(stdout.splitlines()) <= ANVIL_OUTPUT_TAIL_LINES
