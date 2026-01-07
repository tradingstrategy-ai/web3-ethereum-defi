"""Resolv protocol vault support.

Resolv is a protocol that maintains USR, a stablecoin fully backed by ETH and BTC
and pegged to the US Dollar. The stablecoin's delta-neutral design ensures price
stability, and is backed by an innovative insurance pool (RLP) to provide additional
security and overcollateralisation.

The wstUSR (Wrapped stUSR) vault is an ERC-4626 compliant wrapper around the rebasing
stUSR (staked USR) token. stUSR is a yield-bearing token that automatically compounds
returns generated from the basis trade. wstUSR provides a non-rebasing representation
of stUSR, making it suitable for DeFi integrations that don't support rebasing tokens.

Key features:

- ERC-4626 wrapper around rebasing stUSR token
- No deposit/withdrawal fees at the smart contract level
- Yield accrues through the underlying stUSR rebasing mechanism
- Instant deposits and withdrawals

- Homepage: https://resolv.xyz/
- Documentation: https://docs.resolv.xyz/
- Twitter: https://x.com/ResolvLabs
- Contract: https://etherscan.io/address/0x1202f5c7b4b9e47a1a484e8b270be34dbbc75055
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class ResolvVault(ERC4626Vault):
    """Resolv protocol vault support.

    wstUSR (Wrapped stUSR) is an ERC-4626 wrapper around the rebasing stUSR token.
    stUSR is a yield-bearing staked version of USR that compounds returns from
    the Resolv delta-neutral basis trading strategy.

    - Homepage: https://resolv.xyz/
    - Documentation: https://docs.resolv.xyz/
    - Twitter: https://x.com/ResolvLabs
    - Contract: https://etherscan.io/address/0x1202f5c7b4b9e47a1a484e8b270be34dbbc75055
    """

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        Resolv wstUSR vault does not charge deposit/withdrawal fees at the smart
        contract level. Yield is distributed through the underlying stUSR rebasing.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        Resolv does not charge management fees on the wstUSR wrapper.
        Yield comes from the underlying stUSR rebasing mechanism.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        Resolv does not charge performance fees on the wstUSR vault.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        Resolv wstUSR vault has no lock-up period. Withdrawals are instant.
        """
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the Resolv homepage.
        """
        return "https://resolv.xyz/"
