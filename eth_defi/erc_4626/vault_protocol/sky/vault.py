"""Sky protocol vault support.

Sky (formerly MakerDAO) is one of the oldest and most established DeFi protocols.
The protocol provides the USDS stablecoin and allows users to earn yield through
staking USDS in Sky savings vaults.

Sky offers ERC-4626 compliant tokenised vaults:

- **stUSDS** (Staked USDS): USDS lending against staked SKY collateral
- **sUSDS** (Savings USDS): The Sky Savings Rate vault for USDS
- **sDAI** (Savings DAI): The original MakerDAO DAI Savings Rate vault

The vaults accumulate yield through the ``drip()`` mechanism. Staked USDS
withdrawals depend on USDS available after outstanding loans and auctions.

The sDAI vault allows users to deposit DAI and earn the DAI Savings Rate (DSR)
through MakerDAO's Pot contract.

Key features:

- No deposit/withdrawal fees at the smart contract level
- No scheduled withdrawal lock-up
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
from eth_defi.vault.base import INSTANT_WITHDRAWAL_PERIOD, WithdrawalPeriod
from eth_defi.vault.withdrawal_capacity_readers import STAKED_USDS_ADDRESS, fetch_staked_usds_observation

logger = logging.getLogger(__name__)


class SkyVault(ERC4626Vault):
    """Sky protocol vault support.

    stUSDS earns yield from SKY-backed lending, sUSDS earns the Sky Savings
    Rate and sDAI earns the DAI Savings Rate.

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

    def fetch_withdrawal_capacity_observation(self, block_identifier: BlockIdentifier) -> dict:
        """Read the capacity of the reviewed Ethereum Staked USDS deployment.

        The Sky adapter serves several products. Only stUSDS uses the
        SKY-backed lending and auction accounting in this reader.

        :param block_identifier: Numeric observation block or block tag.
        :return: The measured USDS capacity observation, or an unknown result.
        """
        if self.chain_id == 1 and self.address.lower() == STAKED_USDS_ADDRESS:
            block = block_identifier if isinstance(block_identifier, int) else self.web3.eth.get_block(block_identifier)["number"]
            return fetch_staked_usds_observation(self, block)
        return super().fetch_withdrawal_capacity_observation(block_identifier)

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

        Sky vaults have no scheduled lock-up. stUSDS exits still depend on
        available USDS liquidity.
        """
        return datetime.timedelta(days=0)

    def get_withdrawal_period(self) -> WithdrawalPeriod:
        return INSTANT_WITHDRAWAL_PERIOD

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the Sky savings page.
        """
        return "https://sky.money/"
