"""Generate the monthly vault report bundle and publish it as a Ghost draft.

Pipeline:

1. :py:func:`eth_defi.vault_report.data.fetch_vault_report_data` loads vault metrics and prices
2. :py:func:`generate_monthly_vault_report` ranks vaults, renders tables,
   branded charts and the social hero image, and writes a local output bundle
3. :py:func:`publish_report_draft` uploads the images to Ghost and creates a
   draft post, which the editor completes in Ghost Admin

The post structure is described in ``eth_defi/vault_report/README-blog-post-outline.md``.

Output bundle layout::

    {output_dir}/
        post.html          Ghost post body, charts referenced by relative paths
        preview.html       Standalone page for reviewing the report in a browser
        report.json        Title, slug, statistics and file listing
        hero.png           1200×630 social image, used as the Ghost feature image
        hero-square.png    1080×1080 social image for X
        charts/*.png       Branded chart images
        tables/*.html      Individual tables
        tables/*.csv       Raw metrics of the listed vaults
"""

import dataclasses
import datetime
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from tqdm_loggable.auto import tqdm

from eth_defi.research.vault_metrics import USDollarAmount
from eth_defi.vault_report.benchmarks import fetch_benchmark_indices, fetch_treasury_bill_yields, get_latest_yield, select_benchmarks
from eth_defi.vault_report.branding import SQUARE_HERO_SIZE, compose_chart_panel, render_hero_image
from eth_defi.vault_report.charts import (
    PerformanceSeries,
    VaultProperty,
    create_average_yield_figure,
    create_performance_figure,
    create_protocol_tvl_figure,
    create_risk_return_figure,
    create_tvl_change_figure,
    rasterise_logos,
    render_figure_png,
)
from eth_defi.vault_report.data import TVL_OUTLIER_THRESHOLD, VaultReportData, calculate_daily_share_prices, fetch_available_sparklines, read_vault_share_prices, read_vault_tvl_history
from eth_defi.vault_report.ghost import GhostAdminClient, GhostPost
from eth_defi.vault_report.logos import fetch_chain_logo_uri, load_benchmark_logo_uri, load_protocol_logo_uri
from eth_defi.vault_report.post import PostContext, build_post_html, build_preview_html, make_month_label, make_report_slug, make_report_title
from eth_defi.vault_report.sections import (
    CHAIN_TABLE_COLUMNS,
    LENDING,
    OTHER,
    PERP_DEX,
    TABLE_FORMAT_NOTE,
    TOKENISED_FUND,
    ReportCriteria,
    ReportSection,
    calculate_chain_yields,
    calculate_fund_nav_history,
    calculate_protocol_tvl_history,
    calculate_protocol_yields,
    calculate_tvl_changes,
    filter_eligible_vaults,
    render_section_table,
    select_average_yield_vaults,
    select_comparable_vaults,
    select_group,
    select_new_vaults,
    select_tvl_history_vaults,
    select_vaults_by_chain,
    select_yield_vaults,
)
from eth_defi.vault_report.theme import DARK_THEME, ChartTheme

logger = logging.getLogger(__name__)

#: Period of the performance charts and hero sparklines
PERFORMANCE_WINDOW = datetime.timedelta(days=90)

#: Rolling window of the Sharpe ratio chart
SHARPE_WINDOW = datetime.timedelta(days=90)

#: Price history read for charts: the performance window, the Sharpe ratio window before it, and a margin
PRICE_HISTORY = PERFORMANCE_WINDOW + SHARPE_WINDOW + datetime.timedelta(days=10)

#: History shown in the TVL by protocol chart
TVL_HISTORY = datetime.timedelta(days=365)

#: Best-performing vault sections: section key -> (vault group, ranking metric column)
BEST_SECTIONS = {
    "lending": (LENDING, "one_month_cagr_best"),
    "perp_dex": (PERP_DEX, "one_month_cagr_best"),
    "perp_dex_sharpe": (PERP_DEX, "three_months_sharpe_best"),
    "other": (OTHER, "one_month_cagr_best"),
    "tokenised_funds": (TOKENISED_FUND, "one_month_cagr_best"),
}

