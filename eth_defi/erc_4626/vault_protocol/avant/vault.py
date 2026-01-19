"""Avant Protocol vault support.

Avant Protocol is a DeFi protocol on Avalanche that provides avUSD, a stablecoin backed
by yield-bearing assets. Users can stake avUSD to receive savUSD (Staked avUSD), an
ERC-4626 vault token that earns yield from the protocol's yield distribution mechanism.

Key features:

- No management or performance fees at the smart contract level
- Yield comes from protocol-distributed rewards via the rewarder role
- 8-hour vesting period for distributed rewards
- Configurable cooldown period for withdrawals (governance controlled)
- Blacklist functionality for compliance

- Homepage: https://www.avantprotocol.com/
- GitHub: https://github.com/Avant-Protocol/avUSD-Contracts
- Contract: https://snowtrace.io/address/0x06d47f3fb376649c3a9dafe069b3d6e35572219e
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class AvantVault(ERC4626Vault):
    """Avant Protocol vault support.

    Avant savUSD vault allows users to stake avUSD and earn yield from protocol
    reward distributions. The vault implements a vesting mechanism for rewards
    (8 hours) and a configurable cooldown period for withdrawals.

    - Homepage: https://www.avantprotocol.com/
    - GitHub: https://github.com/Avant-Protocol/avUSD-Contracts
    - Contract: https://snowtrace.io/address/0x06d47f3fb376649c3a9dafe069b3d6e35572219e
    """

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        Avant savUSD vault does not charge deposit/withdrawal fees at the
        smart contract level.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        Avant does not charge management fees. Yield comes directly from
        protocol reward distributions managed by governance.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        Avant does not charge performance fees on the savUSD vault.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        Avant savUSD vault has a governance-configurable cooldown period
        (MAX_COOLDOWN_DURATION is 90 days). Users may need to initiate a
        cooldown and wait before withdrawing depending on the current
        cooldown duration setting.

        The actual cooldown duration can be read from cooldownDuration().
        This returns a conservative estimate.
        """
        return datetime.timedelta(days=7)

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the Avant Protocol homepage.
        """
        return "https://www.avantprotocol.com/"
