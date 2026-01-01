"""Deltr protocol vault support.

- Contract: https://etherscan.io/address/0xa7a31e6a81300120b7c4488ec3126bc1ad11f320
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class DeltrVault(ERC4626Vault):
    """Deltr protocol vault support.

    StakeddUSD vault allows users to deposit dUSD and receive sdUSD shares.
    Yield is distributed through owner-initiated reward deposits.

    - Unknown protocol
    """

    def has_custom_fees(self) -> bool:
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        # Generated: Human can add details later
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        # Generated: Human can add details later
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        return None
