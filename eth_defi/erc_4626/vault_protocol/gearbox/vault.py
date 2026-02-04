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
- GHO v3 Pool on Ethereum: https://etherscan.io/address/0x4d56c9cba373ad39df69eb18f076b7348000ae09
"""

import datetime
import logging

from eth_typing import BlockIdentifier
from web3 import Web3

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.vault.base import (
    DEPOSIT_CLOSED_PAUSED,
    REDEMPTION_CLOSED_INSUFFICIENT_LIQUIDITY,
    REDEMPTION_CLOSED_PAUSED,
)

logger = logging.getLogger(__name__)

#: Minimal ABI for Gearbox PoolV3 functions not in standard ERC-4626
GEARBOX_POOL_V3_ABI = [
    {"inputs": [], "name": "paused", "outputs": [{"type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "availableLiquidity", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
]


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

    def _get_gearbox_contract(self):
        """Get contract instance with Gearbox-specific ABI."""
        return self.web3.eth.contract(address=self.address, abi=GEARBOX_POOL_V3_ABI)

    def fetch_deposit_closed_reason(self) -> str | None:
        """Check if deposits are closed.

        Gearbox pools can be paused by governance.
        Deposits are generally always open unless paused.

        Note: We don't use maxDeposit(address(0)) because Gearbox's implementation
        checks owner balance, making it unsuitable as a global availability check.
        """
        try:
            gearbox_contract = self._get_gearbox_contract()
            paused = gearbox_contract.functions.paused().call()
            if paused:
                return f"{DEPOSIT_CLOSED_PAUSED} (paused=true)"
        except Exception:
            pass
        return None

    def fetch_redemption_closed_reason(self) -> str | None:
        """Check if redemptions are closed due to paused state or no liquidity.

        Gearbox pools may have limited withdrawal liquidity when utilisation is high.
        All deposited funds could be lent to credit accounts, leaving no liquidity
        for immediate redemptions.

        Note: We don't use maxRedeem(address(0)) because Gearbox's implementation
        is: `Math.min(balanceOf(owner), convertToShares(availableLiquidity()))`.
        Since balanceOf(address(0)) is always 0, maxRedeem(address(0)) always returns 0
        regardless of actual available liquidity.
        """
        try:
            gearbox_contract = self._get_gearbox_contract()

            # Check if paused first
            paused = gearbox_contract.functions.paused().call()
            if paused:
                return f"{REDEMPTION_CLOSED_PAUSED} (paused=true)"

            # Check available liquidity
            available_liquidity = gearbox_contract.functions.availableLiquidity().call()
            if available_liquidity == 0:
                return f"{REDEMPTION_CLOSED_INSUFFICIENT_LIQUIDITY} (availableLiquidity=0)"

        except Exception:
            pass
        return None
