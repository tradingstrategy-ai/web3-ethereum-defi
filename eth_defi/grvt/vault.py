"""GRVT vault data extraction and analysis.

This module provides functionality for extracting GRVT vault data
via public endpoints:

- **Vault discovery** is done via the public GraphQL API at
  ``https://edge.grvt.io/query``, which provides vault metadata
  including per-vault fee percentages.

- **Vault details** (TVL, share price, performance, risk metrics,
  share price history) come from the public market data API at
  ``https://market-data.grvt.io``.

No authentication is required for these endpoints.

For more information about GRVT strategies see:

- https://grvt.io/exchange/strategies
- https://help.grvt.io/en/articles/11424324-what-is-grvt-strategies
- https://help.grvt.io/en/articles/11424466-grvt-strategies-core-concepts
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
from requests import Session

from eth_defi.grvt.constants import GRVT_FEE_PPM_DIVISOR, GRVT_GRAPHQL_URL, GRVT_MARKET_DATA_URL
from eth_defi.types import Percent

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GRVTVaultSummary:
    """Summary information for a GRVT vault.

    Combines data from the GRVT GraphQL API
    with live data from the market data API.
    """

    #: Vault string ID on the GRVT platform (e.g. ``VLT:34dTZyg6LhkGM49Je5AABi9tEbW``)
    vault_id: str

    #: Numeric on-chain vault ID used by the market data API
    chain_vault_id: int

    #: Vault display name
    name: str

    #: Description of the strategy
    description: str

    #: Vault type (``prime`` or ``launchpad``)
    vault_type: str

    #: Whether the vault is listed on the strategies page
    discoverable: bool

    #: Vault status (e.g. ``active``)
    status: str

    #: Manager name
    manager_name: str

    #: Strategy categories (e.g. ``["Market Making", "Delta Neutral"]``)
    categories: list[str]

    #: Creation timestamp
    create_time: datetime | None = None

    #: Current TVL in USDT (from market data API)
    tvl: float | None = None

    #: Current share price (from market data API)
    share_price: float | None = None

    #: Annual management fee as a decimal fraction (e.g. 0.01 = 1%).
    #: From the GRVT GraphQL API ``managementFee`` field (PPM).
    management_fee: Percent | None = None

    #: Performance fee as a decimal fraction (e.g. 0.20 = 20%).
    #: From the GRVT GraphQL API ``performanceFee`` field (PPM).
    performance_fee: Percent | None = None


@dataclass(slots=True)
class GRVTVaultPerformance:
    """Performance metrics for a GRVT vault from the market data API."""

    #: Numeric on-chain vault ID
    chain_vault_id: int
    #: Annualised percentage return
    apr: Percent
    #: 30-day return
    return_30d: Percent
    #: 90-day return
    return_90d: Percent
    #: Year-to-date return
    return_ytd: Percent
    #: Return since inception
    return_since_inception: Percent
    #: Total trading volume in USDT
    trading_volume: float
    #: Cumulative PnL in USDT
    cumulative_pnl: float


@dataclass(slots=True)
class GRVTVaultRiskMetric:
    """Risk metrics for a GRVT vault from the market data API."""

    #: Numeric on-chain vault ID
    chain_vault_id: int
    #: Sharpe ratio
    sharpe_ratio: float
    #: Sortino ratio
    sortino_ratio: float
    #: Maximum drawdown as a decimal (e.g. 0.12 = 12%)
    max_drawdown: Percent


#: GraphQL query for fetching GRVT vault listing with fee data.
_VAULT_LISTING_GRAPHQL_QUERY = """
query VaultListingQuery($first: Int, $where: VaultWhereInput) {
    vaults(first: $first, where: $where) {
        totalCount
        edges {
            node {
                id
                name
                chainVaultID
                description
                type
                discoverable
                status
                managerName
                managementFee
                performanceFee
                createTime
                valuationCap
                mappedCategories {
                    name
                }
            }
        }
    }
}
"""


def _graphql_node_to_summary(node: dict[str, Any]) -> GRVTVaultSummary:
    """Convert a GraphQL vault node into a :py:class:`GRVTVaultSummary`.

    :param node:
        Vault node dict from the GraphQL response.
    :return:
        Parsed :py:class:`GRVTVaultSummary`.
    """
    create_time = None
    if node.get("createTime"):
        try:
            ct = node["createTime"].rstrip("Z")
            create_time = datetime.fromisoformat(ct)
        except (ValueError, TypeError):
            pass

    categories = [c.get("name", "") for c in node.get("mappedCategories", []) or []]

    # Convert PPM fee values to decimal fractions
    mgmt_fee_ppm = node.get("managementFee")
    perf_fee_ppm = node.get("performanceFee")
    management_fee = mgmt_fee_ppm / GRVT_FEE_PPM_DIVISOR if mgmt_fee_ppm is not None else None
    performance_fee = perf_fee_ppm / GRVT_FEE_PPM_DIVISOR if perf_fee_ppm is not None else None

    return GRVTVaultSummary(
        vault_id=node["id"],
        chain_vault_id=int(node["chainVaultID"]),
        name=node.get("name", ""),
        description=node.get("description", ""),
        vault_type=node.get("type", ""),
        discoverable=node.get("discoverable", False),
        status=node.get("status", ""),
        manager_name=node.get("managerName", ""),
        categories=categories,
        create_time=create_time,
        management_fee=management_fee,
        performance_fee=performance_fee,
    )


def fetch_vault_listing_graphql(
    session: Session,
    graphql_url: str = GRVT_GRAPHQL_URL,
    only_discoverable: bool = True,
    timeout: float = 30.0,
) -> list[GRVTVaultSummary]:
    """Fetch GRVT vault listing via the public GraphQL API.

    Returns per-vault fee data (``managementFee``, ``performanceFee``)
    alongside vault metadata.

    The GraphQL endpoint at ``https://edge.grvt.io/query`` is public
    and requires no authentication.

    Example::

        from eth_defi.grvt.session import create_grvt_session
        from eth_defi.grvt.vault import fetch_vault_listing_graphql

        session = create_grvt_session()
        vaults = fetch_vault_listing_graphql(session)
        for v in vaults:
            print(f"{v.name}: mgmt={v.management_fee}, perf={v.performance_fee}")

    :param session:
        HTTP session (no authentication needed).
    :param graphql_url:
        GRVT GraphQL API URL.
    :param only_discoverable:
        If True, only return vaults marked as discoverable.
    :param timeout:
        HTTP request timeout in seconds.
    :return:
        List of :py:class:`GRVTVaultSummary` objects with fee data populated.
    """
    logger.info("Fetching GRVT vault listing from GraphQL API at %s", graphql_url)

    where = {"discoverable": True} if only_discoverable else {}
    payload = {
        "query": _VAULT_LISTING_GRAPHQL_QUERY,
        "variables": {"first": 100, "where": where},
    }

    resp = session.post(graphql_url, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        raise ValueError(f"GraphQL errors: {data['errors']}")

    edges = data.get("data", {}).get("vaults", {}).get("edges", [])
    total_count = data.get("data", {}).get("vaults", {}).get("totalCount", 0)

    logger.info("GraphQL returned %d vaults (totalCount=%d)", len(edges), total_count)

    summaries = [_graphql_node_to_summary(e["node"]) for e in edges]

    if only_discoverable:
        summaries = [s for s in summaries if s.discoverable]
        logger.info("Filtered to %d discoverable vaults", len(summaries))

    return summaries


def fetch_vault_details(
    session: Session,
    chain_vault_ids: list[int],
    market_data_url: str = GRVT_MARKET_DATA_URL,
    timeout: float = 30.0,
) -> dict[int, dict[str, Any]]:
    """Fetch vault detail data (TVL, share price) from the market data API.

    Uses ``/full/v1/vault_detail``. No authentication required.

    :param session:
        HTTP session.
    :param chain_vault_ids:
        List of numeric chain vault IDs.
    :param market_data_url:
        Market data API base URL.
    :param timeout:
        HTTP request timeout.
    :return:
        Dict mapping chain_vault_id to detail dict with keys:
        ``share_price``, ``total_equity``, ``valuation_cap``,
        ``total_supply_lp_tokens``.
    """
    url = f"{market_data_url}/full/v1/vault_detail"
    payload = {"vault_i_ds": [str(cid) for cid in chain_vault_ids]}

    resp = session.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    results = resp.json().get("result", [])

    return {int(r["vault_id"]): r for r in results}


def fetch_vault_performance(
    session: Session,
    chain_vault_ids: list[int],
    market_data_url: str = GRVT_MARKET_DATA_URL,
    timeout: float = 30.0,
) -> dict[int, GRVTVaultPerformance]:
    """Fetch vault performance metrics from the market data API.

    Uses ``/full/v1/vault_performance``. No authentication required.

    :param session:
        HTTP session.
    :param chain_vault_ids:
        List of numeric chain vault IDs.
    :param market_data_url:
        Market data API base URL.
    :param timeout:
        HTTP request timeout.
    :return:
        Dict mapping chain_vault_id to :py:class:`GRVTVaultPerformance`.
    """
    url = f"{market_data_url}/full/v1/vault_performance"
    payload = {"vault_i_ds": [str(cid) for cid in chain_vault_ids]}

    resp = session.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    results = resp.json().get("result", [])

    out = {}
    for r in results:
        cid = int(r["vault_id"])
        out[cid] = GRVTVaultPerformance(
            chain_vault_id=cid,
            apr=r.get("apr", 0.0),
            return_30d=r.get("return_30_d", 0.0),
            return_90d=r.get("return_90_d", 0.0),
            return_ytd=r.get("return_ytd", 0.0),
            return_since_inception=r.get("return_since_interception", 0.0),
            trading_volume=float(r.get("trading_volume", "0")),
            cumulative_pnl=float(r.get("cumulative_pnl", "0")),
        )
    return out


def fetch_vault_risk_metrics(
    session: Session,
    chain_vault_ids: list[int],
    market_data_url: str = GRVT_MARKET_DATA_URL,
    timeout: float = 30.0,
) -> dict[int, GRVTVaultRiskMetric]:
    """Fetch vault risk metrics from the market data API.

    Uses ``/full/v1/vault_risk_metric``. No authentication required.

    :param session:
        HTTP session.
    :param chain_vault_ids:
        List of numeric chain vault IDs.
    :param market_data_url:
        Market data API base URL.
    :param timeout:
        HTTP request timeout.
    :return:
        Dict mapping chain_vault_id to :py:class:`GRVTVaultRiskMetric`.
    """
    url = f"{market_data_url}/full/v1/vault_risk_metric"
    payload = {"vault_i_ds": [str(cid) for cid in chain_vault_ids]}

    resp = session.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    results = resp.json().get("result", [])

    out = {}
    for r in results:
        cid = int(r["vault_id"])
        out[cid] = GRVTVaultRiskMetric(
            chain_vault_id=cid,
            sharpe_ratio=r.get("sharpe_ratio", 0.0),
            sortino_ratio=r.get("sortino_ratio", 0.0),
            max_drawdown=r.get("max_drawdown", 0.0),
        )
    return out


def fetch_vault_summary_history(
    session: Session,
    chain_vault_id: int,
    market_data_url: str = GRVT_MARKET_DATA_URL,
    timeout: float = 30.0,
) -> pd.DataFrame:
    """Fetch share price history for a vault from the market data API.

    Uses ``/full/v1/vault_summary_history``. No authentication required.

    Returns a DataFrame with daily share prices, resampled from the
    ~8-hourly intervals provided by the API.

    :param session:
        HTTP session.
    :param chain_vault_id:
        Numeric chain vault ID.
    :param market_data_url:
        Market data API base URL.
    :param timeout:
        HTTP request timeout.
    :return:
        DataFrame indexed by date with ``share_price`` and
        ``daily_return`` columns. Empty if no data.
    """
    url = f"{market_data_url}/full/v1/vault_summary_history"
    payload = {"vault_id": str(chain_vault_id)}

    resp = session.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    results = resp.json().get("result", [])

    if len(results) < 2:
        return pd.DataFrame(columns=["share_price", "daily_return"])

    records = []
    for entry in results:
        # event_time is in nanoseconds from epoch
        ts = pd.Timestamp(int(entry["event_time"]), unit="ns")
        records.append(
            {
                "timestamp": ts,
                "share_price": float(entry["share_price"]),
            }
        )

    df = pd.DataFrame(records).set_index("timestamp").sort_index()

    # Resample to daily, taking the last share price of each day
    daily = df.resample("D").last().dropna()

    if daily.empty or len(daily) < 2:
        return pd.DataFrame(columns=["share_price", "daily_return"])

    daily["daily_return"] = daily["share_price"].pct_change().fillna(0.0)

    return daily
