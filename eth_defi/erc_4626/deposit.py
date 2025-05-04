"""Deposit and redemption from ERC-4626 vaults."""
from decimal import Decimal

from eth_typing import HexAddress
from web3.contract.contract import ContractFunction

from eth_defi.erc_4626.vault import ERC4626Vault


def deposit(
    vault: ERC4626Vault,
    from_: HexAddress,
    amount: Decimal,
) -> ContractFunction:
    """Craft a transaction for ERC-4626 vault deposit.

    - The resulting payload must be signed by a wallet/vault
    """