#: Raw metrics columns written to the per-section CSV files
CSV_COLUMNS = [
    "id",
    "name",
    "chain",
    "protocol",
    "group",
    "denomination",
    "one_month_cagr",
    "one_month_cagr_net",
    "three_months_cagr",
    "three_months_cagr_net",
    "cagr",
    "cagr_net",
    "three_months_sharpe_best",
    "three_months_volatility",
    "current_nav",
    "peak_nav",
    "years",
    "event_count",
    "risk",
    "trading_strategy_link",
]


@dataclass(slots=True, frozen=True)
class ChartPanel:
    """Header and footer texts of a branded chart panel."""

    #: Panel title, heading case
    title: str

    #: One-line description of the data
    subtitle: str

    #: Live chart page shown in the footer, without ``https://``
    link: str


@dataclass(slots=True)
class GeneratedReport:
    """Result of :py:func:`generate_monthly_vault_report`."""

    #: Output bundle directory
    output_dir: Path

    #: Post title
    title: str

    #: Post slug
    slug: str

    #: Post excerpt
    excerpt: str

    #: Report data date, naive UTC
    data_end_at: datetime.datetime

    #: Rendering inputs, charts referenced by paths relative to :py:attr:`output_dir`
    context: PostContext

    #: Chart key -> PNG path
    chart_paths: dict[str, Path] = field(default_factory=dict)

    #: Section key -> ranked vaults
    sections: dict[str, ReportSection] = field(default_factory=dict)

    #: Social hero image, or ``None`` when charts were not rendered
    hero_path: Path | None = None


def format_usd(value: USDollarAmount) -> str:
    """Format a rounded US dollar amount for prose.

    :param value:
        Amount.

    :return:
        E.g. ``$200k``, ``$2M`` or ``$31.9B``.
    """
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.1f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:,.0f}M"
    return f"${value / 1_000:,.0f}k"


def calculate_report_stats(vaults_df: pd.DataFrame, eligible_df: pd.DataFrame, data_end_at: datetime.datetime) -> list[str]:
    """Create the summary statistics bullet points.

    :param vaults_df:
        All vault metrics in the top vaults export.

    :param eligible_df:
        Vaults eligible for listings.

    :param data_end_at:
        Report data date.

    :return:
        Plain text bullet points.
    """
    protocol_count = vaults_df.loc[vaults_df["protocol_identified"], "protocol_slug"].nunique()
    denominations = eligible_df.groupby("normalised_denomination")["current_nav"].sum().sort_values(ascending=False)
    return [
        f"{vaults_df['chain'].nunique()} blockchains and {protocol_count} identified vault protocols",
        f"The report data is dated {data_end_at.strftime('%Y-%m-%d')}",
        f"{len(vaults_df):,} stablecoin-denominated vaults, of which {len(eligible_df):,} have up-to-date data and are not blacklisted",
        f"Combined TVL of the up-to-date vaults is {format_usd(eligible_df['current_nav'].sum())}",
        f"The largest denomination stablecoins by TVL are {', '.join(denominations.head(8).index)}",
    ]


def make_vault_properties(vault: pd.Series, theme: ChartTheme, chain_logo: Callable[[str], str | None]) -> tuple[VaultProperty, ...]:
    """List the curator, protocol and chain shown under a vault name in the charts.

    The curator is left out when the vault has none or when it is the
    protocol itself, e.g. a protocol curating its own vaults, and the chain
    when it has the protocol's name, e.g. a native perp DEX chain. Protocol and
    curator logos come from the vault metadata, chain logos from the website.

    :param vault:
        Vault metrics row with ``curator_name``, ``curator_slug``, ``protocol_label``,
        ``protocol_slug``, ``protocol_identified`` and ``chain``.

    :param theme:
        Chart theme, for logo variants.

    :param chain_logo:
        Chain name -> logo data URI, or ``None``.

    :return:
        Properties in the order curator, protocol, chain.
    """
    properties = []
    curator, curator_slug = vault.get("curator_name"), vault.get("curator_slug")
    protocol = vault["protocol_label"]
    if isinstance(curator, str) and curator.strip() and curator_slug != vault["protocol_slug"] and curator.strip().lower() != protocol.lower():
        properties.append(VaultProperty(curator.strip(), load_protocol_logo_uri(curator_slug if isinstance(curator_slug, str) else None, theme)))
    properties.append(VaultProperty(protocol, load_protocol_logo_uri(vault["protocol_slug"], theme) if vault["protocol_identified"] else None))
    # Native perp DEX chains share the protocol's name, e.g. GRVT on GRVT, so the chain is shown once
    if vault["chain"].strip().lower() != protocol.lower():
        properties.append(VaultProperty(vault["chain"], chain_logo(vault["chain"])))
    return tuple(properties)


