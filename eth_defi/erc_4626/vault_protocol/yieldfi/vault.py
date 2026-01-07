"""YieldFi vault support.

YieldFi is a Web3 asset management platform that actively deploys capital
into risk-managed, high-performing DeFi strategies.
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class YieldFiVault(ERC4626Vault):
    """YieldFi vyToken vault support.

    YieldFi is an asset management platform for the onchain economy,
    deploying capital into risk-managed DeFi strategies across stablecoins,
    ETH, and BTC.

    - Homepage: https://yield.fi/
    - Twitter: https://x.com/getyieldfi
    - GitHub: https://github.com/YieldFiLabs
    - Example vault (vyUSD): https://etherscan.io/address/0x2e3c5e514eef46727de1fe44618027a9b70d92fc

    The vyToken vaults use a vesting mechanism for reward distribution
    with configurable vesting periods.
    """

    def has_custom_fees(self) -> bool:
        """YieldFi uses internalised fee mechanism."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """YieldFi does not charge management fees.

        Fee information based on protocol analysis.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """YieldFi fee structure.

        Fee is configurable via setFee() but currently set to 0 on vyUSD vault.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """YieldFi vaults may have vesting periods for rewards.

        The vesting period is configurable via setVestingPeriod().
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get link to YieldFi vault page.

        YieldFi uses a single app page for all vaults.
        """
        return "https://yield.fi/"
