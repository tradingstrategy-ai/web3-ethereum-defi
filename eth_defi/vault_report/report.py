"""Generate the monthly vault report bundle and publish it as a Ghost draft.

Pipeline:

1. :py:func:`eth_defi.vault_report.data.fetch_vault_report_data` loads vault metrics and prices
2. :py:func:`generate_monthly_vault_report` ranks vaults, renders tables and charts
   and writes a local output bundle
3. :py:func:`publish_report_draft` uploads the charts to Ghost and creates a
   draft post, which the editor completes in Ghost Admin

Output bundle layout::

    {output_dir}/
        post.html          Ghost post body, charts referenced by relative paths
        preview.html       Standalone page for reviewing the report in a browser
        report.json        Title, slug, statistics and file listing
        charts/*.png       Chart images
        tables/*.html      Individual tables
        tables/*.csv       Raw metrics of the listed vaults
"""

import dataclasses
import datetime
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from tqdm_loggable.auto import tqdm

from eth_defi.research.vault_correlation import choose_vaults_for_correlation_comparison
from eth_defi.research.vault_metrics import USDollarAmount
from eth_defi.vault_report.charts import (
    calculate_rolling_returns,
    create_chain_yield_figure,
    create_correlation_figure,
    create_rolling_returns_figure,
    make_vault_label,
    render_figure_png,
)
from eth_defi.vault_report.data import VaultReportData, calculate_daily_share_prices, read_vault_share_prices
from eth_defi.vault_report.ghost import GhostAdminClient, GhostPost
from eth_defi.vault_report.post import PostContext, build_post_html, build_preview_html, make_month_label, make_report_slug, make_report_title
from eth_defi.vault_report.sections import (
    CHAIN_TABLE_COLUMNS,
    CORRELATION_TABLE_COLUMNS,
    UNKNOWN_PROTOCOL_SLUG,
    ReportCriteria,
    ReportSection,
    calculate_chain_yields,
    filter_eligible_vaults,
    render_section_table,
    select_best_vaults,
    select_new_vaults,
    select_perp_dex_vaults,
    select_vaults_by_chain,
)

logger = logging.getLogger(__name__)

#: Price history read for charts: 180 days of chart history plus the 90-day rolling window
PRICE_HISTORY = datetime.timedelta(days=280)

