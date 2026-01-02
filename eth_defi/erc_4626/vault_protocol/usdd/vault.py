"""Decentralized USD (USDD) protocol vault support.

USDD is a decentralized stablecoin protocol that provides the USDD stablecoin
and allows users to earn yield through staking USDD in sUSDD savings vaults.

USDD offers ERC-4626 compliant tokenised vaults on multiple chains:

- **sUSDD** (Savings USDD) on Ethereum
- **sUSDD** (Savings USDD) on BNB Chain

Key features:

- No deposit/withdrawal fees at the smart contract level
- Instant deposits and withdrawals
- Cross-chain deployment on Ethereum and BNB Chain

- Homepage: https://usdd.io/
- Documentation: https://docs.usdd.io/
- sUSDD Contract (Ethereum): https://etherscan.io/address/0xC5d6A7B61d18AfA11435a889557b068BB9f29930
- sUSDD Contract (BNB Chain): https://bscscan.com/address/0x8bA9dA757d1D66c58b1ae7e2ED6c04087348A82d
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class USSDVault(ERC4626Vault):
    """Decentralized USD (USDD) protocol vault support.

    USDD savings vaults (sUSDD) allow users to stake USDD and earn yield.
    The vaults are deployed on both Ethereum and BNB Chain.

    - Homepage: https://usdd.io/
    - Documentation: https://docs.usdd.io/
    - sUSDD Contract (Ethereum): https://etherscan.io/address/0xC5d6A7B61d18AfA11435a889557b068BB9f29930
    - sUSDD Contract (BNB Chain): https://bscscan.com/address/0x8bA9dA757d1D66c58b1ae7e2ED6c04087348A82d
    """

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        USDD sUSDD vault does not charge deposit/withdrawal fees.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        USDD does not charge management fees.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        USDD does not charge performance fees on the sUSDD vault.

        :return:
            0.1 = 10%
        """
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        USDD sUSDD vault has no lock-up period. Withdrawals are instant.
        """
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the USDD homepage.
        """
        return "https://usdd.io/"
