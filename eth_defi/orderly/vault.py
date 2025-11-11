"""Orderly deposit vault handling"""

from eth_typing import HexAddress
from web3 import Web3
from web3.contract.contract import Contract, ContractFunction

from eth_defi.abi import get_deployed_contract


class OrderlyVault:
    """Orderly deposit vault instance.

    Orderly handles deposits as "vaults" that can be used to deposit and withdraw tokens.
    Vautls also have "settle" functionality to reflect the balances back onchain.
    """

    def __init__(self, web3: Web3, address: str):
        self.web3 = web3
        self.address = address
        self.contract = get_deployed_contract(web3, "orderly/vault.json", address)


def deposit(
    vault: OrderlyVault,
    *,
    amount: int,
    token: Contract,
    depositor_address: HexAddress,
    orderly_account_id: HexAddress,
    broker_id: str,
    token_id: str | None = None,
) -> tuple[ContractFunction, ContractFunction, ContractFunction]:
    """
    Deposit tokens into the Orderly vault.

    See also: https://orderly.network/docs/build-on-omnichain/user-flows/withdrawal-deposit

    :param vault:
        The vault to deposit into.
    :param amount:
        The amount of tokens to deposit.
    :param token:
        The token to deposit.
    :param depositor_address:
        The address that will be used to deposit.
    :param orderly_account_id:
        The orderly account ID to deposit to.
    :param broker_id:
        The broker ID to deposit to.
    :param token_id:
        The token ID to deposit to.
    """
    web3 = vault.web3
    approve_function = token.functions.approve(vault.address, amount)

    # get deposit fee
    broker_hash = web3.keccak(text=broker_id)
    token_hash = web3.keccak(text=token_id)

    deposit_input = (
        bytes.fromhex(orderly_account_id[2:]),
        bytes.fromhex(broker_hash.hex()[2:]),
        bytes.fromhex(token_hash.hex()[2:]),
        amount,
    )

    get_deposit_fee_function = vault.contract.functions.getDepositFee(
        Web3.to_checksum_address(depositor_address),
        deposit_input,
    )

    deposit_function = vault.contract.functions.deposit(deposit_input)

    return approve_function, get_deposit_fee_function, deposit_function


def withdraw(
    vault: OrderlyVault,
    *,
    amount: int,
    token: Contract,
    wallet_address: HexAddress,
    orderly_account_id: HexAddress,
    broker_id: str,
    token_id: str | None = None,
) -> tuple[ContractFunction, ContractFunction, ContractFunction]:
    """
    Withdraw tokens from the Orderly vault.

    See also: https://orderly.network/docs/build-on-omnichain/user-flows/withdrawal-deposit

    :param vault:
        The vault to deposit into.
    :param amount:
        The amount of tokens to deposit.
    :param token:
        The token to deposit.
    :param wallet_address:
        The wallet address to deposit from.
    :param orderly_account_id:
        The orderly account ID to deposit to.
    :param broker_id:
        The broker ID to deposit to.
    :param token_id:
        The token ID to deposit to.
    """
    web3 = vault.web3
    approve_function = token.functions.approve(vault.address, amount)

    # get deposit fee
    broker_hash = web3.keccak(text=broker_id)
    token_hash = web3.keccak(text=token_id)

    deposit_input = (
        bytes.fromhex(orderly_account_id[2:]),
        bytes.fromhex(broker_hash.hex()[2:]),
        bytes.fromhex(token_hash.hex()[2:]),
        amount,
    )

    get_deposit_fee_function = vault.contract.functions.getDepositFee(
        Web3.to_checksum_address(wallet_address),
        deposit_input,
    )

    deposit_function = vault.contract.functions.deposit(deposit_input)

    return approve_function, get_deposit_fee_function, deposit_function
