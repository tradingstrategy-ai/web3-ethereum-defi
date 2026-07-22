"""cSigma Finance vault support.

cSigma Finance is a blockchain-based protocol that connects global borrowers and lenders
by standardising and streamlining the commercial lending process. The protocol offers
crypto-uncorrelated yield opportunities to stablecoin holders through tokenised real-world
assets (RWA) and risk-adjusted DeFi strategies.

csUSD allows users to deposit stablecoins and earn yield from two sources:
- RWA credit markets
- Onchain DeFi yield strategies

The protocol dynamically allocates between these sources based on market conditions
to optimise yield performance.

- Homepage: https://csigma.finance
- csUSD vault: https://www.csigma.finance/csusd
- Documentation: https://csigma.medium.com/
- Twitter: https://x.com/csigmafinance
- Contract: https://etherscan.io/address/0xd5d097f278a735d0a3c609deee71234cac14b47e
"""

import datetime
import logging

from eth_typing import BlockIdentifier, HexAddress

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.csigma.deposit_redeem import CsigmaDepositManager
from eth_defi.vault.deposit_redeem import VaultDepositManager, VaultDepositManagerCapability

logger = logging.getLogger(__name__)


#: cSigma V2 pool with verified synchronous ERC-4626 lifecycle support.
CSIGMA_V2_POOL_ADDRESS: HexAddress = "0x438982ea288763370946625fd76c2508ee1fb229"

#: Ethereum mainnet, where the representative V2 pool was verified.
CSIGMA_V2_POOL_CHAIN_ID = 1


class CsigmaVault(ERC4626Vault):
    """cSigma Finance vault support.

    cSigma Finance is a DeFi protocol focused on providing fixed-rate, real-world yields
    for stablecoins through tokenised RWA private credit. The protocol has tokenised
    over $80 million in business loans from mid-market companies.

    - Homepage: https://csigma.finance
    - csUSD vault: https://www.csigma.finance/csusd
    - Medium: https://csigma.medium.com/
    - Twitter: https://x.com/csigmafinance
    - Contract: https://etherscan.io/address/0xd5d097f278a735d0a3c609deee71234cac14b47e
    """

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        cSigma does not charge deposit/withdrawal fees at the smart contract level.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        Generated: Human can add details later based on protocol documentation.

        :return:
            0.1 = 10%
        """
        return 0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        Generated: Human can add details later based on protocol documentation.

        :return:
            0.1 = 10%
        """
        return 0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        cSigma uses a First-In-First-Out queue for redemptions when vault reserves
        are depleted. The lock-up period depends on RWA credit market liquidity.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not supported by cSigma currently).

        :return:
            Link to the csUSD vault page.
        """
        return "https://edge.csigma.finance/"

    def get_deposit_manager(self) -> VaultDepositManager:
        """Create the manager appropriate for this cSigma deployment.

        :return:
            Capacity-aware manager for the verified V2 pool; otherwise the
            unadvertised generic ERC-4626 manager inherited from the base class.
        """
        if self.chain_id == CSIGMA_V2_POOL_CHAIN_ID and self.address.lower() == CSIGMA_V2_POOL_ADDRESS:
            return CsigmaDepositManager(self)
        return super().get_deposit_manager()

    def get_deposit_manager_capability(self) -> VaultDepositManagerCapability | None:
        """Declare support only for the verified cSigma V2 pool.

        :return:
            Static support metadata for the V2 pool, otherwise ``None``.
        """
        if self.chain_id == CSIGMA_V2_POOL_CHAIN_ID and self.address.lower() == CSIGMA_V2_POOL_ADDRESS:
            return self.get_synchronous_deposit_manager_capability()
        return super().get_deposit_manager_capability()
