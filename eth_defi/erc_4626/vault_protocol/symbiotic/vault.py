"""Symbiotic Core V2 ERC-4626 vault support.

Symbiotic V2 vaults allocate a single collateral asset through a Universal
Delegator. The official `Core V2 interface <https://github.com/symbioticfi/core/blob/main/src/interfaces/vault/IVaultV2.sol>`__
defines the vault's management and performance fees, while the `vault guide
<https://docs.symbiotic.fi/get-started/what/components/vaults>`__ describes
how adapter liquidity affects redemptions.
"""

import datetime
from functools import cached_property

from eth_typing import BlockIdentifier, HexAddress
from web3 import Web3

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.symbiotic.offchain_metadata import SymbioticVaultMetadata, fetch_symbiotic_vault_metadata
from eth_defi.erc_4626.vault_protocol.symbiotic.tags import STRATEGY_TAGS
from eth_defi.vault.strategy_tag import StrategyTag


class SymbioticVault(ERC4626Vault):
    """Read Symbiotic Core V2 fee configuration and vault links.

    A Symbiotic curator may charge management and performance fees, and the
    protocol may add its own cached fee component. Both are collected by share
    minting and are reflected in the ERC-4626 share price. Direct redemption
    can wait for adapter deallocation, so this adapter deliberately does not
    certify the inherited synchronous deposit manager.
    """

    #: Symbiotic V2 fee denominator from ``IVaultV2.MAX_FEE``.
    FEE_SCALE = 10**18

    @cached_property
    def symbiotic_metadata(self) -> SymbioticVaultMetadata | None:
        """Fetch official curator-submitted vault metadata on first access.

        :return:
            Metadata from Symbiotic's public metadata API, or ``None`` when
            the vault has no reviewed metadata record.
        """
        return fetch_symbiotic_vault_metadata(self.vault_address)

    @property
    def description(self) -> str | None:
        """Return the curator-provided vault strategy description.

        :return:
            Full vault description from official offchain metadata, if present.
        """
        return self.symbiotic_metadata.get("description") if self.symbiotic_metadata else None

    @property
    def short_description(self) -> str | None:
        """Return a listing-friendly first sentence of the vault description.

        :return:
            First sentence of the official description, if present.
        """
        description = self.description
        if not description:
            return None
        sentence_end = description.find(". ")
        return description[: sentence_end + 1] if sentence_end >= 0 else description.rstrip(".") + "."

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return the maintained strategy tags for this Symbiotic vault.

        :return:
            Copy of the tag set, or ``None`` when this vault has not yet been
            classified.
        """
        tags = STRATEGY_TAGS.get(HexAddress(str(self.vault_address).lower()))
        return tags.copy() if tags is not None else None

    @property
    def manager_name(self) -> str | None:
        """Return the official Symbiotic curator display name.

        :return:
            Curator name from official metadata, if present.
        """
        return self.symbiotic_metadata.get("curator_name") if self.symbiotic_metadata else None

    def fetch_uint256(self, function_signature: str, block_identifier: BlockIdentifier) -> int:
        """Read a no-argument unsigned integer method from the vault.

        Symbiotic publishes the canonical interface, so a short stable-selector
        reader is sufficient for the four fee fields and avoids vendoring a
        large generated ABI.

        :param function_signature:
            Canonical Solidity signature of the no-argument view function.
        :param block_identifier:
            Block at which to read the configuration.
        :return:
            Decoded unsigned integer result.
        """
        result = self.web3.eth.call(
            {
                "to": self.vault_address,
                "data": Web3.keccak(text=function_signature)[:4],
            },
            block_identifier=block_identifier,
        )
        return int.from_bytes(result, byteorder="big")

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float:
        """Return the combined annual management fee as a ratio.

        Symbiotic stores management fees per second. This method annualises the
        curator fee and the protocol fee cached on the vault so the common vault
        fee schema can report the amount paid by a share holder.

        :param block_identifier:
            Block at which to read the active fee configuration.
        :return:
            Annual management fee ratio, such as ``0.01`` for 1%.
        """
        raw_fee_per_second = self.fetch_uint256("managementFee()", block_identifier) + self.fetch_uint256("lastProtocolManagementFee()", block_identifier)
        return raw_fee_per_second * datetime.timedelta(days=365).total_seconds() / self.FEE_SCALE

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float:
        """Return the combined performance fee as a ratio.

        Both curator and protocol performance fees use ``MAX_FEE`` scaling and
        are collected through share minting when the vault realises interest.

        :param block_identifier:
            Block at which to read the active fee configuration.
        :return:
            Performance fee ratio, such as ``0.20`` for 20%.
        """
        raw_fee = self.fetch_uint256("performanceFee()", block_identifier) + self.fetch_uint256("lastProtocolPerformanceFee()", block_identifier)
        return raw_fee / self.FEE_SCALE

    def get_estimated_lock_up(self) -> datetime.timedelta | None:  # noqa: PLR6301
        """Return no fixed lock-up estimate for adapter-backed withdrawals.

        A redemption can trigger adapter deallocation and may be queued. The
        duration depends on the curator's selected adapters rather than a vault
        constant, so reporting a zero duration would be misleading.

        :return:
            ``None`` because no protocol-wide duration can be calculated.
        """
        return None

    def get_link(self, referral: str | None = None) -> str:  # noqa: ARG002
        """Return the official Symbiotic application link for this vault.

        :param referral:
            Optional referral code, unsupported by the Symbiotic vault URL.
        :return:
            Address-specific Symbiotic application URL.
        """
        return f"https://app.symbiotic.fi/vault/{self.vault_address}"
