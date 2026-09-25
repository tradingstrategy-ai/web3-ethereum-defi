"""Vault rankings and tables for the monthly vault report.

Each report section is a ranked subset of the vault metrics loaded by
:py:func:`eth_defi.vault_report.data.fetch_vault_report_data`. The post
structure is described in ``eth_defi/vault_report/README-blog-post-outline.md``.

Vaults are classified into four groups, each ranked in its own table:

- **Lending:** strategy tagged as lending, or a known lending protocol, see
  :py:data:`LENDING_PROTOCOL_SLUGS`
- **Perpetual futures DEX:** Hyperliquid, GRVT, Lighter and other native trading vaults
- **Tokenised funds:** vaults flagged ``tokenised_fund``, e.g. money market funds
- **Other:** everything else, e.g. yield aggregators, trading and RWA vaults

Returns are annualised one-month returns, net of fees when fee data is known
and gross otherwise, marked with ``(n)`` and ``(g)`` respectively.
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

#: Badge colours for vaults without a technical risk rating; the mid-grey text stays readable on the dark blog page and in light newsletters
UNRATED_BADGE_COLOURS = ("#ecebe8", "#8f8d88")

#: Link target of the risk badges
RISK_FRAMEWORK_URL = "https://tradingstrategy.ai/blog/announcing-vault-technical-risk-framework-beta"

#: Public vault sparkline images, rendered by :py:mod:`eth_defi.research.sparkline_export`
#: PNG rather than SVG, because email clients such as Gmail strip SVG images from newsletters
SPARKLINE_URL = "https://vault-sparklines.tradingstrategy.ai/sparkline-90d-{vault_id}.png"


#: Strategy tags that make a vault a lending vault
LENDING_STRATEGY_TAGS = frozenset({"lending", "lending_optimisation", "lending_looping", "rwa_lending"})

#: Protocols whose vaults are lending vaults even without a strategy tag.
#:
#: Strategy tags are set only by some protocol adapters (Aave, Euler, Morpho),
#: so the report also recognises the lending markets it knows. Tagging these
#: adapters would make this list unnecessary.
LENDING_PROTOCOL_SLUGS = frozenset({"aave", "morpho", "euler", "fluid", "spark", "silo-finance", "llama-lend", "curvance", "dolomite", "gearbox", "sentiment", "arcadia-finance", "term-finance", "40acres", "3jane", "frax"})

#: Vault flag for tokenised funds, such as money market and treasury funds
TOKENISED_FUND_FLAG = "tokenised_fund"

#: Protocol slugs that mean the vault's protocol has not been identified yet: generic ERC-4626 and
#: ERC-7540 vaults and placeholders. The same set as the website's ``isUnknownVaultProtocol()``
#: in ``src/lib/top-vaults/helpers.ts``.
UNIDENTIFIED_PROTOCOL_SLUGS = frozenset({UNKNOWN_PROTOCOL_SLUG, "erc-4626", "unknown", "unknown-erc-7450"})

#: Protocol names, lower case, that mean the same
UNIDENTIFIED_PROTOCOL_NAMES = frozenset({"", "unknown", "unknown vault protocol"})

#: Label of the single pile of vaults whose protocol is not identified. Charts also sum
#: their small protocols into it, so unidentified vaults never show as a protocol of their own.
OTHER_PROTOCOL = "Other"

#: Vault group labels, see :py:func:`classify_vault`
LENDING = "lending"
PERP_DEX = "perp_dex"
TOKENISED_FUND = "tokenised_fund"
OTHER = "other"


@dataclass(slots=True)
class ReportCriteria:
    """Selection thresholds for the report sections."""

    #: Minimum current TVL for the best-performing vault tables
    min_tvl: USDollarAmount = 100_000

    #: Minimum lifetime deposit and redemption events for non-perp vaults, filters out inactive vaults
    min_events: int = 10

    #: Number of vaults in each best-performing table
    top_n: int = 20

    #: Exclude vaults whose latest metrics are older than this, relative to the report data date
    max_data_age: datetime.timedelta = datetime.timedelta(days=7)

    #: Minimum TVL for the per-chain table
    chain_min_tvl: USDollarAmount = 100_000

    #: Vaults listed per chain
    chain_top_n: int = 3

    #: Minimum TVL for the new vaults table
    new_vault_min_tvl: USDollarAmount = 15_000

    #: Maximum age of a vault in the new vaults table
    new_vault_max_age: datetime.timedelta = datetime.timedelta(days=60)

    #: Number of vaults in each performance chart grid
    performance_chart_vaults: int = 8

    #: Vaults at or above this annualised three-month volatility are compared with BTC and ETH instead of the Treasury bill
    crypto_benchmark_min_volatility: Percent = 0.25

    #: Vaults with a three-month drawdown at or below this are compared with BTC and ETH instead of the Treasury bill
    crypto_benchmark_max_drawdown: Percent = -0.10

    #: Leave vaults above this annualised one-month return out of the hero image,
    #: where one outlier number would dwarf the others
    chart_max_return: Percent = 4.0

    #: Technical risk ratings left out of the hero image, which promotes vaults on social media
    hero_excluded_risks: tuple[str, ...] = ("Dangerous", "Severe")

    #: Leave vaults above this annualised three-month volatility out of the hero image
    hero_max_volatility: Percent = 0.5

    #: Clip the risk and return scatter's y axis at this annualised return; vaults above are drawn on the top edge
    scatter_max_return: Percent = 1.0

    #: Number of blockchains in the average yield chart, the largest by TVL
    yield_top_chains: int = 10

    #: Number of protocols in the average yield chart, the largest by TVL
    yield_top_protocols: int = 10

    #: Minimum TVL of a protocol in the average yield chart
    yield_min_protocol_tvl: USDollarAmount = 1_000_000

    #: Minimum TVL per vault to be counted in the average yield charts
    yield_min_vault_tvl: USDollarAmount = 10_000

    #: Exclude outlier vaults above this annualised one-month return from the average yields
    yield_max_return: Percent = 4.0

    #: Exclude vaults above this annualised three-month volatility from the average yields.
    #:
    #: Keeps volatile crypto index funds and AMM liquidity positions out of the stablecoin yield figures.
    yield_max_volatility: Percent = 0.5

    #: Clip individual vault dots in the average yield charts at this annualised return
    yield_chart_max_return: Percent = 0.4

    #: Number of largest inflows and of largest outflows in the TVL change chart
    tvl_change_top_n: int = 10


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


def select_comparable_vaults(eligible_df: pd.DataFrame) -> pd.DataFrame:
    """Select the vaults that may appear in performance comparisons.

    Vaults without an identified protocol, the :py:data:`OTHER_PROTOCOL` pile,
    are often broken or misread: generic ERC-4626 wrappers, lending pool
    receipts and tokens with unusual share accounting. They are left out of
    every table and chart that compares returns, and counted only in TVL
    summaries: the TVL by protocol chart, inflows and outflows and the report
    statistics.

    :param eligible_df:
        Output of :py:func:`filter_eligible_vaults`.

    :return:
        Eligible vaults with an identified protocol.
    """
    return eligible_df.loc[eligible_df["protocol_identified"]]


def is_identified_protocol(protocol: str | None, protocol_slug: str | None) -> bool:
    """Check whether a vault's protocol has been identified.

    Generic ERC-4626 and ERC-7540 vaults, unknown and placeholder protocols
    are not identified. The report puts them all in one :py:data:`OTHER_PROTOCOL`
    pile until their protocols are mapped, following the website's
    ``isUnknownVaultProtocol()``.

    :param protocol:
        Protocol name.

    :param protocol_slug:
        Protocol slug.

    :return:
        ``True`` for a named, mapped protocol.
    """
    name = (protocol or "").strip().lower()
    slug = (protocol_slug or "").strip().lower()
    return bool(name) and not name.startswith("<") and name not in UNIDENTIFIED_PROTOCOL_NAMES and slug not in UNIDENTIFIED_PROTOCOL_SLUGS


def classify_vault(vault: pd.Series) -> str:
    """Classify a vault into one of the report's vault groups.

    :param vault:
        Vault metrics row with ``is_perp_dex``, ``flags``, ``strategy_tags`` and ``protocol_slug``.

    :return:
        :py:data:`PERP_DEX`, :py:data:`TOKENISED_FUND`, :py:data:`LENDING` or :py:data:`OTHER`.
    """
    if vault["is_perp_dex"]:
        return PERP_DEX
    if TOKENISED_FUND_FLAG in (vault["flags"] if isinstance(vault["flags"], list) else []):
        return TOKENISED_FUND
    tags = vault["strategy_tags"] if isinstance(vault["strategy_tags"], list) else []
    if LENDING_STRATEGY_TAGS.intersection(tags) or vault["protocol_slug"] in LENDING_PROTOCOL_SLUGS:
        return LENDING
    return OTHER


def rank_vaults(df: pd.DataFrame, column: str = "one_month_cagr_best") -> pd.DataFrame:
    """Sort vaults by a metric, best first.

    The export caps annualised returns at 10,000%, so ties are broken with
    the absolute one-month return.

    :param df:
        Vault metrics.

    :param column:
        Ranking metric, by default the annualised one-month return.

    :return:
        Sorted vault metrics.
    """
    return df.sort_values([column, "one_month_returns"], ascending=False, na_position="last")


def select_group(eligible_df: pd.DataFrame, criteria: ReportCriteria, group: str, *, by: str = "one_month_cagr_best") -> pd.DataFrame:
    """Select the best vaults of a vault group.

    Applies the TVL threshold, and the activity threshold for groups other than
    perpetual futures DEX vaults and tokenised funds, whose deposits are not
    comparable onchain events.

    :param eligible_df:
        Output of :py:func:`filter_eligible_vaults`.

    :param criteria:
        Report thresholds.

    :param group:
        Vault group, see :py:func:`classify_vault`.

    :param by:
        Ranking metric column.

    :return:
        All matching vaults, best first, not truncated.
    """
    df = eligible_df
    mask = (df["group"] == group) & (df["current_nav"] >= criteria.min_tvl)
    if group in (LENDING, OTHER):
        mask &= df["event_count"] >= criteria.min_events
    return rank_vaults(df.loc[mask & df[by].notna()], by)


def select_yield_vaults(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Stablecoin yield vaults: every group except perpetual futures DEX vaults.

    Used for the hero image and the Treasury bill caption.

    :param eligible_df:
        Output of :py:func:`filter_eligible_vaults`.

    :param criteria:
        Report thresholds.

    :return:
        Vaults above the TVL and activity thresholds, best first.
    """
    df = eligible_df
    mask = (df["group"] != PERP_DEX) & (df["current_nav"] >= criteria.min_tvl) & ((df["event_count"] >= criteria.min_events) | (df["group"] == TOKENISED_FUND))
    return rank_vaults(df.loc[mask])


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


