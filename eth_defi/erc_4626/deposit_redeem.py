"""ERC-4626 deposit and redeem requests."""
from eth_defi.erc_4626.flow import deposit_4626, redeem_4626
from eth_defi.vault.deposit_redeem import DepositRequest, RedemptionRequest, RedemptionTicket, VaultDepositManager

import datetime
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from web3 import Web3
from web3.contract.contract import ContractFunction

from hexbytes import HexBytes
from eth_typing import HexAddress


class ERC4626DepositTicket(DepositRequest):
    """Synchronous deposit request for ERC-4626 vaults.

    - No-op as requests are synchronous
    """


class ERC4626DepositRequest(DepositRequest):
    """Synchronous deposit request for ERC-4626 vaults."""



class ERC4626RedemptionTicket(RedemptionTicket):
    """Synchronous deposit request for ERC-4626 vaults.

    - No-op as requests are synchronous
    """


class ERC4626RedemptionRequest(RedemptionRequest):
    """Synchronous deposit request for ERC-4626 vaults."""


class ERC4626DepositManager(VaultDepositManager):
    """Abstraction over different deposit/redeem flows of vaults."""

    def __init__(self, vault: "eth_defi.erc_4626.vault.ERC4626Vault"):
        from eth_defi.erc_4626.vault import ERC4626Vault
        assert isinstance(vault, ERC4626Vault), f"Got {type(vault)}"
        self.vault = vault

    def create_deposit_request(
        self,
        owner: HexAddress,
        amount: Decimal = None,
        raw_amount: int = None,
        check_max_deposit=True,
        check_enough_token=True,
    ) -> ERC4626DepositRequest:
        func = deposit_4626(
            self.vault,
            owner,
            amount=amount,
            raw_amount=raw_amount,
            check_max_deposit=check_max_deposit,
            check_enough_token=check_enough_token,
        )
        return ERC4626DepositRequest(
            vault=self.vault,
            owner=owner,
            funcs=[func],
            amount=amount,
            raw_amount=raw_amount,
        )

    def create_redemption_request(
        self,
        owner: HexAddress,
        to: HexAddress,
        shares: Decimal = None,
        raw_shares: int = None,
        check_max_deposit=True,
        check_enough_token=True,
    ) -> ERC4626RedemptionRequest:
        assert not raw_shares, f"Unsupported raw_shares={raw_shares}"
        func = redeem_4626(
            self,
            owner,
            shares,
            check_enough_token=True,
            check_max_redeem=True,
        )
        return ERC4626RedemptionRequest(
            vault=self.vault,
            owner=owner,
            funcs=[func],
            shares=shares,
            raw_shares=raw_shares,
        )

    def can_finish_deposit(
        self,
        deposit_ticket: ERC4626DepositTicket,
    ):
        """Synchronous deposits can be finished immediately."""
        return True

    def can_finish_redeem(
        self,
        redemption_ticket: ERC4626RedemptionTicket,
    ):
        """Synchronous redemptions can be finished immediately."""
        return True

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        return True

    def has_synchronous_deposit(self) -> bool:
        """Does this vault support synchronous deposits?

        - E.g. ERC-4626 vaults
        """
        return True

    def has_synchronous_redemption(self) -> bool:
        """Does this vault support synchronous deposits?

        - E.g. ERC-4626 vaults
        """
        return True
