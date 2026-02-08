"""sBOLD protocol vault support.

sBOLD is a yield-bearing tokenised representation of deposits into Liquity V2 Stability Pools.
It allows users to deposit BOLD tokens across multiple stability pools (wstETH, rETH, wETH)
and receive ERC-4626 vault shares representing their position.

The protocol earns yield through two mechanisms:

1. Interest distributions from borrowers paid to stability pools
2. Liquidation penalties through automated collateral swaps

Key features:

- Entry fee is configurable (initially 0)
- Swap fees are configurable (initially 0)
- Automatic rebalancing across stability pools
- Collateral exposure limits to incentivise external swappers

- Homepage: https://www.k3.capital/
- GitHub: https://github.com/K3Capital/sBOLD
- Audit: https://www.chainsecurity.com/security-audit/k3-sbold
- Contract: https://etherscan.io/address/0x50bd66d59911f5e086ec87ae43c811e0d059dd11
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class SBOLDVault(ERC4626Vault):
    """sBOLD protocol vault support.

    sBOLD is a yield-bearing stablecoin that aggregates and tokenises the stability
    pools of Liquity V2, automatically hedging liquidation premiums. It serves as
    a passive non-custodial savings account for BOLD token holders.

    The vault deposits BOLD across multiple stability pools:

    - wstETH pool (60% initial allocation)
    - rETH pool (30% initial allocation)
    - wETH pool (10% initial allocation)

    Yield is generated from:

    - Interest distributions from borrowers
    - Liquidation penalties via automated collateral swaps

    - Homepage: https://www.k3.capital/
    - GitHub: https://github.com/K3Capital/sBOLD
    - Audit: https://www.chainsecurity.com/security-audit/k3-sbold
    - Twitter: https://x.com/k3_capital
    - Contract: https://etherscan.io/address/0x50bd66d59911f5e086ec87ae43c811e0d059dd11
    """

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        sBOLD has configurable entry fees (initially 0) and swap fees (initially 0).
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        sBOLD does not charge management fees. Yield comes directly from
        stability pool rewards.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        sBOLD has configurable swap rewards that are paid to the fee receiver
        when collateral is swapped back to BOLD. Initially set to 0.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        sBOLD vault has no lock-up period. Withdrawals are instant
        subject to available liquidity.
        """
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to K3 Capital homepage.
        """
        return "https://www.k3.capital/"
