"""Hyperliquid vault deposit and redemption history analysis.

This module provides functionality for fetching and analysing historical deposit
and redemption (withdrawal) events for Hyperliquid vaults.

The Hyperliquid API provides the ``userNonFundingLedgerUpdates`` endpoint which
returns all ledger updates excluding funding payments. This includes vault-specific
events:

- ``vaultDeposit`` - User deposits into a vault
- ``vaultWithdraw`` - User withdraws from a vault
- ``vaultCreate`` - Vault creation event
- ``vaultDistribution`` - Distribution event (e.g., profit sharing)
- ``vaultLeaderCommission`` - Commission paid to vault leader

API Endpoints Used
------------------

- ``userNonFundingLedgerUpdates`` - Paginated ledger history with time range support

Example::

    from datetime import datetime, timedelta
    from eth_defi.hyperliquid.session import create_hyperliquid_session
    from eth_defi.hyperliquid.deposit import (
        fetch_vault_deposits,
        create_deposit_dataframe,
    )

    session = create_hyperliquid_session()
    vault_address = "0x3df9769bbbb335340872f01d8157c779d73c6ed0"

    # Fetch deposit/withdrawal history for the last 30 days
    start_time = datetime.now() - timedelta(days=30)
    events = list(fetch_vault_deposits(session, vault_address, start_time=start_time))

    # Convert to DataFrame for analysis
    df = create_deposit_dataframe(events)
    print(f"Total deposits: ${df[df['event_type'] == 'vault_deposit']['usdc'].sum():,.2f}")

See Also
--------

- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
- https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults/for-vault-depositors
"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterator

import pandas as pd
from eth_typing import HexAddress

from eth_defi.hyperliquid.session import HyperliquidSession

logger = logging.getLogger(__name__)

#: Maximum ledger updates returned per API request
MAX_UPDATES_PER_REQUEST = 2000


class VaultEventType(Enum):
    """Type of vault deposit/withdrawal event."""

    #: Initial vault creation
    vault_create = "vault_create"
    #: Deposit into the vault
    vault_deposit = "vault_deposit"
    #: Withdrawal from the vault
    vault_withdraw = "vault_withdraw"
    #: Distribution event (e.g., profit sharing)
    vault_distribution = "vault_distribution"
    #: Commission paid to vault leader
    vault_leader_commission = "vault_leader_commission"


@dataclass(slots=True)
class VaultDepositEvent:
    """Represents a vault deposit, withdrawal, or related event.

    This dataclass captures vault-related ledger events from the
    ``userNonFundingLedgerUpdates`` API endpoint.
    """

    #: Type of event
    event_type: VaultEventType
    #: Vault address
    vault_address: HexAddress
    #: User address (for withdrawals and commissions)
    user_address: HexAddress | None
    #: USDC amount (positive for deposits/inflows, negative for withdrawals/outflows)
    usdc: Decimal
    #: Event timestamp
    timestamp: datetime.datetime
    #: Transaction hash
    hash: str | None = None
    #: Requested USD amount (for withdrawals)
    requested_usd: Decimal | None = None
    #: Commission amount (for withdrawals)
    commission: Decimal | None = None
    #: Closing cost (for withdrawals)
    closing_cost: Decimal | None = None
    #: Basis amount (for withdrawals)
    basis: Decimal | None = None
    #: Net withdrawn USD (for withdrawals)
    net_withdrawn_usd: Decimal | None = None


@dataclass(slots=True)
class RawLedgerUpdate:
    """Parsed ledger update data from Hyperliquid API.

    This is an intermediate representation of raw API ledger data
    with proper typing.
    """

    #: Timestamp in milliseconds
    timestamp_ms: int
    #: Transaction hash
    hash: str | None
    #: Delta type and data
    delta: dict

    @classmethod
    def from_api_response(cls, data: dict) -> "RawLedgerUpdate":
        """Parse a ledger update from API response data.

        :param data: Raw ledger update dict from API
        :return: Parsed RawLedgerUpdate object
        """
        return cls(
            timestamp_ms=data["time"],
            hash=data.get("hash"),
            delta=data.get("delta", {}),
        )

    @property
    def timestamp(self) -> datetime.datetime:
        """Convert millisecond timestamp to datetime."""
        return datetime.datetime.fromtimestamp(self.timestamp_ms / 1000)


def fetch_vault_deposits(
    session: HyperliquidSession,
    vault_address: HexAddress,
    start_time: datetime.datetime | None = None,
    end_time: datetime.datetime | None = None,
    timeout: float = 30.0,
) -> Iterator[VaultDepositEvent]:
    """Fetch all deposit and withdrawal events for a vault.

    Fetches ledger updates from the Hyperliquid API using the
    ``userNonFundingLedgerUpdates`` endpoint and filters for vault-related
    events (deposits, withdrawals, distributions, etc.).

    The events are yielded in chronological order (oldest first).

    Example::

        from datetime import datetime, timedelta
        from eth_defi.hyperliquid.session import create_hyperliquid_session
        from eth_defi.hyperliquid.deposit import fetch_vault_deposits

        session = create_hyperliquid_session()
        vault = "0x3df9769bbbb335340872f01d8157c779d73c6ed0"

        # Fetch last 7 days of deposits/withdrawals
        events = list(
            fetch_vault_deposits(
                session,
                vault,
                start_time=datetime.now() - timedelta(days=7),
            )
        )
        print(f"Fetched {len(events)} vault events")

    :param session:
        Session from :py:func:`~eth_defi.hyperliquid.session.create_hyperliquid_session`
    :param vault_address:
        Vault address to fetch events for
    :param start_time:
        Start of time range (inclusive). Defaults to 30 days ago.
    :param end_time:
        End of time range (inclusive). Defaults to current time.
    :param timeout:
        HTTP request timeout in seconds
    :return:
        Iterator of vault events sorted by timestamp ascending (oldest first)
    :raises requests.HTTPError:
        If the HTTP request fails after retries
    """
    if end_time is None:
        end_time = datetime.datetime.now()

    if start_time is None:
        start_time = end_time - datetime.timedelta(days=30)

    all_events: list[VaultDepositEvent] = []
    current_end_ms = int(end_time.timestamp() * 1000)
    start_ms = int(start_time.timestamp() * 1000)

    logger.info(
        "Fetching vault events for %s from %s to %s",
        vault_address,
        start_time.isoformat(),
        end_time.isoformat(),
    )

    while current_end_ms > start_ms:
        payload = {
            "type": "userNonFundingLedgerUpdates",
            "user": vault_address,
            "startTime": start_ms,
            "endTime": current_end_ms,
        }

        logger.debug("Fetching ledger updates: startTime=%s, endTime=%s", start_ms, current_end_ms)

        response = session.post(
            f"{session.api_url}/info",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        raw_updates = response.json()

        if not raw_updates:
            logger.debug("No more ledger updates returned, pagination complete")
            break

        # Parse updates and filter for vault events
        batch_events = []
        oldest_timestamp_ms = None

        for raw in raw_updates:
            update = RawLedgerUpdate.from_api_response(raw)
            event = _parse_vault_event(update, vault_address)
            if event is not None:
                batch_events.append(event)

            if oldest_timestamp_ms is None or update.timestamp_ms < oldest_timestamp_ms:
                oldest_timestamp_ms = update.timestamp_ms

        all_events.extend(batch_events)

        logger.debug(
            "Fetched %d ledger updates, %d vault events, total: %d",
            len(raw_updates),
            len(batch_events),
            len(all_events),
        )

        # Move end time before oldest update to avoid duplicates
        if oldest_timestamp_ms is not None:
            current_end_ms = oldest_timestamp_ms - 1

        # If we got fewer than max per request, we've likely exhausted the range
        if len(raw_updates) < MAX_UPDATES_PER_REQUEST:
            break

    # Sort by timestamp ascending for chronological processing
    all_events.sort(key=lambda e: e.timestamp)

    logger.info("Fetched %d total vault events for %s", len(all_events), vault_address)

    yield from all_events


def _parse_vault_event(
    update: RawLedgerUpdate,
    vault_address: HexAddress,
) -> VaultDepositEvent | None:
    """Parse a vault event from a ledger update.

    :param update: Raw ledger update
    :param vault_address: The vault address we're querying for
    :return: VaultDepositEvent if this is a vault event, None otherwise
    """
    delta = update.delta
    delta_type = delta.get("type", "")

    if delta_type == "vaultCreate":
        return VaultDepositEvent(
            event_type=VaultEventType.vault_create,
            vault_address=delta.get("vault", vault_address),
            user_address=None,
            usdc=Decimal(str(delta.get("usdc", "0"))),
            timestamp=update.timestamp,
            hash=update.hash,
        )

    elif delta_type == "vaultDeposit":
        return VaultDepositEvent(
            event_type=VaultEventType.vault_deposit,
            vault_address=delta.get("vault", vault_address),
            user_address=delta.get("user"),
            usdc=Decimal(str(delta.get("usdc", "0"))),
            timestamp=update.timestamp,
            hash=update.hash,
        )

    elif delta_type == "vaultWithdraw":
        return VaultDepositEvent(
            event_type=VaultEventType.vault_withdraw,
            vault_address=delta.get("vault", vault_address),
            user_address=delta.get("user"),
            usdc=-abs(Decimal(str(delta.get("usdc", "0")))),  # Negative for outflows
            timestamp=update.timestamp,
            hash=update.hash,
            requested_usd=Decimal(str(delta.get("requestedUsd", "0"))) if "requestedUsd" in delta else None,
            commission=Decimal(str(delta.get("commission", "0"))) if "commission" in delta else None,
            closing_cost=Decimal(str(delta.get("closingCost", "0"))) if "closingCost" in delta else None,
            basis=Decimal(str(delta.get("basis", "0"))) if "basis" in delta else None,
            net_withdrawn_usd=Decimal(str(delta.get("netWithdrawnUsd", "0"))) if "netWithdrawnUsd" in delta else None,
        )

    elif delta_type == "vaultDistribution":
        return VaultDepositEvent(
            event_type=VaultEventType.vault_distribution,
            vault_address=delta.get("vault", vault_address),
            user_address=None,
            usdc=Decimal(str(delta.get("usdc", "0"))),
            timestamp=update.timestamp,
            hash=update.hash,
        )

    elif delta_type == "vaultLeaderCommission":
        return VaultDepositEvent(
            event_type=VaultEventType.vault_leader_commission,
            vault_address=vault_address,
            user_address=delta.get("user"),
            usdc=Decimal(str(delta.get("usdc", "0"))),
            timestamp=update.timestamp,
            hash=update.hash,
        )

    return None


def create_deposit_dataframe(events: list[VaultDepositEvent]) -> pd.DataFrame:
    """Create a DataFrame from vault deposit/withdrawal events.

    Creates a time-indexed DataFrame where each row represents a vault event
    (deposit, withdrawal, distribution, etc.).

    Example::

        from eth_defi.hyperliquid.deposit import fetch_vault_deposits, create_deposit_dataframe

        events = list(fetch_vault_deposits(session, vault_address))
        df = create_deposit_dataframe(events)

        # Calculate net flows
        total_deposits = df[df["event_type"] == "vault_deposit"]["usdc"].sum()
        total_withdrawals = df[df["event_type"] == "vault_withdraw"]["usdc"].abs().sum()
        net_flow = total_deposits - total_withdrawals

    :param events:
        List of vault events from :py:func:`fetch_vault_deposits`
    :return:
        DataFrame with timestamp index and columns for event details
    """
    if not events:
        return pd.DataFrame()

    rows = []
    for event in events:
        row = {
            "event_type": event.event_type.value,
            "vault_address": event.vault_address,
            "user_address": event.user_address,
            "usdc": float(event.usdc),
            "hash": event.hash,
        }

        # Add withdrawal-specific fields if present
        if event.requested_usd is not None:
            row["requested_usd"] = float(event.requested_usd)
        if event.commission is not None:
            row["commission"] = float(event.commission)
        if event.closing_cost is not None:
            row["closing_cost"] = float(event.closing_cost)
        if event.basis is not None:
            row["basis"] = float(event.basis)
        if event.net_withdrawn_usd is not None:
            row["net_withdrawn_usd"] = float(event.net_withdrawn_usd)

        rows.append(row)

    timestamps = [event.timestamp for event in events]
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(timestamps, name="timestamp"))

    return df


def get_deposit_summary(events: list[VaultDepositEvent]) -> dict:
    """Generate a summary of vault deposit/withdrawal activity.

    :param events:
        List of vault events from :py:func:`fetch_vault_deposits`
    :return:
        Dict with summary statistics
    """
    summary = {
        "total_events": len(events),
        "deposits": 0,
        "withdrawals": 0,
        "distributions": 0,
        "commissions": 0,
        "total_deposited": Decimal("0"),
        "total_withdrawn": Decimal("0"),
        "total_distributed": Decimal("0"),
        "total_commission": Decimal("0"),
        "net_flow": Decimal("0"),
        "unique_depositors": set(),
    }

    for event in events:
        if event.event_type == VaultEventType.vault_deposit:
            summary["deposits"] += 1
            summary["total_deposited"] += event.usdc
            if event.user_address:
                summary["unique_depositors"].add(event.user_address)

        elif event.event_type == VaultEventType.vault_withdraw:
            summary["withdrawals"] += 1
            summary["total_withdrawn"] += abs(event.usdc)
            if event.user_address:
                summary["unique_depositors"].add(event.user_address)

        elif event.event_type == VaultEventType.vault_distribution:
            summary["distributions"] += 1
            summary["total_distributed"] += event.usdc

        elif event.event_type == VaultEventType.vault_leader_commission:
            summary["commissions"] += 1
            summary["total_commission"] += event.usdc

    summary["net_flow"] = summary["total_deposited"] - summary["total_withdrawn"]
    summary["unique_depositors"] = len(summary["unique_depositors"])

    return summary
