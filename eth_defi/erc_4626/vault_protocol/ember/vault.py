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
- ``platformFee()`` returns protocol-level fee, not curator fees
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
    - `Fee structure <https://learn.ember.so/ember-protocol/core-concepts>`__
    - `Example vault <https://etherscan.io/address/0xf3190a3ecc109f88e7947b849b281918c798a0c4>`__
    - Ember uses a vault rate mechanism following ERC-4626 principles
    - Management fee: annualised % of AUM, accrued continuously and reflected in the vault share price
    - Performance fee: % of positive performance, embedded in share price, collected monthly
    - Both fees are set per curator and internalised in the vault rate — no on-chain getter available
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
        return False

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Management fee is not readable on-chain.

        Ember curators set their own management fee (annualised % of AUM),
        which is accrued continuously and embedded in the vault share price.
        There is no on-chain getter for the curator management fee.

        The ``platformFee()`` function returns a separate protocol-level fee,
        not the curator's management fee.

        See `fee documentation <https://learn.ember.so/ember-protocol/core-concepts>`__.
        """
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Performance fee is not readable on-chain.

        Ember curators set their own performance fee (% of positive performance),
        which is embedded in the vault share price and collected monthly.
        There is no on-chain getter for the curator performance fee.

        See `fee documentation <https://learn.ember.so/ember-protocol/core-concepts>`__.
        """
        return None

    def get_estimated_lock_up(self) -> datetime.timedelta | None:
        """Ember withdrawals go through a pending queue.

        Settlement is typically T+4 based on documentation.
        """
        return datetime.timedelta(days=4)

    def get_link(self, referral: str | None = None) -> str:
        return "https://ember.so/earn"
