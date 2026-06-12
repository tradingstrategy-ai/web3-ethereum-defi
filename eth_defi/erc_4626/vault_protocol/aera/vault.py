"""Aera vault protocol support.

Aera is non-custodial vault infrastructure for onchain treasury management
and multi-depositor yield strategies. Vault owners configure assets,
protocol allowlists, guardians, and hooks that constrain strategy execution
onchain while allowing optimisation logic to run offchain.

- `Homepage <https://www.aera.finance/>`__
- `Documentation <https://docs.aera.finance/>`__
- `Protocol overview <https://docs.aera.finance/aera-protocol-in-one-page>`__
- `BaseVault documentation <https://docs.aera.finance/basevault-and-core-interactions>`__
- `GitHub <https://github.com/aera-finance/aera-contracts-public>`__

This integration initially identifies known Aera vaults by hardcoded vault
addresses. A generic contract probe can be added once we have a stable
protocol-specific signature that avoids false positives with wrapper and
strategy contracts.
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class AeraVault(ERC4626Vault):
    """Aera vault support.

    Aera V3 vaults inherit from a common ``BaseVault`` security model that
    supports guardian operations protected by owner-approved hooks and
    whitelists. The public documentation describes two audited implementations:
    ``SingleDepositorVault`` for treasury management and ``MultiDepositorVault``
    for yield vaults with multiple depositors.

    Fee handling is flexible and depends on the deployed vault and calculator
    contracts, so this adapter leaves protocol-level management and performance
    fee values unknown until per-vault fee reading is added.
    """

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:  # noqa: ARG002, PLR6301
        """Return the management fee.

        Aera fee configuration is vault-specific and can use fee calculator
        contracts. The current hardcoded-address integration does not yet
        decode per-vault fee calculators.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            ``None`` because fee data is not yet extracted.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:  # noqa: ARG002, PLR6301
        """Return the performance fee.

        Aera fee configuration is vault-specific and can use fee calculator
        contracts. The current hardcoded-address integration does not yet
        decode per-vault fee calculators.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            ``None`` because fee data is not yet extracted.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:  # noqa: PLR6301
        """Return the estimated lock-up period.

        Aera vault deposit and redemption mechanics vary by implementation and
        wrapper strategy. No protocol-wide lock-up estimate is available.

        :return:
            ``None`` because lock-up data is vault-specific.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:  # noqa: ARG002, PLR6301
        """Return the Aera app link.

        :param referral:
            Optional referral code. Not used by Aera links.

        :return:
            Aera application URL.
        """
        return "https://app.aera.finance/"
