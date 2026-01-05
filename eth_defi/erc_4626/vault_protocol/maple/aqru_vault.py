"""Maple Finance AQRU Pool vault support.

The AQRU Pool on Maple refers to the AQRU Receivables Pool (also known as the Real-World
Receivables account), a liquidity pool on the Maple Finance DeFi platform. It bridged
decentralised finance with traditional assets by providing financing for IRS tax credit
receivables owed to US businesses by the government.

AQRU plc served as the pool delegate, managing the loan book and overseeing borrower
applications, while partnering with Intero Capital Solutions for sourcing, due diligence,
and execution of transactions. Intero focused on vetted originators in sectors like
renewable energy and R&D, with the pool advancing USDC against pledged tax credits that
were typically settled by the IRS within 3-5 months.

Key features:

- Yield and returns: Lenders deposited USDC to earn competitive yields, starting at
  around 10% APY net of fees, later increased to 14.2% net (16.2% gross)
- Loan structure: Quasi-government backed, with low default risk due to IRS obligations
- Lock-up period: Initially 45 days, later updated to weekly liquidity after lock-up

The pool was launched in January 2023 as part of Maple's comeback strategy post-2022
defaults. The underlying US Treasury tax credits programme was set to run until the
end of Q3 2025.

- Homepage: https://aqru.io/real-world-receivables/
- Pool contract: https://etherscan.io/address/0xe9d33286f0E37f517B1204aA6dA085564414996d
- Maple Finance: https://maple.finance/
- Documentation: https://docs.maple.finance/
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class AQRUPoolVault(ERC4626Vault):
    """Maple Finance AQRU Pool vault.

    The AQRU Receivables Pool is a real-world asset (RWA) pool on Maple Finance that
    provided financing for IRS tax credit receivables. AQRU plc served as the pool
    delegate, partnering with Intero Capital Solutions for transaction sourcing
    and due diligence.

    Key features:

    - Real-world receivables: Backed by IRS tax credit receivables from US businesses
    - Low default risk: Quasi-government backed through IRS obligations
    - Competitive yields: 10-16% APY depending on market conditions
    - Lock-up period: 45-day initial lock-up, then weekly liquidity

    - Homepage: https://aqru.io/real-world-receivables/
    - Pool contract: https://etherscan.io/address/0xe9d33286f0E37f517B1204aA6dA085564414996d
    - Maple Finance: https://maple.finance/
    - Documentation: https://docs.maple.finance/
    """

    def has_custom_fees(self) -> bool:
        """AQRU Pool uses internalised fee structure."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fees are internalised in the share price."""
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fees are internalised in the share price."""
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """AQRU Pool has a 45-day initial lock-up period.

        After the initial lock-up, withdrawals are possible on a weekly basis,
        subject to available liquidity in the pool.
        """
        return datetime.timedelta(days=45)

    def get_link(self, referral: str | None = None) -> str:
        """Get link to the AQRU Real-World Receivables page."""
        return "https://aqru.io/real-world-receivables/"
