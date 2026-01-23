"""aarnâ vault support.

aarnâ is an Agentic Onchain Treasury (AOT) protocol that uses AI agents to manage
DeFi complexity through programmable, transparent agent governance.
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class AarnaVault(ERC4626Vault):
    """aarnâ protocol vault support.

    aarnâ develops an Agentic Onchain Treasury (AOT), a fully autonomous onchain treasury
    that allocates, rotates, and secures assets through programmable, transparent agent governance.
    The system uses AI agents to manage decentralised finance complexity.

    - Homepage: https://www.aarna.ai/
    - App: https://engine.aarna.ai/
    - Documentation: https://docs.aarna.ai/
    - Example vault (atvPTmax Token): https://etherscan.io/address/0xb9c1344105faa4681bc7ffd68c5c526da61f2ae8

    The vault uses a queue-based settlement system with multi-adapter strategy coordination.
    """

    def has_custom_fees(self) -> bool:
        """Deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fee information not publicly documented."""
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fee information not publicly documented."""
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Lock-up period not documented.

        The vault has a queue-based withdrawal system which may introduce delays.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Link to the aarnâ app."""
        return "https://engine.aarna.ai/"
