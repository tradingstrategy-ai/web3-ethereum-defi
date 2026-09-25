"""Vault rankings and tables for the monthly vault report.

Each report section is a ranked subset of the vault metrics loaded by
:py:func:`eth_defi.vault_report.data.fetch_vault_report_data`. The selection
criteria follow the ``erc-4626-vault-report-markdown.ipynb`` notebook used
for the 2025-2026 blog posts, with the following changes:

- Perpetual DEX native trading vaults (Hyperliquid, GRVT, Lighter, ...) are
  ranked in their own section, because their volatile trading returns crowd
  out stablecoin yield vaults in the main listing.
- Vaults whose latest metrics are more than a week older than the report
  data date are excluded.

All rankings use annualised one-month returns, net of fees when fee data is
known and gross otherwise, marked with ``(n)`` and ``(g)`` respectively.
"""

import datetime
import html
import logging
from dataclasses import dataclass, field

import pandas as pd

from eth_defi.research.vault_metrics import USDollarAmount, _get_trading_strategy_chain_link, _get_trading_strategy_protocol_link
from eth_defi.types import Percent

logger = logging.getLogger(__name__)

#: Protocol slug for vaults whose protocol we could not identify
UNKNOWN_PROTOCOL_SLUG = "protocol-not-yet-identified"

#: Technical risk label for vaults excluded from all listings
BLACKLISTED_RISK = "Blacklisted"

#: Sharpe ratios above this are displayed as ``>100``
MAX_DISPLAYED_SHARPE = 100

#: The top vaults export caps annualised returns at 10,000%; values at the cap are displayed as ``>9,999%``
CAPPED_ANNUALISED_RETURN = 99.99

#: Risk badge colours as (background, text), for the light blog page.
#: Follows the status palette: good, warning, serious and critical, plus grey for unrated vaults.
RISK_BADGE_COLOURS = {
    "Negligible": ("#dff3df", "#0a6e0a"),
    "Low": ("#dff3df", "#0a6e0a"),
    "High": ("#fdf0d3", "#7a5100"),
    "Dangerous": ("#fbe2d6", "#933a15"),
    "Severe": ("#f8dada", "#9e2424"),
}

#: Badge colours for vaults without a technical risk rating
UNRATED_BADGE_COLOURS = ("#ecebe8", "#52514e")

#: Link target of the risk badges
RISK_FRAMEWORK_URL = "https://tradingstrategy.ai/blog/announcing-vault-technical-risk-framework-beta"

#: Public vault sparkline images, rendered by :py:mod:`eth_defi.research.sparkline_export`
#: PNG rather than SVG, because email clients such as Gmail strip SVG images from newsletters
SPARKLINE_URL = "https://vault-sparklines.tradingstrategy.ai/sparkline-90d-{vault_id}.png"