def make_criteria_notes(criteria: ReportCriteria) -> dict[str, list[str]]:
    """Describe the selection criteria of each section for readers.

    :param criteria:
        Report thresholds.

    :return:
        Section key -> bullet point HTML strings.
    """
    live = '<a href="{url}">View the live benchmark</a> to examine the data in real time'
    min_tvl = f"Minimum {format_usd(criteria.min_tvl)} TVL"
    active = f"at least {criteria.min_events} deposit and redemption events"
    performance = "The chart compares the 90-day equity curves of the top {count} vaults of the table with {benchmark}; the legend numbers are table ranks"
    matching = "the benchmarks matching the vaults: the 3-month US Treasury bill for calm yield vaults, BTC and ETH for volatile vaults"
    unidentified = "Vaults without an identified protocol, such as generic ERC-4626 vaults, are left out, because their data is often unreliable"
    average = [
        "Each small dot is a vault's annualised one-month return; the large dot is the TVL-weighted average",
        f"Vaults with at least {format_usd(criteria.yield_min_vault_tvl)} TVL; outliers above {criteria.yield_max_return:.0%} annualised return or {criteria.yield_max_volatility:.0%} annualised volatility excluded",
        "The dashed line is the current 3-month US Treasury bill yield; the right column shows the difference to it in percentage points, and the TVL",
        unidentified,
    ]
    return {
        "chain_yields": [f"The {criteria.yield_top_chains} largest blockchains by stablecoin vault TVL, perp DEX vaults included", *average, live.format(url="https://tradingstrategy.ai/trading-view/vaults/chains")],
        "protocol_yields": [f"The {criteria.yield_top_protocols} largest identified vault protocols by stablecoin vault TVL, with at least {format_usd(criteria.yield_min_protocol_tvl)} TVL", *average, live.format(url="https://tradingstrategy.ai/trading-view/vaults/protocols")],
        "protocol_tvl": [
            "Weekly total value locked in stablecoin DeFi vaults over the last year, by vault protocol; tokenised funds are shown separately below",
            "Vaults without an identified protocol, such as generic ERC-4626 vaults, are counted in Other",
            f"Excludes blacklisted vaults and TVL data points above {format_usd(TVL_OUTLIER_THRESHOLD)}, like the website's TVL charts",
            live.format(url="https://tradingstrategy.ai/trading-view/vaults/historical-tvl-protocol?history=1y"),
        ],
        "fund_nav": [
            "Weekly net asset value of tokenised money market, treasury and credit funds over the last year, by fund",
            "A fund deployed on several chains under the same name is counted once",
            f"Excludes blacklisted funds and data points above {format_usd(TVL_OUTLIER_THRESHOLD)}",
            live.format(url="https://tradingstrategy.ai/trading-view/vaults/funds"),
        ],
        "tvl_changes": [
            f"The {criteria.tvl_change_top_n} largest TVL increases and decreases over the last 30 days, in US dollars",
            "A TVL change includes deposits, redemptions and the vault's own returns",
        ],
        "best": [
            "Vaults are ranked by their annualised last one-month returns, net of fees (n) when fee data is available and gross (g) otherwise",
            f"{min_tvl} in every table; lending and other vaults also need {active}",
            unidentified,
            TABLE_FORMAT_NOTE,
            live.format(url="https://tradingstrategy.ai/trading-view/vaults"),
        ],
        "lending": ["Vaults supplying stablecoins to lending markets, identified by their strategy or lending protocol", performance.format(count=criteria.performance_chart_vaults, benchmark=matching)],
        "perp_dex": ["Hyperliquid, GRVT, Lighter and other perpetual futures DEX vaults", performance.format(count=criteria.performance_chart_vaults, benchmark="BTC and ETH")],
        "perp_dex_sharpe": [
            "The same vaults ranked by three-month Sharpe ratio, rewarding steady returns over high but volatile ones",
            f"The chart shows the 90-day rolling Sharpe ratio of the top {criteria.performance_chart_vaults} vaults of the table over the last 90 days, against BTC and ETH; the legend shows the latest value",
        ],
        "other": ["Yield aggregators, trading and other vaults that are not lending, perp DEX or tokenised fund vaults", performance.format(count=criteria.performance_chart_vaults, benchmark=matching)],
        "tokenised_funds": ["Onchain money market, treasury and credit funds", min_tvl, performance.format(count=criteria.performance_chart_vaults, benchmark=matching)],
        "new": [f"Vaults launched in the last {criteria.new_vault_max_age.days} days", f"Minimum {format_usd(criteria.new_vault_min_tvl)} TVL and {active}; perp DEX vaults excluded", unidentified],
        "risk_return": [
            "Each bubble is a stablecoin yield vault from the tables above; bubble area shows TVL",
            f"Vaults with annualised three-month returns above {criteria.scatter_max_return:.0%} are drawn as triangles on the top edge; volatility is on a log scale",
            unidentified,
        ],
        "by_chain": [f"The top {criteria.chain_top_n} performing vaults for each blockchain", f"Minimum {format_usd(criteria.chain_min_tvl)} TVL", unidentified, live.format(url="https://tradingstrategy.ai/trading-view/vaults/chains")],
    }


