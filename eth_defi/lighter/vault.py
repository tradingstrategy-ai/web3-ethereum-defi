"""Lighter pool data extraction and analysis.

This module provides functionality for extracting Lighter pool data
via public endpoints:

- **Pool listing** via ``/api/v1/publicPoolsMetadata`` — bulk fetch all pools with
  TVL, APY, Sharpe ratio, and operator fee
- **Pool details** (share price history, daily returns, positions) via
  ``/api/v1/account`` — per-pool detailed data
- **System config** via ``/api/v1/systemConfig`` — reported LLP account index

No authentication required.

For more information about Lighter:

- `Lighter <https://lighter.xyz/>`__
- `Lighter API docs <https://apidocs.lighter.xyz/>`__
"""

import datetime
import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

from eth_defi.compat import native_datetime_utc_now
from eth_defi.lighter.session import LighterSession
from eth_defi.types import Percent

logger = logging.getLogger(__name__)

#: Display-name fallback for the canonical protocol liquidity pool. The
#: Robinhood deployment currently returns an empty API name for this account.
LIGHTER_LLP_NAME = "Lighter Liquidity Provider (LLP)"

#: Description fallback for the same protocol-operated insurance pool.
LIGHTER_LLP_DESCRIPTION = "Protocol-operated liquidity and insurance pool that provides market-making liquidity and handles liquidations on Lighter."


@dataclass(slots=True)
class LighterPoolSummary:
    """Summary information for a Lighter pool from the bulk listing.

    From ``/api/v1/publicPoolsMetadata``.
    """

    #: Pool account index (int64 primary identifier)
    account_index: int

    #: Pool display name (e.g. "ETH 3x long")
    name: str

    #: Operator address from the API's legacy ``l1_address`` field
    l1_address: str

    #: Annual percentage yield
    annual_percentage_yield: float

    #: Risk-adjusted return metric
    sharpe_ratio: float | None

    #: Operator fee percentage (e.g. 10.0 = 10%)
    operator_fee: Percent

    #: Total asset value (TVL) in the deployment's collateral currency
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

    #: Total asset value in the deployment's collateral currency
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

    #: Point-in-time account and pool metrics observed by this API read
    snapshot: "LighterPoolSnapshot"


@dataclass(slots=True)
class LighterPoolSnapshot:
    """Point-in-time Lighter pool and account state.

    Values come from one ``/api/v1/account`` response. Historical arrays such
    as ``share_prices`` and ``daily_returns`` are deliberately excluded because
    they are stored in the daily-price table. Collection is append-only from
    the collection start date; the API cannot reconstruct earlier snapshots, so
    pre-collection values remain SQL ``NULL``/Pandas ``NaN`` when joined to
    older price history.
    """

    #: Naive UTC time at which the API response was observed
    snapshot_timestamp: datetime.datetime

    #: Lighter pool account index
    account_index: int

    #: Account-level status code
    account_status: int | None

    #: Pool-level status code from ``pool_info``
    pool_status: int | None

    #: Lighter account type code
    account_type: int | None

    #: Lighter account trading-mode code
    account_trading_mode: int | None

    #: Canonical account NAV in the deployment's collateral currency
    total_asset_value: float | None

    #: Cross-margin asset value in the deployment's collateral currency
    cross_asset_value: float | None

    #: Account collateral in the deployment's collateral currency
    collateral: float | None

    #: Free account balance in the deployment's collateral currency
    available_balance: float | None

    #: Cross-margin initial requirement in the deployment's collateral currency
    initial_margin_requirement: float | None

    #: Cross-margin maintenance requirement in the deployment's collateral currency
    maintenance_margin_requirement: float | None

    #: Operator performance fee as reported by Lighter
    operator_fee: Percent | None

    #: Minimum operator ownership rate as reported by Lighter
    min_operator_share_rate: Percent | None

    #: API annual percentage yield snapshot
    annual_percentage_yield: float | None

    #: API Sharpe ratio snapshot
    sharpe_ratio: float | None

    #: Total outstanding pool shares
    total_shares: int | None

    #: Shares owned by the pool operator
    operator_shares: int | None

    #: Operator ownership fraction calculated from current share counts
    operator_share_fraction: Percent | None

    #: Account-level pending order count
    pending_order_count: int | None

    #: Lifetime order count
    total_order_count: int | None

    #: Lifetime isolated-order count
    total_isolated_order_count: int | None

    #: Source transaction time, in the API's integer time unit
    transaction_time: int | None

    #: Number of current position records
    position_count: int | None

    #: Sum of absolute position values in the deployment's collateral currency
    gross_position_value: float | None

    #: Signed position value in the deployment's collateral currency
    net_position_value: float | None

    #: Sum of long position values in the deployment's collateral currency
    long_position_value: float | None

    #: Sum of short position values in the deployment's collateral currency
    short_position_value: float | None

    #: Largest position value divided by gross position value
    top_position_fraction: Percent | None

    #: Sum of position allocated margin in the deployment's collateral currency
    allocated_margin: float | None

    #: Sum of unrealised position PnL in the deployment's collateral currency
    unrealised_pnl: float | None

    #: Sum of realised position PnL in the deployment's collateral currency
    realised_pnl: float | None

    #: Sum of funding paid out in the deployment's collateral currency
    funding_paid_out: float | None

    #: Sum of open-order counts attached to positions
    open_order_count: int | None

    #: Number of current asset records
    asset_count: int | None

    #: Number of configured strategy records
    strategy_count: int | None

    #: Sum of strategy collateral in the deployment's collateral currency
    strategy_collateral: float | None

    #: Number of pending unlock records
    pending_unlock_count: int | None

    #: Complete current-state account response with historical arrays removed
    source_account: dict[str, Any]