#: Raw metrics columns written to the per-section CSV files
CSV_COLUMNS = [
    "id",
    "name",
    "chain",
    "protocol",
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
    protocol_count = vaults_df.loc[vaults_df["protocol_slug"] != UNKNOWN_PROTOCOL_SLUG, "protocol_slug"].nunique()
    denominations = eligible_df.groupby("normalised_denomination")["current_nav"].sum().sort_values(ascending=False)
    return [
        f"{vaults_df['chain'].nunique()} blockchains and {protocol_count} identified vault protocols",
        f"The report data is dated {data_end_at.strftime('%Y-%m-%d')}",
        f"{len(vaults_df):,} stablecoin-denominated vaults, of which {len(eligible_df):,} have up-to-date data and are not blacklisted",
        f"Combined TVL of the up-to-date vaults is {format_usd(eligible_df['current_nav'].sum())}",
        f"The largest denomination stablecoins by TVL are {', '.join(denominations.head(8).index)}",
    ]


def make_criteria_notes(criteria: ReportCriteria) -> dict[str, list[str]]:
    """Describe the selection criteria of each section for readers.

    :param criteria:
        Report thresholds.

    :return:
        Section key -> bullet point HTML strings.
    """
    live = '<a href="{url}">View the live benchmark</a> to examine the data in real time'
    yield_vaults = f"Excludes perp DEX vaults and vaults with fewer than {criteria.min_events} deposit and redemption events"
    return {
        "chain_yields": [
            "TVL-weighted average of the annualised one-month returns of stablecoin vaults on each blockchain, including perp DEX vaults",
            f"Vaults with at least {format_usd(criteria.chain_yield_min_vault_tvl)} TVL, blockchains with at least {format_usd(criteria.chain_yield_min_chain_tvl)} TVL",
            f"Outlier vaults with over {criteria.chain_yield_max_return:.0%} annualised returns or over {criteria.chain_yield_max_volatility:.0%} annualised volatility excluded",
        ],
        "best": [
            live.format(url="https://tradingstrategy.ai/trading-view/vaults"),
            "Vaults are ranked by their annualised last one-month returns, net of fees (n) when fee data is available and gross (g) otherwise",
            f"The charts show three-month rolling returns for the top vaults, and for the top low-volatility vaults, leaving out vaults with over {criteria.chart_max_return:.0%} annualised returns for readability",
            f"Minimum {format_usd(criteria.min_tvl)} TVL",
            yield_vaults,
        ],
        "perp_dex": [
            f"Hyperliquid, GRVT, Lighter and other perpetual futures DEX vaults with minimum {format_usd(criteria.perp_dex_min_tvl)} TVL",
        ],
        "correlation": [
            f"The top vaults by three-month returns, with minimum {format_usd(criteria.correlation_min_tvl)} TVL",
            f"At most {criteria.correlation_per_protocol} vaults per protocol for more variety",
        ],
        "by_chain": [
            f"The top {criteria.chain_top_n} performing vaults for each blockchain",
            f"Minimum {format_usd(criteria.chain_min_tvl)} TVL",
            live.format(url="https://tradingstrategy.ai/trading-view/vaults/chains"),
        ],
        "large": [
            f"Vaults with a minimum of {format_usd(criteria.large_min_tvl)} TVL",
            yield_vaults,
            live.format(url="https://tradingstrategy.ai/trading-view/vaults/high-tvl"),
        ],
        "new": [
            f"Vaults launched in the last {criteria.new_vault_max_age.days} days",
            f"Minimum {format_usd(criteria.new_vault_min_tvl)} TVL",
            yield_vaults,
        ],
    }


def build_report_sections(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> dict[str, ReportSection]:
    """Select vaults for all report tables.

    :param eligible_df:
        Output of :py:func:`eth_defi.vault_report.sections.filter_eligible_vaults`.

    :param criteria:
        Report thresholds.

    :return:
        Section key -> section. Sections without vaults are omitted.
    """
    best = select_best_vaults(eligible_df, criteria)
    assert len(best) > 0, "No vaults matched the best-performing vaults criteria, check the input data"
    correlation = choose_vaults_for_correlation_comparison(
        eligible_df,
        min_nav=criteria.correlation_min_tvl,
        per_protocol=criteria.correlation_per_protocol,
        max=criteria.correlation_vault_count,
        printer=logger.info,
    )
    sections = {
        "best": ReportSection(best.head(criteria.top_n)),
        "low_volatility": ReportSection(best.loc[best["three_months_volatility"] < criteria.low_volatility_threshold].head(criteria.top_n)),
        "perp_dex": ReportSection(select_perp_dex_vaults(eligible_df, criteria)),
        "correlation": ReportSection(correlation, CORRELATION_TABLE_COLUMNS),
        "by_chain": ReportSection(select_vaults_by_chain(eligible_df, criteria), CHAIN_TABLE_COLUMNS, numbered=False),
        "large": ReportSection(best.loc[best["current_nav"] >= criteria.large_min_tvl].head(criteria.top_n)),
        "new": ReportSection(select_new_vaults(eligible_df, criteria)),
    }
    for key, section in sections.items():
        logger.info("Section %s: %d vaults", key, len(section.vaults_df))
    return {key: section for key, section in sections.items() if len(section.vaults_df) > 0}


def render_report_charts(
    data: VaultReportData,
    eligible_df: pd.DataFrame,
    sections: dict[str, ReportSection],
    criteria: ReportCriteria,
    chart_dir: Path,
) -> dict[str, Path]:
    """Render all report charts as PNG files.

    :param data:
        Report data, for the price file.

    :param eligible_df:
        Eligible vaults, for the chain yield chart.

    :param sections:
        Output of :py:func:`build_report_sections`.

    :param criteria:
        Report thresholds.

    :param chart_dir:
        Output directory.

    :return:
        Chart key -> PNG path.
    """
    empty = pd.DataFrame()

    def _chartable(section_key: str) -> pd.DataFrame:
        df = sections[section_key].vaults_df if section_key in sections else empty
        return df.loc[df["one_month_cagr_best"] <= criteria.chart_max_return].head(criteria.chart_vault_count) if len(df) else df

    rolling_vaults = {"best_rolling": _chartable("best"), "low_volatility_rolling": _chartable("low_volatility")}
    correlation_df = sections["correlation"].vaults_df if "correlation" in sections else empty

    chart_ids = set(correlation_df.index).union(*(df.index for df in rolling_vaults.values()))
    prices_df = read_vault_share_prices(data.prices_path, sorted(chart_ids), start_at=data.data_end_at - PRICE_HISTORY)
    daily_prices = calculate_daily_share_prices(prices_df)
    rolling_returns = calculate_rolling_returns(daily_prices)

    figures = {"chain_yields": create_chain_yield_figure(calculate_chain_yields(eligible_df, criteria))}
    if daily_prices.empty:
        logger.warning("No price data for the chart vaults in %s, rendering only the chain yield chart", data.prices_path)
        rolling_vaults, correlation_df = {}, empty
    titles = {"best_rolling": "3M rolling returns, best-performing vaults", "low_volatility_rolling": "3M rolling returns, low-volatility vaults"}
    for key, df in rolling_vaults.items():
        if len(df):
            figures[key] = create_rolling_returns_figure(rolling_returns, {vault_id: make_vault_label(row) for vault_id, row in df.iterrows()}, title=titles[key])
    if len(correlation_df):
        figures["correlation"] = create_correlation_figure(daily_prices, {vault_id: row["name"] or row["address"] for vault_id, row in correlation_df.iterrows()})

    return {key: render_figure_png(fig, chart_dir / f"{key}.png") for key, fig in tqdm(figures.items(), desc="Rendering charts")}


def generate_monthly_vault_report(
    data: VaultReportData,
    output_dir: Path,
    criteria: ReportCriteria | None = None,
    previous: GhostPost | None = None,
    changelog_entries: list[str] | None = None,
    *,
    render_charts: bool = True,
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
        Render PNG charts. Needs Chrome for Kaleido.

    :return:
        Generated report description.
    """
    criteria = criteria or ReportCriteria()
    (output_dir / "tables").mkdir(parents=True, exist_ok=True)

    data_end_at = data.data_end_at
    eligible_df = filter_eligible_vaults(data.vaults_df, data_end_at, criteria)
    sections = build_report_sections(eligible_df, criteria)

    tables = {}
    for key, section in sections.items():
        tables[key] = render_section_table(section)
        (output_dir / "tables" / f"{key}.html").write_text(tables[key])
        section.vaults_df[CSV_COLUMNS].to_csv(output_dir / "tables" / f"{key}.csv", index=False)

    chart_paths = render_report_charts(data, eligible_df, sections, criteria, output_dir / "charts") if render_charts else {}

    month_label = make_month_label(data_end_at)
    context = PostContext(
        month_label=month_label,
        stats=calculate_report_stats(data.vaults_df, eligible_df, data_end_at),
        tables=tables,
        charts={key: path.relative_to(output_dir).as_posix() for key, path in chart_paths.items()},
        criteria_notes=make_criteria_notes(criteria),
        previous=previous,
        changelog_entries=changelog_entries or [],
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
    )

    post_html = build_post_html(context)
    (output_dir / "post.html").write_text(post_html)
    (output_dir / "preview.html").write_text(build_preview_html(report.title, post_html))
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
    """Upload charts and create the Ghost draft post.

    The draft is never published automatically. An existing draft with the
    same slug is replaced only with ``overwrite_draft``, as replacing it loses
    manual edits made in Ghost.

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
    post = admin_client.create_or_update_draft(
        title=report.title,
        slug=report.slug,
        html=build_post_html(dataclasses.replace(report.context, charts=chart_urls)),
        custom_excerpt=report.excerpt,
        tags=tags,
        overwrite_draft=overwrite_draft,
    )
    write_report_manifest(report, post, admin_client.get_editor_url(post))
    return post
