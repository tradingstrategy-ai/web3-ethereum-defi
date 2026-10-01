"""Generate the monthly vault report bundle and publish it as a Ghost draft.

This module is the orchestration layer of the monthly "The best-performing
stablecoin vaults" blog post. It ties together the data loading
(:py:mod:`~eth_defi.vault_report.data`), the vault selection
(:py:mod:`~eth_defi.vault_report.sections`), the investability check
(:py:mod:`~eth_defi.vault_report.vault_checks`), the chart rendering
(:py:mod:`~eth_defi.vault_report.charts`, :py:mod:`~eth_defi.vault_report.branding`),
the post templates (:py:mod:`~eth_defi.vault_report.post`) and the Ghost
client (:py:mod:`~eth_defi.vault_report.ghost`). It is driven by
``scripts/erc-4626/generate-monthly-vault-report.py``.

Pipeline:

1. :py:func:`eth_defi.vault_report.data.fetch_vault_report_data` loads the
   top vaults export (the same JSON the website's vault dashboard renders, so
   the report numbers match the website) and the cleaned vault price Parquet
2. :py:func:`generate_monthly_vault_report` filters and ranks the vaults,
   optionally runs the investability check, renders the tables, branded
   charts and the social hero images, and writes a local output bundle
3. :py:func:`publish_report_draft` uploads the images to Ghost and creates or
   replaces an unpublished draft post, which the editor completes and
   publishes by hand in Ghost Admin

The generation and the publishing are separate steps so the bundle can be
reviewed locally (``preview.html``, ``GHOST_DRAFT=false``) without Ghost
credentials, and so a failed upload never forces the minutes-long check and
rendering to run again.

The post structure and the editorial decisions behind it are described in
``eth_defi/vault_report/README-blog-post-outline.md``; the operator workflow
and the Ghost draft safety rules in ``README-vault-report.md`` and
``README-best-vaults-news.md``.

Output bundle layout::

    {output_dir}/
        post.html          Ghost post body, charts referenced by relative paths
        preview.html       Standalone page for reviewing the report in a browser
        report.json        Title, slug, statistics, changelog candidates, check summary and Ghost draft link
        hero.png           1200×630 social image, a ready-made feature image for the editor
        hero-square.png    1080×1080 social image for X
        charts/*.png       Branded chart images
        tables/*.html      Individual tables
        tables/*.csv       Raw metrics of the listed vaults
        tables/excluded.csv               Vaults the investability check left out, when it ran
        {date}-excluded-vaults.md         Dated record of the excluded vaults, when the check ran
        vault-check-*                     Investability check round files, when the check ran
        podcasts/*.png     Podcast guest logo tiles and listening service icons
        cache/             Logo and benchmark download cache, unless ``cache_dir`` is given
"""

import dataclasses
import datetime
import functools
import json
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

import pandas as pd
from tqdm_loggable.auto import tqdm

from eth_defi.perp_dex.parquet import PERP_DEX_NATIVE_CHAIN_IDS
from eth_defi.research.vault_metrics import USDollarAmount
from eth_defi.vault_report.benchmarks import fetch_benchmark_indices, fetch_treasury_bill_yields, get_latest_yield, select_benchmarks
from eth_defi.vault_report.branding import CHART_SCALE, HERO_SIZE, SQUARE_HERO_SIZE, compose_chart_panel, render_hero_image, render_logo_tile
from eth_defi.vault_report.charts import (
    PerformanceSeries,
    VaultProperty,
    chart_renderer,
    create_average_yield_figure,
    create_chain_best_figure,
    create_performance_figure,
    create_protocol_tvl_figure,
    create_risk_return_figure,
    create_tvl_change_figure,
    rasterise_logos,
    render_figure_png,
    select_moving_vaults,
    trim_logos,
)
from eth_defi.vault_report.data import VaultReportData, calculate_daily_share_prices, fetch_available_sparklines, read_vault_share_prices, read_vault_tvl_history
from eth_defi.vault_report.ghost import DraftRecord, GhostAdminClient, GhostPost, fingerprint_post_text
from eth_defi.vault_report.logos import fetch_chain_logo_uri, load_benchmark_logo_uri, load_protocol_logo_path, load_protocol_logo_uri
from eth_defi.vault_report.podcasts import PODCAST_SERVICES, PodcastEpisode, icon_image_key, logo_image_key
from eth_defi.vault_report.post import BEST_SECTIONS, NEW_VAULTS_CHART, BestSection, PostContext, build_post_html, build_preview_html, make_month_label, make_report_slug, make_report_title
from eth_defi.vault_report.sections import (
    CHAIN_TABLE_COLUMNS,
    CHART_RETURN,
    TOKENISED_FUND,
    ReportCriteria,
    ReportSection,
    calculate_chain_tvl_changes,
    calculate_chain_tvl_history,
    calculate_chain_yields,
    calculate_fund_nav_history,
    calculate_high_yield_protocols,
    calculate_protocol_tvl_history,
    calculate_protocol_yields,
    calculate_tvl_changes,
    exclude_amm_pools,
    exclude_chart_risks,
    filter_eligible_vaults,
    group_min_tvl,
    rank_vaults,
    render_section_table,
    select_average_yield_vaults,
    select_chain_chart_vaults,
    select_comparable_vaults,
    select_group,
    select_new_vaults,
    select_risk_return_vaults,
    select_tvl_history_vaults,
    select_vaults_by_chain,
    select_yield_vaults,
)
from eth_defi.vault_report.theme import ASSETS_DIR, DARK_THEME, ChartTheme
from eth_defi.vault_report.vault_checks import CheckResult, VaultCheckSettings, apply_check_decisions, candidate_depth, excluded_rows, render_excluded_vaults_markdown, run_vault_checks, summarise_checks

logger = logging.getLogger(__name__)

#: Period of the performance charts and hero sparklines
PERFORMANCE_WINDOW = datetime.timedelta(days=90)

#: Rolling window of the Sharpe ratio chart
SHARPE_WINDOW = datetime.timedelta(days=90)

#: Price history read for charts: the performance window, the Sharpe ratio window before it, and a margin
PRICE_HISTORY = PERFORMANCE_WINDOW + SHARPE_WINDOW + datetime.timedelta(days=10)

#: Vaults in the hero image
HERO_VAULTS = 5

#: History shown in the TVL by protocol, TVL by blockchain and NAV by tokenised fund charts
TVL_HISTORY = datetime.timedelta(days=365)


#: Raw metrics columns written to the per-section CSV files.
#:
#: The HTML tables show rounded, formatted values; the CSV files keep the
#: exact export values (both net and gross returns, current and peak TVL,
#: risk) so the editor and reviewers can check a surprising ranking without
#: opening the top vaults JSON. Columns come from
#: :py:func:`eth_defi.vault_report.data.prepare_vault_metrics` and
#: :py:func:`eth_defi.vault_report.sections.classify_vault` (``group``).
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
    """Header and footer texts of a branded chart panel.

    The Plotly figures carry no titles of their own: Kaleido renders only the
    plot, and :py:func:`eth_defi.vault_report.branding.compose_chart_panel`
    draws these texts with Pillow into the branded frame, so every chart in
    the post has the same header, footer and margins whatever its Plotly
    layout. Following the writing rules in ``README-blog-post-outline.md``,
    the subtitle carries the selection rules that the title, axes and legend
    do not show, which is why most sections need no criteria notes.
    """

    #: Panel title, heading case
    title: str

    #: One-line description of the data
    subtitle: str

    #: Live chart page shown in the footer, without ``https://``
    link: str