@dataclass(slots=True)
class LighterPoolDailyPnl:
    """One daily Lighter pool PnL observation.

    The Lighter PnL endpoint exposes cumulative flow counters rather than
    individual deposit and withdrawal events.  Keep the source counters intact
    until export, where adjacent complete-day observations can be safely
    differenced.
    """

    #: UTC calendar date of the observation
    date: datetime.date

    #: Outstanding pool shares at the observation
    total_shares: int | None

    #: Cumulative pool deposits in the deployment's collateral currency
    cumulative_pool_inflow: float | None

    #: Cumulative pool withdrawals in the deployment's collateral currency
    cumulative_pool_outflow: float | None

    #: Cumulative account-level inflow
    cumulative_account_inflow: float | None

    #: Cumulative account-level outflow
    cumulative_account_outflow: float | None

    #: Cumulative spot-account inflow
    cumulative_spot_inflow: float | None

    #: Cumulative spot-account outflow
    cumulative_spot_outflow: float | None

    #: Cumulative staking inflow
    cumulative_staking_inflow: float | None

    #: Cumulative staking outflow
    cumulative_staking_outflow: float | None

    #: Source trade PnL value
    trade_pnl: float | None

    #: Source spot-trade PnL value
    trade_spot_pnl: float | None

    #: Source pool PnL value
    pool_pnl: float | None

    #: Source staking PnL value
    staking_pnl: float | None

    #: Source trading-volume value
    volume: float | None


def _parse_optional_float(value: Any) -> float | None:
    """Parse an optional numeric Lighter API value.

    :param value:
        API value, commonly a decimal string.
    :return:
        Parsed float, or ``None`` when the source value is absent.
    """
    return float(value) if value is not None and value != "" else None


def _parse_optional_int(value: Any) -> int | None:
    """Parse an optional integer Lighter API value.

    :param value:
        API value.
    :return:
        Parsed integer, or ``None`` when the source value is absent.
    """
    return int(value) if value is not None and value != "" else None