def build_report_sections(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> dict[str, ReportSection]:
    """Select vaults for all report tables.

    :param eligible_df:
        Output of :py:func:`eth_defi.vault_report.sections.select_comparable_vaults`:
        eligible vaults with an identified protocol.

    :param criteria:
        Report thresholds.

    :return:
        Section key -> section. Sections without vaults are omitted.
    """
    sections = {key: ReportSection(select_group(eligible_df, criteria, group, by=metric).head(criteria.top_n)) for key, (group, metric) in BEST_SECTIONS.items()}
    sections["new"] = ReportSection(select_new_vaults(eligible_df, criteria))
    sections["by_chain"] = ReportSection(select_vaults_by_chain(eligible_df, criteria), CHAIN_TABLE_COLUMNS, numbered=False)
    for key, section in sections.items():
        logger.info("Section %s: %d vaults", key, len(section.vaults_df))
    return {key: section for key, section in sections.items() if len(section.vaults_df) > 0}


def make_benchmark_caption(universe_df: pd.DataFrame, benchmark_yield: float | None, min_tvl: USDollarAmount) -> str | None:
    """Summarise how many yield vaults beat the Treasury bill.

    Uses the whole ranking universe, not the top of a table, which beats the
    benchmark by construction.

    :param universe_df:
        All stablecoin yield vaults above the table thresholds.

    :param benchmark_yield:
        Latest 3-month US Treasury bill yield as a fraction, or ``None``.

    :param min_tvl:
        TVL threshold of the universe, for the sentence.

    :return:
        One sentence, or ``None`` without benchmark data.
    """
    if benchmark_yield is None or universe_df.empty:
        return None
    returns = universe_df["one_month_cagr_best"]
    beat = int((returns > benchmark_yield).sum())
    return f"{beat} of the {len(universe_df)} stablecoin yield vaults with at least {format_usd(min_tvl)} TVL beat the 3-month US Treasury bill yield of {benchmark_yield:.1%} over the last month. Their median annualised one-month return was {returns.median():.1%}."


def render_report_charts(
    data: VaultReportData,
    eligible_df: pd.DataFrame,
    sections: dict[str, ReportSection],
    criteria: ReportCriteria,
    theme: ChartTheme,
    output_dir: Path,
    cache_dir: Path,
    tbill_yields: pd.Series | None = None,
) -> tuple[dict[str, Path], Path]:
    """Render all branded report charts and the hero images.

    Optional inputs (logos, benchmarks) degrade gracefully: a missing input
    leaves the element out of the chart.

    :param data:
        Report data, for the price file.

    :param eligible_df:
        Eligible vaults.

    :param sections:
        Output of :py:func:`build_report_sections`.

    :param criteria:
        Report thresholds.

    :param theme:
        Chart theme.

    :param output_dir:
        Bundle directory; charts are written to ``charts/``.

    :param cache_dir:
        Download cache for logos and benchmark data.

    :param tbill_yields:
        US Treasury bill yields, see :py:func:`eth_defi.vault_report.benchmarks.fetch_treasury_bill_yields`.

    :return:
        Tuple (chart key -> PNG path, hero image path). The square hero image is
        written next to the hero image as ``hero-square.png``.
    """
    empty = pd.DataFrame()
    data_date = data.data_end_at.strftime("%Y-%m-%d")
    month_label = make_month_label(data.data_end_at)
    # Performance charts compare only vaults with an identified protocol; TVL charts use all eligible vaults
    comparable_df = select_comparable_vaults(eligible_df)
    yield_universe = select_yield_vaults(comparable_df, criteria)
    protocol_slugs = eligible_df.drop_duplicates("protocol").set_index("protocol")["protocol_slug"]

    performance_vaults = {key: sections[key].vaults_df.head(criteria.performance_chart_vaults) for key in BEST_SECTIONS if key in sections}
    hero_vaults = yield_universe.loc[(yield_universe["one_month_cagr_best"] <= criteria.chart_max_return) & (yield_universe["three_months_volatility"] <= criteria.hero_max_volatility) & ~yield_universe["risk"].isin(criteria.hero_excluded_risks)].head(5)

    chart_ids = set(hero_vaults.index) | {vault_id for df in performance_vaults.values() for vault_id in df.index}
    share_prices = read_vault_share_prices(data.prices_path, sorted(chart_ids), start_at=data.data_end_at - PRICE_HISTORY)
    daily_prices = calculate_daily_share_prices(share_prices)
    sharpe_prices = calculate_daily_share_prices(share_prices, interpolate=False)
    if daily_prices.empty:
        logger.warning("No price data for the chart vaults in %s, leaving out the performance charts", data.prices_path)
        performance_vaults = {}

    tbill_latest = get_latest_yield(tbill_yields) if tbill_yields is not None else None
    benchmark_indices = fetch_benchmark_indices(data.data_end_at - PRICE_HISTORY, data.data_end_at, cache_dir, tbill_yields)

    average_yield_vaults = select_average_yield_vaults(comparable_df, criteria)
    chain_yields = calculate_chain_yields(average_yield_vaults, criteria)
    protocol_yields = calculate_protocol_yields(average_yield_vaults, criteria)
    chain_logo_cache: dict[str, str | None] = {}

    def chain_logo(chain: str) -> str | None:
        if chain not in chain_logo_cache:
            chain_logo_cache[chain] = fetch_chain_logo_uri(chain, cache_dir / "logos")
        return chain_logo_cache[chain]

    chain_logos = {chain: chain_logo(chain) for chain in chain_yields.index}
    protocol_group_logos = {name: load_protocol_logo_uri(protocol_slugs.get(name), theme) for name in protocol_yields.index}

    tvl_vaults = select_tvl_history_vaults(data.vaults_df)
    tvl_history = read_vault_tvl_history(data.prices_path, list(tvl_vaults.index), start_at=data.data_end_at - TVL_HISTORY)
    # DeFi vault protocols and tokenised funds are charted separately
    is_fund = tvl_vaults["group"] == TOKENISED_FUND
    defi_vaults, fund_vaults = tvl_vaults.loc[~is_fund], tvl_vaults.loc[is_fund]
    defi_history = tvl_history[[vault_id for vault_id in tvl_history.columns if vault_id in defi_vaults.index]]
    fund_history = tvl_history[[vault_id for vault_id in tvl_history.columns if vault_id in fund_vaults.index]]
    protocol_tvl = calculate_protocol_tvl_history(defi_history, defi_vaults) if len(defi_history.columns) else empty
    fund_nav = calculate_fund_nav_history(fund_history, fund_vaults) if len(fund_history.columns) else empty
    tvl_vault_slugs = defi_vaults.loc[defi_vaults["protocol_identified"]].drop_duplicates("protocol").set_index("protocol")["protocol_slug"]
    fund_slugs = fund_vaults.assign(name=fund_vaults["name"].fillna(fund_vaults["address"])).drop_duplicates("name").set_index("name")["protocol_slug"]
    tvl_changes = calculate_tvl_changes(eligible_df, criteria)
    # Curator, protocol and chain under each vault name, with their icons
    vault_properties = {vault_id: make_vault_properties(eligible_df.loc[vault_id], theme, chain_logo) for vault_id in chart_ids | set(tvl_changes.index)}

    difference = "Small dots: vaults · large dots: TVL-weighted average · right: difference to the US 3M T-bill and TVL"
    figures = {
        "chain_yields": (
            create_average_yield_figure(chain_yields, average_yield_vaults, "chain", theme, chain_logos, tbill_latest, criteria.yield_chart_max_return),
            ChartPanel(f"Stablecoin vault yield on the {criteria.yield_top_chains} largest blockchains", difference, "tradingstrategy.ai/trading-view/vaults/chains"),
        ),
        "protocol_yields": (
            create_average_yield_figure(protocol_yields, average_yield_vaults, "protocol", theme, protocol_group_logos, tbill_latest, criteria.yield_chart_max_return),
            ChartPanel(f"Stablecoin vault yield of the {criteria.yield_top_protocols} largest protocols", difference, "tradingstrategy.ai/trading-view/vaults/protocols"),
        ),
    }
    if len(protocol_tvl):
        figures["protocol_tvl"] = (
            create_protocol_tvl_figure(protocol_tvl, theme, {name: load_protocol_logo_uri(tvl_vault_slugs.get(name), theme) for name in protocol_tvl.columns}),
            ChartPanel("Stablecoin TVL by DeFi vault protocol", "Weekly total value locked over the last 12 months, tokenised funds excluded", "tradingstrategy.ai/trading-view/vaults/historical-tvl-protocol"),
        )
    if len(fund_nav):
        figures["fund_nav"] = (
            create_protocol_tvl_figure(fund_nav, theme, {name: load_protocol_logo_uri(fund_slugs.get(name), theme) for name in fund_nav.columns}, value_label="NAV"),
            ChartPanel("Stablecoin NAV by tokenised fund", "Weekly net asset value over the last 12 months", "tradingstrategy.ai/trading-view/vaults/funds"),
        )
    if len(tvl_changes):
        figures["tvl_changes"] = (
            create_tvl_change_figure(tvl_changes, theme, vault_properties),
            ChartPanel("Inflows and outflows", f"The {criteria.tvl_change_top_n} largest TVL increases and decreases over the last 30 days, in US dollars", "tradingstrategy.ai/trading-view/vaults"),
        )

    period = f"{PERFORMANCE_WINDOW.days}-day equity"
    selection = f"Top {criteria.performance_chart_vaults} {{by}} with at least {format_usd(criteria.min_tvl)} TVL"
    by_return = selection.format(by="by return")
    performance_panels = {
        "lending": ChartPanel("Performance of the best-performing lending vaults", f"{by_return}, {period}, against their benchmarks", "tradingstrategy.ai/trading-view/vaults"),
        "perp_dex": ChartPanel("Performance of the best-performing perp DEX vaults", f"{by_return}, {period}, against BTC and ETH", "tradingstrategy.ai/trading-view/vaults"),
        "perp_dex_sharpe": ChartPanel("Performance of perp DEX vaults with the best Sharpe ratio", f"{selection.format(by='by 3M Sharpe ratio')}, {SHARPE_WINDOW.days}-day rolling Sharpe ratio, against BTC and ETH", "tradingstrategy.ai/trading-view/vaults"),
        "other": ChartPanel("Performance of other best-performing vaults", f"{by_return}, {period}, against their benchmarks", "tradingstrategy.ai/trading-view/vaults"),
        "tokenised_funds": ChartPanel("Performance of the best-performing tokenised funds", f"{selection.format(by='funds by return')}, {period}, against their benchmarks", "tradingstrategy.ai/trading-view/vaults/funds"),
    }
    benchmark_logos = {name: load_benchmark_logo_uri(name) for name in benchmark_indices}
    for key, df in performance_vaults.items():
        if not len(df):
            continue
        series = [
            PerformanceSeries(
                vault_id=vault_id,
                name=vault["name"] or vault["address"],
                properties=vault_properties[vault_id],
                benchmarks=select_benchmarks(vault, criteria.crypto_benchmark_min_volatility, criteria.crypto_benchmark_max_drawdown),
            )
            for vault_id, vault in df.iterrows()
        ]
        if key == "perp_dex_sharpe":
            figure = create_performance_figure(series, sharpe_prices, benchmark_indices, theme, PERFORMANCE_WINDOW, benchmark_logos=benchmark_logos, measure="sharpe", sharpe_window=SHARPE_WINDOW)
        else:
            figure = create_performance_figure(series, daily_prices, benchmark_indices, theme, PERFORMANCE_WINDOW, benchmark_logos=benchmark_logos)
        figures[f"{key}_performance"] = (figure, performance_panels[key])

    figures["risk_return"] = (
        create_risk_return_figure(yield_universe, {tag: category.get("label", tag) for tag, category in data.categories.items()}, theme, criteria.scatter_max_return, tbill_latest),
        ChartPanel("Risk and return of stablecoin yield vaults", f"{len(yield_universe)} vaults with at least {format_usd(criteria.min_tvl)} TVL, bubble area shows TVL", "tradingstrategy.ai/trading-view/vaults/yield-risk"),
    )

    chart_dir = output_dir / "charts"
    chart_paths = {}
    for key, (fig, panel) in tqdm(figures.items(), desc="Rendering charts"):
        path = render_figure_png(fig, chart_dir / f"{key}.png")
        chart_paths[key] = compose_chart_panel(path, theme, panel.title, panel.subtitle, f"Data {data_date}", panel.link, path)

    sparkline_start = pd.Timestamp(data.data_end_at - PERFORMANCE_WINDOW)
    sparklines = {vault_id: daily_prices.loc[daily_prices.index >= sparkline_start, vault_id] for vault_id in hero_vaults.index if vault_id in daily_prices.columns}
    hero_subtitle = f"1M annualised return · ≥ {format_usd(criteria.min_tvl)} TVL · high-risk vaults excluded · {data_date}"
    hero_logos = rasterise_logos({prop.logo_uri for vault_id in hero_vaults.index for prop in vault_properties[vault_id] if prop.logo_uri})
    hero_properties = {vault_id: [(prop.text, hero_logos.get(prop.logo_uri)) for prop in vault_properties[vault_id]] for vault_id in hero_vaults.index}
    hero_path = render_hero_image(hero_vaults, sparklines, month_label, hero_subtitle, theme, output_dir / "hero.png", properties=hero_properties)
    render_hero_image(hero_vaults, sparklines, month_label, hero_subtitle, theme, output_dir / "hero-square.png", size=SQUARE_HERO_SIZE, properties=hero_properties)
    return chart_paths, hero_path


def generate_monthly_vault_report(
    data: VaultReportData,
    output_dir: Path,
    criteria: ReportCriteria | None = None,
    previous: GhostPost | None = None,
    changelog_entries: list[str] | None = None,
    *,
    render_charts: bool = True,
    theme: ChartTheme = DARK_THEME,
    cache_dir: Path | None = None,
    check_sparklines: bool = True,
) -> GeneratedReport:
    """Generate the report tables, charts and post body into a local bundle.

    :param data:
        Loaded vault metrics and prices.

    :param output_dir:
        Where to write the bundle. Created if needed.

    :param criteria:
        Report thresholds. Defaults to :py:class:`~eth_defi.vault_report.sections.ReportCriteria` defaults.

    :param previous:
        The previous report post, for continuity.

    :param changelog_entries:
        Changelog entries to offer to the editor.

    :param render_charts:
        Render PNG charts and the hero images. Needs Chrome for Kaleido.

    :param theme:
        Chart theme.

    :param cache_dir:
        Cache for logos and benchmark data. Defaults to ``{output_dir}/cache``.

    :param check_sparklines:
        Check which vaults have a published sparkline and show them in the tables.

    :return:
        Generated report description.
    """
    criteria = criteria or ReportCriteria()
    cache_dir = cache_dir or output_dir / "cache"
    tbill_yields = fetch_treasury_bill_yields(cache_dir)
    (output_dir / "tables").mkdir(parents=True, exist_ok=True)

    data_end_at = data.data_end_at
    eligible_df = filter_eligible_vaults(data.vaults_df, data_end_at, criteria)
    comparable_df = select_comparable_vaults(eligible_df)
    sections = build_report_sections(comparable_df, criteria)
    if check_sparklines:
        sparkline_ids = frozenset(fetch_available_sparklines([vault_id for section in sections.values() for vault_id in section.vaults_df.index]))
        sections = {key: dataclasses.replace(section, sparkline_ids=sparkline_ids) for key, section in sections.items()}

    tables = {}
    for key, section in sections.items():
        tables[key] = render_section_table(section)
        (output_dir / "tables" / f"{key}.html").write_text(tables[key])
        section.vaults_df[CSV_COLUMNS].to_csv(output_dir / "tables" / f"{key}.csv", index=False)

    chart_paths, hero_path = render_report_charts(data, eligible_df, sections, criteria, theme, output_dir, cache_dir, tbill_yields) if render_charts else ({}, None)

    month_label = make_month_label(data_end_at)
    tbill_latest = get_latest_yield(tbill_yields) if tbill_yields is not None else None
    best_caption = make_benchmark_caption(select_yield_vaults(comparable_df, criteria), tbill_latest, criteria.min_tvl)
    context = PostContext(
        month_label=month_label,
        stats=calculate_report_stats(data.vaults_df, eligible_df, data_end_at),
        tables=tables,
        charts={key: path.relative_to(output_dir).as_posix() for key, path in chart_paths.items()},
        criteria_notes=make_criteria_notes(criteria),
        previous=previous,
        changelog_entries=changelog_entries or [],
        captions={"best": best_caption} if best_caption else {},
    )
    report = GeneratedReport(
        output_dir=output_dir,
        title=make_report_title(month_label),
        slug=make_report_slug(data_end_at),
        excerpt=f"The best stablecoin yield in DeFi, {month_label} report.",
        data_end_at=data_end_at,
        context=context,
        chart_paths=chart_paths,
        sections=sections,
        hero_path=hero_path,
    )

    post_html = build_post_html(context)
    (output_dir / "post.html").write_text(post_html)
    (output_dir / "preview.html").write_text(build_preview_html(report.title, post_html, hero_path.relative_to(output_dir).as_posix() if hero_path else None))
    write_report_manifest(report)
    logger.info("Report bundle written to %s", output_dir)
    return report


def write_report_manifest(report: GeneratedReport, ghost_post: GhostPost | None = None, editor_url: str | None = None) -> Path:
    """Write ``report.json`` describing the bundle.

    :param report:
        Generated report.

    :param ghost_post:
        The Ghost draft, if published.

    :param editor_url:
        Ghost Admin editor link of the draft.

    :return:
        Manifest path.
    """
    previous = report.context.previous
    manifest = {
        "title": report.title,
        "slug": report.slug,
        "excerpt": report.excerpt,
        "data_end_at": report.data_end_at.isoformat(),
        "stats": report.context.stats,
        "sections": {key: len(section.vaults_df) for key, section in report.sections.items()},
        "charts": report.context.charts,
        "hero": report.hero_path.relative_to(report.output_dir).as_posix() if report.hero_path else None,
        "hero_square": "hero-square.png" if report.hero_path else None,
        "previous_report_slug": previous.slug if previous else None,
        "ghost_draft": {"id": ghost_post.id, "slug": ghost_post.slug, "editor_url": editor_url} if ghost_post else None,
    }
    path = report.output_dir / "report.json"
    path.write_text(json.dumps(manifest, indent=2))
    return path


def publish_report_draft(
    report: GeneratedReport,
    admin_client: GhostAdminClient,
    tags: list[str] | None = None,
    *,
    overwrite_draft: bool = False,
) -> GhostPost:
    """Upload the images and create the Ghost draft post.

    The hero image becomes the post's feature image. The draft is never
    published automatically. An existing draft with the same slug is replaced
    only with ``overwrite_draft``, as replacing it loses manual edits made in Ghost.

    :param report:
        Output of :py:func:`generate_monthly_vault_report`.

    :param admin_client:
        Ghost Admin API client.

    :param tags:
        Tag names for the post.

    :param overwrite_draft:
        Replace an existing draft with the same slug.

    :return:
        The Ghost draft post.
    """
    # Fail before uploading images if the draft cannot be written
    admin_client.fetch_writable_draft(report.slug, overwrite_draft=overwrite_draft)
    chart_urls = {key: admin_client.upload_image(path) for key, path in tqdm(report.chart_paths.items(), desc="Uploading charts")}
    feature_image = admin_client.upload_image(report.hero_path) if report.hero_path else None
    post = admin_client.create_or_update_draft(
        title=report.title,
        slug=report.slug,
        html=build_post_html(dataclasses.replace(report.context, charts=chart_urls)),
        custom_excerpt=report.excerpt,
        tags=tags,
        feature_image=feature_image,
        feature_image_alt=f"The best-performing stablecoin vaults, {report.context.month_label}",
        overwrite_draft=overwrite_draft,
    )
    write_report_manifest(report, post, admin_client.get_editor_url(post))
    return post
