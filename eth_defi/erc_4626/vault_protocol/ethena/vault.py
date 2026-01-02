"""Ethena protocol vault support.

Ethena is a synthetic dollar protocol built on Ethereum that provides a crypto-native
solution for money, USDe, alongside a globally accessible dollar savings asset, sUSDe.

USDe is a synthetic dollar backed by crypto assets and corresponding short futures
positions, maintaining its $1 peg through delta-neutral hedging. sUSDe (staked USDe)
allows users to stake USDe and earn yield from funding rates and staking rewards.

Key features:

- No management or performance fees at the smart contract level
- Yield comes from protocol funding rates and staking rewards
- Cooldown period may be required for withdrawals (configurable by governance)
- Fully backed by USDe

- Homepage: https://ethena.fi/
- Documentation: https://docs.ethena.fi/
- GitHub: https://github.com/ethena-labs
- Twitter: https://x.com/ethena_labs
- Contract: https://etherscan.io/address/0x9d39a5de30e57443bff2a8307a4256c8797a3497
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class EthenaVault(ERC4626Vault):
    """Ethena protocol vault support.

    Ethena sUSDe vault allows users to stake USDe and earn yield from protocol
    funding rates and staking rewards. The vault implements a flexible cooldown
    mechanism controlled by governance.

    - Homepage: https://ethena.fi/
    - Documentation: https://docs.ethena.fi/
    - GitHub: https://github.com/ethena-labs
    - Twitter: https://x.com/ethena_labs
    - Contract: https://etherscan.io/address/0x9d39a5de30e57443bff2a8307a4256c8797a3497
    """

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        Ethena sUSDe vault does not charge deposit/withdrawal fees at the
        smart contract level.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        Ethena does not charge management fees. Yield comes directly from
        protocol funding rates and staking rewards.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        Ethena does not charge performance fees on the sUSDe vault.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        Ethena sUSDe vault may have a governance-configurable cooldown period
        of up to 90 days. When cooldown is enabled, users must initiate a
        cooldown and wait before withdrawing. When cooldown is disabled
        (duration = 0), withdrawals are instant.

        This returns the maximum possible cooldown for conservative estimation.
        The actual cooldown duration can be read from the contract.
        """
        return datetime.timedelta(days=7)

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the Ethena staking page.
        """
        return "https://ethena.fi/"
