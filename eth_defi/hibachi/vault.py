"""Hibachi vault data extraction.

This module fetches Hibachi vault metadata and daily share price
history from the public data API at ``https://data-api.hibachi.xyz``.

No authentication is required — all data comes from public endpoints.

Endpoints used:

- ``GET /vault/info`` — vault metadata (all vaults or filtered by ``vaultId``)
- ``GET /vault/performance?vaultId={id}&timeRange=All`` — daily share price history

For full API documentation see ``eth_defi/hibachi/README.md``.
"""

import datetime
import logging
from dataclasses import dataclass

from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.hibachi.session import HibachiSession

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HibachiVaultInfo:
    """Metadata for a Hibachi vault.

    Parsed from the ``/vault/info`` endpoint response.

    - `Hibachi vaults page <https://hibachi.xyz/vaults>`__
    """

    #: Unique vault ID on the Hibachi platform (e.g. 2, 3)
    vault_id: int

    #: Short ticker symbol (e.g. ``GAV``, ``FLP``)
    symbol: str

    #: Display name (e.g. ``"Growi Alpha Vault"``)
    short_description: str

    #: Long description of the vault strategy
    description: str | None

    #: Current share price in USDT
    per_share_price: float

    #: Total shares issued
    outstanding_shares: float

    #: Minimum lockup period in hours (0 = no lockup)
    min_unlock_hours: int

    #: Vault's on-exchange public key, for traceability
    vault_pub_key: str

    #: Native asset ID of the vault share token
    vault_asset_id: int

    @property
    def tvl(self) -> float:
        """Current TVL in USDT (``perSharePrice × outstandingShares``)."""
        return self.per_share_price * self.outstanding_shares

    @property
    def address(self) -> str:
        """Synthetic pipeline address (``hibachi-vault-{vault_id}``)."""
        return f"hibachi-vault-{self.vault_id}"


@dataclass(slots=True)
class HibachiVaultDailyPrice:
    """One daily price snapshot for a Hibachi vault.

    Parsed from the ``/vault/performance`` endpoint response.
    """

    #: Vault ID
    vault_id: int

    #: Date of the snapshot (UTC)
    date: datetime.date

    #: Share price in USDT at this snapshot
    per_share_price: float

    #: TVL in USDT at this snapshot
    tvl: float

    #: Daily return as a decimal fraction, or ``None`` for the first data point
    daily_return: float | None


def _parse_vault_info(raw: list[dict]) -> list[HibachiVaultInfo]:
    """Parse raw JSON from ``/vault/info`` into dataclass instances.

    :param raw:
        JSON array from the API response.
    :return:
        List of parsed vault info objects.
    """
    results = []
    for entry in raw:
        results.append(
            HibachiVaultInfo(
                vault_id=entry["vaultId"],
                symbol=entry["symbol"],
                short_description=entry["shortDescription"],
                description=entry.get("description"),
                per_share_price=float(entry["perSharePrice"]),
                outstanding_shares=float(entry["outstandingShares"]),
                min_unlock_hours=entry.get("minUnlockHours", 0),
                vault_pub_key=entry.get("vaultPubKey", ""),
                vault_asset_id=entry.get("vaultAssetId", 0),
            )
        )
    return results


def _parse_vault_performance(raw: dict, vault_id: int) -> list[HibachiVaultDailyPrice]:
    """Parse raw JSON from ``/vault/performance`` into daily price snapshots.

    Timestamps are converted to UTC dates. Rows are sorted ascending
    by timestamp, and daily returns are computed from consecutive
    share prices.

    :param raw:
        JSON object from the API response.
    :param vault_id:
        Vault ID to tag each row with.
    :return:
        List of daily price snapshots, sorted by date ascending.
    """
    intervals = raw.get("vaultPerformanceIntervals", [])

    # Sort by timestamp ascending
    intervals = sorted(intervals, key=lambda x: x["timestamp"])

    results = []
    prev_price = None
    for entry in intervals:
        ts = entry["timestamp"]
        price = float(entry["perSharePrice"])
        tvl = float(entry["totalValueLocked"])
        date = native_datetime_utc_fromtimestamp(ts).date()

        if prev_price is not None and prev_price != 0:
            daily_return = (price - prev_price) / prev_price
        else:
            daily_return = None

        results.append(
            HibachiVaultDailyPrice(
                vault_id=vault_id,
                date=date,
                per_share_price=price,
                tvl=tvl,
                daily_return=daily_return,
            )
        )
        prev_price = price

    return results


def fetch_vault_info(
    session: HibachiSession,
    timeout: float = 30.0,
) -> list[HibachiVaultInfo]:
    """Fetch metadata for all Hibachi vaults.

    Calls ``GET /vault/info`` on the public data API.

    :param session:
        HTTP session.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of vault info objects.
    """
    url = f"{session.api_url}/vault/info"
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return _parse_vault_info(resp.json())


def fetch_vault_performance(
    session: HibachiSession,
    vault_id: int,
    timeout: float = 30.0,
) -> list[HibachiVaultDailyPrice]:
    """Fetch daily share price history for a single Hibachi vault.

    Calls ``GET /vault/performance?vaultId={vault_id}&timeRange=All``
    on the public data API.

    Only ``timeRange=All`` is supported; other values return HTTP 400.

    :param session:
        HTTP session.
    :param vault_id:
        Vault ID to query (e.g. 2 or 3).
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of daily price snapshots, sorted by date ascending.
    """
    url = f"{session.api_url}/vault/performance"
    params = {"vaultId": vault_id, "timeRange": "All"}
    resp = session.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return _parse_vault_performance(resp.json(), vault_id)
