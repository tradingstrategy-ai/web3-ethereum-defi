"""Hyperliquid equity curve reconstruction from local DuckDB data.

Reconstructs PnL curves, account value time series, and (for vaults)
share price equity curves from locally stored fills, funding payments,
and ledger events. Provides a more accurate alternative to the daily
metrics pipeline which suffers from resolution artefacts.

All data is read from the local trade history DuckDB — no API calls are made.

Example::

    from pathlib import Path
    from eth_defi.hyperliquid.trade_history_db import HyperliquidTradeHistoryDatabase
    from eth_defi.hyperliquid.equity_curve_reconstruction import (
        reconstruct_equity_curve,
        create_equity_curve_figure,
    )

    db = HyperliquidTradeHistoryDatabase(Path("trade-history.duckdb"))
    data = reconstruct_equity_curve(db, "0x15be61aef0ea4e4dc93c79b668f26b3f1be75a66")
    if data is not None:
        fig = create_equity_curve_figure(data)
        fig.show()
    db.close()
"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal

import pandas as pd
from eth_typing import HexAddress
from plotly.subplots import make_subplots

import plotly.graph_objects as go

from eth_defi.hyperliquid.position import Fill
from eth_defi.hyperliquid.trade_history import (
    FundingPayment,
    compute_event_share_prices,
    create_share_price_dataframe,
)
from eth_defi.hyperliquid.trade_history_db import (
    HyperliquidTradeHistoryDatabase,
    LedgerEvent,
)

logger = logging.getLogger(__name__)

#: Ledger event types that represent inflows (deposits)
DEPOSIT_EVENT_TYPES = {"deposit", "vaultDeposit", "vaultCreate"}

#: Ledger event types that represent outflows (withdrawals)
WITHDRAW_EVENT_TYPES = {"withdraw", "vaultWithdraw"}

#: Map raw DB event_type strings to the values expected by compute_event_share_prices
_VAULT_EVENT_TYPE_MAP = {
    "vaultDeposit": "vault_deposit",
    "vaultWithdraw": "vault_withdraw",
    "vaultCreate": "vault_create",
    "vaultDistribution": "vault_distribution",
    "vaultLeaderCommission": "vault_leader_commission",
}


@dataclass(slots=True)
class EquityCurveData:
    """Container for reconstructed equity curve data.

    Holds all DataFrames needed for visualisation of an account's
    performance history.
    """

    #: Account address
    address: str

    #: Human-readable label (from accounts table), or None
    label: str | None

    #: Whether this address is a vault
    is_vault: bool

    #: Cumulative PnL DataFrame indexed by timestamp.
    #: Columns: ``closed_pnl``, ``funding_pnl``, ``fee``,
    #: ``cumulative_closed_pnl``, ``cumulative_funding_pnl``,
    #: ``cumulative_fees``, ``cumulative_net_pnl``.
    pnl_curve: pd.DataFrame

    #: Account value DataFrame indexed by timestamp.
    #: Columns: ``flow``, ``cumulative_deposits``,
    #: ``cumulative_withdrawals``, ``net_deposits``,
    #: ``cumulative_net_pnl``, ``account_value``.
    account_value_curve: pd.DataFrame

    #: Vault share price DataFrame indexed by timestamp (vaults only).
    #: Columns from :py:func:`create_share_price_dataframe`:
    #: ``total_assets``, ``total_supply``, ``share_price``,
    #: ``event_type``, ``delta``, ``epoch_reset``.
    share_price_curve: pd.DataFrame | None

    #: Number of fills used in reconstruction
    fill_count: int

    #: Number of funding payments used
    funding_count: int

    #: Number of ledger events used
    ledger_count: int


def reconstruct_pnl_curve(
    fills: list[Fill],
    funding: list[FundingPayment],
) -> pd.DataFrame:
    """Reconstruct cumulative PnL time series from fills and funding payments.

    Merges closed PnL from fills (with fees separated) and funding
    payments into a single time-ordered cumulative curve using Pandas
    vectorised operations.

    :param fills:
        Fills sorted by timestamp ascending.
    :param funding:
        Funding payments sorted by timestamp ascending.
    :return:
        DataFrame indexed by timestamp with columns:
        ``closed_pnl``, ``funding_pnl``, ``fee``,
        ``cumulative_closed_pnl``, ``cumulative_funding_pnl``,
        ``cumulative_fees``, ``cumulative_net_pnl``.
        Empty DataFrame if no fills and no funding.
    """
    frames = []

    if fills:
        fills_df = pd.DataFrame(
            {
                "timestamp": [f.timestamp for f in fills],
                "closed_pnl": [float(f.closed_pnl) for f in fills],
                "funding_pnl": 0.0,
                "fee": [float(f.fee) for f in fills],
            }
        )
        frames.append(fills_df)

    if funding:
        funding_df = pd.DataFrame(
            {
                "timestamp": [f.timestamp for f in funding],
                "closed_pnl": 0.0,
                "funding_pnl": [float(f.usdc) for f in funding],
                "fee": 0.0,
            }
        )
        frames.append(funding_df)

    if not frames:
        return pd.DataFrame(
            columns=[
                "closed_pnl",
                "funding_pnl",
                "fee",
                "cumulative_closed_pnl",
                "cumulative_funding_pnl",
                "cumulative_fees",
                "cumulative_net_pnl",
            ]
        )

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values("timestamp").reset_index(drop=True)
    combined.index = pd.DatetimeIndex(combined["timestamp"], name="timestamp")
    combined = combined.drop(columns=["timestamp"])

    combined["cumulative_closed_pnl"] = combined["closed_pnl"].cumsum()
    combined["cumulative_funding_pnl"] = combined["funding_pnl"].cumsum()
    combined["cumulative_fees"] = combined["fee"].cumsum()
    combined["cumulative_net_pnl"] = combined["cumulative_closed_pnl"] + combined["cumulative_funding_pnl"] - combined["cumulative_fees"]

    return combined


def reconstruct_account_value_curve(
    ledger_events: list[LedgerEvent],
    pnl_curve: pd.DataFrame,
) -> pd.DataFrame:
    """Reconstruct account value over time from ledger events and PnL curve.

    Account value at any point equals cumulative net deposits plus
    cumulative net PnL. Deposits and withdrawals come from the ledger
    table. The PnL component comes from the previously reconstructed
    PnL curve.

    :param ledger_events:
        Ledger events sorted by timestamp ascending.
    :param pnl_curve:
        PnL curve from :py:func:`reconstruct_pnl_curve`.
    :return:
        DataFrame indexed by timestamp with columns:
        ``flow``, ``cumulative_deposits``, ``cumulative_withdrawals``,
        ``net_deposits``, ``cumulative_net_pnl``, ``account_value``.
        Empty DataFrame if no deposit/withdrawal events in ledger.
    """
    empty_columns = [
        "flow",
        "cumulative_deposits",
        "cumulative_withdrawals",
        "net_deposits",
        "cumulative_net_pnl",
        "account_value",
    ]

    # Filter to deposit/withdrawal events only
    flow_events = [e for e in ledger_events if e.event_type in DEPOSIT_EVENT_TYPES or e.event_type in WITHDRAW_EVENT_TYPES]

    if not flow_events:
        return pd.DataFrame(columns=empty_columns)

    ledger_df = pd.DataFrame(
        {
            "timestamp": [e.timestamp for e in flow_events],
            "flow": [abs(e.usdc) if e.event_type in DEPOSIT_EVENT_TYPES else -abs(e.usdc) for e in flow_events],
        }
    )
    ledger_df = ledger_df.sort_values("timestamp").reset_index(drop=True)
    ledger_df.index = pd.DatetimeIndex(ledger_df["timestamp"], name="timestamp")
    ledger_df = ledger_df.drop(columns=["timestamp"])

    # Compute cumulative deposit and withdrawal totals
    ledger_df["cumulative_deposits"] = ledger_df["flow"].clip(lower=0).cumsum()
    ledger_df["cumulative_withdrawals"] = (-ledger_df["flow"].clip(upper=0)).cumsum()
    ledger_df["net_deposits"] = ledger_df["flow"].cumsum()

    # Merge with PnL curve using merge_asof for time alignment
    if pnl_curve.empty:
        ledger_df["cumulative_net_pnl"] = 0.0
    else:
        pnl_for_merge = pnl_curve[["cumulative_net_pnl"]].reset_index()
        pnl_for_merge = pnl_for_merge.rename(columns={"timestamp": "ts"})
        ledger_for_merge = ledger_df.reset_index()
        ledger_for_merge = ledger_for_merge.rename(columns={"timestamp": "ts"})

        merged = pd.merge_asof(
            ledger_for_merge.sort_values("ts"),
            pnl_for_merge.sort_values("ts"),
            on="ts",
            direction="backward",
        )
        merged = merged.fillna({"cumulative_net_pnl": 0.0})
        merged.index = pd.DatetimeIndex(merged["ts"], name="timestamp")
        ledger_df = merged.drop(columns=["ts"])

    ledger_df["account_value"] = ledger_df["net_deposits"] + ledger_df["cumulative_net_pnl"]

    return ledger_df[empty_columns]


def _convert_ledger_for_share_price(ledger_events: list[LedgerEvent]) -> list:
    """Convert DB LedgerEvent objects to VaultDepositEvent-compatible objects.

    Maps raw event_type strings from the DB to :py:class:`VaultEventType`
    enum values expected by :py:func:`compute_event_share_prices`.

    :param ledger_events:
        Ledger events from the trade history DB.
    :return:
        List of objects with ``event_type``, ``timestamp``, and ``usdc``
        attributes compatible with ``compute_event_share_prices``.
    """
    from eth_defi.hyperliquid.deposit import VaultDepositEvent, VaultEventType

    result = []
    for event in ledger_events:
        mapped_type = _VAULT_EVENT_TYPE_MAP.get(event.event_type)
        if mapped_type is None:
            continue

        try:
            vault_event_type = VaultEventType(mapped_type)
        except ValueError:
            continue

        usdc = Decimal(str(event.usdc))
        if vault_event_type == VaultEventType.vault_withdraw:
            usdc = -abs(usdc)

        result.append(
            VaultDepositEvent(
                event_type=vault_event_type,
                vault_address=event.vault or "",
                user_address=None,
                usdc=usdc,
                timestamp=event.timestamp,
                hash=None,
            )
        )

    return result


def reconstruct_vault_share_price(
    fills: list[Fill],
    funding: list[FundingPayment],
    ledger_events: list[LedgerEvent],
) -> pd.DataFrame:
    """Reconstruct event-accurate share price for a vault.

    Reuses :py:func:`~eth_defi.hyperliquid.trade_history.compute_event_share_prices`
    which implements ERC-4626-style share price mechanics from actual
    event data (fills, funding, deposits/withdrawals).

    :param fills:
        Fills sorted chronologically.
    :param funding:
        Funding payments sorted chronologically.
    :param ledger_events:
        Ledger events from the trade history DB.
    :return:
        DataFrame from :py:func:`~eth_defi.hyperliquid.trade_history.create_share_price_dataframe`
        with columns: ``total_assets``, ``total_supply``, ``share_price``,
        ``event_type``, ``delta``, ``epoch_reset``.
        Empty DataFrame if no events.
    """
    converted_ledger = _convert_ledger_for_share_price(ledger_events)

    sp_events = compute_event_share_prices(
        fills=fills,
        funding_payments=funding,
        ledger_events=converted_ledger,
    )

    if not sp_events:
        return pd.DataFrame()

    return create_share_price_dataframe(sp_events)


def reconstruct_equity_curve(
    trade_history_db: HyperliquidTradeHistoryDatabase,
    address: HexAddress,
) -> EquityCurveData | None:
    """Reconstruct complete equity curve data for a Hyperliquid account.

    Reads all data from the local trade history DuckDB. No API calls
    are made. For vault addresses, additionally computes event-accurate
    share prices.

    :param trade_history_db:
        Trade history database with fills, funding, and ledger data.
    :param address:
        Hyperliquid address (vault or trader).
    :return:
        Reconstructed equity curve data, or ``None`` if the address
        is not found in the database.
    """
    address = address.lower()

    # Look up account
    accounts = trade_history_db.get_accounts()
    account = next((a for a in accounts if a["address"] == address), None)
    if account is None:
        return None

    is_vault = account["is_vault"]
    label = account["label"]

    logger.info("Reconstructing equity curve for %s (%s, vault=%s)", address, label or "unlabelled", is_vault)

    # Fetch data
    fills = trade_history_db.get_fills(address)
    funding = trade_history_db.get_funding(address)
    ledger = trade_history_db.get_ledger(address)

    logger.info("Data: %d fills, %d funding, %d ledger", len(fills), len(funding), len(ledger))

    # Reconstruct curves
    pnl_curve = reconstruct_pnl_curve(fills, funding)
    account_value_curve = reconstruct_account_value_curve(ledger, pnl_curve)

    share_price_curve = None
    if is_vault:
        share_price_curve = reconstruct_vault_share_price(fills, funding, ledger)
        if share_price_curve.empty:
            share_price_curve = None

    return EquityCurveData(
        address=address,
        label=label,
        is_vault=is_vault,
        pnl_curve=pnl_curve,
        account_value_curve=account_value_curve,
        share_price_curve=share_price_curve,
        fill_count=len(fills),
        funding_count=len(funding),
        ledger_count=len(ledger),
    )


def _build_monthly_pnl_heatmap_data(pnl_curve: pd.DataFrame) -> pd.DataFrame | None:
    """Aggregate PnL curve into monthly buckets for heatmap display.

    Groups the per-event PnL data by calendar month and computes:

    - ``trades``: number of fill/funding events in the month
    - ``net_pnl``: sum of ``closed_pnl + funding_pnl - fee`` for the month

    Returns a pivoted DataFrame with years as rows, month names as columns,
    and net PnL as values — ready for ``go.Heatmap``.

    :param pnl_curve:
        PnL curve from :py:func:`reconstruct_pnl_curve`.
    :return:
        Tuple of (pnl_pivot, trades_pivot) DataFrames, or ``None`` if
        the PnL curve is empty.
    """
    if pnl_curve.empty:
        return None

    monthly = pnl_curve.copy()
    monthly["net_pnl"] = monthly["closed_pnl"] + monthly["funding_pnl"] - monthly["fee"]
    monthly["year"] = monthly.index.year
    monthly["month"] = monthly.index.month

    agg = (
        monthly.groupby(["year", "month"])
        .agg(
            trades=("net_pnl", "count"),
            net_pnl=("net_pnl", "sum"),
        )
        .reset_index()
    )

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    # Build full year x month grid with NaN for missing months
    years = sorted(agg["year"].unique())
    pnl_grid = []
    trades_grid = []
    for year in years:
        pnl_row = []
        trades_row = []
        for m in range(1, 13):
            match = agg[(agg["year"] == year) & (agg["month"] == m)]
            if len(match) == 1:
                pnl_row.append(match.iloc[0]["net_pnl"])
                trades_row.append(int(match.iloc[0]["trades"]))
            else:
                pnl_row.append(None)
                trades_row.append(None)
        pnl_grid.append(pnl_row)
        trades_grid.append(trades_row)

    pnl_pivot = pd.DataFrame(pnl_grid, index=[str(y) for y in years], columns=month_names)
    trades_pivot = pd.DataFrame(trades_grid, index=[str(y) for y in years], columns=month_names)

    return pnl_pivot, trades_pivot


def create_equity_curve_figure(data: EquityCurveData) -> go.Figure:
    """Create a Plotly figure with equity curve subplots.

    Creates a multi-subplot figure showing:

    - Account value over time (from ledger deposits/withdrawals + PnL)
    - Cumulative PnL over time with separate traces for closed PnL,
      funding PnL, fees, and net PnL
    - Monthly PnL heatmap (year x month grid, coloured by profit)

    For vaults, additionally shows:

    - Share price from event-level reconstruction
    - Total supply (share count) over time

    :param data:
        Reconstructed equity curve data from :py:func:`reconstruct_equity_curve`.
    :return:
        Plotly figure ready for display.
    """
    title_label = f" ({data.label})" if data.label else ""
    title = f"Equity curve: {data.address[:10]}...{title_label}"

    has_vault_data = data.is_vault and data.share_price_curve is not None
    heatmap_data = _build_monthly_pnl_heatmap_data(data.pnl_curve)
    has_heatmap = heatmap_data is not None

    # Calculate row count and heights
    n_rows = 2  # account value + cumulative PnL
    if has_vault_data:
        n_rows += 2  # share price + total supply
    if has_heatmap:
        n_rows += 1  # monthly heatmap

    subplot_titles = ["Account value", "Cumulative PnL"]
    if has_vault_data:
        subplot_titles.extend(["Share price", "Total supply"])
    if has_heatmap:
        subplot_titles.append("Monthly PnL heatmap")

    # Use different row heights: heatmap is shorter than line charts
    row_heights = [300] * (n_rows - (1 if has_heatmap else 0))
    if has_heatmap:
        pnl_pivot, _ = heatmap_data
        heatmap_height = max(120, 40 * len(pnl_pivot))
        row_heights.append(heatmap_height)

    total_height = sum(row_heights)

    fig = make_subplots(
        rows=n_rows,
        cols=1,
        shared_xaxes=False,
        subplot_titles=subplot_titles,
        vertical_spacing=0.06,
        row_heights=row_heights,
    )

    # Row 1: Account value
    if not data.account_value_curve.empty:
        fig.add_trace(
            go.Scatter(
                x=data.account_value_curve.index,
                y=data.account_value_curve["account_value"],
                name="Account value",
                line=dict(color="royalblue"),
            ),
            row=1,
            col=1,
        )
        fig.update_yaxes(title_text="USD", row=1, col=1)
    elif has_vault_data:
        # Use total_assets from share price curve as account value
        fig.add_trace(
            go.Scatter(
                x=data.share_price_curve.index,
                y=data.share_price_curve["total_assets"],
                name="Total assets",
                line=dict(color="royalblue"),
            ),
            row=1,
            col=1,
        )
        fig.update_yaxes(title_text="USD", row=1, col=1)

    # Row 2: Cumulative PnL breakdown
    if not data.pnl_curve.empty:
        fig.add_trace(
            go.Scatter(
                x=data.pnl_curve.index,
                y=data.pnl_curve["cumulative_net_pnl"],
                name="Net PnL",
                line=dict(color="green", width=2),
            ),
            row=2,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=data.pnl_curve.index,
                y=data.pnl_curve["cumulative_closed_pnl"],
                name="Closed PnL",
                line=dict(color="dodgerblue", dash="dot"),
            ),
            row=2,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=data.pnl_curve.index,
                y=data.pnl_curve["cumulative_funding_pnl"],
                name="Funding PnL",
                line=dict(color="orange", dash="dot"),
            ),
            row=2,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=data.pnl_curve.index,
                y=-data.pnl_curve["cumulative_fees"],
                name="Fees (cost)",
                line=dict(color="red", dash="dot"),
            ),
            row=2,
            col=1,
        )
        fig.update_yaxes(title_text="USD", row=2, col=1)

    current_row = 3

    # Vault-specific rows
    if has_vault_data:
        sp = data.share_price_curve

        # Share price
        fig.add_trace(
            go.Scatter(
                x=sp.index,
                y=sp["share_price"],
                name="Share price",
                line=dict(color="purple"),
            ),
            row=current_row,
            col=1,
        )
        fig.update_yaxes(title_text="Price", row=current_row, col=1)
        current_row += 1

        # Total supply
        fig.add_trace(
            go.Scatter(
                x=sp.index,
                y=sp["total_supply"],
                name="Total supply",
                line=dict(color="teal"),
                fill="tozeroy",
            ),
            row=current_row,
            col=1,
        )
        fig.update_yaxes(title_text="Shares", row=current_row, col=1)
        current_row += 1

    # Monthly PnL heatmap
    if has_heatmap:
        pnl_pivot, trades_pivot = heatmap_data

        # Build hover and annotation text: "N trades\n$X,XXX.XX"
        hover_text = []
        annotation_text = []
        for i in range(len(pnl_pivot)):
            hover_row = []
            anno_row = []
            for j in range(len(pnl_pivot.columns)):
                pnl_val = pnl_pivot.iloc[i, j]
                trades_val = trades_pivot.iloc[i, j]
                if pd.notna(pnl_val) and pd.notna(trades_val):
                    cell = f"{int(trades_val):,} trades<br>${pnl_val:,.0f}"
                    hover_row.append(cell)
                    anno_row.append(cell)
                else:
                    hover_row.append("")
                    anno_row.append("")
            hover_text.append(hover_row)
            annotation_text.append(anno_row)

        # Find abs max for symmetric colour scale
        flat_vals = [v for row in pnl_pivot.values for v in row if v is not None]
        abs_max = max(abs(v) for v in flat_vals) if flat_vals else 1.0

        fig.add_trace(
            go.Heatmap(
                z=pnl_pivot.values,
                x=pnl_pivot.columns.tolist(),
                y=pnl_pivot.index.tolist(),
                text=annotation_text,
                texttemplate="%{text}",
                hovertext=hover_text,
                hovertemplate="%{y} %{x}<br>%{hovertext}<extra></extra>",
                colorscale="RdYlGn",
                zmid=0,
                zmin=-abs_max,
                zmax=abs_max,
                colorbar=dict(title="Net PnL ($)", len=0.3, y=0.0, yanchor="bottom"),
                showscale=True,
            ),
            row=current_row,
            col=1,
        )
        fig.update_yaxes(autorange="reversed", row=current_row, col=1)
        fig.update_xaxes(side="bottom", row=current_row, col=1)

    fig.update_layout(
        title=title,
        height=total_height,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    return fig
