"""BaseVol vault support on Base.

`BaseVol <https://basevol.com/>`__ is an onchain options protocol on Base,
offering zero-day-to-expiry (0DTE) binary options trading and AI-managed
yield vaults. The protocol was incubated by Base (Batch #001, 2nd place global)
and raised $3M in a seed round led by Neoclassic Capital.

The Genesis Vault systematically deploys funds using a 90/10 allocation:
90% to USDC lending on Morpho/Spark, and 10% to 0DTE options selling.
The vault is managed by A.T.M. (Autonomous Trading Machine), an AI agent
that handles trade sizing, hedging, and settlement.

Vault types:

- Genesis Vault (gVAULT) - neutral market strategy
- High Vol Vault (gVAULT-over101-under99) - high volatility strategy
- 99 Over Vault (gVAULT-over99) - directional strategy
- 101 Under Vault (gVAULT-under101) - directional strategy

The vaults use Diamond proxy (EIP-2535) architecture with multiple facets.

Security:

- Audited by `FailSafe <https://getfailsafe.com/basevol-smart-contract-audit/>`__

Vault addresses sourced from `DefiLlama adapters
<https://github.com/DefiLlama/DefiLlama-Adapters/blob/3a63c0665de8d6a89f85ff360c5dc61fd40e72dd/projects/basevol/index.js#L6>`__
and `BaseVol documentation <https://basevol.gitbook.io/docs/developers/contracts>`__.

- Homepage: https://basevol.com/
- Documentation: https://basevol.gitbook.io/docs/
- Twitter: https://x.com/BaseVolApp
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class BaseVolVault(ERC4626Vault):
    """BaseVol vault support on Base.

    BaseVol is an onchain options protocol offering AI-managed yield vaults
    that combine 0DTE binary options selling with stablecoin lending.

    The vaults use Diamond proxy (EIP-2535) architecture.
    Fee structure is not publicly documented in the smart contracts.

    - Homepage: https://basevol.com/
    - Documentation: https://basevol.gitbook.io/docs/
    - Twitter: https://x.com/BaseVolApp
    - Audit: https://getfailsafe.com/basevol-smart-contract-audit/
    """

    def has_custom_fees(self) -> bool:
        """Whether this vault has deposit/withdrawal fees.

        Fee structure is not publicly documented.
        """
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current management fee as a percent.

        Fee structure is not publicly documented for BaseVol vaults.

        :return:
            None - fee unknown
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Get the current performance fee as a percent.

        Fee structure is not publicly documented for BaseVol vaults.

        :return:
            None - fee unknown
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Get estimated lock-up period if any.

        Lock-up period is not documented.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get the vault's web UI link.

        :param referral:
            Optional referral code (not used currently).

        :return:
            Link to the BaseVol homepage.
        """
        return "https://basevol.com/"
