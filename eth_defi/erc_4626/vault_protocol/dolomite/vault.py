"""Dolomite ERC-4626 vault support."""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class DolomiteVault(ERC4626Vault):
    """Dolomite ERC-4626 vault support.

    Dolomite is a next-generation DeFi lending and borrowing platform
    that supports over 1,000 unique assets with capital-efficient money markets.

    Dolomite ERC-4626 vaults wrap user Dolomite margin positions into
    standard ERC-4626 tokenised vault shares, enabling integration with
    other DeFi protocols.

    Key features:

    - Yield accrues from lending in Dolomite money markets
    - No explicit deposit/withdrawal fees at the vault level
    - Fees are internalised through interest rate spreads
    - Instant deposits and withdrawals (subject to market liquidity)

    - `Homepage <https://dolomite.io/>`__
    - `Application <https://app.dolomite.io/>`__
    - `Documentation <https://docs.dolomite.io/>`__
    - `GitHub <https://github.com/dolomite-exchange/>`__
    - `Twitter <https://twitter.com/dolomite_io>`__
    - `Example contract on Arbiscan (dUSDC) <https://arbiscan.io/address/0x444868b6e8079ac2c55eea115250f92c2b2c4d14>`__
    - `Example contract on Arbiscan (dUSDT) <https://arbiscan.io/address/0xf2d2d55daf93b0660297eaa10969ebe90ead5ce8>`__
    """

    def has_custom_fees(self) -> bool:
        """Dolomite has no explicit deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Dolomite has no management fee.

        Fees are internalised through interest rate spreads between
        borrowers and lenders.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Dolomite has no explicit performance fee.

        The protocol earns from interest rate spreads, not from explicit
        performance fees on vault returns.
        """
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Dolomite vaults have no lock-up period.

        Deposits and withdrawals are instant, subject to market liquidity.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get link to Dolomite application."""
        return "https://app.dolomite.io/"
