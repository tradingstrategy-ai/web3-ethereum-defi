"""Perform Safe transaction on forked mainnet when you do not have all the private keys."""

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract.contract import ContractFunction

from eth_defi.provider.anvil import make_anvil_custom_rpc_request
from eth_defi.safe.deployment import fetch_safe_deployment


def simulate_safe_execution_anvil(
    web3: Web3,
    safe_address: HexAddress | str,
    contract_func: ContractFunction,
    gas=10_000_000,
) -> HexBytes:
    """Simulate Safe transaction execution on a forked mainnet using Anvil.

    - We use Anvil to take over Safe contract as EOA address
    - We construct a transaction that skips all Safe smart contract code,
      and just sends the transaction as EOA

    TODO: Later we might want to change this to use mock Safe contract where we
    override ``checkSignatures()`` as described in https://chatgpt.com/share/684f0e0d-6000-8013-be12-a9d7a0a1d751

    Example:

    .. code-block:: python

        func = safe.contract.functions.enableModule(new_guard_address)
        tx_hash = simulate_safe_execution_anvil(
            web3,
            safe_address,
            func,
        )
        assert_transaction_success_with_explanation(web3, tx_hash)

    :return:
        Transaction hash.
    """

    safe = fetch_safe_deployment(web3, safe_address)

    make_anvil_custom_rpc_request(
        web3,
        "anvil_impersonateAccount",
        [safe_address],
    )

    # Top up ETH needed to pay for the gas,
    # as Safe contract does not hold it itself
    wei_amount = 5 * 10**18  # 5 ETH

    # Call Anvil's custom RPC to set the balance
    web3.provider.make_request(
        "anvil_setBalance",
        [safe_address, hex(wei_amount)],
    )

    tx_hash = contract_func.transact(
        {
            "from": safe_address,
            "gas": gas,
        }
    )

    return tx_hash
