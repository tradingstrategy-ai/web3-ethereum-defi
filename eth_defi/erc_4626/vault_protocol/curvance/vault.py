"""Curvance lending protocol vault support.

Curvance is a next-generation DeFi lending protocol offering capital-efficient
money markets with advanced risk management across multiple blockchains including
Monad, Ethereum, Arbitrum, Base, and more.

- Homepage: https://www.curvance.com/
- Documentation: https://docs.curvance.com/
- GitHub: https://github.com/curvance/curvance-contracts
- Twitter: https://twitter.com/cuabordelasal

Key features:

- Risk-isolated, intent-based lending markets
- Auto-compounding vaults and optimised DeFi strategies
- One-click looping for maximised capital efficiency
- Dual oracle protection for accurate pricing
- Non-custodial architecture

The protocol uses cToken contracts (BorrowableCToken, SimpleCToken, etc.) that
implement ERC-4626 for deposits and withdrawals. The ``interestFee`` on borrowable
tokens represents the protocol's share of borrower interest (not a direct fee on
depositors).
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class CurvanceVault(ERC4626Vault):
    """Curvance lending protocol vault.

    Curvance cToken vaults allow users to deposit assets to earn yield from
    lending activities. The protocol supports multiple vault types:

    - BorrowableCToken: Assets can be borrowed against collateral
    - SimpleCToken: Basic deposit/withdrawal functionality
    - StrategyCToken: Yield-generating strategy vaults

    Fee structure:

    - Interest fee: Protocol takes a percentage of borrower interest (up to 60%)
    - Flashloan fee: 0.04% (4 basis points)
    - No explicit management or performance fees on deposits

    Example contracts:

    - Monad BorrowableCToken: https://monadscan.com/address/0xad4aa2a713fb86fbb6b60de2af9e32a11db6abf2
    """

    def has_custom_fees(self) -> bool:
        """Curvance uses interest-based fees rather than deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """No management fee on deposits.

        The protocol earns through interest fees on borrowers, not depositors.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """No explicit performance fee.

        Interest fees (up to 60%) are taken from borrower interest payments,
        but this is not a direct performance fee on depositor returns.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """No lock-up period for Curvance deposits.

        Users can withdraw at any time, subject to available liquidity.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return link to Curvance app."""
        # Curvance uses chain-specific URLs
        return "https://www.curvance.com/"
