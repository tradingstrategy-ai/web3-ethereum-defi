"""Lighter pool data extraction and analysis.

This module provides functionality for extracting Lighter pool data
via public endpoints:

- **Pool listing** via ``/api/v1/publicPoolsMetadata`` — bulk fetch all pools with
  TVL, APY, Sharpe ratio, and operator fee
- **Pool details** (share price history, daily returns, positions) via
  ``/api/v1/account`` — per-pool detailed data
- **System config** via ``/api/v1/systemConfig`` — LLP account index

No authentication required.

For more information about Lighter:

- `Lighter <https://lighter.xyz/>`__
- `Lighter API docs <https://apidocs.lighter.xyz/>`__
"""

import datetime
import logging
from dataclasses import dataclass
from typing import Any, Iterator

import pandas as pd

from eth_defi.lighter.session import LighterSession
from eth_defi.types import Percent

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LighterPoolSummary:
    """Summary information for a Lighter pool from the bulk listing.

    From ``/api/v1/publicPoolsMetadata``.
    """

    #: Pool account index (int64 primary identifier)
    account_index: int

    #: Pool display name (e.g. "ETH 3x long")
    name: str

    #: L1 (Ethereum) address of the pool operator
    l1_address: str

    #: Annual percentage yield
    annual_percentage_yield: float

    #: Risk-adjusted return metric
    sharpe_ratio: float | None

    #: Operator fee percentage (e.g. 10.0 = 10%)
    operator_fee: Percent

    #: Total asset value (TVL) in USDC
    total_asset_value: float

    #: Total shares outstanding
    total_shares: int

    #: Pool status code (0 = active)
    status: int

    #: Account type code (2 = pool)
    account_type: int

    #: Master account index (operator's main account)
    master_account_index: int

    #: Creation timestamp
    created_at: datetime.datetime | None

    #: Whether this is the LLP (Lighter Liquidity Pool) protocol pool
    is_llp: bool = False


@dataclass(slots=True)
class LighterPoolDetail:
    """Detailed pool information from the ``/api/v1/account`` endpoint.

    Includes share price history and daily returns from ``pool_info``.
    """

    #: Pool account index
    account_index: int

    #: Pool display name
    name: str

    #: Pool description text
    description: str

    #: Total asset value in USDC
    total_asset_value: float

    #: Operator fee percentage (e.g. 10.0 = 10%)
    operator_fee: Percent

    #: Annual percentage yield
    annual_percentage_yield: float

    #: Sharpe ratio
    sharpe_ratio: float | None

    #: Historical share prices as (timestamp_seconds, share_price) tuples
    share_prices: list[tuple[int, float]]

    #: Historical daily returns as (timestamp_seconds, daily_return) tuples
    daily_returns: list[tuple[int, float]]

    #: Total shares outstanding
    total_shares: int

    #: Operator's shares
    operator_shares: int


