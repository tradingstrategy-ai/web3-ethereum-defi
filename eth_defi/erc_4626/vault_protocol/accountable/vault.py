"""Accountable Capital vault support."""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class AccountableVault(ERC4626Vault):
    """Accountable Capital vault support.

    Accountable Capital develops blockchain-based financial verification technology
    that enables organisations and investors to demonstrate solvency, liquidity,
    and compliance through transparent, verifiable attestations. The platform
    combines cryptographic proofs with auditable financial data to enhance trust
    across Web3 and traditional finance.

    Accountable vaults implement ERC-7540 async redemption pattern with a queue
    system for processing withdrawal requests.

    - Homepage: https://www.accountable.capital/
    - Twitter: https://x.com/AccountableData
    - No public GitHub repository available for smart contracts
    - Example contract: https://monadscan.com/address/0x58ba69b289De313E66A13B7D1F822Fc98b970554
    """

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Management fee is not publicly available.

        Accountable vaults do not expose fee information on-chain.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Performance fee is not publicly available.

        Accountable vaults do not expose fee information on-chain.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Accountable vaults use async redemption queue.

        Lock-up period depends on the vault strategy and available liquidity.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Return the protocol homepage link.

        Accountable does not have individual vault pages.
        """
        return "https://www.accountable.capital/"
