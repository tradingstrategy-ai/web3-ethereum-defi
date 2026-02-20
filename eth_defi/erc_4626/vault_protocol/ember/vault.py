"""Ember vault support.

Ember is the investment platform and infrastructure for launching, accessing,
and distributing traditional and onchain financial products through crypto capital markets.

- `Homepage <https://ember.so/>`__
- `Documentation <https://learn.ember.so/>`__
- `Example vault on Etherscan <https://etherscan.io/address/0xf3190a3ecc109f88e7947b849b281918c798a0c4>`__
- `Audit report <https://ember.so/documents/ember_protocol_audit.pdf>`__
- Operates on Ethereum and Sui
- Uses ``protocolConfig()`` returning ``IEmberProtocolConfig`` for protocol identification
- Uses custom ``VaultDeposit`` event instead of standard ERC-4626 ``Deposit``
- Uses ``RequestRedeemed``/``RequestProcessed`` events instead of standard ``Withdraw``
- Has a ``platformFee`` with ``platformFeePercentage`` field
- Withdrawal requests go through a pending queue (``redeemShares`` -> ``processWithdrawalRequests``)
"""

import datetime
import logging
from functools import cached_property

from eth_typing import BlockIdentifier
from web3.contract import Contract

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault

logger = logging.getLogger(__name__)


class EmberVault(ERC4626Vault):
    """Ember protocol vaults.

    - `Homepage <https://ember.so/>`__
    - `Documentation <https://learn.ember.so/>`__
    - `Example vault <https://etherscan.io/address/0xf3190a3ecc109f88e7947b849b281918c798a0c4>`__
    - Ember uses a vault rate mechanism following ERC-4626 principles
    - Platform fees are embedded in the vault rate updates
    - Has ``platformFee()`` view returning ``(accrued, lastChargedAt, platformFeePercentage)``
    - Withdrawals go through a pending queue with ``redeemShares`` -> ``processWithdrawalRequests``
    """

    @cached_property
    def vault_contract(self) -> Contract:
        """Get vault deployment."""
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="ember/EmberVault.json",
        )

    def has_custom_fees(self) -> bool:
        """Ember has platform fees embedded in the vault rate."""
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Read management fee from ``platformFee()`` on-chain.

        The ``platformFee()`` function returns ``(accrued, lastChargedAt, platformFeePercentage)``.
        ``platformFeePercentage`` is in basis points (1e18 = 100%).
        """
        try:
            result = self.vault_contract.functions.platformFee().call(block_identifier=block_identifier)
            # platformFeePercentage is the third element, in 1e18 basis (1e18 = 100%)
            platform_fee_pct = result[2]
            return platform_fee_pct / 1e18
        except Exception as e:
            logger.warning("Could not read platformFee for %s: %s", self.address, e)
            return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Ember does not have a separate performance fee.

        Performance is captured through the vault rate update mechanism.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Ember withdrawals go through a pending queue.

        Settlement is typically T+4 based on documentation.
        """
        return datetime.timedelta(days=4)

    def get_link(self, referral: str | None = None) -> str:
        return "https://ember.so/earn"