@dataclass(slots=True)
class ReportCriteria:
    """Selection thresholds for the report sections.

    The listing thresholds are the same as in the February 2026 blog post.
    """

    #: Minimum current TVL for the main best-performing vaults listing
    min_tvl: USDollarAmount = 200_000

    #: Minimum lifetime deposit and redemption events, filters out inactive vaults
    min_events: int = 10

    #: Number of vaults in the main, large and new vault tables
    top_n: int = 50

    #: Exclude vaults whose latest metrics are older than this, relative to the report data date
    max_data_age: datetime.timedelta = datetime.timedelta(days=7)

    #: Minimum TVL for the large vaults table
    large_min_tvl: USDollarAmount = 2_000_000

    #: Minimum TVL for the per-chain table
    chain_min_tvl: USDollarAmount = 100_000

    #: Vaults listed per chain
    chain_top_n: int = 3

    #: Minimum TVL for the perpetual DEX trading vaults table
    perp_dex_min_tvl: USDollarAmount = 200_000

    #: Number of perpetual DEX trading vaults listed
    perp_dex_top_n: int = 20

    #: Minimum TVL for the new vaults table
    new_vault_min_tvl: USDollarAmount = 15_000

    #: Maximum age of a vault in the new vaults table
    new_vault_max_age: datetime.timedelta = datetime.timedelta(days=60)

    #: Maximum annualised three-month volatility for the low-volatility chart
    low_volatility_threshold: Percent = 0.005

    #: Leave vaults above this annualised one-month return out of the hero image,
    #: where one outlier bar or number would dwarf the others
    chart_max_return: Percent = 4.0

    #: Vaults at or above this annualised three-month volatility are compared with BTC and ETH instead of the Treasury bill
    crypto_benchmark_min_volatility: Percent = 0.25

    #: Vaults with a three-month drawdown at or below this are compared with BTC and ETH instead of the Treasury bill
    crypto_benchmark_max_drawdown: Percent = -0.10

    #: Number of vaults in each performance chart grid
    performance_chart_vaults: int = 8

    #: Clip individual vault returns in the chain yield dot plot at this annualised return
    chain_yield_chart_max_return: Percent = 0.4

    #: Technical risk ratings left out of the hero image, which promotes vaults on social media
    hero_excluded_risks: tuple[str, ...] = ("Dangerous", "Severe")

    #: Leave vaults above this annualised three-month volatility out of the hero image
    hero_max_volatility: Percent = 0.5

    #: Clip the risk and return scatter's y axis at this annualised return; vaults above are drawn on the top edge
    scatter_max_return: Percent = 1.0

    #: Minimum TVL for the correlation matrix vaults
    correlation_min_tvl: USDollarAmount = 50_000

    #: Maximum vaults per protocol in the correlation matrix
    correlation_per_protocol: int = 2

    #: Number of vaults in the correlation matrix
    correlation_vault_count: int = 20

    #: Minimum TVL per vault to be counted in the average yield per chain chart
    chain_yield_min_vault_tvl: USDollarAmount = 10_000

    #: Minimum total TVL of a chain to be included in the average yield per chain chart
    chain_yield_min_chain_tvl: USDollarAmount = 1_000_000

    #: Exclude outlier vaults above this annualised one-month return from the chain averages
    chain_yield_max_return: Percent = 4.0

    #: Exclude vaults above this annualised three-month volatility from the chain averages.
    #:
    #: Keeps volatile crypto index funds and AMM liquidity positions out of the stablecoin yield figures.
    chain_yield_max_volatility: Percent = 0.5


#: Default columns for vault tables, same as in the previous blog posts
VAULT_TABLE_COLUMNS = [
    "Vault",
    "3M price",
    "1M ann.",
    "3M ann.",
    "Lifetime ann.",
    "3M Sharpe",
    "TVL USD (peak)",
    "Risk",
    "Age (y)",
    "Token",
    "Chain",
    "Protocol",
]

#: Columns for the per-chain table, chain first
CHAIN_TABLE_COLUMNS = [
    "Chain",
    "Protocol",
    "Vault",
    "1M ann.",
    "3M ann.",
    "Lifetime ann.",
    "TVL USD (peak)",
    "Risk",
    "Age (y)",
    "Token",
]

#: Columns for the correlation vault table
CORRELATION_TABLE_COLUMNS = [
    "Vault",
    "3M ann.",
    "3M Sharpe",
    "Age (y)",
    "Chain",
    "Protocol",
    "Token",
    "TVL USD (peak)",
]

#: Right-aligned numeric columns
NUMERIC_COLUMNS = {"3M Sharpe", "Age (y)"}

#: Columns whose values must not wrap onto two lines
NOWRAP_COLUMNS = {"1M ann.", "3M ann.", "Lifetime ann.", "TVL USD (peak)", "Risk"}

#: Explanation of the table cell formats, shown once above the first table
TABLE_FORMAT_NOTE = "Returns are annualised: (n) net of fees, (g) gross when fee data is not available. TVL shows the current value, with the all-time peak in brackets."


@dataclass(slots=True)
class ReportSection:
    """One ranked vault table in the report."""

    #: Vaults in display order, rows from :py:attr:`eth_defi.vault_report.data.VaultReportData.vaults_df`
    vaults_df: pd.DataFrame

    #: Table columns, see :py:data:`VAULT_TABLE_COLUMNS`
    columns: list[str] = field(default_factory=lambda: list(VAULT_TABLE_COLUMNS))

    #: Show a 1, 2, 3... rank column
    numbered: bool = True

    #: Vault ids with a published sparkline image, see :py:func:`eth_defi.vault_report.data.fetch_available_sparklines`
    sparkline_ids: frozenset[str] = frozenset()


def filter_eligible_vaults(
    vaults_df: pd.DataFrame,
    data_end_at: datetime.datetime,
    criteria: ReportCriteria,
) -> pd.DataFrame:
    """Remove vaults that must not appear in any listing.

    Drops blacklisted vaults (this includes vaults with bad flags, see
    :py:func:`eth_defi.research.vault_metrics.apply_bad_flag_check`), vaults
    without a known TVL or one-month return, and vaults with stale data.

    :param vaults_df:
        Vault metrics.

    :param data_end_at:
        The report data date.

    :param criteria:
        Report thresholds.

    :return:
        Filtered vault metrics.
    """
    stale_before = pd.Timestamp(data_end_at - criteria.max_data_age)
    mask = (vaults_df["risk"] != BLACKLISTED_RISK) & vaults_df["current_nav"].notna() & vaults_df["one_month_cagr_best"].notna() & (vaults_df["end_date"] >= stale_before)
    eligible = vaults_df.loc[mask]
    logger.info("Eligible vaults for the report: %d out of %d", len(eligible), len(vaults_df))
    return eligible


