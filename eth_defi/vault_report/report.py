"""Generate the monthly vault report bundle and publish it as a Ghost draft.

Pipeline:

1. :py:func:`eth_defi.vault_report.data.fetch_vault_report_data` loads vault metrics and prices
2. :py:func:`generate_monthly_vault_report` ranks vaults, renders tables,
   branded charts and the social hero image, and writes a local output bundle
3. :py:func:`publish_report_draft` uploads the images to Ghost and creates a
   draft post, which the editor completes in Ghost Admin

Output bundle layout::

    {output_dir}/
        post.html          Ghost post body, charts referenced by relative paths
        preview.html       Standalone page for reviewing the report in a browser
        report.json        Title, slug, statistics, rankings and file listing
        hero.png           1200×630 social image, used as the Ghost feature image
        charts/*.png       Branded chart images
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
from eth_defi.vault_report.benchmarks import calculate_treasury_bill_rolling_returns, fetch_treasury_bill_yields, get_latest_yield
from eth_defi.vault_report.branding import compose_chart_panel, render_hero_image
from eth_defi.vault_report.charts import (
    calculate_rolling_returns,
    create_chain_yield_figure,
    create_correlation_figure,
    create_movers_figure,
    create_protocol_tvl_figure,
    create_risk_return_figure,
    create_rolling_returns_figure,
    make_vault_label,
    render_figure_png,
)
from eth_defi.vault_report.data import VaultReportData, calculate_daily_share_prices, fetch_available_sparklines, read_vault_share_prices, read_vault_tvl_history
from eth_defi.vault_report.ghost import GhostAdminClient, GhostPost
from eth_defi.vault_report.logos import fetch_chain_logo_uri, load_protocol_logo_uri, load_watermark_logo_uri
from eth_defi.vault_report.movers import RankChange, calculate_rank_changes, parse_ranked_vault_links, resolve_vault_id
from eth_defi.vault_report.post import PostContext, build_post_html, build_preview_html, make_month_label, make_report_slug, make_report_title
from eth_defi.vault_report.sections import (
    CHAIN_TABLE_COLUMNS,
    CORRELATION_TABLE_COLUMNS,
    UNKNOWN_PROTOCOL_SLUG,
    ReportCriteria,
    ReportSection,
    calculate_chain_yields,
    calculate_protocol_tvl_history,
    filter_eligible_vaults,
    render_section_table,
    select_best_vaults,
    select_new_vaults,
    select_perp_dex_vaults,
    select_tvl_history_vaults,
    select_vaults_by_chain,
)
from eth_defi.vault_report.theme import DARK_THEME, ChartTheme

logger = logging.getLogger(__name__)

#: Price history read for charts: 180 days of chart history plus the 90-day rolling window
PRICE_HISTORY = datetime.timedelta(days=280)

#: History shown in the TVL by protocol chart
TVL_HISTORY = datetime.timedelta(days=365)

#: Number of vaults compared in the movers chart
MOVERS_TOP_N = 20

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

    #: Section key -> ranked vault ids, stored for next month's movers chart
    rankings: dict[str, list[str]] = field(default_factory=dict)


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
            "The dashed line is the current 3-month US Treasury bill yield",
        ],
        "protocol_tvl": [
            "Weekly total value locked in stablecoin vaults over the last year, by vault protocol",
            "Excludes blacklisted vaults and vaults with abnormal TVL data",
            live.format(url="https://tradingstrategy.ai/trading-view/vaults/historical-tvl-protocol?history=1y"),
        ],
        "best": [
            live.format(url="https://tradingstrategy.ai/trading-view/vaults"),
            "Vaults are ranked by their annualised last one-month returns, net of fees (n) when fee data is available and gross (g) otherwise",
            f"The charts show three-month rolling returns for the top vaults, and for the top low-volatility vaults, against the 3-month US Treasury bill. Vaults with over {criteria.chart_max_return:.0%} annualised returns are left out for readability",
            f"Minimum {format_usd(criteria.min_tvl)} TVL",
            yield_vaults,
        ],
        "movers": [
            f"How the top {MOVERS_TOP_N} vaults changed since the previous report, among the vaults eligible for the list above",
        ],
        "risk_return": [
            "Each bubble is a vault from the best-performing vaults universe; bubble area shows TVL",
            f"Annualised three-month returns are clipped at {criteria.chart_max_return:.0%}; volatility is on a log scale",
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


def calculate_movers(previous: GhostPost | None, vaults_df: pd.DataFrame, ranking: list[str]) -> list[RankChange]:
    """Compare the current best-performing vaults ranking with the previous report.

    :param previous:
        Previous report post, or ``None``.

    :param vaults_df:
        All vault metrics, to resolve previous vault links.

    :param ranking:
        Current ranking universe, best first, not truncated.

    :return:
        Rank changes of the current top list, or an empty list without a usable previous ranking.
    """
    if previous is None or not previous.html:
        return []
    links = parse_ranked_vault_links(previous.html, "the-best-performing-vaults")
    previous_ids = [resolve_vault_id(link, vaults_df) for link in links]
    if not any(previous_ids):
        logger.warning("Could not resolve any vault of the previous report %s", previous.slug)
        return []
    changes, dropped = calculate_rank_changes(previous_ids, ranking, top_n=MOVERS_TOP_N)
    logger.info("%d vaults dropped out of the top %d: %s", len(dropped), MOVERS_TOP_N, dropped)
    return changes


def render_report_charts(
    data: VaultReportData,
    eligible_df: pd.DataFrame,
    sections: dict[str, ReportSection],
    criteria: ReportCriteria,
    theme: ChartTheme,
    output_dir: Path,
    cache_dir: Path,
    previous: GhostPost | None = None,
) -> tuple[dict[str, Path], Path]:
    """Render all branded report charts and the hero image.

    Optional inputs (logos, the Treasury bill benchmark, the previous report)
    degrade gracefully: a missing input leaves the element out of the chart.

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

    :param previous:
        Previous report post, for the movers chart.

    :return:
        Tuple (chart key -> PNG path, hero image path).
    """
    empty = pd.DataFrame()
    data_date = data.data_end_at.strftime("%Y-%m-%d")
    watermark = load_watermark_logo_uri(theme)
    best_all = select_best_vaults(eligible_df, criteria)

    def _chartable(section_key: str) -> pd.DataFrame:
        df = sections[section_key].vaults_df if section_key in sections else empty
        return df.loc[df["one_month_cagr_best"] <= criteria.chart_max_return].head(criteria.chart_vault_count) if len(df) else df

    rolling_vaults = {"best_rolling": _chartable("best"), "low_volatility_rolling": _chartable("low_volatility")}
    correlation_df = sections["correlation"].vaults_df if "correlation" in sections else empty

    chart_ids = set(correlation_df.index).union(*(df.index for df in rolling_vaults.values()))
    daily_prices = calculate_daily_share_prices(read_vault_share_prices(data.prices_path, sorted(chart_ids), start_at=data.data_end_at - PRICE_HISTORY))
    rolling_returns = calculate_rolling_returns(daily_prices)
    if daily_prices.empty:
        logger.warning("No price data for the chart vaults in %s, leaving out the price charts", data.prices_path)
        rolling_vaults, correlation_df = {}, empty

    tbill_yields = fetch_treasury_bill_yields(cache_dir)
    tbill_latest = get_latest_yield(tbill_yields) if tbill_yields is not None else None
    tbill_rolling = calculate_treasury_bill_rolling_returns(tbill_yields, datetime.timedelta(days=90), rolling_returns.index) if tbill_yields is not None and not rolling_returns.empty else None

    protocol_logos = {vault_id: load_protocol_logo_uri(slug, theme) for vault_id, slug in eligible_df["protocol_slug"].items()}
    chain_yields = calculate_chain_yields(eligible_df, criteria)
    chain_logos = {chain: fetch_chain_logo_uri(chain, cache_dir / "logos") for chain in chain_yields.index}

    tvl_vaults = select_tvl_history_vaults(data.vaults_df)
    tvl_history = read_vault_tvl_history(data.prices_path, list(tvl_vaults.index), start_at=data.data_end_at - TVL_HISTORY)
    protocol_tvl = calculate_protocol_tvl_history(tvl_history, tvl_vaults) if not tvl_history.empty else empty
    protocol_names = tvl_vaults.drop_duplicates("protocol").set_index("protocol")["protocol_slug"]

    figures = {
        "chain_yields": (
            create_chain_yield_figure(chain_yields, theme, chain_logos, tbill_latest, watermark),
            ChartPanel("Average stablecoin vault yield by blockchain", "TVL-weighted annualised one-month return, compared to the 3-month US Treasury bill", "tradingstrategy.ai/trading-view/vaults/chains"),
        ),
    }
    if len(protocol_tvl):
        figures["protocol_tvl"] = (
            create_protocol_tvl_figure(protocol_tvl, theme, {name: load_protocol_logo_uri(protocol_names.get(name), theme) for name in protocol_tvl.columns}, watermark),
            ChartPanel("Stablecoin vault TVL by protocol", "Weekly total value locked over the last 12 months", "tradingstrategy.ai/trading-view/vaults/historical-tvl-protocol"),
        )
    panels = {
        "best_rolling": ChartPanel("3M rolling returns, best-performing vaults", f"Top {criteria.chart_vault_count} stablecoin yield vaults by 1M return, not annualised, against the US Treasury bill", "tradingstrategy.ai/trading-view/vaults"),
        "low_volatility_rolling": ChartPanel("3M rolling returns, low-volatility vaults", f"Top {criteria.chart_vault_count} vaults with under {criteria.low_volatility_threshold:.1%} annualised volatility, not annualised", "tradingstrategy.ai/trading-view/vaults"),
    }
    for key, df in rolling_vaults.items():
        if len(df):
            labels = {vault_id: make_vault_label(row) for vault_id, row in df.iterrows()}
            figures[key] = (create_rolling_returns_figure(rolling_returns, labels, theme, protocol_logos, tbill_rolling, watermark), panels[key])

    figures["risk_return"] = (
        create_risk_return_figure(best_all, {tag: category.get("label", tag) for tag, category in data.categories.items()}, theme, criteria.chart_max_return, tbill_latest, watermark),
        ChartPanel("Risk and return of stablecoin yield vaults", f"{len(best_all)} vaults with at least {format_usd(criteria.min_tvl)} TVL, bubble area shows TVL", "tradingstrategy.ai/trading-view/vaults/yield-risk"),
    )

    changes = calculate_movers(previous, data.vaults_df, list(best_all.index))
    if changes and previous is not None and previous.published_at is not None:
        names = {vault_id: best_all.loc[vault_id, "name"] or vault_id for vault_id in (c.vault_id for c in changes)}
        figures["movers"] = (
            create_movers_figure(changes, names, theme, previous.published_at.strftime("%B %Y"), make_month_label(data.data_end_at), protocol_logos),
            ChartPanel(f"Top {MOVERS_TOP_N} movers", f"Rank changes since the {previous.published_at.strftime('%B %Y')} report", "tradingstrategy.ai/trading-view/vaults"),
        )

    if len(correlation_df):
        figures["correlation"] = (
            create_correlation_figure(daily_prices, {vault_id: row["name"] or row["address"] for vault_id, row in correlation_df.iterrows()}, theme),
            ChartPanel("Vault daily returns correlation", "Last 90 days, top vaults by 3M return, at most two per protocol", "tradingstrategy.ai/trading-view/vaults"),
        )

    chart_dir = output_dir / "charts"
    chart_paths = {}
    for key, (fig, panel) in tqdm(figures.items(), desc="Rendering charts"):
        path = render_figure_png(fig, chart_dir / f"{key}.png")
        chart_paths[key] = compose_chart_panel(path, theme, panel.title, panel.subtitle, f"Data {data_date}", panel.link, path)

    hero_vaults = _chartable("best").head(5)
    hero_subtitle = f"1M annualised return · stablecoin yield vaults with ≥ {format_usd(criteria.min_tvl)} TVL · data {data_date}"
    hero_path = render_hero_image(hero_vaults, make_month_label(data.data_end_at), hero_subtitle, theme, output_dir / "hero.png")
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
        The previous report post, for continuity and the movers chart.

    :param changelog_entries:
        Changelog entries to offer to the editor.

    :param render_charts:
        Render PNG charts and the hero image. Needs Chrome for Kaleido.

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
    (output_dir / "tables").mkdir(parents=True, exist_ok=True)

    data_end_at = data.data_end_at
    eligible_df = filter_eligible_vaults(data.vaults_df, data_end_at, criteria)
    sections = build_report_sections(eligible_df, criteria)
    if check_sparklines:
        sparkline_ids = frozenset(fetch_available_sparklines([vault_id for section in sections.values() for vault_id in section.vaults_df.index]))
        sections = {key: dataclasses.replace(section, sparkline_ids=sparkline_ids) for key, section in sections.items()}

    tables = {}
    for key, section in sections.items():
        tables[key] = render_section_table(section)
        (output_dir / "tables" / f"{key}.html").write_text(tables[key])
        section.vaults_df[CSV_COLUMNS].to_csv(output_dir / "tables" / f"{key}.csv", index=False)

    chart_paths, hero_path = render_report_charts(data, eligible_df, sections, criteria, theme, output_dir, cache_dir, previous) if render_charts else ({}, None)

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
        hero_path=hero_path,
        rankings={"best": list(select_best_vaults(eligible_df, criteria).index)},
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
        "rankings": report.rankings,
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
        overwrite_draft=overwrite_draft,
    )
    write_report_manifest(report, post, admin_client.get_editor_url(post))
    return post