def fetch_system_config(
    session: LighterSession,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch Lighter system configuration.

    Returns system config including the LLP account index.

    :param session:
        HTTP session.
    :param timeout:
        HTTP request timeout.
    :return:
        System config dict with key field ``liquidity_pool_index``.
    """
    url = f"{session.api_url}/api/v1/systemConfig"
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_all_pools(
    session: LighterSession,
    timeout: float = 30.0,
    page_size: int = 100,
) -> list[LighterPoolSummary]:
    """Fetch all Lighter public pools.

    Uses ``/api/v1/publicPoolsMetadata`` with pagination.
    Also fetches system config to identify the LLP pool.

    :param session:
        HTTP session.
    :param timeout:
        HTTP request timeout.
    :param page_size:
        Number of pools per page (max 100).
    :return:
        List of :py:class:`LighterPoolSummary` objects.
    """
    # Get LLP index from system config
    config = fetch_system_config(session, timeout=timeout)
    llp_index = config.get("liquidity_pool_index")

    all_pools = []

    # The publicPoolsMetadata endpoint returns pools starting from the given index downward.
    # Start from the LLP index (the highest) to get all pools.
    start_index = llp_index if llp_index else 0

    while True:
        url = f"{session.api_url}/api/v1/publicPoolsMetadata"
        params = {"filter": "all", "index": start_index, "limit": page_size}
        resp = session.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        pools_data = data.get("public_pools", [])
        if not pools_data:
            break

        for p in pools_data:
            account_index = int(p["account_index"])
            created_ts = p.get("created_at")
            created_at = datetime.datetime.fromtimestamp(created_ts) if created_ts else None

            sharpe_str = p.get("sharpe_ratio")
            sharpe_val = float(sharpe_str) if sharpe_str else None

            all_pools.append(
                LighterPoolSummary(
                    account_index=account_index,
                    name=p.get("name", ""),
                    l1_address=p.get("l1_address", ""),
                    annual_percentage_yield=float(p.get("annual_percentage_yield", 0)),
                    sharpe_ratio=sharpe_val,
                    operator_fee=float(p.get("operator_fee", "0")),
                    total_asset_value=float(p.get("total_asset_value", "0")),
                    total_shares=int(p.get("total_shares", 0)),
                    status=int(p.get("status", 0)),
                    account_type=int(p.get("account_type", 0)),
                    master_account_index=int(p.get("master_account_index", 0)),
                    created_at=created_at,
                    is_llp=(account_index == llp_index),
                )
            )

        if len(pools_data) < page_size:
            break

        # Move to next page — pools are indexed downward from the start
        min_index = min(p["account_index"] for p in pools_data)
        start_index = int(min_index) - 1
        if start_index < 0:
            break

    # The LLP (Lighter Liquidity Pool) is a special system pool that
    # is NOT included in publicPoolsMetadata. Add it explicitly by
    # fetching its account details.
    llp_in_listing = any(p.account_index == llp_index for p in all_pools)
    if llp_index and not llp_in_listing:
        try:
            llp_detail = fetch_pool_detail(session, llp_index, timeout=timeout)
            all_pools.append(
                LighterPoolSummary(
                    account_index=llp_index,
                    name=llp_detail.name or "LLP",
                    l1_address="",
                    annual_percentage_yield=llp_detail.annual_percentage_yield,
                    sharpe_ratio=llp_detail.sharpe_ratio,
                    operator_fee=llp_detail.operator_fee,
                    total_asset_value=llp_detail.total_asset_value,
                    total_shares=llp_detail.total_shares,
                    status=0,
                    account_type=2,
                    master_account_index=0,
                    created_at=None,
                    is_llp=True,
                )
            )
        except Exception as e:
            logger.warning("Failed to fetch LLP details: %s", e)

    logger.info("Fetched %d Lighter public pools (LLP index: %s)", len(all_pools), llp_index)
    return all_pools


def fetch_pool_detail(
    session: LighterSession,
    account_index: int,
    timeout: float = 30.0,
) -> LighterPoolDetail:
    """Fetch detailed pool data including share price history.

    Uses ``/api/v1/account?by=index&value={account_index}``.
    The response includes ``pool_info`` with ``share_prices`` and
    ``daily_returns`` arrays for pool accounts.

    :param session:
        HTTP session.
    :param account_index:
        Pool account index.
    :param timeout:
        HTTP request timeout.
    :return:
        :py:class:`LighterPoolDetail` with share price history.
    """
    url = f"{session.api_url}/api/v1/account"
    params = {"by": "index", "value": str(account_index)}
    resp = session.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    # The response wraps account data in an accounts array
    accounts = data.get("accounts", [])
    if not accounts:
        raise ValueError(f"No account data returned for index {account_index}")

    account = accounts[0]
    pool_info = account.get("pool_info") or {}

    # Parse share_prices array: [{"timestamp": ..., "share_price": ...}, ...]
    raw_share_prices = pool_info.get("share_prices") or []
    share_prices = []
    for entry in raw_share_prices:
        ts = int(entry["timestamp"])
        price = float(entry["share_price"])
        share_prices.append((ts, price))

    # Parse daily_returns array: [{"timestamp": ..., "daily_return": ...}, ...]
    raw_daily_returns = pool_info.get("daily_returns") or []
    daily_returns_list = []
    for entry in raw_daily_returns:
        ts = int(entry["timestamp"])
        ret = float(entry["daily_return"])
        daily_returns_list.append((ts, ret))

    sharpe_str = pool_info.get("sharpe_ratio")
    sharpe_val = float(sharpe_str) if sharpe_str else None

    return LighterPoolDetail(
        account_index=account_index,
        name=account.get("name", ""),
        description=account.get("description", ""),
        total_asset_value=float(account.get("total_asset_value", "0")),
        operator_fee=float(pool_info.get("operator_fee", "0")),
        annual_percentage_yield=float(pool_info.get("annual_percentage_yield", 0)),
        sharpe_ratio=sharpe_val,
        share_prices=share_prices,
        daily_returns=daily_returns_list,
        total_shares=int(pool_info.get("total_shares", 0)),
        operator_shares=int(pool_info.get("operator_shares", 0)),
    )


def fetch_pool_total_shares_history(
    session: LighterSession,
    account_index: int,
    start_timestamp: int | None = None,
    timeout: float = 30.0,
) -> dict[datetime.date, int]:
    """Fetch historical total shares from the PnL endpoint.

    Uses ``/api/v1/pnl`` at daily resolution to get ``pool_total_shares``
    at each timestamp. This is the only endpoint that provides full
    history for all pool types (including user pools).

    The returned shares can be combined with share prices to compute
    historical TVL: ``tvl = pool_total_shares * share_price``.

    :param session:
        HTTP session.
    :param account_index:
        Pool account index.
    :param start_timestamp:
        Unix timestamp for the start of the range. Defaults to Jan 1 2025.
    :param timeout:
        HTTP request timeout.
    :return:
        Mapping of ``{date: pool_total_shares}``.
    """
    if start_timestamp is None:
        start_timestamp = int(datetime.datetime(2025, 1, 1).timestamp())

    # Use a far-future end timestamp to get all available data
    end_timestamp = int(datetime.datetime(2030, 1, 1).timestamp())

    url = f"{session.api_url}/api/v1/pnl"
    params = {
        "by": "index",
        "value": str(account_index),
        "resolution": "1d",
        "start_timestamp": str(start_timestamp),
        "end_timestamp": str(end_timestamp),
        "count_back": "0",
        "ignore_transfers": "true",
    }
    resp = session.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    result = {}
    now = datetime.datetime(2030, 1, 1)
    for entry in data.get("pnl", []):
        ts = int(entry["timestamp"])
        dt = datetime.datetime.fromtimestamp(ts)
        # Skip entries with future timestamps (API sometimes returns
        # a "current state" entry with a far-future timestamp)
        if dt > now:
            continue
        total_shares = int(entry.get("pool_total_shares", 0))
        result[dt.date()] = total_shares

    logger.debug(
        "Fetched %d daily total_shares entries for pool %d",
        len(result),
        account_index,
    )
    return result


def pool_detail_to_daily_dataframe(
    detail: LighterPoolDetail,
    total_shares_by_date: dict[datetime.date, int] | None = None,
) -> pd.DataFrame:
    """Convert pool detail share prices into a daily DataFrame.

    Takes the share price history from the ``/api/v1/account`` endpoint
    and produces a DataFrame indexed by date with ``share_price``,
    ``daily_return``, and ``tvl`` columns.

    Historical TVL is computed as ``pool_total_shares * share_price``
    when ``total_shares_by_date`` is provided (from
    :py:func:`fetch_pool_total_shares_history`). Without it, TVL
    defaults to 0.

    The share price array from the API contains daily entries with unix
    timestamps. We convert to dates and compute daily returns via
    ``pct_change()``.

    :param detail:
        Pool detail with share_prices array.
    :param total_shares_by_date:
        Mapping of ``{date: pool_total_shares}`` from the PnL endpoint.
        Used to compute historical TVL.
    :return:
        DataFrame indexed by date with ``share_price``,
        ``daily_return``, and ``tvl`` columns. Empty if insufficient data.
    """
    if len(detail.share_prices) < 2:
        return pd.DataFrame(columns=["share_price", "daily_return", "tvl"])

    records = []
    for ts, price in detail.share_prices:
        dt = datetime.datetime.fromtimestamp(ts)
        records.append(
            {
                "date": dt.date(),
                "share_price": price,
            }
        )

    df = pd.DataFrame(records)

    # Group by date, take last share price per day (handles sub-daily data)
    daily = df.groupby("date").last().sort_index()

    if len(daily) < 2:
        return pd.DataFrame(columns=["share_price", "daily_return", "tvl"])

    daily["daily_return"] = daily["share_price"].pct_change().fillna(0.0)

    # Compute historical TVL from total_shares * share_price
    if total_shares_by_date:
        shares_series = pd.Series(total_shares_by_date, name="total_shares")
        shares_series.index = pd.to_datetime(shares_series.index)
        shares_series.index = shares_series.index.date

        # Reindex to match daily dates, forward-fill gaps
        shares_aligned = shares_series.reindex(daily.index).ffill().fillna(0)
        daily["tvl"] = daily["share_price"] * shares_aligned
    else:
        daily["tvl"] = 0.0

    return daily