def parse_lighter_pool_snapshot(
    account: dict[str, Any],
    pool_info: dict[str, Any],
    snapshot_timestamp: datetime.datetime,
) -> LighterPoolSnapshot:
    """Build a point-in-time pool snapshot from one account response.

    Captures every current account/pool collection exposed by the public API
    while deriving queryable exposure aggregates from ``positions``. Historical
    share-price and return arrays are excluded because the daily-price table
    already retains them.

    :param account:
        Raw account object from ``/api/v1/account``.
    :param pool_info:
        Raw ``account["pool_info"]`` object.
    :param snapshot_timestamp:
        Naive UTC observation timestamp.
    :return:
        Parsed point-in-time snapshot.
    """
    positions_value = account.get("positions")
    positions = positions_value if isinstance(positions_value, list) else None
    position_records = [position for position in positions or [] if isinstance(position, dict)]

    signed_position_values = [
        (
            _parse_optional_int(position.get("sign")) or 0,
            abs(_parse_optional_float(position.get("position_value")) or 0.0),
        )
        for position in position_records
    ]
    gross_position_value = sum(value for _, value in signed_position_values) if positions is not None else None
    long_position_value = sum(value for sign, value in signed_position_values if sign > 0) if positions is not None else None
    short_position_value = sum(value for sign, value in signed_position_values if sign < 0) if positions is not None else None
    net_position_value = sum(sign * value for sign, value in signed_position_values) if positions is not None else None
    top_position_fraction: Percent | None = max((value for _, value in signed_position_values), default=0.0) / gross_position_value if gross_position_value else None

    strategies_value = pool_info.get("strategies")
    strategies = strategies_value if isinstance(strategies_value, list) else None
    strategy_records = [strategy for strategy in strategies or [] if isinstance(strategy, dict)]
    strategy_collateral = sum(_parse_optional_float(strategy.get("collateral")) or 0.0 for strategy in strategy_records) if strategies is not None else None

    assets_value = account.get("assets")
    assets = assets_value if isinstance(assets_value, list) else None
    pending_unlocks_value = account.get("pending_unlocks")
    pending_unlocks = pending_unlocks_value if isinstance(pending_unlocks_value, list) else None
    source_account = dict(account)
    source_pool_info = dict(pool_info)
    source_pool_info.pop("share_prices", None)
    source_pool_info.pop("daily_returns", None)
    source_account["pool_info"] = source_pool_info

    total_shares = _parse_optional_int(pool_info.get("total_shares"))
    operator_shares = _parse_optional_int(pool_info.get("operator_shares"))
    operator_share_fraction: Percent | None = operator_shares / total_shares if total_shares and operator_shares is not None else None

    return LighterPoolSnapshot(
        snapshot_timestamp=snapshot_timestamp,
        account_index=int(account.get("account_index", account.get("index"))),
        account_status=_parse_optional_int(account.get("status")),
        pool_status=_parse_optional_int(pool_info.get("status")),
        account_type=_parse_optional_int(account.get("account_type")),
        account_trading_mode=_parse_optional_int(account.get("account_trading_mode")),
        total_asset_value=_parse_optional_float(account.get("total_asset_value")),
        cross_asset_value=_parse_optional_float(account.get("cross_asset_value")),
        collateral=_parse_optional_float(account.get("collateral")),
        available_balance=_parse_optional_float(account.get("available_balance")),
        initial_margin_requirement=_parse_optional_float(account.get("cross_initial_margin_requirement")),
        maintenance_margin_requirement=_parse_optional_float(account.get("cross_maintenance_margin_requirement")),
        operator_fee=_parse_optional_float(pool_info.get("operator_fee")),
        min_operator_share_rate=_parse_optional_float(pool_info.get("min_operator_share_rate")),
        annual_percentage_yield=_parse_optional_float(pool_info.get("annual_percentage_yield")),
        sharpe_ratio=_parse_optional_float(pool_info.get("sharpe_ratio")),
        total_shares=total_shares,
        operator_shares=operator_shares,
        operator_share_fraction=operator_share_fraction,
        pending_order_count=_parse_optional_int(account.get("pending_order_count")),
        total_order_count=_parse_optional_int(account.get("total_order_count")),
        total_isolated_order_count=_parse_optional_int(account.get("total_isolated_order_count")),
        transaction_time=_parse_optional_int(account.get("transaction_time")),
        position_count=len(position_records) if positions is not None else None,
        gross_position_value=gross_position_value,
        net_position_value=net_position_value,
        long_position_value=long_position_value,
        short_position_value=short_position_value,
        top_position_fraction=top_position_fraction,
        allocated_margin=sum(_parse_optional_float(position.get("allocated_margin")) or 0.0 for position in position_records) if positions is not None else None,
        unrealised_pnl=sum(_parse_optional_float(position.get("unrealized_pnl")) or 0.0 for position in position_records) if positions is not None else None,
        realised_pnl=sum(_parse_optional_float(position.get("realized_pnl")) or 0.0 for position in position_records) if positions is not None else None,
        funding_paid_out=sum(_parse_optional_float(position.get("total_funding_paid_out")) or 0.0 for position in position_records) if positions is not None else None,
        open_order_count=sum(_parse_optional_int(position.get("open_order_count")) or 0 for position in position_records) if positions is not None else None,
        asset_count=len(assets) if assets is not None else None,
        strategy_count=len(strategy_records) if strategies is not None else None,
        strategy_collateral=strategy_collateral,
        pending_unlock_count=len(pending_unlocks) if pending_unlocks is not None else None,
        source_account=source_account,
    )


