"""Renalta vault support.

Unverified smart contract source code - treat with caution.
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class RenaltaVault(ERC4626Vault):
    """Renalta vaults.

    - Homepage: https://renalta.com/
    - Basescan (unverified): https://basescan.org/address/0x0ff79b6d6c0fb5faf54bd26db5ce97062a105f81

    .. warning::

        Unverified smart contract source code. No Github repository. No audits.
        Treat with extreme caution.

    """

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Unknown fees - unverified contract."""
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Unknown fees - unverified contract."""
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Lock-up unknown."""
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get link to the Renalta homepage."""
        return "https://renalta.com/"
