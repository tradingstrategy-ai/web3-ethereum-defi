"""Deposit/redemption flow."""
from eth_defi.vault.deposit_redeem import DepositRequest, RedemptionTicket, RedemptionRequest, DepositTicket
from eth_defi.vault.deposit_redeem import DepositRequest, RedemptionRequest, RedemptionTicket, VaultDepositManager

import datetime
from decimal import Decimal

from web3.contract.contract import ContractFunction
from eth_typing import HexAddress


class ERC7540DepositTicket(DepositRequest):
    """Synchronous deposit request for ERC-7540 vaults.

    - No-op as requests are synchronous
    """


class ERC7540DepositRequest(DepositTicket):
    """Synchronous deposit request for ERC-7540 vaults."""



class ERC7540RedemptionTicket(RedemptionTicket):
    """Synchronous deposit request for ERC-7540 vaults.

    - No-op as requests are synchronous
    """


class ERC7540RedemptionRequest(RedemptionRequest):
    """Synchronous deposit request for ERC-7540 vaults."""



class ERC7540DepositManager:
    """ERC-7540 async deposit/redeem flow.

    - Currently coded for Lagoon, but should work with any vault
    """

    def __init__(self, vault: "eth_defi.erc_7540.vault.ERC7540Vault"):
        from eth_defi.lagoon.vault import LagoonVault
        assert isinstance(vault, LagoonVault), f"Got {type(vault)}"
        self.vault = vault

    def __init__(self, vault: "eth_defi.erc_7540.vault.ERC7540Vault"):
        from eth_defi.erc_7540.vault import ERC7540Vault
        assert isinstance(vault, ERC7540Vault), f"Got {type(vault)}"
        self.vault = vault

    def create_deposit_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        amount: Decimal = None,
        raw_amount: int = None,
        check_max_deposit=True,
        check_enough_token=True,
    ) -> ERC7540DepositRequest:
        func = deposit_7540(
            self.vault,
            owner,
            amount=amount,
            raw_amount=raw_amount,
            check_max_deposit=check_max_deposit,
            check_enough_token=check_enough_token,
        )
        return ERC7540DepositRequest(
            vault=self.vault,
            owner=owner,
            to=owner,
            funcs=[func],
            amount=amount,
            raw_amount=raw_amount,
        )

    def create_redemption_request(
        self,
        owner: HexAddress,
        to: HexAddress = None,
        shares: Decimal = None,
        raw_shares: int = None,
        check_max_deposit=True,
        check_enough_token=True,
    ) -> ERC7540DepositRequest:
        assert not raw_shares, f"Unsupported raw_shares={raw_shares}"
        assert not to, f"Unsupported to={to}"
        func = redeem_7540(
            self.vault,
            owner,
            shares,
            check_enough_token=True,
            check_max_redeem=True,
        )
        return ERC7540RedemptionRequest(
            vault=self.vault,
            owner=owner,
            to=owner,
            funcs=[func],
            shares=shares,
            raw_shares=raw_shares,
        )

    def can_finish_deposit(
        self,
        deposit_ticket: ERC7540DepositTicket,
    ):
        """Synchronous deposits can be finished immediately."""
        return True

    def can_finish_redeem(
        self,
        redemption_ticket: ERC7540RedemptionTicket,
    ):
        """Synchronous redemptions can be finished immediately."""
        return True

    def can_create_redemption_request(self, owner: HexAddress) -> bool:
        return True

    def has_synchronous_deposit(self) -> bool:
        """Does this vault support synchronous deposits?

        - E.g. ERC-7540 vaults
        """
        return False

    def has_synchronous_redemption(self) -> bool:
        """Does this vault support synchronous deposits?

        - E.g. ERC-7540 vaults
        """
        return False

    def estimate_redemption_delay(self) -> datetime.timedelta:
        return datetime.timedelta(seconds=0)

    def get_redemption_delay_over(self, address: HexAddress | str) -> datetime.datetime:
        return datetime.datetime(1970, 1, 1)

    def is_redemption_in_progress(self, owner: HexAddress) -> bool:
        return False

    def is_deposit_in_progress(self, owner: HexAddress) -> bool:
        return False

    def settle_redemption(
        self,
        redemption_ticket: RedemptionTicket,
    ) -> ContractFunction:
        raise NotImplementedError("Redemptions are synchronous, nothing to settle")