def fetch_system_config(
    session: LighterSession,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch Lighter system configuration.

    Returns system config including the deployment's reported LLP account
    index. Deployment configuration may override a stale reported value.

    :param session:
        HTTP session.
    :param timeout:
        HTTP request timeout.
    :return:
        System config dict with reported ``liquidity_pool_index`` field.
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
    Also fetches system config and applies any deployment-specific LLP account
    override to identify the canonical pool exactly.

    :param session:
        HTTP session.
    :param timeout:
        HTTP request timeout.
    :param page_size:
        Number of pools per page (max 100).
    :return:
        List of :py:class:`LighterPoolSummary` objects.
    """
    # Get the canonical LLP index from deployment configuration when the API
    # cannot currently provide it reliably. For now this override is needed by
    # Lighter on Robinhood: its systemConfig points at an uninitialised account,
    # while the live USDG LLP is present in publicPoolsMetadata at the preceding
    # index. Ethereum continues to trust its systemConfig value.
    config = fetch_system_config(session, timeout=timeout)
    reported_llp_index = config.get("liquidity_pool_index")
    llp_index = session.deployment.llp_account_index_override or reported_llp_index

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
            account_type = int(p.get("account_type", 0))
            # Identify LLP by its canonical deployment-local account index.
            # Account type 3 is not sufficient: Ethereum currently exposes both
            # LLP and XLP with that type. This distinction became load-bearing
            # when adding Lighter on Robinhood because its systemConfig LLP
            # index needs the deployment override above.
            is_llp = account_index == llp_index
            created_ts = p.get("created_at")
            created_at = datetime.datetime.fromtimestamp(created_ts, tz=datetime.timezone.utc).replace(tzinfo=None) if created_ts else None

            sharpe_str = p.get("sharpe_ratio")
            sharpe_val = float(sharpe_str) if sharpe_str else None

            all_pools.append(
                LighterPoolSummary(
                    account_index=account_index,
                    name=p.get("name") or (LIGHTER_LLP_NAME if is_llp else ""),
                    l1_address=p.get("l1_address", ""),
                    annual_percentage_yield=float(p.get("annual_percentage_yield", 0)),
                    sharpe_ratio=sharpe_val,
                    operator_fee=float(p.get("operator_fee", "0")),
                    total_asset_value=float(p.get("total_asset_value", "0")),
                    total_shares=int(p.get("total_shares", 0)),
                    status=int(p.get("status", 0)),
                    account_type=account_type,
                    master_account_index=int(p.get("master_account_index", 0)),
                    created_at=created_at,
                    is_llp=is_llp,
                )
            )

        if len(pools_data) < page_size:
            break

        # Move to next page — pools are indexed downward from the start
        min_index = min(p["account_index"] for p in pools_data)
        start_index = int(min_index) - 1
        if start_index < 0:
            break

    # The LLP can be absent from publicPoolsMetadata on some deployments. Add
    # the exact canonical account explicitly, without allowing another type-3
    # protocol pool such as Ethereum XLP to suppress this recovery path. For
    # now Robinhood reaches the correct canonical identity through its
    # deployment-specific override above.
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
        :py:class:`LighterPoolDetail` with share-price history and the current
        point-in-time account/pool snapshot.
    """
    url = f"{session.api_url}/api/v1/account"
    params = {"by": "index", "value": str(account_index)}
    resp = session.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    snapshot_timestamp = native_datetime_utc_now()

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
        snapshot=parse_lighter_pool_snapshot(account, pool_info, snapshot_timestamp),
    )


