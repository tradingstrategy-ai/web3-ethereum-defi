"""cSigma Finance vault support.

cSigma Finance is a blockchain-based protocol that connects global borrowers and lenders
by standardising and streamlining the commercial lending process. The protocol offers
crypto-uncorrelated yield opportunities to stablecoin holders through tokenised real-world
assets (RWA) and risk-adjusted DeFi strategies.

csUSD allows users to deposit stablecoins and earn yield from two sources:
- RWA credit markets
- Onchain DeFi yield strategies

The protocol dynamically allocates between these sources based on market conditions
to optimise yield performance.

- Homepage: https://csigma.finance
- csUSD vault: https://www.csigma.finance/csusd
- Documentation: https://csigma.medium.com/
- Twitter: https://x.com/csigmafinance
- Contract: https://etherscan.io/address/0xd5d097f278a735d0a3c609deee71234cac14b47e
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class CsigmaVault(ERC4626Vault):
    """cSigma Finance vault support.

    cSigma Finance is a DeFi protocol focused on providing fixed-rate, real-world yields
    for stablecoins through tokenised RWA private credit. The protocol has tokenised
    over $80 million in business loans from mid-market companies.

    - Homepage: https://csigma.finance
    - csUSD vault: https://www.csigma.finance/csusd
    - Medium: https://csigma.medium.com/
    - Twitter: https://x.com/csigmafinance
    - Contract: https://etherscan.io/address/0xd5d097f278a735d0a3c609deee71234cac14b47e
    """

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        cSigma does not charge deposit/withdrawal fees at the smart contract level.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        Generated: Human can add details later based on protocol documentation.

        :return:
            0.1 = 10%
        """
        return 0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        Generated: Human can add details later based on protocol documentation.

        :return:
            0.1 = 10%
        """
        return 0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        cSigma uses a First-In-First-Out queue for redemptions when vault reserves
        are depleted. The lock-up period depends on RWA credit market liquidity.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not supported by cSigma currently).

        :return:
            Link to the csUSD vault page.
        """
        return "https://edge.csigma.finance/"
