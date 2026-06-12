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
from functools import cached_property

from eth_typing import BlockIdentifier
from web3.contract import Contract
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_defi.erc_4626.core import get_deployed_erc_4626_contract
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.provider.fallback import ExtraValueError

logger = logging.getLogger(__name__)

_AERA_BASIS_POINTS = 10_000
_AERA_FEE_FIXED_POINT_ONE = 10**18
_SECONDS_PER_YEAR = 365 * 24 * 60 * 60


class AeraVault(ERC4626Vault):
    """Aera vault support.

    Aera V3 vaults inherit from a common ``BaseVault`` security model that
    supports guardian operations protected by owner-approved hooks and
    whitelists. The public documentation describes two audited implementations:
    ``SingleDepositorVault`` for treasury management and ``MultiDepositorVault``
    for yield vaults with multiple depositors.

    The current deployed ERC-4626 Aera vaults are Yearn TokenizedStrategy-style
    wrappers around Aera V2 vaults. The wrapper exposes ``performanceFee()`` in
    basis points through the TokenizedStrategy fallback, while ``vaultAera()``
    points to the underlying Aera V2 vault whose ``fee()`` is a per-second
    TVL fee in 18-decimal fixed point format.
    """

    @cached_property
    def strategy_contract(self) -> Contract:
        """Get the deployed Aera strategy wrapper contract.

        :return:
            Web3 contract proxy using the Aera strategy ABI fragment.
        """
        return get_deployed_erc_4626_contract(
            self.web3,
            self.spec.vault_address,
            abi_fname="aera/AeraStrategy.json",
        )

    def fetch_aera_vault_address(self, block_identifier: BlockIdentifier) -> str | None:
        """Read the underlying Aera V2 vault address from the strategy wrapper.

        Some hardcoded Aera addresses are legacy wrappers that do not expose
        the same strategy ABI. Those return ``None`` until a dedicated adapter
        is added for them.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            Underlying Aera V2 vault address, or ``None`` if not available.
        """
        try:
            return self.strategy_contract.functions.vaultAera().call(block_identifier=block_identifier)
        except (BadFunctionCallOutput, ContractLogicError, ExtraValueError):
            return None

    def fetch_aera_vault_contract(self, block_identifier: BlockIdentifier) -> Contract | None:
        """Read the underlying Aera V2 vault contract.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            Web3 contract proxy using the Aera V2 vault ABI fragment, or
            ``None`` if the wrapper does not expose ``vaultAera()``.
        """
        vault_address = self.fetch_aera_vault_address(block_identifier)
        if vault_address is None:
            return None

        return get_deployed_erc_4626_contract(
            self.web3,
            vault_address,
            abi_fname="aera/AeraVaultV2.json",
        )

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Return the management fee.

        Aera V2 calls this a TVL fee. It maps to our annual management fee
        because it accrues continuously on vault value. The contract exposes the
        rate as a per-second 18-decimal fixed point number, so the annualised
        percentage is ``fee() * 365 days / 1e18``.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            Annual management fee as a fraction, e.g. ``0.02`` for 2%.
        """
        aera_vault_contract = self.fetch_aera_vault_contract(block_identifier)
        if aera_vault_contract is None:
            return None

        raw_fee_per_second = aera_vault_contract.functions.fee().call(block_identifier=block_identifier)
        return raw_fee_per_second * _SECONDS_PER_YEAR / _AERA_FEE_FIXED_POINT_ONE

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:
        """Return the performance fee.

        Current Aera ERC-4626 strategy wrappers expose the Yearn
        TokenizedStrategy ``performanceFee()`` getter through their fallback.
        The value is in basis points.

        :param block_identifier:
            Block number or ``"latest"``.

        :return:
            Performance fee as a fraction, e.g. ``0.1`` for 10%.
        """
        try:
            raw_fee = self.strategy_contract.functions.performanceFee().call(block_identifier=block_identifier)
        except (BadFunctionCallOutput, ContractLogicError, ExtraValueError):
            return None

        return raw_fee / _AERA_BASIS_POINTS

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
