"""Hyperliquid vault data extraction and analysis.

This module provides functionality for extracting historical Hyperliquid vault
events and serializing them into Pandas DataFrames for analysis.

API Endpoints
-------------

Hyperliquid has two relevant API surfaces for vault data:

**Official Info API** (``https://api.hyperliquid.xyz/info``):

- ``vaultDetails`` - Get details for a specific vault (works)
- ``userVaultEquities`` - Get user's vault positions (works)
- ``vaultSummaries`` - Documented but returns empty array (non-functional)

**Stats Data API** (``https://stats-data.hyperliquid.xyz/Mainnet/vaults``):

- Undocumented internal endpoint that powers the Hyperliquid web UI
- Returns all vaults (~8,000+) with APR, TVL, and PNL history arrays
- Uses GET instead of POST - serves pre-aggregated/cached data
- More comprehensive than the official API but may change without notice

The ``vaultSummaries`` endpoint in the official API is documented but non-functional
(returns ``[]``). For bulk vault listing, use the stats-data endpoint or the
bash script at ``scripts/hyperliquid/fetch-vault-metadata.sh``.

For more information about Hyperliquid vaults see:

- https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults
- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
- https://app.hyperliquid.xyz/vaults (web UI using stats-data endpoint)
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from functools import cached_property
from typing import Any, Iterator

import pandas as pd
from eth_typing import HexAddress
from eth_defi.hyperliquid.session import (
    HYPERLIQUID_API_URL,
    HYPERLIQUID_TESTNET_API_URL,
    HyperliquidSession,
    create_hyperliquid_session,
)
from eth_defi.types import Percent

logger = logging.getLogger(__name__)

#: Hyperliquid stats-data API URL for vault listing (mainnet).
#: This is an undocumented internal endpoint that powers the Hyperliquid web UI.
#: It must be used instead of the official ``vaultSummaries`` endpoint which is
#: documented but non-functional (returns empty array). This endpoint returns
#: all vaults (~8,000+) with APR, TVL, and PNL history via GET request.
#: See: https://app.hyperliquid.xyz/vaults
HYPERLIQUID_STATS_URL = "https://stats-data.hyperliquid.xyz/Mainnet/vaults"

#: Hyperliquid stats-data API URL for vault listing (testnet)
HYPERLIQUID_STATS_TESTNET_URL = "https://stats-data.hyperliquid-testnet.xyz/Mainnet/vaults"


@dataclass(slots=True)
class VaultFollower:
    """Represents a follower (depositor) in a Hyperliquid vault."""

    #: Follower's wallet address
    user: HexAddress
    #: Current equity in the vault (USD)
    vault_equity: Decimal
    #: Profit/loss since entry
    pnl: Decimal
    #: All-time profit/loss
    all_time_pnl: Decimal
    #: Number of days following this vault
    days_following: int
    #: Timestamp when user entered the vault (milliseconds)
    vault_entry_time: int
    #: Timestamp when lockup period ends (milliseconds), if applicable
    lockup_until: int | None = None


@dataclass(slots=True)
class PortfolioHistory:
    """Historical portfolio data for a specific time period.

    Contains account value history, PNL history, and trading volume
    for a given period (day, week, month, or allTime).
    """

    #: Time period identifier (day, week, month, allTime)
    period: str
    #: Account value history as list of (timestamp, value) tuples
    account_value_history: list[tuple[datetime, Decimal]]
    #: PNL history as list of (timestamp, pnl) tuples
    pnl_history: list[tuple[datetime, Decimal]]
    #: Trading volume for the period (USD)
    volume: Decimal


@dataclass(slots=True)
class VaultInfo:
    """Detailed information about a Hyperliquid vault.

    This dataclass represents the response from the ``vaultDetails`` API endpoint,
    containing comprehensive vault metadata, follower information, and portfolio history.
    """

    #: Vault display name
    name: str
    #: Vault's blockchain address
    vault_address: HexAddress
    #: Vault manager/operator address
    leader: HexAddress
    #: Vault description text
    description: str
    #: List of vault followers (depositors)
    followers: list[VaultFollower]
    #: Portfolio history by time period
    portfolio: dict[str, PortfolioHistory]
    #: Maximum distributable amount (USD)
    max_distributable: Decimal
    #: Maximum withdrawable amount (USD)
    max_withdrawable: Decimal
    #: Whether vault is closed for deposits
    is_closed: bool
    #: Whether vault allows deposits
    allow_deposits: bool
    #: Vault relationship type (normal, child, parent)
    relationship_type: str
    #: Commission rate for the vault leader (as decimal, e.g., 0.1 = 10%)
    commission_rate: Percent | None = None
    #: Parent vault address if this is a child vault
    parent: HexAddress | None = None


@dataclass(slots=True)
class VaultSummary:
    """Summary information for a Hyperliquid vault.

    Contains both basic vault metadata and performance metrics from the
    stats-data endpoint.
    """

    #: Vault display name
    name: str
    #: Vault's blockchain address
    vault_address: HexAddress
    #: Vault manager/operator address
    leader: HexAddress
    #: Total Value Locked (USD)
    tvl: Decimal
    #: Whether deposits are closed
    is_closed: bool
    #: Vault relationship type (normal, child, parent)
    relationship_type: str
    #: Vault creation timestamp
    create_time: datetime | None = None
    #: Annual Percentage Rate (as decimal, e.g., 0.15 = 15%)
    apr: Percent | None = None
    #: PNL history for daily period (list of decimal strings)
    pnl_day: list[str] | None = None
    #: PNL history for weekly period (list of decimal strings)
    pnl_week: list[str] | None = None
    #: PNL history for monthly period (list of decimal strings)
    pnl_month: list[str] | None = None
    #: PNL history for all-time period (list of decimal strings)
    pnl_all_time: list[str] | None = None


class HyperliquidVault:
    """Client for extracting historical Hyperliquid vault events and data.

    This class provides methods to fetch vault information from the Hyperliquid
    API and serialize the data into Pandas DataFrames for analysis.

    Example::

        from eth_defi.hyperliquid.session import create_hyperliquid_session, HYPERLIQUID_TESTNET_API_URL
        from eth_defi.hyperliquid.vault import HyperliquidVault

        # Mainnet
        session = create_hyperliquid_session()
        vault = HyperliquidVault(
            session=session,
            vault_address="0x1234567890abcdef1234567890abcdef12345678",
        )

        # Testnet
        session = create_hyperliquid_session(api_url=HYPERLIQUID_TESTNET_API_URL)
        vault = HyperliquidVault(
            session=session,
            vault_address="0x1234567890abcdef1234567890abcdef12345678",
        )

    :param session: Session from :py:func:`create_hyperliquid_session`
    :param vault_address: The vault's blockchain address
    :param timeout: HTTP request timeout in seconds
    """

    def __init__(
        self,
        session: HyperliquidSession,
        vault_address: HexAddress,
        timeout: float = 30.0,
    ):
        """Initialise the Hyperliquid vault client.

        :param session:
            Session from :py:func:`create_hyperliquid_session`.
            The API URL is read from :py:attr:`HyperliquidSession.api_url`.
        :param vault_address:
            The vault's blockchain address
        :param timeout:
            HTTP request timeout in seconds
        """
        self.session = session
        self.vault_address = vault_address
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"<HyperliquidVault {self.vault_address}>"

    def _make_request(
        self,
        request_type: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Make a POST request to the Hyperliquid info endpoint.

        :param request_type:
            The type of info request (e.g., "vaultSummaries", "vaultDetails")
        :param params:
            Additional parameters for the request
        :return:
            Parsed JSON response
        :raises requests.HTTPError:
            If the HTTP request fails
        :raises requests.Timeout:
            If the request times out
        """
        url = f"{self.session.api_url}/info"
        payload = {"type": request_type}
        if params:
            payload.update(params)

        logger.debug(f"Making request to {url} with payload: {payload}")

        response = self.session.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def fetch_info(self) -> VaultInfo:
        """Fetch detailed vault information from the Hyperliquid API.

        Makes a request to the ``vaultDetails`` endpoint and returns
        a typed :py:class:`VaultInfo` dataclass with all vault metadata.

        Use :py:attr:`info` property for cached access.

        Example::

            from eth_defi.hyperliquid.session import create_hyperliquid_session
            from eth_defi.hyperliquid.vault import HyperliquidVault

            session = create_hyperliquid_session()
            vault = HyperliquidVault(
                session=session,
                vault_address="0x3df9769bbbb335340872f01d8157c779d73c6ed0",
            )

            info = vault.fetch_info()
            print(f"Vault: {info.name}")
            print(f"Leader: {info.leader}")
            print(f"Followers: {len(info.followers)}")

        :return:
            VaultInfo dataclass with vault details
        :raises requests.HTTPError:
            If the HTTP request fails
        """
        data = self._make_request("vaultDetails", {"vaultAddress": self.vault_address})

        # Parse followers
        followers = []
        for f in data.get("followers", []):
            followers.append(
                VaultFollower(
                    user=f["user"],
                    vault_equity=Decimal(str(f["vaultEquity"])),
                    pnl=Decimal(str(f["pnl"])),
                    all_time_pnl=Decimal(str(f["allTimePnl"])),
                    days_following=f["daysFollowing"],
                    vault_entry_time=f["vaultEntryTime"],
                    lockup_until=f.get("lockupUntil"),
                )
            )

        # Parse portfolio history
        portfolio: dict[str, PortfolioHistory] = {}
        for period_name, period_data in data.get("portfolio", []):
            account_value_history = [(datetime.fromtimestamp(ts / 1000), Decimal(str(value))) for ts, value in period_data.get("accountValueHistory", [])]
            pnl_history = [(datetime.fromtimestamp(ts / 1000), Decimal(str(value))) for ts, value in period_data.get("pnlHistory", [])]
            volume = Decimal(str(period_data.get("vlm", "0")))

            portfolio[period_name] = PortfolioHistory(
                period=period_name,
                account_value_history=account_value_history,
                pnl_history=pnl_history,
                volume=volume,
            )

        # Parse relationship
        relationship = data.get("relationship", {})
        relationship_type = relationship.get("type", "normal")
        parent = relationship.get("parent")

        return VaultInfo(
            name=data["name"],
            vault_address=data["vaultAddress"],
            leader=data["leader"],
            description=data.get("description", ""),
            followers=followers,
            portfolio=portfolio,
            max_distributable=Decimal(str(data.get("maxDistributable", "0"))),
            max_withdrawable=Decimal(str(data.get("maxWithdrawable", "0"))),
            is_closed=data.get("isClosed", False),
            allow_deposits=data.get("allowDeposits", True),
            relationship_type=relationship_type,
            commission_rate=data.get("commissionRate"),
            parent=parent,
        )

    @cached_property
    def info(self) -> VaultInfo:
        """Cached vault information.

        Fetches vault details on first access and caches the result.
        Use :py:meth:`fetch_info` to force a fresh fetch.

        :return:
            VaultInfo dataclass with vault details
        """
        return self.fetch_info()