@dataclass(slots=True)
class GeneratedReport:
    """Result of :py:func:`generate_monthly_vault_report`.

    Describes the local bundle and keeps the rendering inputs, so
    :py:func:`publish_report_draft` can render the same post again with the
    uploaded Ghost image URLs instead of the bundle's relative paths, without
    repeating the data processing, the investability check or the chart
    rendering. The command line script also reads :py:attr:`sections` and
    :py:attr:`vault_checks` for its summary tables.
    """

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

    #: Investability check result, or ``None`` when the check did not run
    vault_checks: CheckResult | None = None

    #: Podcast guest logos and service icons -> PNG path in the bundle,
    #: keyed like :py:attr:`eth_defi.vault_report.post.PostContext.podcast_images`
    podcast_image_paths: dict[str, Path] = field(default_factory=dict)

    #: Dated Markdown record of the vaults the investability check left out, or ``None`` without the check
    excluded_vaults_path: Path | None = None

    #: Changelog entries since the previous report, offered to the editor in ``report.json``
    changelog_entries: list[str] = field(default_factory=list)


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

    The bullets open the *Report content updates* section of the post, where
    :py:func:`eth_defi.vault_report.post.build_post_html` bolds their figures.
    The blockchain, protocol and vault counts describe the whole export, so
    readers see the coverage of the dataset; the TVL is summed over the
    eligible vaults only, so stale, broken and blacklisted vaults do not inflate it.

    :param vaults_df:
        All vault metrics in the top vaults export,
        :py:attr:`eth_defi.vault_report.data.VaultReportData.vaults_df`.
        Needs the ``chain``, ``protocol_identified`` and ``protocol_slug`` columns.

    :param eligible_df:
        Vaults eligible for listings, output of
        :py:func:`eth_defi.vault_report.sections.filter_eligible_vaults`.
        Needs the ``current_nav`` column, in USD.

    :param data_end_at:
        Report data date.

    :return:
        Plain text bullet points.
    """
    # Plain counts for readers, without qualifiers such as "identified" or "not blacklisted",
    # and no list of denomination stablecoins; see the writing rules in README-blog-post-outline.md.
    # Generic ERC-4626 and unknown protocols are not counted as protocols, but their vaults are counted.
    protocol_count = vaults_df.loc[vaults_df["protocol_identified"], "protocol_slug"].nunique()
    return [
        f"{vaults_df['chain'].nunique()} blockchains and {protocol_count} vault protocols",
        f"The report data is dated {data_end_at.strftime('%Y-%m-%d')}",
        f"{len(vaults_df):,} stablecoin-denominated vaults, of which {len(eligible_df):,} have up-to-date data",
        f"Combined TVL of the vaults is {format_usd(eligible_df['current_nav'].sum())}",
    ]


def make_vault_properties(vault: pd.Series, theme: ChartTheme, chain_logo: Callable[[str], str | None]) -> tuple[VaultProperty, ...]:
    """List the curator, protocol and chain shown under a vault name in the charts.

    The curator is left out when the vault has none or when it is the
    protocol itself, e.g. a protocol curating its own vaults, and the chain
    when it belongs to the protocol: a native perp DEX chain such as
    Hyperliquid's Hypercore, or a chain with the protocol's name. Protocol and
    curator logos come from the vault metadata, chain logos from the website.

    :param vault:
        Vault metrics row with ``curator_name``, ``curator_slug``, ``protocol_label``,
        ``protocol_slug``, ``protocol_identified``, ``chain`` and ``chain_id``.

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
    # Curator columns are NaN for uncurated vaults; a curator matching the protocol by slug or
    # by name, e.g. a protocol curating its own vaults, would repeat the same label and logo
    if isinstance(curator, str) and curator.strip() and curator_slug != vault["protocol_slug"] and curator.strip().lower() != protocol.lower():
        properties.append(VaultProperty(curator.strip(), load_protocol_logo_uri(curator_slug if isinstance(curator_slug, str) else None, theme)))
    properties.append(VaultProperty(protocol, load_protocol_logo_uri(vault["protocol_slug"], theme) if vault["protocol_identified"] else None))
    # A native perp DEX chain belongs to its protocol, e.g. Hyperliquid on Hypercore or GRVT on GRVT, so the protocol is shown once
    if vault["chain_id"] not in PERP_DEX_NATIVE_CHAIN_IDS and vault["chain"].strip().lower() != protocol.lower():
        properties.append(VaultProperty(vault["chain"], chain_logo(vault["chain"])))
    return tuple(properties)


def make_performance_panel(section: BestSection, criteria: ReportCriteria) -> ChartPanel:
    """Header and footer texts of a best-performing section's chart.

    :param section:
        Best-performing section.

    :param criteria:
        Report thresholds.

    :return:
        Chart panel texts. The subtitle adds only what the title, axes and
        legend do not show: the ranking, the minimum TVL and, for equity
        curves, that the legend returns are annualised, because the legend
        shows them without a unit label.
    """
    subtitle = f"{section.ranked_by[0].upper()}{section.ranked_by[1:]}, at least {format_usd(group_min_tvl(section.group, criteria))} TVL"
    if section.measure == "equity":
        subtitle += ", returns annualised"
    return ChartPanel(f"Performance of {section.subject}", subtitle, section.link)


def make_criteria_notes(criteria: ReportCriteria) -> dict[str, list[str]]:
    """Describe the selection criteria of the tables for readers.

    Only *The best-performing vaults* has notes: how its tables rank vaults,
    their TVL minimums and a link to the live ranking. Every other section
    relies on its introduction, chart title and subtitle; see the writing
    rules in ``README-blog-post-outline.md`` before adding a note.

    The TVL minimums and the activity requirement are formatted from
    ``criteria``, so the notes cannot drift from the thresholds
    :py:func:`eth_defi.vault_report.sections.select_group` and
    :py:func:`eth_defi.vault_report.sections.select_new_vaults` apply.

    :param criteria:
        Report thresholds.

    :return:
        Section key -> bullet point HTML strings. The strings are inserted
        into the post unescaped, so they may contain links but must not
        contain data from the export.
    """
    active = f"at least {criteria.min_events} deposit and redemption events"
    return {
        "best": [
            "Vaults are ranked by their annualised last one-month returns",
            f"Minimum {format_usd(criteria.min_tvl)} TVL in every table, {format_usd(criteria.amm_min_tvl)} for AMM pools and {format_usd(criteria.new_vault_min_tvl)} for new vaults; lending, RWA and new vaults also need {active}",
            '<a href="https://tradingstrategy.ai/vaults">View the live vault ranking</a>',
        ],
    }


