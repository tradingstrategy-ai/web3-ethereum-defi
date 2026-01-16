"""Fluid fToken vault support.

Fluid is a DeFi lending protocol by Instadapp featuring ERC-4626 compliant fToken vaults.
Users deposit assets to earn yield through the liquidity layer.

- `Protocol homepage <https://fluid.io/>`__
- `Documentation <https://docs.fluid.instadapp.io/>`__
- `GitHub repository <https://github.com/Instadapp/fluid-contracts-public>`__
- `Twitter <https://x.com/0xfluid>`__
- `Example fToken on Plasma <https://plasmascan.to/address/0x1DD4b13fcAE900C60a350589BE8052959D2Ed27B>`__

Fee structure:

- Fluid fTokens have fees internalised through the exchange price mechanism
- Interest accrues to the share price over time
- No explicit deposit/withdraw fees

"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class FluidVault(ERC4626Vault):
    """Fluid fToken vault support.

    Fluid is a DeFi lending protocol by Instadapp where users can deposit assets
    to earn yield. The protocol uses ERC-4626 compliant fTokens to represent
    user deposits.

    - `Protocol homepage <https://fluid.io/>`__
    - `Documentation <https://docs.fluid.instadapp.io/>`__
    - `GitHub repository <https://github.com/Instadapp/fluid-contracts-public>`__
    - `Twitter <https://x.com/0xfluid>`__

    Key features:

    - ERC-4626 compliant fTokens for lending
    - Fees are internalised into the share price through interest accrual
    - No explicit deposit or withdrawal fees
    """

    def has_custom_fees(self) -> bool:
        """Fluid has no deposit/withdrawal fees."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Fluid has no management fee.

        Interest spread is handled at the protocol level, not as an explicit fee.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fluid has no explicit performance fee.

        Fees are internalised in the interest rate mechanism.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Fluid fTokens have instant liquidity - no lock-up period."""
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        """Get the Fluid protocol link.

        Since Fluid doesn't have individual vault pages, we link to the main app.
        """
        return "https://fluid.io/"