def fetch_pool_daily_pnl_history(
    session: LighterSession,
    account_index: int,
    start_timestamp: int | None = None,
    timeout: float = 30.0,
) -> dict[datetime.date, LighterPoolDailyPnl]:
    """Fetch daily Lighter pool accounting and activity history.

    Uses ``/api/v1/pnl`` at daily resolution. This endpoint provides share
    history for all pool types (including user pools) and exposes the
    cumulative flow counters, PnL components, and trading volume.

    Lighter reports human-readable values in the deployment's collateral
    currency. The counters are retained as source values; do not calculate
    flows here because a bounded re-scan must not overwrite a previously known
    daily delta with an unknown first row.

    :param session:
        HTTP session.
    :param account_index:
        Pool account index.
    :param start_timestamp:
        Unix timestamp for the start of the range. Defaults to Jan 1 2025.
    :param timeout:
        HTTP request timeout.
    :return:
        Mapping of UTC date to source PnL observation. Duplicate observations
        for a date retain the last API entry.
    """
    if start_timestamp is None:
        start_timestamp = int(datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc).timestamp())

    # Bound the request at the current UTC time. A far-future range can make
    # the API return a synthetic end-of-range observation as future history.
    now = native_datetime_utc_now()
    end_timestamp = int(now.replace(tzinfo=datetime.timezone.utc).timestamp())

    url = f"{session.api_url}/api/v1/pnl"
    params = {
        "by": "index",
        "value": str(account_index),
        "resolution": "1d",
        "start_timestamp": str(start_timestamp),
        "end_timestamp": str(end_timestamp),
        "count_back": "0",
        "ignore_transfers": "false",
    }
    resp = session.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    result: dict[datetime.date, LighterPoolDailyPnl] = {}
    for entry in data.get("pnl", []):
        ts = int(entry["timestamp"])
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).replace(tzinfo=None)
        # Defensive guard in case the API still includes a future state.
        if dt.date() > now.date():
            continue
        raw_total_shares = entry.get("pool_total_shares")
        raw_inflow = entry.get("pool_inflow")
        raw_outflow = entry.get("pool_outflow")
        result[dt.date()] = LighterPoolDailyPnl(
            date=dt.date(),
            total_shares=int(raw_total_shares) if raw_total_shares is not None else None,
            cumulative_pool_inflow=float(raw_inflow) if raw_inflow is not None else None,
            cumulative_pool_outflow=float(raw_outflow) if raw_outflow is not None else None,
            cumulative_account_inflow=_parse_optional_float(entry.get("inflow")),
            cumulative_account_outflow=_parse_optional_float(entry.get("outflow")),
            cumulative_spot_inflow=_parse_optional_float(entry.get("spot_inflow")),
            cumulative_spot_outflow=_parse_optional_float(entry.get("spot_outflow")),
            cumulative_staking_inflow=_parse_optional_float(entry.get("staking_inflow")),
            cumulative_staking_outflow=_parse_optional_float(entry.get("staking_outflow")),
            trade_pnl=_parse_optional_float(entry.get("trade_pnl")),
            trade_spot_pnl=_parse_optional_float(entry.get("trade_spot_pnl")),
            pool_pnl=_parse_optional_float(entry.get("pool_pnl")),
            staking_pnl=_parse_optional_float(entry.get("staking_pnl")),
            volume=_parse_optional_float(entry.get("volume")),
        )

    logger.debug(
        "Fetched %d daily PnL entries for pool %d",
        len(result),
        account_index,
    )
    return result


