"""Gearbox Protocol vault support.

Gearbox Protocol is a composable leverage protocol that provides lending pools
compatible with ERC-4626. The PoolV3 contract manages liquidity deposits from
passive lenders and borrowing by credit accounts.

- Homepage: https://gearbox.finance/
- App: https://app.gearbox.fi/
- Documentation: https://docs.gearbox.finance/
- GitHub: https://github.com/Gearbox-protocol/core-v3
- Twitter: https://x.com/GearboxProtocol
- Audits: https://docs.gearbox.finance/risk-and-security/audits-bug-bounty

Fee structure:

- Withdrawal fee: 0% for passive lenders
- APY spread: ~50% between borrower rate and lender rate goes to DAO
- For passive lenders, fees are internalised in the share price

Example vault contracts:

- Hyperithm USDT0 Pool on Plasma: https://plasmascan.to/address/0xb74760fd26400030620027dd29d19d74d514700e
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class GearboxVault(ERC4626Vault):
    """Gearbox Protocol PoolV3 vault.

    Gearbox pools allow passive liquidity providers to deposit assets and earn
    yield from borrowers (credit accounts) who pay interest on borrowed funds.

    Key features:

    - ERC-4626 compatible lending pool
    - Yield from institutional-grade leveraged positions
    - Zero withdrawal fees for passive lenders
    - Credit manager integration for leveraged borrowing

    - Homepage: https://gearbox.finance/
    - App: https://app.gearbox.fi/
    - Documentation: https://docs.gearbox.finance/
    - GitHub: https://github.com/Gearbox-protocol/core-v3
    - Twitter: https://x.com/GearboxProtocol
    """

    def has_custom_fees(self) -> bool:
        """Gearbox pools have no custom deposit/withdrawal fees for passive lenders."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """No management fee for passive lenders."""
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """No performance fee for passive lenders (fees internalised in share price)."""
        return 0.0

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Gearbox pools have no lock-up for passive lenders."""
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get link to the Gearbox app."""
        return "https://app.gearbox.fi/"
