"""Axis StakedUSDx rewards vault support.

Axis's reviewed Plasma deployment accepts USDx and issues sUSDx shares. Its
redemption path is asynchronous, so this reader deliberately does not advertise
a generic synchronous deposit manager until the request-and-claim lifecycle has
been implemented and certified on an Anvil fork.

- Homepage: https://www.axis.to/
- Product documentation: https://docs.axis.to/susdx-the-rewards-vault/susdx
- Contract reference: https://docs.axis.to/reference/staking-contracts
- Reviewed deployment: https://plasmaexplorer.com/address/0x13A099765B34b3aAFedb8698CF7fd418E7730012
"""

import datetime

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.axis.constants import AXIS_SHORT_DESCRIPTION
from eth_defi.types import Percent
from eth_defi.vault.fee import VaultFeeMode


class AxisVault(ERC4626Vault):
    """Axis's USDx staking rewards vault.

    The StakedUSDx vault mints non-rebasing sUSDx shares for deposited USDx.
    Its share price grows as funded rewards vest. Axis documents redemptions as
    ERC-7540 requests that are later serviced and claimed, which differs from
    the base class's immediate ERC-4626 redemption assumption.

    - Product documentation: https://docs.axis.to/susdx-the-rewards-vault/susdx
    - Redemption guide: https://docs.axis.to/susdx-the-rewards-vault/stake-and-unstake
    """

    @property
    def short_description(self) -> str:
        """Return the static product summary used in scanner output.

        The description is maintained with the address-routed Axis deployment
        constants so an automatic rescan preserves the same listing metadata
        as the historical-cache repair command.

        :return:
            Axis's concise StakedUSDx product description.
        """
        return AXIS_SHORT_DESCRIPTION

    def get_fee_mode(self) -> VaultFeeMode:  # noqa: PLR6301
        """Return Axis's internalised StakedUSDx fee accounting mode.

        Rewards vest into the vault exchange rate, while Axis sets the
        investor-facing management, performance, deposit and withdrawal fees
        to zero.

        :return:
            Internalised-skimming fee accounting.
        """
        return VaultFeeMode.internalised_skimming

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent:  # noqa: PLR6301
        """Return Axis's zero management fee.

        The argument is retained for the common vault-reader interface.

        :param block_identifier:
            Block at which a fee would be read.
        :return:
            ``0.0``, the configured management fee fraction.
        """
        del block_identifier
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent:  # noqa: PLR6301
        """Return Axis's zero performance fee.

        The argument is retained for the common vault-reader interface.

        :param block_identifier:
            Block at which a fee would be read.
        :return:
            ``0.0``, the configured performance fee fraction.
        """
        del block_identifier
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:  # noqa: PLR6301
        """Return Axis's documented StakedUSDx redemption cooldown.

        Axis documents a seven-day policy cooldown between a holder's ERC-7540
        redemption request and its later servicing. This is an estimate rather
        than a settlement guarantee, because the redemption servicer controls
        when a matured request becomes claimable.

        :return:
            Seven days, the currently documented redemption cooldown.
        """
        return datetime.timedelta(days=7)

    def get_link(self, referral: str | None = None) -> str:  # noqa: PLR6301
        """Return the public Axis application link.

        Axis publishes its user-facing staking interface under its application
        domain. Referral parameters are not part of Axis's documented URL
        format and are therefore ignored.

        :param referral:
            Unsupported referral code.
        :return:
            Axis's public application URL.
        """
        del referral
        return "https://app.axis.to/"
