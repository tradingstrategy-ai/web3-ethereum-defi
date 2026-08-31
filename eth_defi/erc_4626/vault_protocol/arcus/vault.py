"""Arcus pToken vault reader support."""

from functools import cached_property

from eth_typing import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.arcus.offchain_data import ArcusVaultOffchainData, get_arcus_vault_offchain_data
from eth_defi.types import Percent


class ArcusVault(ERC4626Vault):
    """Read an Arcus pToken vault on Robinhood Chain.

    The pToken implementation behind the reviewed BeaconProxy is not
    source-verified. This adapter therefore provides generic ERC-4626 reads
    plus reviewed, address-scoped product and fee metadata. The metadata comes
    from Arcus's public pToken announcement and API; it is not an independent
    verification of the implementation.

    See the `Arcus website <https://arcus.xyz/>`__ for product information.
    """

    @cached_property
    def arcus_offchain_data(self) -> ArcusVaultOffchainData | None:
        """Return reviewed pToken copy.

        The local overlay is address-scoped and avoids runtime API requests so
        vault metadata scans remain deterministic.

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
        """Return Arcus as the protocol-level manager attribution.

        Arcus is the registered protocol curator for the reviewed pToken
        products. This display name does not identify the unlabelled address
        returned by the onchain ``manager()`` accessor.

        :return:
            ``"Arcus"`` for reviewed pTokens, or ``None`` for an unreviewed
            contract address.
        """

        return "Arcus" if self.arcus_offchain_data else None

    def get_notes(self) -> str | None:
        """Return the reviewed explanation of Arcus pToken mechanics.

        Manually curated shared notes retain priority. Otherwise, reviewed
        address-scoped copy explains how NAV, rebalancing and pooled collateral
        affect pToken holders.

        :return:
            Markdown-formatted pToken notes, or ``None`` for an unreviewed
            contract address.
        """

        manual_notes = super().get_notes()
        if manual_notes:
            return manual_notes
        return self.arcus_offchain_data["notes"] if self.arcus_offchain_data else None

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent | None:  # noqa: PLR6301
        """Return the verified Arcus pToken management fee when available.

        No management-fee rate has been verified for the reviewed pTokens.

        :param block_identifier:
            Block at which fee data would be read.

        :return:
            ``None`` because no management-fee value is established.
        """
        del block_identifier
        return None

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return the reviewed Arcus pToken profit share.

        Arcus's public vault API reports this value as ``profitShareBps``. The
        adapter keeps an address-scoped snapshot rather than depending on the
        API during metadata scans.

        :param block_identifier:
            Accepted for the common vault interface. Static offchain fee data
            is not historical.

        :return:
            Reviewed profit-share rate, or ``None`` for an unreviewed pToken.
        """
        del block_identifier
        return self.arcus_offchain_data["performance_fee"] if self.arcus_offchain_data else None

    def get_deposit_fee(self, block_identifier: BlockIdentifier) -> Percent | None:
        """Return the reviewed Arcus pToken entry fee.

        Arcus's public vault API reports this value as ``depositFeeBps``. The
        adapter keeps an address-scoped snapshot rather than depending on the
        API during metadata scans.

        :param block_identifier:
            Accepted for the common vault interface. Static offchain fee data
            is not historical.

        :return:
            Reviewed entry-fee rate, or ``None`` for an unreviewed pToken.
        """
        del block_identifier
        return self.arcus_offchain_data["deposit_fee"] if self.arcus_offchain_data else None

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