def collect_top_lists(comparable_df: pd.DataFrame, criteria: ReportCriteria) -> dict[str, pd.DataFrame]:
    """Collect every top list of the report, with a buffer, for the investability check.

    The investability check must see every vault a reader could see in a
    ranking, so this function mirrors the selectors and ranking metrics of
    the tables (:py:func:`build_report_sections`) and the charts
    (:py:func:`render_report_charts`) rather than keeping its own candidate
    rules, which would drift. Each list is extended by
    :py:attr:`ReportCriteria.check_buffer_ratio`, so enough checked vaults
    remain to fill the lists after exclusions. When a selector changes in
    one of those functions, change it here too;
    ``test_check_candidates_follow_report_selectors`` guards the match.

    :py:func:`eth_defi.vault_report.vault_checks.run_vault_checks` calls this
    once per check round with the vaults excluded so far dropped, so the next
    round checks the vaults that move up into the lists.

    :param comparable_df:
        Eligible vaults with an identified protocol, excluded vaults already dropped.

    :param criteria:
        Report thresholds.

    :return:
        List name -> ranked vaults, e.g. ``table:lending`` or ``chart:hero``.
        The names appear in the check's candidate files, telling the agent and
        the reviewer where a vault would be shown.
    """
    ratio = criteria.check_buffer_ratio
    table_depth = candidate_depth(criteria.top_n, ratio)
    chart_depth = candidate_depth(criteria.performance_chart_vaults, ratio)
    chain_depth = candidate_depth(criteria.chain_top_n, ratio)
    # AMM pools are ranked only in their own best-performing section, see build_report_sections()
    ranked_df = exclude_amm_pools(comparable_df, criteria)
    lists = {}
    for section in BEST_SECTIONS:
        lists[f"table:{section.key}"] = select_group(comparable_df, criteria, section.group, by=section.metric).head(table_depth)
        lists[f"chart:{section.key}"] = select_performance_chart_vaults(comparable_df, criteria, section, chart_depth)
    lists["table:new"] = select_new_vaults(ranked_df, dataclasses.replace(criteria, top_n=table_depth))
    # Same rule as select_vaults_by_chain(), but deeper per chain for the buffer
    by_chain = rank_vaults(ranked_df.loc[ranked_df["current_nav"] >= criteria.chain_min_tvl])
    lists["table:by_chain"] = by_chain.groupby("chain", sort=False).head(chain_depth)
    lists["chart:by_chain_best"] = select_chain_chart_vaults(ranked_df, dataclasses.replace(criteria, chain_top_n=chain_depth))
    lists["chart:hero"] = select_hero_vaults(select_yield_vaults(ranked_df, criteria), criteria, candidate_depth(HERO_VAULTS, ratio))
    return lists


def select_performance_chart_vaults(comparable_df: pd.DataFrame, criteria: ReportCriteria, section: BestSection, depth: int) -> pd.DataFrame:
    """Select the vaults of a best-performing section's chart.

    Charts rank by :py:attr:`~eth_defi.vault_report.post.BestSection.chart_metric`,
    the annualised three-month return, which is steadier than the tables'
    one-month ranking, and leave out Dangerous and worse vaults. The
    investability check and the renderer both use this selection.

    :param comparable_df:
        Eligible vaults with an identified protocol, excluded vaults already dropped.

    :param criteria:
        Report thresholds.

    :param section:
        Best-performing section.

    :param depth:
        Number of vaults.

    :return:
        Chart vaults, best first.
    """
    return select_group(comparable_df, criteria, section.group, by=section.chart_metric).pipe(exclude_chart_risks, criteria).head(depth)


