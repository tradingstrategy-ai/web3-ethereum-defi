"""Atoma protocol vault support.

Atoma runs an Arbitrum USDC vault that seeks delta-neutral yield from perpetual
DEX funding-rate spreads. Users deposit USDC into an ERC-4626 vault share token
and withdraw through an epoch-based request/claim flow.

The verified AtomaVault implementation exposes fixed fee constants:

- ``PERFORMANCE_FEE_BPS = 2000`` (20%)
- ``WITHDRAWAL_FEE_BPS = 50`` (0.5%)
- ``MIN_DEPOSIT = 100e6`` (100 USDC)

The performance fee is internalised through share minting when NAV exceeds the
high-water mark. The withdrawal fee is externalised and deducted from the USDC
payout in ``claimWithdrawal()``.

- App: https://app.atoma.fi/
- Proxy vault: https://arbiscan.io/address/0xCC56410e1a136aF0eCEb7241c6aE394F4d8b581c
- Verified implementation: https://arbitrum.blockscout.com/address/0xd4242FD8DE6E3128f0435b52DCe29155098CbBFF
"""

import datetime
import logging

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)

#: Atoma Vault Share (AVS) vault address on Arbitrum.
#:
#: https://arbiscan.io/address/0xCC56410e1a136aF0eCEb7241c6aE394F4d8b581c
ATOMA_VAULT_ADDRESS = "0xcc56410e1a136af0eceb7241c6ae394f4d8b581c"

#: Atoma performance fee in basis points.
PERFORMANCE_FEE_BPS = 2_000

#: Atoma withdrawal fee in basis points.
WITHDRAWAL_FEE_BPS = 50

#: Basis point divisor used by Atoma fee constants.
BPS_DIVISOR = 10_000


class AtomaVault(ERC4626Vault):
    """Atoma delta-neutral USDC vault on Arbitrum.

    Atoma uses standard ERC-4626 deposits but disables direct
    ``withdraw()``/``redeem()``. Users call ``requestWithdrawal(shares)`` and
    later ``claimWithdrawal(epochId)`` after the settlement epoch has been
    processed.
    """

    def has_custom_fees(self) -> bool:
        """Atoma has a mixed internalised performance fee and external withdrawal fee."""
        _ = self.vault_address
        return True

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Atoma has no management fee in the verified source.

        :param block_identifier:
            Unused block identifier kept for the shared vault fee API.

        :return:
            Management fee as a fraction.
        """
        _ = self.vault_address, block_identifier
        return 0.0

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Return Atoma's fixed 20% high-water-mark performance fee.

        :param block_identifier:
            Unused block identifier kept for the shared vault fee API.

        :return:
            Performance fee as a fraction.
        """
        _ = self.vault_address, block_identifier
        return PERFORMANCE_FEE_BPS / BPS_DIVISOR

    def get_withdraw_fee(self, block_identifier: BlockIdentifier) -> float:
        """Return Atoma's fixed 0.5% withdrawal fee.

        :param block_identifier:
            Unused block identifier kept for the shared vault fee API.

        :return:
            Withdrawal fee as a fraction.
        """
        _ = self.vault_address, block_identifier
        return WITHDRAWAL_FEE_BPS / BPS_DIVISOR

    def get_estimated_lock_up(self) -> datetime.timedelta:
        """Return the current public app epoch length as the lock-up estimate.

        The Atoma app reports weekly epochs for the live vault. A user can
        request withdrawal after the deposit epoch, then claim after the
        settlement epoch is processed.

        :return:
            Estimated withdrawal settlement interval.
        """
        _ = self.vault_address
        return datetime.timedelta(days=7)

    def get_link(self, referral: str | None = None) -> str:
        """Return the Atoma vault app link."""
        _ = self.vault_address, referral
        return "https://app.atoma.fi/"
