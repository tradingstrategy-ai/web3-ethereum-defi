"""Deposit/redemption flow."""
from eth_defi.vault.base import DepositRequest, RedemptionTicket, RedemptionRequest, DepositTicket


class ERC7540DepositTicket(DepositRequest):
    """Synchronous deposit request for ERC-4626 vaults.

    - No-op as requests are synchronous
    """


class ERC7540DepositRequest(DepositTicket):
    """Synchronous deposit request for ERC-4626 vaults."""



class ERC7540RedemptionTicket(RedemptionTicket):
    """Synchronous deposit request for ERC-4626 vaults.

    - No-op as requests are synchronous
    """


class ERC7540RedemptionRequest(RedemptionRequest):
    """Synchronous deposit request for ERC-4626 vaults."""

