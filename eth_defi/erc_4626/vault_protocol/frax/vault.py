"""Frax Fraxlend vault support.

Frax is a decentralised finance protocol offering stablecoins (frxUSD),
liquid staking (frxETH/sfrxETH), and lending markets (Fraxlend).

- Homepage: https://frax.com/
- Documentation: https://docs.frax.finance/
- Fraxlend documentation: https://docs.frax.finance/fraxlend/fraxlend-overview
- Smart contracts: https://github.com/FraxFinance/fraxlend
- Example Fraxlend pair: https://etherscan.io/address/0xee847a804b67f4887c9e8fe559a2da4278defb52
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class FraxVault(ERC4626Vault):
    """Frax Fraxlend vault.

    Fraxlend is a lending protocol by Frax that allows users to lend
    assets and earn interest from borrowers. Each Fraxlend pair is an isolated
    lending market with its own ERC-4626 compatible vault for lenders.

    - Fraxlend overview: https://docs.frax.finance/fraxlend/fraxlend-overview
    - Protocol fees: 10% of interest revenue goes to the Frax protocol
    - Smart contracts: https://github.com/FraxFinance/fraxlend
    - Audits: https://docs.frax.finance/other/audits
    """

    def has_custom_fees(self) -> bool:
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Fraxlend has no management fee for lenders.

        The protocol takes a 10% cut of interest revenue via ``feeToProtocolRate``
        in the ``currentRateInfo`` struct, but this is already internalised
        in the share price.
        """
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Fraxlend protocol fee.

        The protocol takes 10% of interest earned as a fee.
        This is internalised in the share price via the ``feeToProtocolRate`` field.

        - https://docs.frax.finance/fraxlend/fraxlend-overview
        """
        return 0.10

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """No lock-up for Fraxlend lenders.

        Lenders can withdraw at any time, subject to available liquidity.
        """
        return datetime.timedelta(days=0)

    def get_link(self, referral: str | None = None) -> str:
        return f"https://app.frax.finance/fraxlend/pair/{self.vault_address}"
