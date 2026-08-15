"""Arcus pToken vault reader support."""

from functools import cached_property

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.arcus.offchain_data import ArcusVaultOffchainData, get_arcus_vault_offchain_data


class ArcusVault(ERC4626Vault):
    """Read an Arcus pToken vault on Robinhood Chain.

    The pToken implementation behind the reviewed BeaconProxy is not
    source-verified. This adapter therefore provides generic ERC-4626 reads
    only; it does not certify transaction flows, a withdrawal timetable, or a
    fee schedule. A product name such as ``"BTC (3x Long)"`` is not treated as
    independent evidence of the product mechanics.

    See the `Arcus website <https://arcus.xyz/>`__ for product information.
    """

    @cached_property
    def arcus_offchain_data(self) -> ArcusVaultOffchainData | None:
        """Return reviewed pToken copy.

        The local overlay is address-scoped and does not make public API
        requests, because Arcus's market catalogue is not pToken data.

        :return:
            Reviewed Arcus pToken metadata, or ``None`` for an unsupported
            contract address.
        """

        return get_arcus_vault_offchain_data(self.vault_address)

    @property
    def description(self) -> str | None:
        """Return reviewed pToken copy.

        :return:
            Longer address-scoped product description, or ``None`` for an
            unrecognised pToken.
        """

        return self.arcus_offchain_data["description"] if self.arcus_offchain_data else None

    @property
    def short_description(self) -> str | None:
        """Return the reviewed Arcus pToken listing summary.

        :return:
            Address-scoped concise description, or ``None`` for an
            unrecognised pToken.
        """

        return self.arcus_offchain_data["short_description"] if self.arcus_offchain_data else None

    @property
    def manager_name(self) -> str | None:
        """Return Arcus as the reviewed protocol-level curator attribution.

        :return:
            ``"Arcus"`` for reviewed pTokens, or ``None`` when the pToken has
            not been individually reviewed.
        """

        return self.arcus_offchain_data["curator_name"] if self.arcus_offchain_data else None

    def get_management_fee(self, block_identifier: BlockIdentifier) -> float | None:  # noqa: PLR6301
        """Return the verified Arcus pToken management fee when available.

        No management-fee rate has been verified for the reviewed pTokens.

        :param block_identifier:
            Block at which fee data would be read.

        :return:
            ``None`` because no management-fee value is established.
        """
        del block_identifier
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> float | None:  # noqa: PLR6301
        """Return the verified Arcus pToken performance fee when available.

        No performance-fee rate has been verified for the reviewed pTokens.

        :param block_identifier:
            Block at which fee data would be read.

        :return:
            ``None`` because no performance-fee value is established.
        """
        del block_identifier
        return None

    def get_link(self, referral: str | None = None) -> str:  # noqa: PLR6301
        """Return Arcus's public application.

        Arcus does not publish a stable pToken-specific application route, so
        callers are directed to the main Arcus application.

        :param referral:
            Unsupported referral code.

        :return:
            Arcus application URL.
        """
        del referral
        return "https://app.arcus.xyz/"