def select_new_chart_vaults(new_vaults_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Select the vaults of the new vaults chart.

    The top of the new vaults table, in its order, leaving out Dangerous and
    worse vaults like every chart. Picking from the table, which the
    investability check already covers, needs no check list of its own.

    :param new_vaults_df:
        The new vaults table, see :py:func:`eth_defi.vault_report.sections.select_new_vaults`.

    :param criteria:
        Report thresholds.

    :return:
        Chart vaults, best first.
    """
    return exclude_chart_risks(new_vaults_df, criteria).head(criteria.performance_chart_vaults)


def make_new_vaults_panel(criteria: ReportCriteria) -> ChartPanel:
    """Header and footer texts of the new vaults chart.

    The subtitle carries the selection rules, so the section needs no notes.

    :param criteria:
        Report thresholds.

    :return:
        Chart panel texts.
    """
    subtitle = f"Launched in the last {criteria.new_vault_max_age.days} days, by 1M return, at least {format_usd(criteria.new_vault_min_tvl)} TVL, returns annualised"
    return ChartPanel("Performance of the best-performing new vaults", subtitle, "tradingstrategy.ai/vaults/new-vaults")


def select_hero_vaults(yield_universe: pd.DataFrame, criteria: ReportCriteria, depth: int = 5) -> pd.DataFrame:
    """Select the vaults of the hero image.

    The top stablecoin yield vaults by annualised three-month return, leaving
    out outliers above :py:attr:`ReportCriteria.chart_max_return`, volatile
    vaults above :py:attr:`ReportCriteria.hero_max_volatility` and Dangerous
    and worse vaults.

    The hero image is the social preview of the post and a ready-made feature
    image, seen by people who never open the post and its tables. It
    therefore shows only calm, stablecoin-yield-like vaults: the filters keep
    one-off return spikes and trading vaults off the image, which does not
    state them; its footer shows only the minimum TVL and the data date. The
    investability check covers this list as ``chart:hero``, see
    :py:func:`collect_top_lists`.

    :param yield_universe:
        Output of :py:func:`eth_defi.vault_report.sections.select_yield_vaults`.

    :param criteria:
        Report thresholds.

    :param depth:
        Number of vaults.

    :return:
        Hero vaults, best first.
    """
    candidates = yield_universe.loc[(yield_universe[CHART_RETURN] <= criteria.chart_max_return) & (yield_universe["three_months_volatility"] <= criteria.hero_max_volatility)]
    return rank_vaults(candidates, CHART_RETURN).pipe(exclude_chart_risks, criteria).head(depth)


def run_report_checks(comparable_df: pd.DataFrame, data: VaultReportData, output_dir: Path, settings: VaultCheckSettings, criteria: ReportCriteria) -> CheckResult:
    """Run the investability check on the report's top lists and aggregate charts.

    Some top-ranking vaults are not investable in practice, e.g. a Morpho
    vault lending against a token with no market, and deciding that needs
    research outside the export, so an LLM agent runs the
    ``check-top-list-vaults`` skill, see
    :py:func:`eth_defi.vault_report.vault_checks.run_vault_checks` and the
    *Investability check* section of ``README-vault-report.md``.

    This wrapper binds the report's own list selection,
    :py:func:`collect_top_lists`, and passes the vaults of the average yield
    charts as ``aggregate_df``: those charts draw every vault as a dot, too
    many to research one by one, so their largest in-scope vaults are
    prescreened with onchain probes and only the vaults that raise signals
    go to the agent.

    :param comparable_df:
        Eligible vaults with an identified protocol.

    :param data:
        Report data, for the data date and the price file.

    :param output_dir:
        Report bundle; the check files are written here.

    :param settings:
        Agent and reuse settings.

    :param criteria:
        Report thresholds, including the ``check_*`` round limits.

    :return:
        Decisions of all rounds.
    """
    return run_vault_checks(
        functools.partial(collect_top_lists, criteria=criteria),
        comparable_df,
        data.data_end_at,
        data.prices_path,
        output_dir,
        settings,
        max_rounds=criteria.check_max_rounds,
        aggregate_df=select_average_yield_vaults(exclude_amm_pools(comparable_df, criteria), criteria),
        prescreen_min_tvl=criteria.check_prescreen_min_tvl,
        max_escalations=criteria.check_max_escalations,
    )


def log_check_summary(result: CheckResult | None) -> None:
    """Tell the operator what the investability check left for the editor.

    The post has no editor callouts, so the check's outcome goes to the log;
    the dated excluded vaults file records it for the editor.

    :param result:
        Check result, or ``None`` when the check did not run.
    """
    if result is None:
        logger.warning("The report was generated without the investability check; run it with VAULT_CHECK_AGENT before publishing, see README-vault-report.md")
        return
    logger.info("The investability check left %d vaults out of the post", len(result.excluded))
    if result.uncertain:
        logger.warning("The check could not decide on %d vaults, which stay in the report; resolve them before publishing: %s", len(result.uncertain), ", ".join(candidate.name for candidate in result.uncertain))


def build_report_sections(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> dict[str, ReportSection]:
    """Select vaults for all report tables.

    One table per best-performing vault group in
    :py:data:`~eth_defi.vault_report.post.BEST_SECTIONS`, ranked by the
    group's :py:attr:`~eth_defi.vault_report.post.BestSection.metric`, plus
    the *New vaults* and *Vaults on each chain* tables. The keys match
    :py:attr:`eth_defi.vault_report.post.SectionTemplate.key`, so the post
    template places each table under its heading. The investability check
    selects its candidates with the same rules, see :py:func:`collect_top_lists`.

    :param eligible_df:
        Output of :py:func:`eth_defi.vault_report.sections.select_comparable_vaults`:
        eligible vaults with an identified protocol, with the vaults the
        investability check excluded already dropped.

    :param criteria:
        Report thresholds.

    :return:
        Section key -> section. Sections without vaults are omitted, which
        leaves their headings out of the post.
    """
    sections = {section.key: ReportSection(select_group(eligible_df, criteria, section.group, by=section.metric).head(criteria.top_n)) for section in BEST_SECTIONS}
    # AMM pools move with the prices of their pooled crypto, so they are ranked only in their own
    # section unless ReportCriteria.include_amm_pools is set
    ranked_df = exclude_amm_pools(eligible_df, criteria)
    sections["new"] = ReportSection(select_new_vaults(ranked_df, criteria))
    sections["by_chain"] = ReportSection(select_vaults_by_chain(ranked_df, criteria), CHAIN_TABLE_COLUMNS, numbered=False)
    for key, section in sections.items():
        logger.info("Section %s: %d vaults", key, len(section.vaults_df))
    return {key: section for key, section in sections.items() if len(section.vaults_df) > 0}


def render_report_charts(
    data: VaultReportData,
    eligible_df: pd.DataFrame,
    sections: dict[str, ReportSection],
    criteria: ReportCriteria,
    theme: ChartTheme,
    output_dir: Path,
    cache_dir: Path,
    tbill_yields: pd.Series | None = None,
    excluded: frozenset[str] = frozenset(),
) -> tuple[dict[str, Path], Path]:
    """Render all branded report charts and the hero images.

    Builds every chart of the post as a Plotly figure with its
    :py:class:`ChartPanel` texts, then renders them in one batch: Kaleido
    (headless Chrome, kept running by
    :py:func:`eth_defi.vault_report.charts.chart_renderer` in the caller)
    rasterises each plot, and
    :py:func:`eth_defi.vault_report.branding.compose_chart_panel` frames it
    with the brand header, footer, data date and live chart link. The chart
    keys must match the chart keys of
    :py:data:`eth_defi.vault_report.post.SECTION_TEMPLATES`, which place each
    chart under its post heading:

    - ``{section}_performance`` and ``new_performance``: 90-day equity curves,
      or the rolling Sharpe ratio, of the best-performing groups and new
      vaults against their benchmarks
    - ``by_chain_best``: the two best vaults on each chain
    - ``chain_yields``, ``protocol_yields``, ``protocol_high_yields``: the
      *Yield by chain and protocol* dot plots against the US T-bill
    - ``risk_return``: the volatility and return scatter
    - ``protocol_tvl``, ``chain_tvl``, ``fund_nav``: 12-month weekly TVL and NAV
    - ``tvl_changes``, ``chain_tvl_changes``: the 30-day inflows and outflows

    The vault sets differ on purpose, following ``README-blog-post-outline.md``:
    performance comparisons use only vaults with an identified protocol that
    passed the investability check, and leave out Dangerous and worse risk
    ratings, while the TVL totals use every eligible, non-blacklisted vault so
    they match the website's TVL charts.

    Data comes from the top vaults export (``eligible_df``, ``data.vaults_df``)
    for the metrics and from the cleaned price Parquet (``data.prices_path``)
    for the share price and TVL histories. The Parquet is about 250 MB, so
    only the chart vaults' rows are read. Optional inputs (logos, BTC and ETH
    benchmarks, the T-bill yield) degrade gracefully: a missing input leaves
    the element out of the chart, so a third-party outage does not stop the
    monthly report. A chart without data is left out, and the post template
    then leaves out its section.

    :param data:
        Report data, for the price file, the data date, the full vault list
        and the strategy category labels.

    :param eligible_df:
        Eligible vaults, output of
        :py:func:`eth_defi.vault_report.sections.filter_eligible_vaults`,
        before the investability check: this function applies ``excluded``
        itself, because the TVL totals keep the excluded vaults.

    :param sections:
        Output of :py:func:`build_report_sections`. Decides which performance
        charts are drawn, and the new vaults chart is picked from the
        ``new`` table.

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

    :param excluded:
        Vaults the investability check excluded from all rankings and charts.

    :return:
        Tuple (chart key -> PNG path, hero image path). The square hero image is
        written next to the hero image as ``hero-square.png``.
    """
    empty = pd.DataFrame()
    data_date = data.data_end_at.strftime("%Y-%m-%d")
    month_label = make_month_label(data.data_end_at)
    # Performance charts compare only vaults with an identified protocol that passed the investability check;
    # TVL charts use all eligible vaults
    comparable_df = apply_check_decisions(select_comparable_vaults(eligible_df), excluded)
    # AMM pools are charted only in their own section unless included
    ranked_df = exclude_amm_pools(comparable_df, criteria)
    yield_universe = select_yield_vaults(ranked_df, criteria)
    # The yield charts group vaults by protocol label; this maps a label back to its logo slug
    protocol_slugs = eligible_df.drop_duplicates("protocol").set_index("protocol")["protocol_slug"]

    performance_vaults = {section: select_performance_chart_vaults(comparable_df, criteria, section, criteria.performance_chart_vaults) for section in BEST_SECTIONS if section.key in sections}
    new_chart_vaults = select_new_chart_vaults(sections["new"].vaults_df, criteria) if "new" in sections else empty
    hero_vaults = select_hero_vaults(yield_universe, criteria, HERO_VAULTS)

    # One filtered Parquet read for every vault that needs a price line, instead of one read per chart
    chart_ids = set(hero_vaults.index) | set(new_chart_vaults.index) | {vault_id for df in performance_vaults.values() for vault_id in df.index}
    share_prices = read_vault_share_prices(data.prices_path, sorted(chart_ids), start_at=data.data_end_at - PRICE_HISTORY)
    # Two daily tables: interpolated prices draw smooth equity curves and sparklines for sparsely
    # updated vaults, while forward-filled prices reproduce how the export calculates its 3M Sharpe ratio
    daily_prices = calculate_daily_share_prices(share_prices)
    sharpe_prices = calculate_daily_share_prices(share_prices, interpolate=False)
    if daily_prices.empty:
        logger.warning("No price data for the chart vaults in %s, leaving out the performance charts", data.prices_path)
        performance_vaults = {}
        new_chart_vaults = empty

    # The latest T-bill yield is the risk-free reference line of the dot plots, the scatter and the per-chain chart
    tbill_latest = get_latest_yield(tbill_yields) if tbill_yields is not None else None
    benchmark_indices = fetch_benchmark_indices(data.data_end_at - PRICE_HISTORY, data.data_end_at, cache_dir, tbill_yields)

    # Like every chart, the average yields leave out Dangerous and worse vaults
    average_yield_vaults = select_average_yield_vaults(exclude_chart_risks(ranked_df, criteria), criteria)
    chain_yields = calculate_chain_yields(average_yield_vaults, criteria)
    protocol_yields = calculate_protocol_yields(average_yield_vaults, criteria)
    high_yield_protocols = calculate_high_yield_protocols(average_yield_vaults, criteria)
    chain_logo_cache: dict[str, str | None] = {}

    # Chain logos are downloaded from the website and needed by several charts and the vault
    # properties; memoise them so each chain is fetched, or fails, once per run
    def chain_logo(chain: str) -> str | None:
        if chain not in chain_logo_cache:
            chain_logo_cache[chain] = fetch_chain_logo_uri(chain, cache_dir / "logos")
        return chain_logo_cache[chain]

    chain_logos = {chain: chain_logo(chain) for chain in chain_yields.index}
    protocol_group_logos = {name: load_protocol_logo_uri(protocol_slugs.get(name), theme) for name in protocol_yields.index.union(high_yield_protocols.index)}

    # TVL history starts from the whole export, not eligible_df, and drops only blacklisted vaults,
    # like the website's historical TVL charts, so the totals match the website
    tvl_vaults = select_tvl_history_vaults(data.vaults_df)
    tvl_history = read_vault_tvl_history(data.prices_path, list(tvl_vaults.index), start_at=data.data_end_at - TVL_HISTORY, end_at=data.data_end_at)
    # Tokenised funds are offchain money market, treasury and credit funds rather than DeFi strategies:
    # they get their own NAV chart, and the DeFi TVL charts say "tokenised funds excluded"
    is_fund = tvl_vaults["group"] == TOKENISED_FUND
    defi_vaults, fund_vaults = tvl_vaults.loc[~is_fund], tvl_vaults.loc[is_fund]
    defi_history = tvl_history[tvl_history.columns.intersection(defi_vaults.index, sort=False)]
    fund_history = tvl_history[tvl_history.columns.intersection(fund_vaults.index, sort=False)]
    protocol_tvl = calculate_protocol_tvl_history(defi_history, defi_vaults) if len(defi_history.columns) else empty
    chain_tvl = calculate_chain_tvl_history(defi_history, defi_vaults) if len(defi_history.columns) else empty
    fund_nav = calculate_fund_nav_history(fund_history, fund_vaults) if len(fund_history.columns) else empty
    # Unidentified protocols are summed into "Other" by the TVL history helpers and get no logo
    tvl_vault_slugs = defi_vaults.loc[defi_vaults["protocol_identified"]].drop_duplicates("protocol").set_index("protocol")["protocol_slug"]
    # The fund NAV chart labels series by fund name, falling back to the address like the history helper
    fund_slugs = fund_vaults.assign(name=fund_vaults["name"].fillna(fund_vaults["address"])).drop_duplicates("name").set_index("name")["protocol_slug"]
    # Inflows and outflows name individual vaults, so they leave out the vaults the investability check
    # excluded; the TVL totals keep them because the money is there whether or not we recommend the vault
    flow_vaults = apply_check_decisions(eligible_df, excluded)
    tvl_changes = calculate_tvl_changes(flow_vaults, criteria)
    chain_tvl_changes = calculate_chain_tvl_changes(flow_vaults, criteria)
    # Built once for every vault any chart names (hero, performance legends, inflows and outflows),
    # so the curator, protocol and chain labels and their logos are identical across charts
    vault_properties = {vault_id: make_vault_properties(eligible_df.loc[vault_id], theme, chain_logo) for vault_id in chart_ids | set(tvl_changes.index)}
    # Logo files come with different transparent margins; trimmed logos get icon boxes of their own shape,
    # so every icon sits the same distance from its text. Logos that fail to trim keep their original URI.
    trimmed_logos = trim_logos({prop.logo_uri for properties in vault_properties.values() for prop in properties if prop.logo_uri})
    vault_properties = {vault_id: tuple(replace(prop, logo_uri=trimmed_logos[prop.logo_uri][0], logo_aspect=trimmed_logos[prop.logo_uri][1]) if prop.logo_uri in trimmed_logos else prop for prop in properties) for vault_id, properties in vault_properties.items()}

    difference = "Small dots: vaults · large dots: TVL-weighted average · right: difference to the US 3M T-bill and TVL"
    figures = {
        "chain_yields": (
            create_average_yield_figure(chain_yields, average_yield_vaults, "chain", theme, chain_logos, tbill_latest, criteria.yield_chart_max_return),
            ChartPanel(f"Stablecoin vault yield on the {criteria.yield_top_chains} largest blockchains", difference, "tradingstrategy.ai/vaults/chains"),
        ),
        "protocol_yields": (
            create_average_yield_figure(protocol_yields, average_yield_vaults, "protocol", theme, protocol_group_logos, tbill_latest, criteria.yield_chart_max_return),
            ChartPanel(f"Stablecoin vault yield of the {criteria.yield_top_protocols} largest protocols", difference, "tradingstrategy.ai/vaults/protocols"),
        ),
        "protocol_high_yields": (
            create_average_yield_figure(high_yield_protocols, average_yield_vaults, "protocol", theme, protocol_group_logos, tbill_latest, criteria.yield_max_return),
            ChartPanel(f"The {criteria.yield_top_protocols} highest-yielding protocols with at least {format_usd(criteria.yield_high_yield_min_protocol_tvl)} TVL", difference, "tradingstrategy.ai/vaults/protocols"),
        ),
    }
    if len(protocol_tvl):
        figures["protocol_tvl"] = (
            create_protocol_tvl_figure(protocol_tvl, theme, {name: load_protocol_logo_uri(tvl_vault_slugs.get(name), theme) for name in protocol_tvl.columns}),
            ChartPanel("Stablecoin TVL by DeFi vault protocol", "Weekly over the last 12 months, tokenised funds excluded", "tradingstrategy.ai/vaults/historical-tvl-protocol"),
        )
    if len(chain_tvl):
        figures["chain_tvl"] = (
            create_protocol_tvl_figure(chain_tvl, theme, {chain: chain_logo(chain) for chain in chain_tvl.columns if chain != "Other"}),
            ChartPanel("Stablecoin TVL by blockchain", "Weekly over the last 12 months, tokenised funds excluded", "tradingstrategy.ai/vaults/historical-tvl-chain"),
        )
    if len(fund_nav):
        figures["fund_nav"] = (
            create_protocol_tvl_figure(fund_nav, theme, {name: load_protocol_logo_uri(fund_slugs.get(name), theme) for name in fund_nav.columns}, value_label="NAV"),
            ChartPanel("Stablecoin NAV by tokenised fund", "Weekly over the last 12 months", "tradingstrategy.ai/vaults/funds"),
        )
    if len(tvl_changes):
        figures["tvl_changes"] = (
            create_tvl_change_figure(tvl_changes, theme, vault_properties),
            ChartPanel("Inflows and outflows", f"The {criteria.tvl_change_top_n} vaults with the largest increases and the {criteria.tvl_change_top_n} with the largest decreases", "tradingstrategy.ai/vaults"),
        )
    if len(chain_tvl_changes):
        figures["chain_tvl_changes"] = (
            create_tvl_change_figure(chain_tvl_changes, theme, logos={chain: chain_logo(chain) for chain in chain_tvl_changes.index}),
            ChartPanel("Inflows and outflows by blockchain", f"Net change of all vaults on the {criteria.tvl_change_top_n} blockchains with the largest increases and decreases", "tradingstrategy.ai/vaults/chains"),
        )

    benchmark_logos = {name: load_benchmark_logo_uri(name) for name in benchmark_indices}

    # Each vault picks its own benchmarks, following the website's rules: calm yield vaults against
    # the T-bill, volatile and perp DEX vaults against BTC and ETH; see README-vault-report.md, Benchmarks
    def performance_series(df: pd.DataFrame) -> list[PerformanceSeries]:
        return [
            PerformanceSeries(
                vault_id=vault_id,
                name=vault["name"] or vault["address"],
                properties=vault_properties[vault_id],
                benchmarks=select_benchmarks(vault, criteria.crypto_benchmark_min_volatility, criteria.crypto_benchmark_max_drawdown),
            )
            for vault_id, vault in df.iterrows()
        ]

    for section, df in performance_vaults.items():
        if not len(df):
            continue
        # The Sharpe ratio uses forward-filled prices like the exported 3M Sharpe, so its latest values match the table
        prices = sharpe_prices if section.measure == "sharpe" else daily_prices
        figure = create_performance_figure(performance_series(df), prices, benchmark_indices, theme, PERFORMANCE_WINDOW, benchmark_logos=benchmark_logos, measure=section.measure, sharpe_window=SHARPE_WINDOW, split_legend=section.measure == "sharpe")
        figures[section.chart_key] = (figure, make_performance_panel(section, criteria))

    # New vaults: the window is their age limit, and each line starts at the vault's first price
    if len(new_chart_vaults):
        figure = create_performance_figure(performance_series(new_chart_vaults), daily_prices, benchmark_indices, theme, criteria.new_vault_max_age, benchmark_logos=benchmark_logos)
        figures[NEW_VAULTS_CHART] = (figure, make_new_vaults_panel(criteria))

    chain_chart_vaults = select_chain_chart_vaults(ranked_df, criteria)
    if len(chain_chart_vaults):
        figures["by_chain_best"] = (
            create_chain_best_figure(chain_chart_vaults, theme, {chain: chain_logo(chain) for chain in chain_chart_vaults["chain"].unique()}, tbill_latest),
            ChartPanel(
                "The two best-performing vaults on each chain",
                f"Large dots: the two best vaults · small dots: the runners-up · at least {format_usd(criteria.chain_min_tvl)} TVL",
                "tradingstrategy.ai/vaults/chains",
            ),
        )

    # The scatter needs both axes and the bubble size; dormant vaults would pile up on the log-scale edge
    risk_return_vaults = select_moving_vaults(exclude_chart_risks(select_risk_return_vaults(yield_universe, ranked_df, criteria), criteria)).dropna(subset=["three_months_volatility", "three_months_cagr_best", "current_nav"])
    if not len(risk_return_vaults):
        logger.warning("No vault has three-month volatility and return, leaving out the risk and return chart")
    else:
        risk_return_figure = create_risk_return_figure(risk_return_vaults, {tag: category.get("label", tag) for tag, category in data.categories.items()}, theme, criteria.scatter_max_return, tbill_latest)
        # The subtitle counts the drawn vaults, not the selection: outliers beyond the fitted axes are left out
        drawn = sum(len(trace.x) for trace in risk_return_figure.data)
        figures["risk_return"] = (
            risk_return_figure,
            ChartPanel("Volatility risk and return of stablecoin vaults", f"{drawn} vaults with at least {format_usd(criteria.min_tvl)} TVL, larger bubbles hold more TVL", "tradingstrategy.ai/vaults/yield-risk"),
        )

    # Rendering in headless Chrome takes most of a run without the investability check, so it runs last
    # in one batch with a progress bar. The framed panel overwrites the raw Kaleido PNG at the same path.
    chart_dir = output_dir / "charts"
    chart_paths = {}
    for key, (fig, panel) in tqdm(figures.items(), desc="Rendering charts"):
        path = render_figure_png(fig, chart_dir / f"{key}.png", scale=CHART_SCALE)
        chart_paths[key] = compose_chart_panel(path, theme, panel.title, panel.subtitle, f"Data {data_date}", panel.link, path, scale=CHART_SCALE)

    # Hero images are drawn with Pillow, not Plotly, at fixed social image sizes
    sparkline_start = pd.Timestamp(data.data_end_at - PERFORMANCE_WINDOW)
    sparklines = {vault_id: daily_prices.loc[daily_prices.index >= sparkline_start, vault_id] for vault_id in hero_vaults.index if vault_id in daily_prices.columns}
    # The column header already names the return; the risk and outlier filters are not repeated on the image
    hero_subtitle = f"≥ {format_usd(criteria.min_tvl)} TVL · {data_date}"
    # The Plotly charts take logo data URIs, some of them SVG, which Pillow cannot draw, so the hero logos are rasterised
    hero_logos = rasterise_logos({prop.logo_uri for vault_id in hero_vaults.index for prop in vault_properties[vault_id] if prop.logo_uri})
    hero_properties = {vault_id: [(prop.text, hero_logos.get(prop.logo_uri)) for prop in vault_properties[vault_id]] for vault_id in hero_vaults.index}
    hero_path = output_dir / "hero.png"
    for path, size in ((hero_path, HERO_SIZE), (output_dir / "hero-square.png", SQUARE_HERO_SIZE)):
        render_hero_image(hero_vaults, sparklines, month_label, hero_subtitle, theme, path, size=size, properties=hero_properties)
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
    vault_checks: VaultCheckSettings | None = None,
    podcasts: list[PodcastEpisode] | None = None,
    excluded_vaults_dir: Path | None = None,
) -> GeneratedReport:
    """Generate the report tables, charts and post body into a local bundle.

    The first stage of the monthly workflow; :py:func:`publish_report_draft`
    is the second. The steps run in this order because each one narrows the
    input of the next:

    1. Filter the export to eligible vaults (not blacklisted, known TVL and
       return, data no older than ``max_data_age``) and to comparable vaults
       (identified protocol). The TVL and activity minimums are applied
       later, per table, by the selectors.
    2. Run the investability check, when ``vault_checks`` is given, on the
       top lists before any table is selected, so an excluded vault never
       reaches a table, a chart or the hero image, and the next vault moves
       up instead.
    3. Select and render the tables, and check which vaults have a published
       website sparkline for the "3M history" column.
    4. Record the excluded vaults in a dated Markdown file. The post never
       lists them; the file is committed with the report's pull request.
    5. Render the charts and hero images, unless ``render_charts`` is off.
    6. Copy the podcast images, render ``post.html`` and ``preview.html``
       with bundle-relative image paths, and write ``report.json``.

    Nothing here talks to the Ghost Admin API, so the bundle can be reviewed
    in a browser before anything is uploaded, and rerun cheaply with cached
    downloads and reused check decisions.

    :param data:
        Loaded vault metrics and prices, output of
        :py:func:`eth_defi.vault_report.data.fetch_vault_report_data`.

    :param output_dir:
        Where to write the bundle. Created if needed.

    :param criteria:
        Report thresholds. Defaults to :py:class:`~eth_defi.vault_report.sections.ReportCriteria` defaults.

    :param previous:
        The previous report post, read with the Ghost Content API. The new
        post links back to it and copies its evergreen sections, see
        :py:func:`eth_defi.vault_report.post.build_post_html`. ``None`` uses
        the default evergreen texts.

    :param changelog_entries:
        Changelog entries since the previous report, see
        :py:func:`eth_defi.vault_report.post.read_changelog_entries`. Written
        to ``report.json`` for the editor, never into the post.

    :param render_charts:
        Render PNG charts and the hero images. Needs Chrome for Kaleido.

    :param theme:
        Chart theme.

    :param cache_dir:
        Cache for logos and benchmark data. Defaults to ``{output_dir}/cache``.

    :param check_sparklines:
        Check which vaults have a published sparkline and show them in the
        tables. Makes one HTTP HEAD request per listed vault to the website; tests turn it off.

    :param vault_checks:
        Run the investability check, see :py:mod:`eth_defi.vault_report.vault_checks`.
        ``None`` skips it with a warning in the log.

    :param podcasts:
        Latest podcast episodes for the *Latest podcasts* section, see
        :py:func:`eth_defi.vault_report.podcasts.fetch_latest_podcast_episodes`.
        ``None`` or empty leaves the section out.

    :param excluded_vaults_dir:
        Directory, e.g. in the repository, for the dated Markdown record of the
        vaults the investability check left out. A copy is always written to
        the bundle.

    :return:
        Generated report description.
    """
    criteria = criteria or ReportCriteria()
    cache_dir = cache_dir or output_dir / "cache"
    # None when FRED is unreachable and nothing is cached; the charts then leave out the T-bill
    tbill_yields = fetch_treasury_bill_yields(cache_dir)
    (output_dir / "tables").mkdir(parents=True, exist_ok=True)

    data_end_at = data.data_end_at
    eligible_df = filter_eligible_vaults(data.vaults_df, data_end_at, criteria)
    # Unidentified protocols are counted only in the TVL summaries, never ranked; their data is often broken
    comparable_df = select_comparable_vaults(eligible_df)
    check_result = None
    if vault_checks is not None:
        check_result = run_report_checks(comparable_df, data, output_dir, vault_checks, criteria)
        comparable_df = apply_check_decisions(comparable_df, check_result.excluded)
    # render_report_charts() takes eligible_df and applies the exclusions itself, because the TVL totals keep excluded vaults
    excluded = check_result.excluded if check_result else frozenset()
    sections = build_report_sections(comparable_df, criteria)
    if check_sparklines:
        # A vault without a published sparkline PNG gets an empty cell instead of a broken image
        sparkline_ids = frozenset(fetch_available_sparklines([vault_id for section in sections.values() for vault_id in section.vaults_df.index]))
        sections = {key: dataclasses.replace(section, sparkline_ids=sparkline_ids) for key, section in sections.items()}

    tables = {}
    for key, section in sections.items():
        tables[key] = render_section_table(section)
        (output_dir / "tables" / f"{key}.html").write_text(tables[key])
        section.vaults_df[CSV_COLUMNS].to_csv(output_dir / "tables" / f"{key}.csv", index=False)

    month_label = make_month_label(data_end_at)
    title = make_report_title(month_label)

    # The writing rules keep excluded vaults out of the post; a dated Markdown document records them
    # for the editor and is posted as a comment on the report's pull request
    excluded_vaults_path = None
    if check_result is not None:
        pd.DataFrame(excluded_rows(check_result)).to_csv(output_dir / "tables" / "excluded.csv", index=False)
        excluded_markdown = render_excluded_vaults_markdown(check_result, data_end_at, title)
        excluded_vaults_path = output_dir / f"{data_end_at:%Y-%m-%d}-excluded-vaults.md"
        excluded_vaults_path.write_text(excluded_markdown)
        # The repository copy, eth_defi/vault_report/excluded-vaults/ by default, is committed as the
        # month's audit trail; the reported path then points at it rather than at the temporary bundle
        if excluded_vaults_dir is not None:
            excluded_vaults_dir.mkdir(parents=True, exist_ok=True)
            excluded_vaults_path = excluded_vaults_dir / excluded_vaults_path.name
            excluded_vaults_path.write_text(excluded_markdown)
        logger.info("Excluded vaults written to %s", excluded_vaults_path)
    log_check_summary(check_result)

    chart_paths, hero_path = {}, None
    if render_charts:
        # One shared headless Chrome for the whole batch instead of one browser per image
        with chart_renderer():
            chart_paths, hero_path = render_report_charts(data, eligible_df, sections, criteria, theme, output_dir, cache_dir, tbill_yields, excluded)

    podcast_image_paths = prepare_podcast_images(podcasts or [], theme, output_dir)
    # Images are referenced relative to the bundle, so post.html and preview.html work from disk;
    # publish_report_draft() renders the post again with the uploaded Ghost URLs
    context = PostContext(
        month_label=month_label,
        stats=calculate_report_stats(data.vaults_df, eligible_df, data_end_at),
        tables=tables,
        charts={key: path.relative_to(output_dir).as_posix() for key, path in chart_paths.items()},
        criteria_notes=make_criteria_notes(criteria),
        previous=previous,
        podcasts=podcasts or [],
        podcast_images={key: path.relative_to(output_dir).as_posix() for key, path in podcast_image_paths.items()},
    )
    report = GeneratedReport(
        output_dir=output_dir,
        title=title,
        slug=make_report_slug(data_end_at),
        excerpt=f"The best stablecoin yield in DeFi, {month_label} report.",
        data_end_at=data_end_at,
        context=context,
        chart_paths=chart_paths,
        sections=sections,
        hero_path=hero_path,
        vault_checks=check_result,
        podcast_image_paths=podcast_image_paths,
        excluded_vaults_path=excluded_vaults_path,
        changelog_entries=changelog_entries or [],
    )

    post_html = build_post_html(context)
    (output_dir / "post.html").write_text(post_html)
    (output_dir / "preview.html").write_text(build_preview_html(report.title, post_html, hero_path.relative_to(output_dir).as_posix() if hero_path else None))
    write_report_manifest(report)
    logger.info("Report bundle written to %s", output_dir)
    return report


def prepare_podcast_images(episodes: list[PodcastEpisode], theme: ChartTheme, output_dir: Path) -> dict[str, Path]:
    """Write the podcast guests' logos and the service icons into the report bundle.

    Images for the *Latest podcasts* section of the post, rendered by
    :py:func:`eth_defi.vault_report.podcasts.render_podcast_episodes`. The
    logos come from the protocol and curator logo collection, so the post
    does not depend on the website serving them, and are drawn on dark tiles,
    because many logos are white and would disappear in light newsletter
    emails, see :py:func:`eth_defi.vault_report.branding.render_logo_tile`. The YouTube
    and Spotify icons are PNG renders of ``assets/podcast/*.svg``.
    :py:func:`publish_report_draft` uploads them to Ghost with the charts.

    :param episodes:
        Podcast episodes.

    :param theme:
        Theme of the blog page, for the logo variant.

    :param output_dir:
        Report bundle directory.

    :return:
        Image key -> PNG path in ``podcasts/``, keyed by
        :py:func:`~eth_defi.vault_report.podcasts.logo_image_key` and
        :py:func:`~eth_defi.vault_report.podcasts.icon_image_key`. Guests
        without a logo are left out. Empty when there are no episodes.
    """
    paths = {}
    if not episodes:
        return paths
    for service in PODCAST_SERVICES:
        target = output_dir / "podcasts" / "icons" / f"{service}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ASSETS_DIR / "podcast" / f"{service}.png", target)
        paths[icon_image_key(service)] = target
    # A guest may appear in several episodes: render each logo once, in episode order
    for slug in dict.fromkeys(episode.logo_slug for episode in episodes if episode.logo_slug):
        source = load_protocol_logo_path(slug, theme)
        if source is None:
            logger.warning("No logo for podcast guest %s", slug)
            continue
        paths[logo_image_key(slug)] = render_logo_tile(source, theme, output_dir / "podcasts" / f"{slug}.png")
    return paths


def write_report_manifest(report: GeneratedReport, ghost_post: GhostPost | None = None, editor_url: str | None = None) -> Path:
    """Write ``report.json`` describing the bundle.

    The machine-readable summary of a run, for the people and tools that
    finish the post rather than for readers: the editor takes the changelog
    candidates for *Report content updates* from it, the pull request review
    comment takes the investability check results from it (see
    ``README-best-vaults-news.md``), and the Ghost editor link points at the
    draft to finish. Paths are relative to the
    bundle, except the excluded vaults record, which may live in the
    repository.

    Written twice: by :py:func:`generate_monthly_vault_report` without a
    draft, then again by :py:func:`publish_report_draft` with the draft id,
    slug and editor link, so a bundle-only run still has a complete manifest.

    :param report:
        Generated report.

    :param ghost_post:
        The Ghost draft, if one was created or updated.

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
        "changelog_entries": report.changelog_entries,
        "excluded_vaults": str(report.excluded_vaults_path) if report.excluded_vaults_path else None,
        "sections": {key: len(section.vaults_df) for key, section in report.sections.items()},
        "charts": report.context.charts,
        "hero": report.hero_path.relative_to(report.output_dir).as_posix() if report.hero_path else None,
        # render_report_charts() always writes the square image next to hero.png
        "hero_square": "hero-square.png" if report.hero_path else None,
        "previous_report_slug": previous.slug if previous else None,
        "ghost_draft": {"id": ghost_post.id, "slug": ghost_post.slug, "editor_url": editor_url} if ghost_post else None,
        "vault_checks": summarise_checks(report.vault_checks) if report.vault_checks else None,
        "podcasts": [{**dataclasses.asdict(episode), "published_at": episode.published_at.isoformat() if episode.published_at else None} for episode in report.context.podcasts],
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
    force_overwrite: bool = False,
    draft_record_path: Path | None = None,
) -> GhostPost:
    """Upload the images and create the Ghost draft post.

    The second stage of the monthly workflow, after
    :py:func:`generate_monthly_vault_report`. Uses the
    `Ghost Admin API <https://ghost.org/docs/admin-api/>`__ through
    :py:class:`~eth_defi.vault_report.ghost.GhostAdminClient`.

    Safety properties, see the *Ghost post draft* section of
    ``README-best-vaults-news.md``:

    - The draft is never published: publishing is a manual step in Ghost.
    - A published or scheduled post with the same slug is never touched.
    - An existing draft with the same slug is replaced only with
      ``overwrite_draft``, and only when the record of the pipeline's last
      write shows nobody has edited it since, because replacing it would lose
      the editor's work in Ghost. ``force_overwrite`` skips the comparison.
    - The feature image is left for the editor to choose and never sent, so
      a replaced draft keeps it; the bundle's ``hero.png`` is a ready-made option.

    Order of operations: the overwrite guard runs first, so a refused write
    fails before any image is uploaded and leaves no orphan images in the
    Ghost media library. The guard runs again inside
    :py:meth:`~eth_defi.vault_report.ghost.GhostAdminClient.create_or_update_draft`,
    right before the write, in case the editor saved the draft during the
    uploads. The post is then rendered again from the same
    :py:class:`~eth_defi.vault_report.post.PostContext` with the uploaded
    image URLs, so the draft and the local ``post.html`` differ only in
    their image sources.

    After the write the :py:class:`~eth_defi.vault_report.ghost.DraftRecord`
    is updated, so the next run can tell its own write from an editor's edit,
    and ``report.json`` gets the draft link.

    :param report:
        Output of :py:func:`generate_monthly_vault_report`.

    :param admin_client:
        Ghost Admin API client.

    :param tags:
        Tag names for the post.

    :param overwrite_draft:
        Replace an existing draft with the same slug if it is unedited.

    :param force_overwrite:
        Replace an existing draft without checking it for edits.

    :param draft_record_path:
        JSON record of the pipeline's last write to this draft, see
        :py:class:`~eth_defi.vault_report.ghost.DraftRecord`; the script keeps
        it at ``{CACHE_DIR}/ghost-drafts/{slug}.json``. Without it, an
        existing draft is replaced only with ``force_overwrite``.

    :return:
        The Ghost draft post.

    :raise eth_defi.vault_report.ghost.GhostAPIError:
        The slug belongs to a published or scheduled post, or to a draft that
        may not be overwritten, or a Ghost request failed.
    """
    last_write = DraftRecord.load(draft_record_path) if draft_record_path else None
    # Fail before uploading images if the draft cannot be written
    admin_client.fetch_writable_draft(report.slug, overwrite_draft=overwrite_draft, last_write=last_write, force=force_overwrite)
    # Every run uploads fresh copies, so a replaced draft never points at the images of an earlier run
    chart_urls = {key: admin_client.upload_image(path) for key, path in tqdm(report.chart_paths.items(), desc="Uploading charts")}
    podcast_image_urls = {key: admin_client.upload_image(path) for key, path in report.podcast_image_paths.items()}
    body = build_post_html(dataclasses.replace(report.context, charts=chart_urls, podcast_images=podcast_image_urls))
    post = admin_client.create_or_update_draft(
        title=report.title,
        slug=report.slug,
        html=body,
        custom_excerpt=report.excerpt,
        tags=tags,
        overwrite_draft=overwrite_draft,
        last_write=last_write,
        force=force_overwrite,
    )
    if draft_record_path:
        # Fingerprint the HTML we sent, not Ghost's converted HTML: the fingerprint is designed to be equal
        # for both. updated_at and the feature image come from Ghost's response, so any later save triggers
        # the text comparison and a feature image the editor adds counts as an edit.
        DraftRecord(post_id=post.id, updated_at=post.updated_at, fingerprint=fingerprint_post_text(body), title=report.title, custom_excerpt=report.excerpt, feature_image=post.feature_image).save(draft_record_path)
    write_report_manifest(report, post, admin_client.get_editor_url(post))
    return post