def select_average_yield_vaults(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Select the vaults counted in the average yield charts.

    Leaves out small vaults, outliers above :py:attr:`ReportCriteria.yield_max_return`
    and volatile vaults above :py:attr:`ReportCriteria.yield_max_volatility`,
    like tokenised crypto index funds, which are not stablecoin yield.

    :param eligible_df:
        Output of :py:func:`filter_eligible_vaults`.

    :param criteria:
        Report thresholds.

    :return:
        Selected vaults.
    """
    df = eligible_df
    mask = (df["current_nav"] >= criteria.yield_min_vault_tvl) & (df["one_month_cagr_best"] <= criteria.yield_max_return) & (df["three_months_volatility"] <= criteria.yield_max_volatility)
    return df.loc[mask]


def calculate_average_yields(yield_vaults: pd.DataFrame, group_column: str) -> pd.DataFrame:
    """Calculate the TVL-weighted average one-month yield per group.

    :param yield_vaults:
        Output of :py:func:`select_average_yield_vaults`.

    :param group_column:
        ``chain`` or ``protocol``.

    :return:
        DataFrame indexed by group with columns ``tvl`` (USD), ``avg_return``
        (TVL-weighted annualised one-month return, 0.05 = 5%) and ``vault_count``.
    """
    df = yield_vaults
    grouped = df.assign(weighted=df["current_nav"] * df["one_month_cagr_best"]).groupby(group_column).agg(tvl=("current_nav", "sum"), weighted=("weighted", "sum"), vault_count=("current_nav", "size"))
    grouped["avg_return"] = grouped["weighted"] / grouped["tvl"]
    return grouped[["tvl", "avg_return", "vault_count"]]


def calculate_chain_yields(yield_vaults: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Average yield of the largest blockchains by TVL.

    Automates the "average vault yield per blockchain" figure, previously a
    screenshot of the `chain overview <https://tradingstrategy.ai/trading-view/vaults/chains>`__.

    :param yield_vaults:
        Output of :py:func:`select_average_yield_vaults`.

    :param criteria:
        Report thresholds.

    :return:
        See :py:func:`calculate_average_yields`, the top :py:attr:`ReportCriteria.yield_top_chains` chains by TVL.
    """
    return calculate_average_yields(yield_vaults, "chain").nlargest(criteria.yield_top_chains, "tvl")


def calculate_protocol_yields(yield_vaults: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Average yield of the largest vault protocols by TVL.

    Placeholder protocols, such as generic ERC-4626 vaults of unidentified
    protocols, are left out.

    :param yield_vaults:
        Output of :py:func:`select_average_yield_vaults`.

    :param criteria:
        Report thresholds.

    :return:
        See :py:func:`calculate_average_yields`, the top
        :py:attr:`ReportCriteria.yield_top_protocols` protocols by TVL with at
        least :py:attr:`ReportCriteria.yield_min_protocol_tvl` TVL.
    """
    identified = yield_vaults.loc[yield_vaults["protocol_identified"]]
    yields = calculate_average_yields(identified, "protocol")
    return yields.loc[yields["tvl"] >= criteria.yield_min_protocol_tvl].nlargest(criteria.yield_top_protocols, "tvl")


def calculate_tvl_changes(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Find the vaults with the largest TVL changes over the last month, in dollars.

    Uses the one-month period's start and end TVL from the top vaults export.
    A TVL change includes both deposits and redemptions and the vault's own
    returns. Some funds are listed under several share token addresses with the
    same TVL; duplicates with the same name, chain and change are counted once.

    :param eligible_df:
        Output of :py:func:`filter_eligible_vaults`.

    :param criteria:
        Report thresholds.

    :return:
        The :py:attr:`ReportCriteria.tvl_change_top_n` largest increases and
        decreases, with ``tvl_start``, ``tvl_end`` and ``tvl_change`` columns
        in USD, sorted from the largest increase to the largest decrease.
    """
    one_month = eligible_df["period_results"].apply(lambda periods: next((p for p in (periods or []) if p.get("period") == "1M"), {}))
    df = eligible_df.assign(
        tvl_start=one_month.apply(lambda p: p.get("tvl_start")).astype(float),
        tvl_end=one_month.apply(lambda p: p.get("tvl_end")).astype(float),
    ).dropna(subset=["tvl_start", "tvl_end"])
    df = df.assign(tvl_change=df["tvl_end"] - df["tvl_start"]).drop_duplicates(subset=["name", "chain", "tvl_change"])
    top = criteria.tvl_change_top_n
    changes = pd.concat([df.loc[df["tvl_change"] > 0].nlargest(top, "tvl_change"), df.loc[df["tvl_change"] < 0].nsmallest(top, "tvl_change").iloc[::-1]])
    return changes


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
        "Protocol": _link(row["protocol"], _get_trading_strategy_protocol_link(row["protocol_slug"])) if row["protocol_identified"] else OTHER_PROTOCOL,
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


def _group_tvl_history(tvl_history: pd.DataFrame, groups: pd.Series, top_n: int, excluded: str | None = None) -> pd.DataFrame:
    """Sum vault TVL history per group, keeping the largest groups and summing the rest.

    :param tvl_history:
        Output of :py:func:`eth_defi.vault_report.data.read_vault_tvl_history`, one column per vault id.

    :param groups:
        Vault id -> group name.

    :param top_n:
        Groups shown separately, by their latest TVL.

    :param excluded:
        Group never shown separately, e.g. unknown protocols.

    :return:
        DataFrame with one column per group, largest first, then ``Other`` if any vaults remain.
    """
    by_group = tvl_history.T.groupby(groups.reindex(tvl_history.columns)).sum(min_count=1).T.fillna(0)
    ranked = by_group.iloc[-1].sort_values(ascending=False).index
    top = [group for group in ranked if group != excluded][:top_n]
    result = by_group[top].copy()
    rest = by_group.drop(columns=top)
    if len(rest.columns):
        result["Other"] = rest.sum(axis=1)
    return result


def calculate_protocol_tvl_history(tvl_history: pd.DataFrame, vaults_df: pd.DataFrame, top_n: int = 7) -> pd.DataFrame:
    """Sum vault TVL history per protocol.

    :param tvl_history:
        Output of :py:func:`eth_defi.vault_report.data.read_vault_tvl_history`, one column per vault id.

    :param vaults_df:
        Vault metrics with a ``protocol_label`` column, indexed by vault id.

    :param top_n:
        Identified protocols shown separately, by their latest TVL. Smaller protocols and
        vaults without an identified protocol are summed as :py:data:`OTHER_PROTOCOL`.

    :return:
        DataFrame with one column per protocol, largest first, then ``Other``.
    """
    return _group_tvl_history(tvl_history, vaults_df["protocol_label"], top_n, excluded=OTHER_PROTOCOL)


def calculate_fund_nav_history(tvl_history: pd.DataFrame, funds_df: pd.DataFrame, top_n: int = 7) -> pd.DataFrame:
    """Sum tokenised fund NAV history per fund.

    A fund deployed on several chains under the same name is counted as one fund.

    :param tvl_history:
        Output of :py:func:`eth_defi.vault_report.data.read_vault_tvl_history`, one column per vault id.

    :param funds_df:
        Tokenised fund metrics with ``name`` and ``address`` columns, indexed by vault id.

    :param top_n:
        Funds shown separately, by their latest NAV. The rest are summed as ``Other``.

    :return:
        DataFrame with one column per fund, largest first, then ``Other`` if more funds remain.
    """
    return _group_tvl_history(tvl_history, funds_df["name"].fillna(funds_df["address"]), top_n)