def rank_vaults(df: pd.DataFrame) -> pd.DataFrame:
    """Sort vaults by annualised one-month return, best first.

    The export caps annualised returns at 10,000%, so ties are broken with
    the absolute one-month return.

    :param df:
        Vault metrics.

    :return:
        Sorted vault metrics.
    """
    return df.sort_values(["one_month_cagr_best", "one_month_returns"], ascending=False, na_position="last")


def select_best_vaults(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Stablecoin yield vaults above the TVL and activity thresholds, best first.

    Perpetual DEX trading vaults are excluded; see :py:func:`select_perp_dex_vaults`.

    :param eligible_df:
        Output of :py:func:`filter_eligible_vaults`.

    :param criteria:
        Report thresholds.

    :return:
        All matching vaults ranked by one-month annualised return, not truncated.
    """
    df = eligible_df
    mask = ~df["is_perp_dex"] & (df["current_nav"] >= criteria.min_tvl) & (df["event_count"] >= criteria.min_events)
    return rank_vaults(df.loc[mask])


def select_perp_dex_vaults(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Perpetual DEX native trading vaults, best first.

    Perp DEX vaults record far fewer deposit and redemption events than
    ERC-4626 vaults, so the event threshold is not applied.

    :param eligible_df:
        Output of :py:func:`filter_eligible_vaults`.

    :param criteria:
        Report thresholds.

    :return:
        Top perpetual DEX vaults.
    """
    df = eligible_df
    mask = df["is_perp_dex"] & (df["current_nav"] >= criteria.perp_dex_min_tvl)
    return rank_vaults(df.loc[mask]).head(criteria.perp_dex_top_n)


def select_new_vaults(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Best stablecoin yield vaults launched recently.

    :param eligible_df:
        Output of :py:func:`filter_eligible_vaults`.

    :param criteria:
        Report thresholds.

    :return:
        Top new vaults.
    """
    df = eligible_df
    max_age_years = criteria.new_vault_max_age / datetime.timedelta(days=365)
    mask = ~df["is_perp_dex"] & (df["current_nav"] >= criteria.new_vault_min_tvl) & (df["event_count"] >= criteria.min_events) & (df["years"] <= max_age_years)
    return rank_vaults(df.loc[mask]).head(criteria.top_n)


def select_vaults_by_chain(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Best vaults on each chain, including perpetual DEX chains.

    :param eligible_df:
        Output of :py:func:`filter_eligible_vaults`.

    :param criteria:
        Report thresholds.

    :return:
        Vaults sorted by chain name, then by one-month return.
    """
    df = rank_vaults(eligible_df.loc[eligible_df["current_nav"] >= criteria.chain_min_tvl])
    top = df.groupby("chain", sort=False).head(criteria.chain_top_n)
    return top.sort_values(["chain", "one_month_cagr_best"], ascending=[True, False])


def select_chain_yield_vaults(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Select the vaults counted in the chain yield averages.

    Leaves out small vaults, outliers above :py:attr:`ReportCriteria.chain_yield_max_return`
    and volatile vaults above :py:attr:`ReportCriteria.chain_yield_max_volatility`.

    :param eligible_df:
        Output of :py:func:`filter_eligible_vaults`.

    :param criteria:
        Report thresholds.

    :return:
        Selected vaults.
    """
    df = eligible_df
    mask = (df["current_nav"] >= criteria.chain_yield_min_vault_tvl) & (df["one_month_cagr_best"] <= criteria.chain_yield_max_return) & (df["three_months_volatility"] <= criteria.chain_yield_max_volatility)
    return df.loc[mask]


def calculate_chain_yields(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Calculate TVL-weighted average one-month yield per chain.

    Automates the "average vault yield per blockchain" figure, previously a
    screenshot of the `chain overview <https://tradingstrategy.ai/trading-view/vaults/chains>`__.
    Outlier vaults above :py:attr:`ReportCriteria.chain_yield_max_return` are
    excluded, as a single broken share price would dominate a small chain.
    Volatile vaults above :py:attr:`ReportCriteria.chain_yield_max_volatility`,
    like tokenised crypto index funds, are not stablecoin yield and are excluded too.
    Perpetual DEX native vaults are included, so chains like Hypercore and
    Lighter appear in the chart, as on the website chain overview.

    :param eligible_df:
        Output of :py:func:`filter_eligible_vaults`.

    :param criteria:
        Report thresholds.

    :return:
        DataFrame indexed by chain name with columns ``tvl`` (USD), ``avg_return``
        (TVL-weighted annualised one-month return, 0.05 = 5%) and ``vault_count``,
        sorted by ``avg_return`` descending.
    """
    df = select_chain_yield_vaults(eligible_df, criteria)
    grouped = df.assign(weighted=df["current_nav"] * df["one_month_cagr_best"]).groupby("chain").agg(tvl=("current_nav", "sum"), weighted=("weighted", "sum"), vault_count=("current_nav", "size"))
    grouped["avg_return"] = grouped["weighted"] / grouped["tvl"]
    grouped = grouped.loc[grouped["tvl"] >= criteria.chain_yield_min_chain_tvl, ["tvl", "avg_return", "vault_count"]]
    return grouped.sort_values("avg_return", ascending=False)


def format_return(net: Percent | None, gross: Percent | None) -> str:
    """Format an annualised return, preferring the net value.

    :param net:
        Net return, 0.01 = 1%, or ``None`` when fees are unknown.

    :param gross:
        Gross return.

    :return:
        E.g. ``12.3% (n)``, ``8.1% (g)``, ``>9,999% (n)`` for capped values, or ``---``.
    """

    def _format(value: Percent, marker: str) -> str:
        return f">9,999% ({marker})" if value >= CAPPED_ANNUALISED_RETURN else f"{value:,.1%} ({marker})"

    if pd.notna(net):
        return _format(net, "n")
    if pd.notna(gross):
        return _format(gross, "g")
    return "---"


def format_sharpe(value: float | None) -> str:
    """Format a Sharpe ratio.

    Near-zero volatility lending vaults can have Sharpe ratios in the
    millions, which are displayed as ``>100``.

    :param value:
        Sharpe ratio.

    :return:
        Formatted value.
    """
    if pd.isna(value):
        return "---"
    if value > MAX_DISPLAYED_SHARPE:
        return f">{MAX_DISPLAYED_SHARPE}"
    return f"{value:.2f}"


def format_tvl(current: USDollarAmount | None, peak: USDollarAmount | None) -> str:
    """Format current and peak TVL.

    :param current:
        Current TVL.

    :param peak:
        Peak TVL.

    :return:
        E.g. ``1,234,567 (2,000,000)``.
    """
    if pd.isna(current):
        return "---"
    if pd.isna(peak):
        return f"{current:,.0f}"
    return f"{current:,.0f} ({peak:,.0f})"


def format_risk_badge(risk: str | None) -> str:
    """Render a technical risk rating as a coloured pill.

    The label carries the meaning, so colour is never the only signal.
    Unrated vaults, about a third of all vaults, get muted plain text instead
    of a pill, so the pills stand out.

    :param risk:
        Risk label from the top vaults export, or ``None``.

    :return:
        HTML ``<a>`` pill linking to the risk framework, or muted text.
    """
    if not (isinstance(risk, str) and risk):
        return f'<span style="color:{UNRATED_BADGE_COLOURS[1]};font-size:12px">Unrated</span>'
    label = risk
    background, text = RISK_BADGE_COLOURS.get(label, UNRATED_BADGE_COLOURS)
    style = f"display:inline-block;padding:1px 9px;border-radius:999px;background:{background};color:{text};font-size:12px;font-weight:600;text-decoration:none;white-space:nowrap"
    return f'<a href="{RISK_FRAMEWORK_URL}" style="{style}">{html.escape(label)}</a>'


def format_vault_cells(row: pd.Series, sparkline_ids: frozenset[str] = frozenset()) -> dict[str, str]:
    """Format one vault as HTML table cells.

    :param row:
        Vault metrics row.

    :param sparkline_ids:
        Vault ids with a published sparkline; other vaults get an empty sparkline cell.

    :return:
        Column label -> escaped cell HTML, for all columns in :py:data:`VAULT_TABLE_COLUMNS`.
    """

    def _link(text: str, url: str | None) -> str:
        content = html.escape(text)
        return f'<a href="{html.escape(url)}">{content}</a>' if url else content

    vault_id = row["id"]
    sparkline = f'<img src="{SPARKLINE_URL.format(vault_id=vault_id)}" width="72" height="18" alt="" style="width:72px;max-width:none;height:18px;vertical-align:middle">' if vault_id in sparkline_ids else ""
    known_protocol = row["protocol_slug"] != UNKNOWN_PROTOCOL_SLUG
    return {
        "Vault": _link(row["name"] or row["address"], row["trading_strategy_link"]),
        "3M price": sparkline,
        "1M ann.": html.escape(format_return(row["one_month_cagr_net"], row["one_month_cagr"])),
        "3M ann.": html.escape(format_return(row["three_months_cagr_net"], row["three_months_cagr"])),
        "Lifetime ann.": html.escape(format_return(row["cagr_net"], row["cagr"])),
        "3M Sharpe": html.escape(format_sharpe(row["three_months_sharpe_best"])),
        "TVL USD (peak)": html.escape(format_tvl(row["current_nav"], row["peak_nav"])),
        "Risk": format_risk_badge(row["risk"]),
        "Age (y)": f"{row['years']:.2f}" if pd.notna(row["years"]) else "---",
        "Token": html.escape(row["denomination"] or ""),
        "Chain": _link(row["chain"], _get_trading_strategy_chain_link(row["chain"])),
        "Protocol": _link(row["protocol"], _get_trading_strategy_protocol_link(row["protocol_slug"])) if known_protocol else "",
    }


def render_section_table(section: ReportSection) -> str:
    """Render a report section as a HTML table for the blog.

    The table markup matches the tables in the previous blog posts, so the
    blog styles them the same way. The blog frontend (``BlogPostContent.svelte``)
    wraps post tables in a horizontally scrollable container on narrow screens.

    :param section:
        Report section.

    :return:
        HTML ``<table>`` string.
    """
    columns = section.columns

    def _align(column: str) -> str:
        return "right" if column in NUMERIC_COLUMNS else "left"

    def _style(column: str) -> str:
        return f"text-align:{_align(column)}" + (";white-space:nowrap" if column in NOWRAP_COLUMNS else "")

    header = "".join(f'<th style="text-align:{_align(c)}">{html.escape(c)}</th>' for c in columns)
    if section.numbered:
        header = '<th style="text-align:right"></th>' + header

    rows = []
    for rank, (_, vault) in enumerate(section.vaults_df.iterrows(), start=1):
        cells = format_vault_cells(vault, section.sparkline_ids)
        tds = "".join(f'<td style="{_style(c)}">{cells[c]}</td>' for c in columns)
        if section.numbered:
            tds = f'<td style="text-align:right">{rank}</td>' + tds
        rows.append(f"<tr>{tds}</tr>")

    return "<table>\n<thead>\n<tr>" + header + "</tr>\n</thead>\n<tbody>\n" + "\n".join(rows) + "\n</tbody>\n</table>"


def select_tvl_history_vaults(vaults_df: pd.DataFrame) -> pd.DataFrame:
    """Select vaults whose TVL history is counted in the market TVL chart.

    Excludes blacklisted vaults, like the website's historical TVL charts, so
    the totals match the website.

    :param vaults_df:
        All vault metrics.

    :return:
        Vaults to include.
    """
    return vaults_df.loc[vaults_df["risk"] != BLACKLISTED_RISK]


def calculate_protocol_tvl_history(tvl_history: pd.DataFrame, vaults_df: pd.DataFrame, top_n: int = 7) -> pd.DataFrame:
    """Sum vault TVL history per protocol.

    :param tvl_history:
        Output of :py:func:`eth_defi.vault_report.data.read_vault_tvl_history`, one column per vault id.

    :param vaults_df:
        Vault metrics with ``protocol`` and ``protocol_slug`` columns, indexed by vault id.

    :param top_n:
        Protocols shown separately, by their latest TVL. The rest are summed as ``Other``.

    :return:
        DataFrame with one column per protocol, largest first, then ``Other``.
    """
    protocols = vaults_df["protocol"].where(vaults_df["protocol_slug"] != UNKNOWN_PROTOCOL_SLUG, "Unknown protocol")
    by_protocol = tvl_history.T.groupby(protocols.reindex(tvl_history.columns)).sum(min_count=1).T.fillna(0)
    ranked = by_protocol.iloc[-1].sort_values(ascending=False).index
    top = [protocol for protocol in ranked if protocol != "Unknown protocol"][:top_n]
    result = by_protocol[top].copy()
    result["Other"] = by_protocol.drop(columns=top).sum(axis=1)
    return result
