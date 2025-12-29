"""Hyperliquid vault data extraction and analysis.

This module provides functionality for extracting historical Hyperliquid vault
events and serializing them into Pandas DataFrames for analysis.

Example::

    from eth_defi.hyperliquid.vault import HyperliquidVault

    # Initialize vault client
    vault = HyperliquidVault()

    # Get all vault summaries as DataFrame
    df = vault.get_vault_summaries_dataframe()

    # Get details for a specific vault
    details = vault.get_vault_details("0x1234...")

    # Get vault details with portfolio history as DataFrame
    df_history = vault.get_vault_portfolio_history_dataframe("0x1234...")

For more information about Hyperliquid vaults see:
- https://hyperliquid.gitbook.io/hyperliquid-docs/hypercore/vaults
- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterator

import pandas as pd
import requests
from eth_typing import HexAddress
from requests import Session
from requests.adapters import HTTPAdapter

from eth_defi.velvet.logging_retry import LoggingRetry

logger = logging.getLogger(__name__)

#: Hyperliquid mainnet API URL
HYPERLIQUID_API_URL = "https://api.hyperliquid.xyz"

#: Hyperliquid testnet API URL
HYPERLIQUID_TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"


@dataclass(slots=True)
class VaultFollower:
    """Represents a follower (depositor) in a Hyperliquid vault.

    :param user: Follower's wallet address
    :param vault_equity: Current equity in the vault (USD)
    :param pnl: Profit/loss since entry
    :param all_time_pnl: All-time profit/loss
    :param days_following: Number of days following this vault
    :param vault_entry_time: Timestamp when user entered the vault (milliseconds)
    :param lockup_until: Timestamp when lockup period ends (milliseconds), if applicable
    """
    user: HexAddress
    vault_equity: Decimal
    pnl: Decimal
    all_time_pnl: Decimal
    days_following: int
    vault_entry_time: int
    lockup_until: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for DataFrame serialization."""
        return {
            "user": self.user,
            "vault_equity": float(self.vault_equity),
            "pnl": float(self.pnl),
            "all_time_pnl": float(self.all_time_pnl),
            "days_following": self.days_following,
            "vault_entry_time": datetime.fromtimestamp(self.vault_entry_time / 1000) if self.vault_entry_time else None,
            "lockup_until": datetime.fromtimestamp(self.lockup_until / 1000) if self.lockup_until else None,
        }


@dataclass(slots=True)
class VaultSummary:
    """Summary information for a Hyperliquid vault.

    :param name: Vault display name
    :param vault_address: Vault's blockchain address
    :param leader: Vault manager/operator address
    :param tvl: Total Value Locked (USD)
    :param is_closed: Whether deposits are closed
    :param relationship_type: Vault relationship type (normal, child, parent)
    :param create_time: Vault creation timestamp
    """
    name: str
    vault_address: HexAddress
    leader: HexAddress
    tvl: Decimal
    is_closed: bool
    relationship_type: str
    create_time: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for DataFrame serialization."""
        return {
            "name": self.name,
            "vault_address": self.vault_address,
            "leader": self.leader,
            "tvl": float(self.tvl),
            "is_closed": self.is_closed,
            "relationship_type": self.relationship_type,
            "create_time": self.create_time,
        }


class HyperliquidVault:
    """Client for extracting historical Hyperliquid vault events and data.

    This class provides methods to fetch vault information from the Hyperliquid
    API and serialize the data into Pandas DataFrames for analysis.

    Example::

        # Initialize for mainnet
        vault = HyperliquidVault()

        # Or for testnet
        vault = HyperliquidVault(testnet=True)

        # Get all vaults summary
        summaries_df = vault.get_vault_summaries_dataframe()
        print(f"Found {len(summaries_df)} vaults")

        # Get specific vault details
        details = vault.get_vault_details("0x...")

        # Get portfolio history for a vault
        history_df = vault.get_vault_portfolio_history_dataframe("0x...")

    :param testnet: Use testnet API instead of mainnet
    :param timeout: HTTP request timeout in seconds
    """

    def __init__(
        self,
        testnet: bool = False,
        timeout: float = 30.0,
    ):
        """Initialize the Hyperliquid vault client.

        :param testnet:
            If True, use the testnet API URL instead of mainnet
        :param timeout:
            HTTP request timeout in seconds
        """
        self.base_url = HYPERLIQUID_TESTNET_API_URL if testnet else HYPERLIQUID_API_URL
        self.timeout = timeout
        self.testnet = testnet

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
        url = f"{self.base_url}/info"
        payload = {"type": request_type}
        if params:
            payload.update(params)

        logger.debug(f"Making request to {url} with payload: {payload}")

        response = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()


#: Default number of retries for API requests
DEFAULT_RETRIES = 5

#: Default backoff factor for retries (seconds)
DEFAULT_BACKOFF_FACTOR = 0.5


def fetch_all_vaults(
    api_url: str = HYPERLIQUID_API_URL,
    timeout: float = 30.0,
    retries: int = DEFAULT_RETRIES,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
) -> Iterator[VaultSummary]:
    """Iterate over all Hyperliquid vaults.

    This function fetches all vault summaries from the Hyperliquid API
    and yields them one by one. It handles API throttling using
    exponential backoff retry logic.

    Example::

        from eth_defi.hyperliquid.vault import fetch_all_vaults, HYPERLIQUID_TESTNET_API_URL

        # Iterate over all vaults (mainnet)
        for vault in fetch_all_vaults():
            print(f"Vault: {vault.name}, TVL: ${vault.tvl:,.2f}")

        # Use testnet
        for vault in fetch_all_vaults(api_url=HYPERLIQUID_TESTNET_API_URL):
            print(f"Testnet vault: {vault.name}")

        # Filter vaults with high TVL
        high_tvl_vaults = [v for v in fetch_all_vaults() if v.tvl > 1_000_000]

        # Convert to list
        all_vaults = list(fetch_all_vaults())

    :param api_url:
        Hyperliquid API base URL.
        Use HYPERLIQUID_API_URL for mainnet or HYPERLIQUID_TESTNET_API_URL for testnet.
    :param timeout:
        HTTP request timeout in seconds
    :param retries:
        Maximum number of retry attempts for failed requests
    :param backoff_factor:
        Backoff factor for exponential retry delays
    :return:
        Iterator yielding VaultSummary objects
    :raises requests.HTTPError:
        If the HTTP request fails after all retries
    """
    url = f"{api_url}/info"

    # Set up session with retry policy for API throttling
    session = Session()
    retry_policy = LoggingRetry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        logger=logger,
    )
    session.mount("http://", HTTPAdapter(max_retries=retry_policy))
    session.mount("https://", HTTPAdapter(max_retries=retry_policy))

    payload = {"type": "vaultSummaries"}

    logger.debug(f"Fetching all vaults from {url}")

    response = session.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()

    logger.info(f"Fetched {len(data)} vaults from Hyperliquid")

    for item in data:
        relationship = item.get("relationship", {})
        create_time_millis = item.get("createTimeMillis")

        yield VaultSummary(
            name=item.get("name", ""),
            vault_address=item.get("vaultAddress", ""),
            leader=item.get("leader", ""),
            tvl=Decimal(str(item.get("tvl", "0"))),
            is_closed=item.get("isClosed", False),
            relationship_type=relationship.get("type", "normal"),
            create_time=datetime.fromtimestamp(create_time_millis / 1000) if create_time_millis else None,
        )
