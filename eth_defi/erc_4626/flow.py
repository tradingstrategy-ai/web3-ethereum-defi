"""Deposit and redemption from ERC-4626 vaults."""
import logging
from decimal import Decimal

from eth_typing import HexAddress
from web3.contract.contract import ContractFunction

from eth_defi.erc_4626.vault import ERC4626Vault


logger = logging.getLogger(__name__)


def deposit_4626(
    vault: ERC4626Vault,
    from_: HexAddress,
    amount: Decimal,
    check_max_deposit=True,
    check_enough_token=True,
    receiver=None,
) -> ContractFunction:
    """Craft a transaction for ERC-4626 vault deposit.

    - The resulting payload must be signed by a wallet/vault
    """

    assert isinstance(vault, ERC4626Vault)
    assert isinstance(amount, Decimal)
    assert from_.startswith("0x")
    assert amount > 0

    if receiver is None:
        receiver = from_

    logger.info(
        "Depositing to vault %s, amount %s, from %s",
        vault.address,
        amount,
        from_,
    )

    contract = vault.vault_contract

    raw_amount = vault.denomination_token.convert_to_raw(amount)

    if check_enough_token:
        actual_balance = vault.denomination_token.fetch_raw_balance_of(from_)
        assert actual_balance >= raw_amount, f"Not enough token in {from_} to deposit {amount} to {vault.address}, has {actual_balance}"

    if check_max_deposit:
        max_deposit = contract.functions.maxDeposit(receiver).call()
        assert raw_amount <= max_deposit, f"Max deposit {max_deposit} is less than {raw_amount}"

    call = contract.functions.deposit(raw_amount, receiver)
    return call








