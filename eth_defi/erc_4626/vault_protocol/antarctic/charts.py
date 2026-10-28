"""Plot observed Antarctic subscription equity and reported pre-batch TVL.

Charts use the same canonical subscription reader as the vault-price pipeline.
They exclude redemption ratios and staking rewards. See
https://docs.antarctic.exchange/technical-framework/hybird-lp-model
"""

import logging
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from web3 import Web3

from eth_defi.erc_4626.vault_protocol.antarctic.constants import ANTARCTIC_CHAIN_ID, ANTARCTIC_DEPLOYMENTS, AntarcticDeployment
from eth_defi.erc_4626.vault_protocol.antarctic.vault import AntarcticVault
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec

logger = logging.getLogger(__name__)


def fetch_antarctic_chart_series(web3: Web3, context_path: Path, token_cache: TokenDiskCache, end_block: int) -> Iterator[tuple[AntarcticDeployment, pd.DataFrame]]:
    """Read both equity curves through the production contextual price reader.

    Only actual subscription blocks become samples. Positive event TVL supplies
    the reported pool value; bootstrap zero TVL stays unknown. The equity index
    starts at 100 at the first observed share price and excludes staking income.

    :param web3: Configured Arbitrum metadata connection.
    :param context_path: Prefetched complete settlement context.
    :param token_cache: Private chart-run token cache.
    :param end_block: Exclusive source cutoff shared by both products.
    :return: Reviewed deployment and sparse DataFrame with naive UTC
        ``timestamp``, integer ``block_number``, and float ``share_price``,
        ``total_assets`` (nullable) and ``equity_index`` columns.
    """
    for deployment in ANTARCTIC_DEPLOYMENTS:
        vault = AntarcticVault(web3, VaultSpec(ANTARCTIC_CHAIN_ID, deployment.address), token_cache=token_cache)
        vault.historical_context_path = context_path
        observations = vault.get_historical_reader(False).fetch_contextual_historical_reads(deployment.manager_deployment_block, end_block, 1)
        frame = pd.DataFrame([{"timestamp": read.timestamp, "block_number": read.block_number, "share_price": float(read.share_price), "total_assets": float(read.total_assets) if read.total_assets is not None else None} for read in observations])
        if frame.empty:
            raise ValueError(f"No subscription prices for {deployment.product.upper()}")
        frame["equity_index"] = frame["share_price"] / frame["share_price"].iloc[0] * 100
        logger.info("Prepared %s: %d observed blocks, %s to %s", deployment.product.upper(), len(frame), frame["timestamp"].iloc[0], frame["timestamp"].iloc[-1])
        yield deployment, frame


def create_antarctic_figure(frame: pd.DataFrame, product: str) -> go.Figure:
    """Build an equity and TVL chart with measured observations visibly marked.

    Straight lines connect observations for readability; no daily or hourly
    points are added and the curve stops at the final subscription event.
    Denomination is USDT rather than an assumed US dollar valuation.

    :param frame: Sparse frame from :func:`fetch_antarctic_chart_series` with
        timestamp, block number, price, nullable assets and equity index.
    :param product: AMLP or AHLP product label.
    :return: Plotly figure with equity index and reported TVL panels.
    """
    colour = "#2475df" if product.lower() == "amlp" else "#0f9979"
    figure = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.14, subplot_titles=("Equity index · initial share price = 100", "Handler-reported pre-batch TVL · USDT"))
    figure.add_trace(go.Scatter(x=frame["timestamp"], y=frame["equity_index"], mode="lines+markers", line={"color": colour, "width": 2}, marker={"size": 4}, name="Observed equity", customdata=frame[["block_number", "share_price"]], hovertemplate="%{x|%Y-%m-%d %H:%M:%S} UTC<br>Equity %{y:.4f}<br>Share price %{customdata[1]:.8f} USDT<br>Block %{customdata[0]:.0f}<extra></extra>"), row=1, col=1)
    figure.add_trace(go.Scatter(x=frame["timestamp"], y=frame["total_assets"], mode="lines+markers", line={"color": colour, "width": 2}, marker={"size": 4}, connectgaps=False, name="Reported TVL", hovertemplate="%{x|%Y-%m-%d %H:%M:%S} UTC<br>Reported TVL %{y:,.2f} USDT<extra></extra>"), row=2, col=1)
    latest = frame.iloc[-1]
    change = (latest["equity_index"] / 100 - 1) * 100
    figure.update_layout(template="plotly_white", title={"text": f"Antarctic {product.upper()}<br><sup>{len(frame):,} subscription blocks · share-price return {change:+.2f}% · last observed {latest['timestamp']:%Y-%m-%d}</sup>", "x": 0.05}, width=1200, height=860, margin={"l": 90, "r": 35, "t": 120, "b": 155}, showlegend=False, font={"family": "Arial", "size": 15}, paper_bgcolor="white", plot_bgcolor="white")
    figure.update_yaxes(title_text="Equity index", row=1, col=1)
    figure.update_yaxes(title_text="USDT", tickformat="~s", row=2, col=1)
    figure.update_xaxes(title_text="Settlement date (UTC)", row=2, col=1)
    figure.add_annotation(text="AddLiquidity only · markers are actual observations; connecting lines are illustrative<br>TVL is handler-reported, not independently calculated NAV · staking rewards excluded · bootstrap TVL unknown", xref="paper", yref="paper", x=0, y=-0.14, xanchor="left", yanchor="top", showarrow=False, align="left", font={"size": 12, "color": "#536175"})
    return figure
