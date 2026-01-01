"""Maple Finance Syrup vault support.

Maple Finance is an institutional-grade DeFi lending protocol. The Syrup protocol
provides permissionless access to yield-bearing tokens (syrupUSDC, syrupUSDT) that
represent deposits in Maple's institutional lending pools.

When users deposit USDC or USDT into Syrup, they receive syrup tokens in return.
These tokens are yield-bearing LP tokens, similar to Aave's aUSDC or Compound's cUSDC.
The underlying deposits are lent to vetted, institutional borrowers like market makers
and trading firms, with loans secured by overcollateralised digital asset collateral.

- Homepage: https://maple.finance/
- App: https://app.maple.finance/earn
- Documentation: https://docs.maple.finance/
- GitHub: https://github.com/maple-labs/maple-core-v2
- Twitter: https://x.com/maplefinance

Example vault contracts:

- syrupUSDC: https://etherscan.io/address/0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b
- syrupUSDT: https://etherscan.io/address/0x356b8d89c1e1239cbbb9de4815c39a1474d5ba7d
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class SyrupVault(ERC4626Vault):
    """Maple Finance Syrup vault.

    Syrup is a permissionless yield product by Maple Finance that provides access
    to institutional-grade lending yields. Users deposit stablecoins (USDC, USDT)
    and receive yield-bearing syrup tokens in return.

    Key features:

    - Institutional yield: Access to real-world lending rates typically higher than
      standard DeFi lending (10-15% APY)
    - Overcollateralised: Loans are secured by digital asset collateral (BTC, ETH)
      at ratios significantly above 100%
    - Permissionless: Unlike Maple's core institutional pools, Syrup is accessible
      to anyone with a DeFi wallet

    - Homepage: https://maple.finance/
    - App: https://app.maple.finance/earn
    - Documentation: https://docs.maple.finance/
    - GitHub: https://github.com/maple-labs/maple-core-v2
    - Twitter: https://x.com/maplefinance
    """

    def has_custom_fees(self) -> bool:
        """Maple Syrup vaults use internalised fee structure."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fees are internalised in the share price."""
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fees are internalised in the share price."""
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Syrup has withdrawal request mechanism.

        While deposits can be made instantly, withdrawals may require going through
        a withdrawal queue depending on pool liquidity. The withdrawal window is
        typically cyclical.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:
        """Get link to the Maple Syrup earn page."""
        return "https://app.maple.finance/earn"
