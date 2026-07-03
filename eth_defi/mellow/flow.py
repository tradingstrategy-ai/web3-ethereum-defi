"""Mellow Core Vault flow manager stubs.

Mellow deposits and redemptions are asynchronous and queue based. The initial
adapter intentionally does not expose flow accounting until the queue event ABI
and queue-to-vault mapping tests are pinned.
"""

from decimal import Decimal

from eth_typing import BlockIdentifier

from eth_defi.vault.base import BlockRange, VaultFlowManager, VaultSpec

PENDING_REDEMPTION_UNSUPPORTED = "Mellow queue redemption scanning is not implemented yet"
PENDING_DEPOSIT_UNSUPPORTED = "Mellow queue deposit scanning is not implemented yet"
PENDING_DEPOSIT_EVENTS_UNSUPPORTED = "Mellow queue deposit event scanning is not implemented yet"
PENDING_REDEMPTION_EVENTS_UNSUPPORTED = "Mellow queue redemption event scanning is not implemented yet"


class MellowVaultFlowManager(VaultFlowManager):
    """Unsupported Mellow flow reader placeholder."""

    def fetch_pending_redemption(
        self,
        block_identifier: BlockIdentifier,
    ) -> Decimal:
        """Fetch pending redemption amount.

        Mellow redemption requests are emitted by ``RedeemQueue`` contracts.
        Queue event scanning is not part of the first adapter slice.

        :param block_identifier:
            Block number or tag.

        :return:
            Never returns until queue scanning is implemented.
        """

        raise NotImplementedError(PENDING_REDEMPTION_UNSUPPORTED)

    def fetch_pending_deposit(
        self,
        block_identifier: BlockIdentifier,
    ) -> Decimal:
        """Fetch pending deposit amount.

        Mellow deposit requests are emitted by ``DepositQueue`` contracts.
        Queue event scanning is not part of the first adapter slice.

        :param block_identifier:
            Block number or tag.

        :return:
            Never returns until queue scanning is implemented.
        """

        raise NotImplementedError(PENDING_DEPOSIT_UNSUPPORTED)

    def fetch_pending_deposit_events(
        self,
        range: BlockRange,  # noqa: A002
    ) -> None:
        """Read pending deposit events.

        :param range:
            Block range to read.
        """

        raise NotImplementedError(PENDING_DEPOSIT_EVENTS_UNSUPPORTED)

    def fetch_pending_redemption_event(
        self,
        range: BlockRange,  # noqa: A002
    ) -> None:
        """Read pending redemption events.

        :param range:
            Block range to read.
        """

        raise NotImplementedError(PENDING_REDEMPTION_EVENTS_UNSUPPORTED)

    def fetch_processed_deposit_event(
        self,
        range: BlockRange,  # noqa: A002
    ) -> None:
        """Read processed deposit events.

        :param range:
            Block range to read.
        """

        raise NotImplementedError(PENDING_DEPOSIT_EVENTS_UNSUPPORTED)

    def fetch_processed_redemption_event(
        self,
        vault: VaultSpec,
        range: BlockRange,  # noqa: A002
    ) -> None:
        """Read processed redemption events.

        :param vault:
            Vault whose queue events should be read.

        :param range:
            Block range to read.
        """

        raise NotImplementedError(PENDING_REDEMPTION_EVENTS_UNSUPPORTED)
