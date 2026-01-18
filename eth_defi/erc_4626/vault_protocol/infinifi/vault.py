"""infiniFi staked token vault support.

infiniFi is a DeFi protocol that recreates fractional reserve banking on-chain,
enabling users to mint receipt tokens (iUSD) against collateral and stake them
for yield through siUSD (liquid staking) or liUSD (locked staking).
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class InfiniFiVault(ERC4626Vault):
    """infiniFi vault support.

    infiniFi is a DeFi protocol that replicates traditional fractional reserve
    banking on Ethereum. Users deposit stablecoins to mint iUSD receipt tokens,
    which can then be staked for yield:

    - siUSD (liquid staking): Lower yield, instant liquidity
    - liUSD (locked staking): Higher yield, locked for 1-13 weeks

    The protocol allocates capital into yield strategies through integrations
    with Aave, Pendle, Fluid, and Ethena, while maintaining reserves for
    redemptions.

    In case of losses, there is an explicit waterfall: locked liUSD holders
    absorb losses first, then siUSD stakers, and finally plain iUSD holders.

    - Homepage: https://infinifi.xyz/
    - App: https://app.infinifi.xyz/deposit
    - Documentation: https://research.nansen.ai/articles/understanding-infini-fi-the-on-chain-fractional-reserve-banking-protocol
    - Github: https://github.com/InfiniFi-Labs/infinifi-protocol
    - DefiLlama: https://defillama.com/protocol/infinifi
    - siUSD vault contract: https://etherscan.io/address/0xdbdc1ef57537e34680b898e1febd3d68c7389bcb
    """

    def has_custom_fees(self) -> bool:
        """infiniFi does not have explicit deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """infiniFi management fees.

        infiniFi does not charge explicit management fees.
        Yield is generated through protocol integrations and distributed
        via epoch-based reward smoothing.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """infiniFi performance fees.

        Fee information not publicly documented.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """siUSD (liquid staking) has no lock-up.

        Note: liUSD (locked staking) has 1-13 week lock-up periods,
        but this vault class represents the liquid siUSD staking.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get the link to the infiniFi deposit page."""
        return "https://app.infinifi.xyz/deposit"
