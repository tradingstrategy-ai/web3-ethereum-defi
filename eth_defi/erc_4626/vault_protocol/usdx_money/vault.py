"""USDX Money protocol vault support.

USDX Money is a synthetic USD stablecoin protocol that provides stability without
relying on traditional banking infrastructure. USDX is backed by delta-neutral
positions across multiple exchanges, seamlessly bridging DeFi, CeFi, and TradFi.

sUSDX (Staked USDX) is a reward-bearing token where users stake USDX to receive
a proportionate share of protocol-generated yield. The value of sUSDX appreciates
over time rather than its quantity increasing (similar to cbETH or rETH for ETH).

Key features:

- Reward-bearing staking token (value appreciation, not quantity increase)
- No explicit management or performance fees
- Yield generated from protocol activities
- Deployed on multiple chains with the same contract address
- 8-hour vesting period for reward distributions
- Configurable cooldown mechanism for unstaking (up to 90 days)

- Homepage: https://usdx.money/
- Documentation: https://docs.usdx.money/
- GitHub: https://github.com/X-Financial-Technologies/usdx
- Twitter: https://x.com/StablesLabs
- Contract (BSC): https://bscscan.com/address/0x7788a3538c5fc7f9c7c8a74eac4c898fc8d87d92
- Contract (Ethereum): https://etherscan.io/address/0x7788a3538c5fc7f9c7c8a74eac4c898fc8d87d92
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class USDXMoneyVault(ERC4626Vault):
    """USDX Money sUSDX vault support.

    sUSDX is the staked version of USDX stablecoin. Users stake USDX and receive
    sUSDX tokens representing their share of protocol-generated yield.

    The vault implements:

    - Role-based access control (REWARDER_ROLE, BLACKLIST_MANAGER_ROLE)
    - Cooldown mechanism for unstaking (governance-configurable, up to 90 days)
    - 8-hour vesting period for reward distributions
    - Blacklist functionality for restricted addresses

    - Homepage: https://usdx.money/
    - Documentation: https://docs.usdx.money/
    - GitHub: https://github.com/X-Financial-Technologies/usdx
    - Twitter: https://x.com/StablesLabs
    - Contract: https://bscscan.com/address/0x7788a3538c5fc7f9c7c8a74eac4c898fc8d87d92
    """

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        USDX Money sUSDX vault does not charge explicit deposit/withdrawal fees
        at the smart contract level.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        USDX Money does not charge explicit management fees. Yield comes from
        protocol activities and is distributed to sUSDX holders.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        USDX Money does not charge explicit performance fees on the sUSDX vault.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        USDX Money sUSDX vault has a governance-configurable cooldown period
        of up to 90 days. When cooldown is enabled, users must initiate a
        cooldown and wait before withdrawing.

        The default cooldown duration is typically 7 days.
        """
        return datetime.timedelta(days=7)

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the USDX Money staking page.
        """
        return "https://usdx.money/"
