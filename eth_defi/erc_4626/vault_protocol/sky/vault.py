"""Sky protocol vault support.

Sky (formerly MakerDAO) is one of the oldest and most established DeFi protocols.
The protocol provides the USDS stablecoin and allows users to earn yield through
staking USDS in Sky savings vaults.

Sky offers ERC-4626 compliant tokenised vaults:

- **stUSDS** (Staked USDS): The original Sky savings vault for USDS
- **sUSDS** (Savings USDS): An additional savings vault with the same mechanics
- **sDAI** (Savings DAI): The original MakerDAO DAI Savings Rate vault

The USDS vaults allow users to stake USDS and earn the Sky Savings Rate (SSR). The
vaults accumulate yield through the ``drip()`` mechanism which accrues interest
based on the ``chi`` rate accumulator.

The sDAI vault allows users to deposit DAI and earn the DAI Savings Rate (DSR)
through MakerDAO's Pot contract.

Key features:

- No deposit/withdrawal fees at the smart contract level
- Yield accrues through the Sky Savings Rate (SSR) or DAI Savings Rate (DSR)
- Instant deposits and withdrawals
- Fully decentralised and battle-tested infrastructure

- Homepage: https://sky.money/
- Documentation: https://developers.sky.money/
- GitHub: https://github.com/sky-ecosystem/stusds
- Twitter: https://x.com/SkyEcosystem
- stUSDS Contract: https://etherscan.io/address/0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9
- sUSDS Contract: https://etherscan.io/address/0xa3931d71877c0e7a3148cb7eb4463524fec27fbd
- sDAI Contract: https://etherscan.io/address/0x83f20f44975d03b1b09e64809b757c47f942beea
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class SkyVault(ERC4626Vault):
    """Sky protocol vault support.

    Sky savings vaults (stUSDS, sUSDS, and sDAI) allow users to stake stablecoins
    and earn yield. The USDS vaults earn the Sky Savings Rate while sDAI earns the
    DAI Savings Rate. The vaults accumulate yield through rate accumulators which
    are updated via the ``drip()`` function.

    - Homepage: https://sky.money/
    - Documentation: https://developers.sky.money/
    - GitHub: https://github.com/sky-ecosystem/stusds
    - Twitter: https://x.com/SkyEcosystem
    - stUSDS Contract: https://etherscan.io/address/0x99cd4ec3f88a45940936f469e4bb72a2a701eeb9
    - sUSDS Contract: https://etherscan.io/address/0xa3931d71877c0e7a3148cb7eb4463524fec27fbd
    - sDAI Contract: https://etherscan.io/address/0x83f20f44975d03b1b09e64809b757c47f942beea
    """

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        Sky stUSDS vault does not charge deposit/withdrawal fees.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        Sky does not charge management fees. Yield comes directly from the
        Sky Savings Rate.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        Sky does not charge performance fees on the stUSDS vault.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        Sky stUSDS vault has no lock-up period. Withdrawals are instant.
        """
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the Sky savings page.
        """
        return "https://sky.money/"