def fetch_pool_total_shares_history(
    session: LighterSession,
    account_index: int,
    start_timestamp: int | None = None,
    timeout: float = 30.0,
) -> dict[datetime.date, int]:
    """Fetch historical total shares from the daily PnL endpoint.

    Compatibility helper for existing callers that only need the share
    history. New pipeline code should use :py:func:`fetch_pool_daily_pnl_history`
    to retain the cumulative flow counters as well.

    :param session:
        HTTP session.
    :param account_index:
        Pool account index.
    :param start_timestamp:
        Unix timestamp for the start of the range.
    :param timeout:
        HTTP request timeout.
    :return:
        Mapping of UTC date to total shares for entries that supply shares.
    """
    history = fetch_pool_daily_pnl_history(
        session,
        account_index,
        start_timestamp=start_timestamp,
        timeout=timeout,
    )
    return {date: observation.total_shares for date, observation in history.items() if observation.total_shares is not None}


def pool_detail_to_daily_dataframe(
    detail: LighterPoolDetail,
    total_shares_by_date: dict[datetime.date, int] | None = None,
    pnl_history_by_date: dict[datetime.date, LighterPoolDailyPnl] | None = None,
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
    timestamps. We convert to UTC dates and compute daily returns via
    ``pct_change()``.

    :param detail:
        Pool detail with share_prices array.
    :param total_shares_by_date:
        Mapping of ``{date: pool_total_shares}`` from the PnL endpoint.
        Used to compute historical TVL.
    :param pnl_history_by_date:
        Daily source PnL observations. When supplied, the output also includes
        total shares; account, pool, spot, and staking flow counters; trade,
        spot, pool, and staking PnL; and volume for later storage and export.
    :return:
        DataFrame indexed by date with ``share_price``, ``daily_return``, and
        ``tvl`` columns plus available PnL source columns. Empty if
        insufficient data.
    """
    if len(detail.share_prices) < 2:
        return pd.DataFrame(columns=["share_price", "daily_return", "tvl"])

    records = []
    for ts, price in detail.share_prices:
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).replace(tzinfo=None)
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
    if pnl_history_by_date:
        total_shares_by_date = {date: observation.total_shares for date, observation in pnl_history_by_date.items() if observation.total_shares is not None}

    if total_shares_by_date:
        shares_series = pd.Series(total_shares_by_date, name="total_shares")
        shares_series.index = pd.to_datetime(shares_series.index)
        shares_series.index = shares_series.index.date

        # Reindex to match daily dates, forward-fill gaps
        shares_aligned = shares_series.reindex(daily.index).ffill().fillna(0)
        daily["tvl"] = daily["share_price"] * shares_aligned
        daily["total_shares"] = shares_aligned
    else:
        daily["tvl"] = 0.0
        daily["total_shares"] = pd.NA

    if pnl_history_by_date:
        pnl_df = pd.DataFrame(
            [
                {
                    "date": entry.date,
                    "cumulative_pool_inflow": entry.cumulative_pool_inflow,
                    "cumulative_pool_outflow": entry.cumulative_pool_outflow,
                    "cumulative_account_inflow": entry.cumulative_account_inflow,
                    "cumulative_account_outflow": entry.cumulative_account_outflow,
                    "cumulative_spot_inflow": entry.cumulative_spot_inflow,
                    "cumulative_spot_outflow": entry.cumulative_spot_outflow,
                    "cumulative_staking_inflow": entry.cumulative_staking_inflow,
                    "cumulative_staking_outflow": entry.cumulative_staking_outflow,
                    "trade_pnl": entry.trade_pnl,
                    "trade_spot_pnl": entry.trade_spot_pnl,
                    "pool_pnl": entry.pool_pnl,
                    "staking_pnl": entry.staking_pnl,
                    "volume": entry.volume,
                }
                for entry in pnl_history_by_date.values()
            ]
        ).set_index("date")
        daily = daily.join(pnl_df, how="left")

    return daily
