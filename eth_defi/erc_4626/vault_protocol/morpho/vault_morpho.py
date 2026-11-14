"""Enforce VaultMorpho's additional deposit cap through the existing V1 integration.

The verified implementation can advertise positive ``maxDeposit`` while its
vault-wide ``tvlCap`` is exhausted. Keep this rule in the selected adapter so
shared deposit consumers use their ordinary capacity and closure hooks.

See :ref:`morpho-vault-classification` for detection, adapter precedence,
protocol identity and the verified implementation/ABI sources.
"""

from functools import cached_property

from eth_typing import HexAddress
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.vault_protocol.morpho.deposit_redeem import MorphoV1DepositManager
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault


class VaultMorphoDepositManager(MorphoV1DepositManager):
    """Retain the standard request flow while enforcing the fork's TVL cap."""

    def fetch_depositable_raw_assets(self, owner: HexAddress) -> int:
        """Use remaining TVL capacity even when maxDeposit advertises more.

        :param owner: Account used for the ordinary ERC-4626 maximum.
        :return: Remaining raw denomination units, bounded by a reported maximum.
        """
        maximum = super().fetch_depositable_raw_assets(owner)
        remaining = self.vault.fetch_deposit_capacity()
        return min(maximum, remaining) if maximum is not None else remaining


class VaultMorphoVault(MorphoV1Vault):
    """Reuse Morpho V1 readers and execution with the verified fork interface."""

    @cached_property
    def vault_contract(self) -> Contract:
        """Bind the implementation ABI so the extra cap has a typed getter."""
        return get_deployed_contract(self.web3, "morpho/VaultMorpho.json", self.address)

    def fetch_deposit_capacity(self) -> int:
        """Match the deposit's totalAssets + assets <= tvlCap condition.

        :return: Remaining raw denomination units, floored at zero.
        """
        block = self.default_block_identifier if self.default_block_identifier is not None else self.web3.eth.block_number
        functions = self.vault_contract.functions
        return max(0, functions.tvlCap().call(block_identifier=block) - functions.totalAssets().call(block_identifier=block))

    def fetch_deposit_closed_reason(self) -> str | None:
        """Make a full global cap visible to the existing closure checks.

        :return: Cap refusal or the inherited ERC-4626 assessment.
        """
        if self.fetch_deposit_capacity() == 0:
            return "VaultMorpho tvlCap reached"
        return super().fetch_deposit_closed_reason()

    def get_deposit_manager(self) -> VaultMorphoDepositManager:
        """Keep the cap check in the protocol's ordinary request path."""
        return VaultMorphoDepositManager(self)
