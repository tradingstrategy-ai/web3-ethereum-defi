"""Explicit unsupported flow reader for Enzyme Onyx vaults."""

# ruff: noqa: EM101, PLR6301

from decimal import Decimal

from eth_typing import BlockIdentifier

from eth_defi.vault.base import BlockRange, VaultFlowManager, VaultSpec


class EnzymeVaultFlowManager(VaultFlowManager):
    """Placeholder until Onyx synchronous and queued handlers are supported."""

    def fetch_pending_redemption(self, block_identifier: BlockIdentifier) -> Decimal:
        """Reject unsupported redemption queue accounting."""

        del block_identifier
        raise NotImplementedError("Enzyme Onyx redemption handler event scanning is not implemented")

    def fetch_pending_deposit(self, block_identifier: BlockIdentifier) -> Decimal:
        """Reject unsupported deposit queue accounting."""

        del block_identifier
        raise NotImplementedError("Enzyme Onyx deposit handler event scanning is not implemented")

    def fetch_pending_deposit_events(self, range: BlockRange) -> None:  # noqa: A002
        """Reject unsupported deposit event scanning."""

        del range
        raise NotImplementedError("Enzyme Onyx deposit handler event scanning is not implemented")

    def fetch_pending_redemption_event(self, range: BlockRange) -> None:  # noqa: A002
        """Reject unsupported redemption event scanning."""

        del range
        raise NotImplementedError("Enzyme Onyx redemption handler event scanning is not implemented")

    def fetch_processed_deposit_event(self, range: BlockRange) -> None:  # noqa: A002
        """Reject unsupported deposit event scanning."""

        del range
        raise NotImplementedError("Enzyme Onyx deposit handler event scanning is not implemented")

    def fetch_processed_redemption_event(self, vault: VaultSpec, range: BlockRange) -> None:  # noqa: A002
        """Reject unsupported redemption event scanning."""

        del vault, range
        raise NotImplementedError("Enzyme Onyx redemption handler event scanning is not implemented")