class VaultSortKey(Enum):
    """Supported sort keys for vault listing.

    These correspond to fields available in VaultSummary that provide
    stable, deterministic ordering.
    """

    #: Sort by vault address (hex string, stable and unique)
    VAULT_ADDRESS = "vault_address"
    #: Sort by vault name (alphabetical)
    NAME = "name"
    #: Sort by TVL (Total Value Locked)
    TVL = "tvl"
    #: Sort by APR (Annual Percentage Rate)
    APR = "apr"
    #: Sort by creation time
    CREATE_TIME = "create_time"


def fetch_all_vaults(
    session: HyperliquidSession | None = None,
    stats_url: str = HYPERLIQUID_STATS_URL,
    timeout: float = 30.0,
) -> Iterator[VaultSummary]:
    """Iterate over all Hyperliquid vaults.

    This function fetches all vault summaries from the Hyperliquid stats-data API
    and yields them one by one. It handles API throttling using
    exponential backoff retry logic.

    .. note::

        This uses the undocumented stats-data endpoint (``HYPERLIQUID_STATS_URL``)
        instead of the official ``vaultSummaries`` endpoint which is documented
        but non-functional (returns empty array).

    Example::

        from eth_defi.hyperliquid.session import create_hyperliquid_session
        from eth_defi.hyperliquid.vault import fetch_all_vaults, HYPERLIQUID_STATS_TESTNET_URL

        # Create a session for API requests
        session = create_hyperliquid_session()

        # Iterate over all vaults (mainnet)
        for vault in fetch_all_vaults(session):
            print(f"Vault: {vault.name}, TVL: ${vault.tvl:,.2f}")

        # Use testnet
        for vault in fetch_all_vaults(session, stats_url=HYPERLIQUID_STATS_TESTNET_URL):
            print(f"Testnet vault: {vault.name}")

        # Filter vaults with high TVL
        high_tvl_vaults = [v for v in fetch_all_vaults(session) if v.tvl > 1_000_000]

        # Convert to list
        all_vaults = list(fetch_all_vaults(session))

        # Sort vaults by TVL after fetching
        sorted_by_tvl = sorted(fetch_all_vaults(session), key=lambda v: v.tvl, reverse=True)

        # Get top 10 vaults by TVL
        top_tvl = sorted_by_tvl[:10]

    :param session:
        A requests Session configured for Hyperliquid API.
        Use :py:func:`eth_defi.hyperliquid.session.create_hyperliquid_session` to create one.
        If None, a default session will be created.
    :param stats_url:
        Hyperliquid stats-data API URL.
        Use ``HYPERLIQUID_STATS_URL`` for mainnet or ``HYPERLIQUID_STATS_TESTNET_URL`` for testnet.
    :param timeout:
        HTTP request timeout in seconds
    :return:
        Iterator yielding VaultSummary objects
    :raises requests.HTTPError:
        If the HTTP request fails after all retries
    """
    if session is None:
        session = create_hyperliquid_session()

    logger.debug(f"Fetching all vaults from {stats_url}")

    # Stats-data endpoint uses GET, not POST
    response = session.get(
        stats_url,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()

    logger.info(f"Fetched {len(data)} vaults from Hyperliquid")

    def _parse_vault(item: dict) -> VaultSummary:
        """Parse a single vault item from the API response."""
        # Stats-data response has vault info nested under "summary" key
        summary = item.get("summary", {})
        relationship = summary.get("relationship", {})
        create_time_millis = summary.get("createTimeMillis")

        # Parse PNL history arrays from the response
        # Format: [["day", [...]], ["week", [...]], ["month", [...]], ["allTime", [...]]]
        pnls = {period: values for period, values in item.get("pnls", [])}

        return VaultSummary(
            name=summary.get("name", ""),
            vault_address=summary.get("vaultAddress", ""),
            leader=summary.get("leader", ""),
            tvl=Decimal(str(summary.get("tvl", "0"))),
            is_closed=summary.get("isClosed", False),
            relationship_type=relationship.get("type", "normal"),
            create_time=datetime.fromtimestamp(create_time_millis / 1000) if create_time_millis else None,
            apr=item.get("apr"),
            pnl_day=pnls.get("day"),
            pnl_week=pnls.get("week"),
            pnl_month=pnls.get("month"),
            pnl_all_time=pnls.get("allTime"),
        )

    # No sorting - yield directly for memory efficiency
    for item in data:
        yield _parse_vault(item)
