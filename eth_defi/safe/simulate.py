"""Perform Safe transaction on forked mainnet when you do not have all the keys."""
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.safe.deployment import fetch_safe_deployment


def simulate_safe_execution_anvil(
    web3: Web3,
    safe_address: str,
    contract_func: ContractFunction,
):
    """Simulate Safe transaction execution on a forked mainnet using Anvil.

    - We use Anvil to unlock all the keys and execute the transaction

    :param web3: Web3 instance connected to the Ethereum network.
    :param safe_address: Address of the Safe contract.
    :param safe_tx_hash: Hash of the Safe transaction to simulate.
    :param block_number: Block number at which to simulate the transaction.
        If None, uses the latest block.

    :return: Simulation result as a dictionary.
    """

    safe = fetch_safe_deployment(web3, safe_address)

    # Check that the Safe is valid and deployed
    owners = safe.retrieve_owners()

    payload = contract_func.build_transaction({

    })

    import ipdb ; ipdb.set_trace()

    to = payload["to"]
    value = 0
    data = b""
    operation = 0
    safe_tx = safe.build_safe_tx(
        to=to,
        value=value,
        data=data,
        operation=operation,
        safe_nonce=safe.retrieve_nonce()
    )

