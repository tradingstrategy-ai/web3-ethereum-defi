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
- `Fee structure <https://learn.ember.so/ember-protocol/core-concepts>`__
- Management and performance fees are set per curator and embedded in the vault rate (internalised skimming)
- Fee parameters are available from the offchain API (see :py:mod:`~eth_defi.erc_4626.vault_protocol.ember.offchain_metadata`)
- Withdrawal requests go through a pending queue (``redeemShares`` -> ``processWithdrawalRequests``)
"""

import datetime
import logging
from functools import cached_property

from eth_typing import BlockIdentifier
from web3.contract import Contract

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.ember.offchain_metadata import (
    EmberVaultMetadata,
    fetch_ember_vault_metadata,
)

logger = logging.getLogger(__name__)


class EmberVault(ERC4626Vault):
    """Ember protocol vaults.

    - `Homepage <https://ember.so/>`__
    - `Documentation <https://learn.ember.so/>`__
    - `Fee structure <https://learn.ember.so/ember-protocol/core-concepts>`__
    - `Example vault <https://etherscan.io/address/0xf3190a3ecc109f88e7947b849b281918c798a0c4>`__
    - Ember uses a vault rate mechanism following ERC-4626 principles
    - Management fee: annualised % of AUM, accrued continuously and reflected in the vault share price
    - Performance fee: weekly % of positive performance, embedded in share price
    - Both fees are set per curator and internalised in the vault rate
    - Fee parameters available from the offchain API via :py:attr:`ember_metadata`
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

    @cached_property
    def ember_metadata(self) -> EmberVaultMetadata | None:
        """Offchain metadata from Ember's Bluefin API.

        Fetched from ``vaults.api.sui-prod.bluefin.io/api/v2/vaults``.
        Cached on disk and in-process to avoid repeated API calls.
        """
        return fetch_ember_vault_metadata(self.web3, self.spec.vault_address)

    @property
    def description(self) -> str | None:
        """Full vault strategy description from Ember's offchain metadata."""
        if self.ember_metadata:
            return self.ember_metadata.get("description")
        return None

    @property
    def short_description(self) -> str | None:
        """Strategy type from Ember's offchain metadata (e.g. "Stablecoin Strategy")."""
        if self.ember_metadata:
            return self.ember_metadata.get("strategy")
        return None

    def has_custom_fees(self) -> bool:
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Management fee from Ember's offchain API.

        Ember curators set their own management fee (annualised % of AUM),
        which is accrued continuously and embedded in the vault share price.
        The fee is not readable on-chain but is available from the offchain API.

        See `fee documentation <https://learn.ember.so/ember-protocol/core-concepts>`__.
        """
        if self.ember_metadata:
            return self.ember_metadata.get("management_fee")
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Weekly performance fee from Ember's offchain API.

        Ember curators set their own performance fee (weekly % of positive performance),
        which is embedded in the vault share price.
        The fee is not readable on-chain but is available from the offchain API.

        .. note::

            This is a **weekly** rate, not annualised.

        See `fee documentation <https://learn.ember.so/ember-protocol/core-concepts>`__.
        """
        if self.ember_metadata:
            return self.ember_metadata.get("weekly_performance_fee")
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Ember withdrawals go through a pending queue.

        Uses the ``withdrawalPeriodDays`` from the offchain API if available,
        otherwise defaults to 4 days.
        """
        if self.ember_metadata:
            days = self.ember_metadata.get("withdrawal_period_days")
            if days is not None:
                return datetime.timedelta(days=days)
        return datetime.timedelta(days=4)

    def get_link(self, referral: str | None = None) -> str:
        return "https://ember.so/earn"
