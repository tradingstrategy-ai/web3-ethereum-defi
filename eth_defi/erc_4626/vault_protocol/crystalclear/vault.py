"""CrystalClear algorithmic trading vault support.

- CrystalClear builds ERC-4626 vaults on HyperEVM that trade perpetuals on HyperCore
- UUPS proxy pattern with OpenZeppelin v5, one shared implementation per vault
- USDC denominated, 9 share decimals
- Two-step withdrawal: ``requestWithdraw()`` then ``claimWithdraw()``
- Performance fee (20%) charged at redemption (externalised)
- Vault equity lives on HyperCore; ``totalAssets()`` reflects the full account value

- Homepage: https://crystalclear.finance/
- App: https://app.crystalclear.finance/app.html#vaults
- Docs: https://crystalclear.gitbook.io/crystalclear-docs/
- Verified contracts on Hyperscan: https://www.hyperscan.com/
"""

import datetime
import logging
from functools import cached_property

from eth_typing import BlockIdentifier
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class CrystalClearVault(ERC4626Vault):
    """CrystalClear algorithmic trading vaults on HyperEVM.

    CrystalClear deploys ERC-4626 vaults that trade perpetuals on Hyperliquid's
    HyperCore via HyperEVM smart contracts. Each vault runs a distinct algorithmic
    strategy (e.g. Onyx, Amber, Ruby) with automated two-week rebalancing cycles.

    Key features:

    - ``performanceFeeBps()`` returns the performance fee in basis points (e.g. 2000 = 20%)
    - Two-step withdrawal via ``requestWithdraw()`` / ``claimWithdraw()``
    - ``paused()`` indicates whether the vault is temporarily halted
    - ``maxTVL()`` returns the vault's TVL cap in asset units

    - Homepage: https://crystalclear.finance/
    - App: https://app.crystalclear.finance/app.html#vaults
    - Docs: https://crystalclear.gitbook.io/crystalclear-docs/
    - Example vault (Onyx): https://www.hyperscan.com/address/0x231f66c336512e897855420a2788B83e164C6Adf
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get the CrystalVault contract with the custom ABI."""
        return get_deployed_contract(
            self.web3,
            "crystalclear/CrystalVault.json",
            self.vault_address,
        )

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """CrystalClear has no management fee.

        Only a performance fee at redemption.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Read performance fee from on-chain ``performanceFeeBps()``.

        Returns the fee as a ratio (e.g. 0.20 for 20%).
        """
        bps = self.vault_contract.functions.performanceFeeBps().call(block_identifier=block_identifier)
        return bps / 10_000

    def has_custom_fees(self) -> bool:
        """CrystalClear has on-chain fee reading via ``performanceFeeBps()``."""
        return True

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Two-step withdrawal with no fixed lock-up period.

        Withdrawals are processed via ``requestWithdraw()`` + ``claimWithdraw()``.
        The 1-hour deposit lock is enforced on-chain for MEV protection.
        """
        return datetime.timedelta(hours=1)

    def get_link(self, referral: str | None = None) -> str:
        """Link to the CrystalClear app vaults page."""
        return "https://app.crystalclear.finance/app.html#vaults"
