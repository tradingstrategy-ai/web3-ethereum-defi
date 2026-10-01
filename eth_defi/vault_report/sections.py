"""Vault rankings and tables for the monthly vault report.

Each report section is a ranked subset of the vault metrics loaded by
:py:func:`eth_defi.vault_report.data.fetch_vault_report_data`. The post
structure is described in ``eth_defi/vault_report/README-blog-post-outline.md``.

Vaults are classified into groups; each group except *Other* is ranked in its own table:

- **Lending:** strategy tagged as lending, or a known lending protocol, see
  :py:data:`LENDING_PROTOCOL_SLUGS`
- **Real-world assets (RWA):** strategy tagged as investing in, lending
  against or financing real-world assets, see :py:data:`RWA_STRATEGY_TAGS`
- **Perpetual futures DEX:** Hyperliquid, GRVT, Lighter and other native trading vaults
- **AMM pools:** GMX GM and GLV pools and Curve-based YieldBasis pools
- **Tokenised funds:** vaults flagged ``tokenised_fund``, e.g. money market funds
- **Other:** everything else, e.g. yield aggregators and trading vaults. It has no
  section of its own; its vaults appear in the new vaults, per-chain, yield and
  risk and return sections

Tables rank by the annualised one-month return, like the website's vault
pages, net of fees when fee data is known and gross otherwise, shown without a
net or gross marker. Charts use the steadier annualised three-month return,
:py:data:`CHART_RETURN`.

How the universes are narrowed, see :py:func:`eth_defi.vault_report.report.generate_monthly_vault_report`:

1. :py:func:`filter_eligible_vaults` drops blacklisted vaults, vaults without
   TVL or a one-month return, and stale vaults. The result feeds the report
   statistics and the inflows and outflows.
2. :py:func:`select_comparable_vaults` drops vaults whose protocol is not
   identified, because their returns are often artefacts. Every table and
   chart that compares returns starts from here.
3. The investability check (:py:mod:`eth_defi.vault_report.vault_checks`)
   removes vaults that are not investable in practice.
4. :py:func:`exclude_amm_pools` leaves AMM pools only in their own section.
5. The ``select_*`` and ``calculate_*`` functions apply per-section TVL,
   activity, risk and outlier thresholds from :py:class:`ReportCriteria`.

TVL history charts, :py:func:`calculate_protocol_tvl_history` and friends,
only drop blacklisted vaults, :py:func:`select_tvl_history_vaults`, so their
totals match the website's historical TVL charts.

Units: returns, volatilities and drawdowns are fractions (0.05 = 5%), as in
the top vaults export; TVL is in USD; timestamps are naive UTC.
"""

import datetime
import html
import logging
import re
from dataclasses import dataclass, field

import pandas as pd

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.research.vault_metrics import USDollarAmount, _get_trading_strategy_chain_link, _get_trading_strategy_protocol_link
from eth_defi.types import Percent
from eth_defi.vault.flag import BAD_FLAGS, VaultFlag, get_vault_special_flags
from eth_defi.vault.risk import VaultTechnicalRisk
from eth_defi.vault.strategy_tag import StrategyTag

logger = logging.getLogger(__name__)

#: Protocol slug for vaults whose protocol we could not identify
UNKNOWN_PROTOCOL_SLUG = "protocol-not-yet-identified"

#: Technical risk label for vaults excluded from all listings
BLACKLISTED_RISK = VaultTechnicalRisk.blacklisted.get_risk_level_name()

#: Sharpe ratios above this are displayed as ``>100``.
#:
#: Lending vaults with near-zero volatility get Sharpe ratios in the
#: millions, which carry no information beyond "very smooth".
MAX_DISPLAYED_SHARPE = 100

#: The top vaults export caps annualised returns at 10,000%, as the fraction
#: 100.0 (``max_cagr`` in :py:mod:`eth_defi.research.vault_metrics` period metrics).
#:
#: Annualising a large one-month gain, common for perp DEX vaults and broken
#: share tokens, overflows to absurd numbers, so the exporter clamps it.
#: Values at the cap are displayed as :py:data:`CAPPED_RETURN_LABEL`, and
#: :py:func:`rank_vaults` breaks the resulting ties.
CAPPED_ANNUALISED_RETURN = 100.0

#: Label of a capped annualised return, like on the website
CAPPED_RETURN_LABEL = ">9,999%"

#: Public vault sparkline images, rendered by :py:mod:`eth_defi.research.sparkline_export`
#: PNG rather than SVG, because email clients such as Gmail strip SVG images from newsletters
SPARKLINE_URL = "https://vault-sparklines.tradingstrategy.ai/sparkline-90d-{vault_id}.png"


#: Strategy tags that make a vault a lending vault
LENDING_STRATEGY_TAGS = frozenset(tag.value for tag in (StrategyTag.lending, StrategyTag.lending_optimisation, StrategyTag.lending_looping))

#: Strategy tags that make a vault a real-world asset (RWA) vault, see :py:class:`eth_defi.vault.strategy_tag.StrategyTag`.
#:
#: RWA lending vaults are RWA vaults, not lending vaults: their risk is the
#: real-world borrowers and collateral. Tokenised funds and perp DEX vaults
#: financing real-world credit keep their own groups.
RWA_STRATEGY_TAGS = frozenset(tag.value for tag in (StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending, StrategyTag.rwa_royalties))

#: Protocols whose vaults are lending vaults even without a strategy tag.
#:
#: Not every protocol adapter sets strategy tags, so the report also
#: recognises the lending markets it knows, matched against the export's
#: ``protocol_slug``. Tagging these adapters would make this list unnecessary.
#: Untagged RWA vaults of these protocols land in lending until tagged with
#: the ``categorise-vault-strategy`` skill.
LENDING_PROTOCOL_SLUGS = frozenset({"aave", "morpho", "euler", "fluid", "spark", "silo-finance", "llama-lend", "curvance", "dolomite", "gearbox", "sentiment", "arcadia-finance", "term-finance", "40acres", "3jane", "frax"})

#: Vault feature of AMM liquidity-provider shares, set by the scanner for GMX GM and GLV pools and
#: Curve-based YieldBasis LTs, see :py:attr:`eth_defi.erc_4626.core.ERC4626Feature.amm_pool_like`
AMM_POOL_FEATURE = ERC4626Feature.amm_pool_like.value

#: Vault flag for tokenised funds, such as money market and treasury funds
TOKENISED_FUND_FLAG = VaultFlag.tokenised_fund.value

#: Protocol slugs that mean the vault's protocol has not been identified yet: generic ERC-4626 and
#: ERC-7540 vaults and placeholders. The same set as the website's ``isUnknownVaultProtocol()``
#: in ``src/lib/top-vaults/helpers.ts``.
UNIDENTIFIED_PROTOCOL_SLUGS = frozenset({UNKNOWN_PROTOCOL_SLUG, "erc-4626", "unknown", "unknown-erc-7450"})

#: Protocol names, lower case, that mean the same. Names starting with ``<``,
#: such as ``<unknown ERC-4626>`` placeholders, are also unidentified, see :py:func:`is_identified_protocol`.
UNIDENTIFIED_PROTOCOL_NAMES = frozenset({"", "unknown", "unknown vault protocol"})

#: Label of the single pile of vaults whose protocol is not identified. Charts also sum
#: their small protocols into it, so unidentified vaults never show as a protocol of their own.
OTHER_PROTOCOL = "Other"

#: Vault group labels, stored in the ``group`` column by
#: :py:func:`eth_defi.vault_report.data.prepare_vault_metrics`, see :py:func:`classify_vault`.
#: Each group except :py:data:`OTHER` has its own best-performing section in
#: :py:data:`eth_defi.vault_report.post.BEST_SECTIONS`.
LENDING = "lending"
RWA = "rwa"
PERP_DEX = "perp_dex"
AMM = "amm"
TOKENISED_FUND = "tokenised_fund"
OTHER = "other"


@dataclass(slots=True)
class ReportCriteria:
    """Selection thresholds for the report sections.

    One place for every editorial threshold, so a month's post can be tuned
    with :py:func:`dataclasses.replace` without touching the selectors. The
    investability check reuses the same selectors with deeper ``top_n`` and
    ``chain_top_n`` values to collect replacement candidates, see
    :py:func:`eth_defi.vault_report.report.collect_top_lists`.

    The defaults reproduce the rules in ``README-blog-post-outline.md``; the
    section notes in :py:func:`eth_defi.vault_report.report.make_criteria_notes`
    and the chart subtitles quote these values, so changing one changes the
    post text too.

    Returns, volatilities and drawdowns are fractions (0.25 = 25%), TVL is in USD.
    """

    #: Minimum current TVL for the best-performing vault tables, the yield
    #: universe of the hero image and the risk and return chart.
    #:
    #: Large vaults have no table of their own: this threshold and the TVL
    #: column cover them, see the outline's change log.
    min_tvl: USDollarAmount = 100_000

    #: Minimum lifetime deposit and redemption events (export ``event_count``)
    #: for lending, RWA, other and new vaults.
    #:
    #: Filters out inactive vaults, whose returns no depositors stand behind.
    #: Perp DEX vaults and tokenised funds have no
    #: comparable onchain ERC-4626 events and are exempt, see :py:func:`select_group`.
    min_events: int = 10

    #: Number of vaults in each best-performing table
    top_n: int = 20

    #: Exclude vaults whose latest metrics (export ``end_date``) are older than
    #: this, relative to the report data date.
    #:
    #: A vault the scanner stopped reading keeps its last, possibly
    #: flattering, returns in the export, which would otherwise keep ranking.
    max_data_age: datetime.timedelta = datetime.timedelta(days=7)

    #: Minimum TVL for the per-chain table and chart
    chain_min_tvl: USDollarAmount = 100_000

    #: Vaults listed per chain
    chain_top_n: int = 3

    #: Minimum TVL for the new vaults table, lower than :py:attr:`min_tvl`
    #: because a vault launched weeks ago has not had time to grow
    new_vault_min_tvl: USDollarAmount = 15_000

    #: Include AMM pools in the rankings and charts outside their own section. AMM pools, such as GMX GM
    #: pools and YieldBasis, carry the market risk of the traded assets, so by default they are ranked only
    #: in the AMM pools section and counted only in the TVL summaries.
    include_amm_pools: bool = False

    #: Minimum TVL for the AMM pools table
    amm_min_tvl: USDollarAmount = 1_000_000

    #: Maximum age of a vault in the new vaults table
    new_vault_max_age: datetime.timedelta = datetime.timedelta(days=60)

    #: Number of vaults in each performance chart; more equity curves in one
    #: shared-axis chart become unreadable
    performance_chart_vaults: int = 8

    #: Vaults at or above this annualised three-month volatility are compared with BTC and ETH instead of the Treasury bill.
    #:
    #: The report's own extension of the website's benchmark rules: a
    #: trading-like stablecoin vault is better judged against the crypto
    #: market it trades, see :py:func:`eth_defi.vault_report.benchmarks.select_benchmarks`.
    crypto_benchmark_min_volatility: Percent = 0.25

    #: Vaults with a three-month drawdown, a negative fraction, at or below this are compared with BTC and ETH instead of the Treasury bill
    crypto_benchmark_max_drawdown: Percent = -0.10

    #: Leave vaults above this annualised three-month return, 400%, out of the hero image,
    #: where one outlier number would dwarf the others
    chart_max_return: Percent = 4.0

    #: Lowest technical risk level left out of the charts: the hero image, the performance charts and
    #: risk and return. Dangerous and above, see :py:class:`eth_defi.vault.risk.VaultTechnicalRisk`.
    #: Charts get shared without the tables, so tables keep these vaults.
    #: Unrated vaults are shown.
    chart_min_excluded_risk: int = VaultTechnicalRisk.dangerous.value

    #: Leave vaults above this annualised three-month volatility out of the hero image
    hero_max_volatility: Percent = 0.5

    #: Clip the risk and return scatter's y axis at most at this annualised return, 100%; vaults above are left out
    scatter_max_return: Percent = 1.0

    #: Number of blockchains in the average yield chart, the largest by TVL
    yield_top_chains: int = 10

    #: Number of protocols in the average yield chart, the largest by TVL
    yield_top_protocols: int = 10

    #: Minimum TVL of a protocol in the high TVL average yield chart
    yield_min_protocol_tvl: USDollarAmount = 1_000_000

    #: Minimum TVL of a protocol in the high yield average yield chart, which ranks protocols by yield.
    #:
    #: Lower than :py:attr:`yield_min_protocol_tvl` so smaller, high-yield
    #: protocols can appear; the chart's TVL column shows how much money earns the yield.
    yield_high_yield_min_protocol_tvl: USDollarAmount = 150_000

    #: Minimum TVL per vault to be counted in the average yield charts.
    #:
    #: Lower than the table minimum because the averages are TVL-weighted:
    #: small vaults barely move them but still show as dots.
    yield_min_vault_tvl: USDollarAmount = 10_000

    #: Exclude outlier vaults above this annualised three-month return, 400%, from the average yields
    yield_max_return: Percent = 4.0

    #: Exclude vaults above this annualised three-month volatility from the average yields.
    #:
    #: Keeps volatile crypto index funds and AMM liquidity positions out of the stablecoin yield figures.
    yield_max_volatility: Percent = 0.5

    #: Cap of the average yield charts' x axis for the largest chains and
    #: protocols, 40%. The axis otherwise fits the data, see
    #: :py:func:`eth_defi.vault_report.charts.create_average_yield_figure`;
    #: vault dots beyond it are not drawn but still count in the averages.
    #: The high yield chart is capped at :py:attr:`yield_max_return` instead.
    yield_chart_max_return: Percent = 0.4

    #: Number of largest inflows and of largest outflows in the TVL change chart
    tvl_change_top_n: int = 10

    #: Investability check: extra candidates collected for each top list, as a share of the vaults it shows,
    #: see :py:mod:`eth_defi.vault_report.vault_checks`. Checking 50% deeper
    #: than shown lets excluded vaults be replaced without another agent round.
    check_buffer_ratio: float = 0.5

    #: Investability check: rounds of refilling the top lists after exclusions.
    #: Each round runs the agent, minutes of LLM time, so the number is capped;
    #: unchecked in-scope vaults after the last round stop the report.
    check_max_rounds: int = 3

    #: Investability check: minimum TVL of an aggregate chart vault to prescreen
    check_prescreen_min_tvl: USDollarAmount = 1_000_000

    #: Investability check: maximum number of prescreened vaults sent to the agent
    check_max_escalations: int = 20


#: Default columns for vault tables, same as in the previous blog posts.
#: Cell values come from :py:func:`format_vault_cells`; there is deliberately
#: no risk rating column, the rating only filters the charts.
VAULT_TABLE_COLUMNS = [
    "Vault",
    "3M history",
    "1M ann.",
    "3M ann.",
    "Lifetime ann.",
    "3M Sharpe",
    "TVL",
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
    "TVL",
    "Age (y)",
    "Token",
]

#: Right-aligned numeric columns
NUMERIC_COLUMNS = {"3M Sharpe", "Age (y)"}

#: Columns whose values must not wrap onto two lines, e.g. ``>9,999%`` or ``$1.2M`` split at the comma or point
NOWRAP_COLUMNS = {"1M ann.", "3M ann.", "Lifetime ann.", "TVL"}


@dataclass(slots=True)
class ReportSection:
    """One ranked vault table in the report.

    Built by :py:func:`eth_defi.vault_report.report.build_report_sections`,
    rendered to HTML by :py:func:`render_section_table` and written to the
    bundle's ``tables/*.csv``.
    """

    #: Vaults in display order, rows from :py:attr:`eth_defi.vault_report.data.VaultReportData.vaults_df`,
    #: already truncated to the table length
    vaults_df: pd.DataFrame

    #: Table columns, see :py:data:`VAULT_TABLE_COLUMNS`
    columns: list[str] = field(default_factory=lambda: list(VAULT_TABLE_COLUMNS))

    #: Show a 1, 2, 3... rank column
    numbered: bool = True

    #: Vault ids with a published sparkline image, see :py:func:`eth_defi.vault_report.data.fetch_available_sparklines`.
    #: Filled in after the tables are selected, so only their vaults are probed; other vaults get an empty "3M history" cell
    #: instead of a broken image.
    sparkline_ids: frozenset[str] = frozenset()


def filter_eligible_vaults(
    vaults_df: pd.DataFrame,
    data_end_at: datetime.datetime,
    criteria: ReportCriteria,
) -> pd.DataFrame:
    """Remove vaults that must not appear in any listing.

    The first filter of every ranked section and of the inflows and outflows;
    the report statistics count its result as the vaults with up-to-date data.
    Drops:

    - blacklisted vaults, see :py:func:`is_blacklisted`: rated
      ``Blacklisted`` in the export, or given a bad flag in
      :py:mod:`eth_defi.vault.flag` since the export was made
    - vaults without a known TVL (``current_nav``, also ``NaN`` for broken
      share tokens above :py:data:`~eth_defi.research.vault_metrics.MAX_VALID_NAV`)
      or one-month return, which cannot be ranked
    - stale vaults whose ``end_date`` is more than
      :py:attr:`ReportCriteria.max_data_age` before ``data_end_at``

    Staleness is measured against the report data date, the newest
    ``end_date`` in the export, not the wall clock, so regenerating an old
    report from a cached export gives the same result.

    :param vaults_df:
        Vault metrics from :py:func:`eth_defi.vault_report.data.prepare_vault_metrics`,
        with ``risk``, ``address``, ``protocol``, ``current_nav``,
        ``one_month_cagr_best`` and ``end_date`` (naive UTC) columns.

    :param data_end_at:
        The report data date, :py:attr:`eth_defi.vault_report.data.VaultReportData.data_end_at`, naive UTC.

    :param criteria:
        Report thresholds.

    :return:
        Filtered vault metrics, the same columns.
    """
    stale_before = pd.Timestamp(data_end_at - criteria.max_data_age)
    mask = ~is_blacklisted(vaults_df) & vaults_df["current_nav"].notna() & vaults_df["one_month_cagr_best"].notna() & (vaults_df["end_date"] >= stale_before)
    eligible = vaults_df.loc[mask]
    logger.info("Eligible vaults for the report: %d out of %d", len(eligible), len(vaults_df))
    return eligible


def exclude_amm_pools(vaults_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Leave AMM pools out of the rankings and charts outside their own section.

    AMM pool returns include the price moves of the pooled crypto assets, so
    ranking them next to stablecoin yield would mislead. The AMM pools
    section itself selects from the unfiltered universe, see
    :py:func:`select_group`. See :py:attr:`ReportCriteria.include_amm_pools`.

    :param vaults_df:
        Vault metrics with the ``group`` column.

    :param criteria:
        Report thresholds.

    :return:
        Vaults without AMM pools, or all vaults when AMM pools are included.
    """
    return vaults_df if criteria.include_amm_pools else vaults_df.loc[vaults_df["group"] != AMM]


def exclude_chart_risks(vaults_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Leave out vaults whose risk rating is too high to feature in a chart.

    Charts get shared on social media without the tables around them, so a
    Dangerous vault in a chart looks like a recommendation. Tables keep these
    vaults. The comparison is written as ``~(risk >= threshold)`` rather than
    ``risk < threshold`` so unrated vaults, ``NaN`` ``risk_numeric``, are kept.

    :param vaults_df:
        Vault metrics with ``risk_numeric``, the numeric
        :py:class:`eth_defi.vault.risk.VaultTechnicalRisk` value or ``NaN``.

    :param criteria:
        Report thresholds, see :py:attr:`ReportCriteria.chart_min_excluded_risk`.

    :return:
        Vaults rated below the threshold, and unrated vaults.
    """
    return vaults_df.loc[~(vaults_df["risk_numeric"] >= criteria.chart_min_excluded_risk)]


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
    ``isUnknownVaultProtocol()`` (``src/lib/top-vaults/helpers.ts`` in the
    frontend), so the report and the website agree on what counts as a
    protocol. Both the name and the slug are checked, because a placeholder
    can show up in either. Mapping a protocol in
    the vault metadata moves its vaults out of the pile with no report change.

    Called by :py:func:`eth_defi.vault_report.data.prepare_vault_metrics` to
    fill the ``protocol_identified`` column.

    :param protocol:
        Protocol name, export ``protocol``.

    :param protocol_slug:
        Protocol slug, export ``protocol_slug``.

    :return:
        ``True`` for a named, mapped protocol.
    """
    name = (protocol or "").strip().lower()
    slug = (protocol_slug or "").strip().lower()
    return bool(name) and not name.startswith("<") and name not in UNIDENTIFIED_PROTOCOL_NAMES and slug not in UNIDENTIFIED_PROTOCOL_SLUGS


def classify_vault(vault: pd.Series) -> str:
    """Classify a vault into one of the report's vault groups.

    Every vault belongs to exactly one group, which decides its
    best-performing section, its table thresholds in :py:func:`select_group`
    and whether it counts as stablecoin yield. The rules are checked in
    priority order, the first match wins:

    1. :py:data:`AMM`: the scanner's ``amm_pool_like`` vault feature. The
       ``amm`` strategy tag is deliberately not used: vaults tagged ``amm``,
       such as Gains Network gTrade and KiloEx, stay in their other groups.
    2. :py:data:`PERP_DEX`: the ``perp_dex_trading_vault`` flag, precomputed
       as ``is_perp_dex``.
    3. :py:data:`TOKENISED_FUND`: the ``tokenised_fund`` flag, so funds
       financing real-world credit stay with the other funds.
    4. :py:data:`RWA`: an RWA strategy tag, checked before lending so RWA
       lending vaults count as RWA, see :py:data:`RWA_STRATEGY_TAGS`.
    5. :py:data:`LENDING`: a lending strategy tag, or a known lending protocol
       for adapters that set no tags, see :py:data:`LENDING_PROTOCOL_SLUGS`.
    6. :py:data:`OTHER`: everything else.

    Applied row by row with :py:meth:`pandas.DataFrame.apply` in
    :py:func:`eth_defi.vault_report.data.prepare_vault_metrics`. List
    columns come from JSON and are ``NaN`` or ``None`` when missing, hence
    the ``isinstance`` checks.

    :param vault:
        Vault metrics row with ``features``, ``is_perp_dex``, ``flags``, ``strategy_tags`` and ``protocol_slug``.
        ``features``, ``flags`` and ``strategy_tags`` are lists of enum values or missing.

    :return:
        :py:data:`AMM`, :py:data:`PERP_DEX`, :py:data:`TOKENISED_FUND`, :py:data:`RWA`, :py:data:`LENDING` or :py:data:`OTHER`.
    """
    # The features field can be absent from the record, hence get()
    features = vault.get("features")
    if isinstance(features, list) and AMM_POOL_FEATURE in features:
        return AMM
    if vault["is_perp_dex"]:
        return PERP_DEX
    if TOKENISED_FUND_FLAG in (vault["flags"] if isinstance(vault["flags"], list) else []):
        return TOKENISED_FUND
    tags = vault["strategy_tags"] if isinstance(vault["strategy_tags"], list) else []
    if RWA_STRATEGY_TAGS.intersection(tags):
        return RWA
    if LENDING_STRATEGY_TAGS.intersection(tags) or vault["protocol_slug"] in LENDING_PROTOCOL_SLUGS:
        return LENDING
    return OTHER


#: Column with the return used by all charts: the annualised three-month return, net of fees when known.
#: Tables rank by the one-month return like the website; charts use the steadier three-month return,
#: because one month of an annualised return is dominated by a single good or bad week.
#: Filled by :py:func:`eth_defi.vault_report.data.prepare_vault_metrics`.
CHART_RETURN = "three_months_cagr_best"


def rank_vaults(df: pd.DataFrame, column: str = "one_month_cagr_best") -> pd.DataFrame:
    """Sort vaults by a metric, best first.

    The single ranking used by every table and chart selector, so a vault's
    position is the same wherever it appears.

    The export caps annualised returns at 10,000%,
    :py:data:`CAPPED_ANNUALISED_RETURN`, so several perp DEX or newly
    launched vaults can share the same top value. Ties are broken with the
    absolute, non-annualised return of the same period (export
    ``three_months_returns`` or ``one_month_returns``), which is not capped:
    three months for metrics whose name starts with ``three_months``, which
    includes the three-month Sharpe ranking, one month otherwise. Vaults without the
    metric sort last instead of being dropped, so callers decide whether to
    filter them.

    :param df:
        Vault metrics with ``column`` and its tie-breaker column.

    :param column:
        Ranking metric, by default the annualised one-month return.

    :return:
        Sorted vault metrics, the same rows.
    """
    tie_breaker = "three_months_returns" if column.startswith("three_months") else "one_month_returns"
    return df.sort_values([column, tie_breaker], ascending=False, na_position="last")


def group_min_tvl(group: str, criteria: ReportCriteria) -> USDollarAmount:
    """Minimum TVL of a vault group's table and chart: higher for AMM pools.

    AMM pools need :py:attr:`ReportCriteria.amm_min_tvl` instead of
    :py:attr:`ReportCriteria.min_tvl`: they are exempt from the deposit
    event threshold, and the higher TVL stands in for it. Also quoted in the performance chart subtitles.

    :param group:
        Vault group, see :py:func:`classify_vault`.

    :param criteria:
        Report thresholds.

    :return:
        Minimum TVL in USD.
    """
    return criteria.amm_min_tvl if group == AMM else criteria.min_tvl


def select_group(eligible_df: pd.DataFrame, criteria: ReportCriteria, group: str, *, by: str = "one_month_cagr_best") -> pd.DataFrame:
    """Select the best vaults of a vault group.

    The selector of every best-performing section in
    :py:data:`eth_defi.vault_report.post.BEST_SECTIONS`: the table with
    ``by`` the section's table metric, one-month return or three-month
    Sharpe, and the performance chart with ``by`` :py:data:`CHART_RETURN`.
    The result is not truncated, so the investability check can take deeper
    candidate lists from the same ranking.

    Applies the TVL threshold, :py:attr:`ReportCriteria.amm_min_tvl` for AMM
    pools, and the activity threshold for lending, RWA and other vaults.
    Perpetual futures DEX vaults and tokenised funds have no comparable
    onchain deposit events, and AMM pools have the higher TVL threshold instead.
    Vaults without the ranking metric are dropped, because a table or chart
    cannot place them.

    :param eligible_df:
        Comparable vaults, see :py:func:`select_comparable_vaults`, with
        ``group``, ``current_nav``, ``event_count`` and the ``by`` column.
        AMM pools must still be included, as their own section uses this.

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
    mask = (df["group"] == group) & (df["current_nav"] >= group_min_tvl(group, criteria))
    if group in (LENDING, RWA, OTHER):
        mask &= df["event_count"] >= criteria.min_events
    return rank_vaults(df.loc[mask & df[by].notna()], by)


def select_yield_vaults(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Stablecoin yield vaults: every group except perpetual futures DEX vaults.

    Perp DEX vaults are trading strategies, not yield, so they are left out
    of the hero image, whose headline is the best stablecoin yield. Used for
    the hero image and as the base of :py:func:`select_risk_return_vaults`.
    Applies the table thresholds; tokenised funds are exempt from the deposit
    event threshold, like in :py:func:`select_group`.

    :param eligible_df:
        Comparable vaults with AMM pools already left out, see :py:func:`exclude_amm_pools`.

    :param criteria:
        Report thresholds.

    :return:
        Vaults above the TVL and activity thresholds, best first.
    """
    df = eligible_df
    mask = (df["group"] != PERP_DEX) & (df["current_nav"] >= criteria.min_tvl) & ((df["event_count"] >= criteria.min_events) | (df["group"] == TOKENISED_FUND))
    return rank_vaults(df.loc[mask])


def select_risk_return_vaults(yield_universe: pd.DataFrame, ranked_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Vaults of the risk and return chart: the yield vaults and the perp DEX vaults.

    Perp DEX vaults are selected like their tables, with the table TVL
    minimum and no deposit events, and need the three-month return the chart
    plots. The chart shows them as one *Perpetual futures* category, see
    :py:func:`eth_defi.vault_report.charts.create_risk_return_figure`, so
    readers can see how much more volatility trading vaults take for their
    return. The caller then drops Dangerous vaults and vaults whose share
    price did not move.

    :param yield_universe:
        Output of :py:func:`select_yield_vaults`.

    :param ranked_df:
        Comparable vaults, AMM pools already left out unless included.

    :param criteria:
        Report thresholds.

    :return:
        Vaults, best first.
    """
    return rank_vaults(pd.concat([yield_universe, select_group(ranked_df, criteria, PERP_DEX, by=CHART_RETURN)]))


def select_new_vaults(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Best stablecoin yield vaults launched recently.

    The *New vaults* table, and the base of its performance chart. Vault age
    comes from the export ``years`` field, lifetime in years, so the maximum
    age :py:attr:`ReportCriteria.new_vault_max_age` is converted to years of
    365 days. Perp DEX vaults are left out as trading strategies, and the
    lower :py:attr:`ReportCriteria.new_vault_min_tvl` lets young vaults in;
    the deposit event threshold still applies to every group here, so a new
    vault needs real depositors.

    :param eligible_df:
        Comparable vaults with AMM pools already left out, see :py:func:`exclude_amm_pools`.

    :param criteria:
        Report thresholds.

    :return:
        Top :py:attr:`ReportCriteria.top_n` new vaults, best first by the one-month return.
    """
    df = eligible_df
    max_age_years = criteria.new_vault_max_age / datetime.timedelta(days=365)
    mask = ~df["is_perp_dex"] & (df["current_nav"] >= criteria.new_vault_min_tvl) & (df["event_count"] >= criteria.min_events) & (df["years"] <= max_age_years)
    return rank_vaults(df.loc[mask]).head(criteria.top_n)


def select_vaults_by_chain(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Best vaults on each chain, including perpetual DEX chains.

    The *Vaults on each chain* table. Every group is ranked together, with
    no deposit event threshold, so chains like Hyperliquid whose vaults are
    perp DEX vaults are represented too.

    :param eligible_df:
        Comparable vaults with AMM pools already left out, see :py:func:`exclude_amm_pools`.

    :param criteria:
        Report thresholds.

    :return:
        Up to :py:attr:`ReportCriteria.chain_top_n` vaults per chain, sorted
        by chain name, then by one-month return.
    """
    # Rank globally first: groupby(sort=False).head() keeps the first rows of each chain in frame order
    df = rank_vaults(eligible_df.loc[eligible_df["current_nav"] >= criteria.chain_min_tvl])
    top = df.groupby("chain", sort=False).head(criteria.chain_top_n)
    return top.sort_values(["chain", "one_month_cagr_best"], ascending=[True, False])


def select_chain_chart_vaults(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Best vaults on each chain for the per-chain chart.

    Like :py:func:`select_vaults_by_chain`, but ranked by the charts' annualised
    three-month return, :py:data:`CHART_RETURN`, and leaving out vaults rated
    too risky for charts. The chart names the two best vaults on each chain
    and draws the rest as runners-up, see
    :py:func:`eth_defi.vault_report.charts.create_chain_best_figure`.

    :param eligible_df:
        Eligible vaults, AMM pools and unidentified protocols already left out.

    :param criteria:
        Report thresholds.

    :return:
        Up to :py:attr:`ReportCriteria.chain_top_n` vaults per chain, best first within each chain.
    """
    df = exclude_chart_risks(eligible_df.loc[(eligible_df["current_nav"] >= criteria.chain_min_tvl) & eligible_df[CHART_RETURN].notna()], criteria)
    return rank_vaults(df, CHART_RETURN).groupby("chain", sort=False).head(criteria.chain_top_n)


def select_average_yield_vaults(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Select the vaults counted in the average yield charts.

    The universe of the *Yield by chain and protocol* dot plots, and of the
    investability check's prescreen of large chart vaults. The averages are
    meant to describe what stablecoin holders earn, so the filters remove
    what would skew a TVL-weighted mean:

    - small vaults below :py:attr:`ReportCriteria.yield_min_vault_tvl`
    - outliers above :py:attr:`ReportCriteria.yield_max_return`, typically
      capped or broken returns
    - volatile vaults above :py:attr:`ReportCriteria.yield_max_volatility`,
      like tokenised crypto index funds, AMM liquidity positions and most perp
      DEX vaults, which are not stablecoin yield

    There is no deposit event threshold here, unlike the tables; a small
    vault barely moves a TVL-weighted average.
    Vaults with a missing three-month return or volatility fail the
    comparisons and are dropped.

    :param eligible_df:
        Comparable vaults with AMM pools and, for the charts, Dangerous vaults
        already left out, with ``current_nav``, :py:data:`CHART_RETURN` and
        ``three_months_volatility`` columns.

    :param criteria:
        Report thresholds.

    :return:
        Selected vaults, unsorted.
    """
    df = eligible_df
    mask = (df["current_nav"] >= criteria.yield_min_vault_tvl) & (df[CHART_RETURN] <= criteria.yield_max_return) & (df["three_months_volatility"] <= criteria.yield_max_volatility)
    return df.loc[mask]


def calculate_average_yields(yield_vaults: pd.DataFrame, group_column: str) -> pd.DataFrame:
    """Calculate the TVL-weighted average three-month yield per group.

    Weighting by current TVL answers "what does a dollar in this chain or
    protocol earn", which is what the post compares with the T-bill; an
    unweighted mean would let many small vaults outvote the few large ones
    that hold most of the money. Computed as ``sum(tvl * return) / sum(tvl)``
    in one groupby, with the products precomputed in a ``weighted`` column.

    :param yield_vaults:
        Output of :py:func:`select_average_yield_vaults`.

    :param group_column:
        ``chain`` or ``protocol``.

    :return:
        DataFrame indexed by group with columns ``tvl`` (USD), ``avg_return``
        (TVL-weighted annualised three-month return, 0.05 = 5%) and ``vault_count``.
    """
    df = yield_vaults
    grouped = df.assign(weighted=df["current_nav"] * df[CHART_RETURN]).groupby(group_column).agg(tvl=("current_nav", "sum"), weighted=("weighted", "sum"), vault_count=("current_nav", "size"))
    grouped["avg_return"] = grouped["weighted"] / grouped["tvl"]
    return grouped[["tvl", "avg_return", "vault_count"]]


def calculate_chain_yields(yield_vaults: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Average yield of the largest blockchains by TVL.

    Automates the "average vault yield per blockchain" figure, previously a
    screenshot of the `chain overview <https://tradingstrategy.ai/vaults/chains>`__.
    Unlike the protocol charts, unidentified protocols need no extra filter
    here: the input is already comparable vaults only.

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

    The *Yield by protocol, high TVL* dot plot. Placeholder protocols, such
    as generic ERC-4626 vaults of unidentified protocols, are left out, so
    the :py:data:`OTHER_PROTOCOL` pile never shows up as a protocol. The TVL
    floor is applied before picking the largest, so a month with few large
    protocols shows fewer rows rather than small ones.

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


def calculate_high_yield_protocols(yield_vaults: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Average yield of the highest-yielding vault protocols.

    The *Yield by protocol, high yield* dot plot. Complements
    :py:func:`calculate_protocol_yields`, which shows the largest
    protocols: this ranks all identified protocols with at least
    :py:attr:`ReportCriteria.yield_high_yield_min_protocol_tvl` TVL by their
    TVL-weighted average yield. Small protocols with a few vaults can top
    this chart; the TVL floor keeps single test vaults out, and the chart's
    TVL column shows how much money earns the yield.

    :param yield_vaults:
        Output of :py:func:`select_average_yield_vaults`.

    :param criteria:
        Report thresholds.

    :return:
        See :py:func:`calculate_average_yields`, the top
        :py:attr:`ReportCriteria.yield_top_protocols` protocols by average yield.
    """
    identified = yield_vaults.loc[yield_vaults["protocol_identified"]]
    yields = calculate_average_yields(identified, "protocol")
    return yields.loc[yields["tvl"] >= criteria.yield_high_yield_min_protocol_tvl].nlargest(criteria.yield_top_protocols, "avg_return")


#: Legacy vault page URLs and their current form. The website moved vault pages from
#: ``/trading-view/`` and redirects the old paths; exports and posts made before the move still carry them.
#:
#: The post's writing rules require the current paths, and rewriting saves
#: readers a redirect per click. Applied in order: vault pages first,
#: because the per-chain listing pattern also matches the start of a vault
#: page path and would turn it into a ``/vaults/chains/...`` link.
LEGACY_VAULT_URLS = (
    # /trading-view/{chain}/vaults/{slug} -> /vaults/{slug}
    (re.compile(r"https://tradingstrategy\.ai/trading-view/[a-z0-9-]+/vaults/"), "https://tradingstrategy.ai/vaults/"),
    # /trading-view/{chain}/vaults -> /vaults/chains/{chain}
    (re.compile(r"https://tradingstrategy\.ai/trading-view/(?!vaults\b)([a-z0-9-]+)/vaults\b"), r"https://tradingstrategy.ai/vaults/chains/\1"),
    # /trading-view/vaults... -> /vaults...
    (re.compile(r"https://tradingstrategy\.ai/trading-view/vaults\b"), "https://tradingstrategy.ai/vaults"),
)


def canonical_vault_urls(text: str) -> str:
    """Rewrite legacy vault page URLs to the website's current ``/vaults/`` paths.

    Used on the export's ``trading_strategy_link`` values and on the HTML
    copied from the previous post, see :py:data:`LEGACY_VAULT_URLS`.

    :param text:
        A URL, or HTML with links.

    :return:
        The text with legacy vault URLs rewritten, following the website's redirects.
    """
    for pattern, replacement in LEGACY_VAULT_URLS:
        text = pattern.sub(replacement, text)
    return text


def find_period(period_results: list[dict] | None, period: str) -> dict:
    """Find one period's results in a vault's ``period_results``.

    The top vaults export stores per-period metrics as a list of dicts, one
    per period, see :py:class:`eth_defi.research.vault_metrics.PeriodMetrics`,
    rather than flat columns. The report reads the one-month TVL and the
    three-month maximum drawdown from here.

    :param period_results:
        ``period_results`` list of a top vaults JSON record, or ``None``.

    :param period:
        Period name, e.g. ``1M`` or ``3M``.

    :return:
        The period's results, or an empty dict if missing.
    """
    return next((result for result in (period_results or []) if result.get("period") == period), {})


def calculate_tvl_changes(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Find the vaults with the largest TVL changes over the last month, in dollars.

    The *Inflows and outflows by vault* chart. Uses the one-month period's
    ``tvl_start`` and ``tvl_end`` from the top vaults export
    ``period_results``. A TVL change includes both deposits and redemptions
    and the vault's own returns. Net flows would be cleaner, but the export's
    estimated ``flow_value`` covers only about a fifth of the vaults, so
    dollar TVL changes are used for every vault alike.

    Some funds are listed under several share token addresses with the same
    TVL; duplicates with the same name, chain and change are counted once.
    Separate share classes of one fund have different TVLs and each appear.

    Increases and decreases are ranked separately, so a month with one huge
    outflow still shows the largest inflows. A single change far larger than
    the rest is drawn off scale by
    :py:func:`eth_defi.vault_report.charts.create_tvl_change_figure`, which
    labels the bars with ``name``, falling back to ``address``.

    :param eligible_df:
        Output of :py:func:`filter_eligible_vaults` with the investability
        check's exclusions applied, with ``period_results``, ``name`` and
        ``chain`` columns. Unidentified protocols are kept: this is a TVL
        summary, not a performance comparison.

    :param criteria:
        Report thresholds.

    :return:
        The :py:attr:`ReportCriteria.tvl_change_top_n` largest increases and
        decreases, with ``tvl_start``, ``tvl_end`` and ``tvl_change`` columns
        in USD, sorted from the largest increase to the largest decrease.
    """
    return _largest_changes(_one_month_tvl_changes(eligible_df), criteria.tvl_change_top_n)


def calculate_chain_tvl_changes(eligible_df: pd.DataFrame, criteria: ReportCriteria) -> pd.DataFrame:
    """Find the blockchains whose vault TVL changed the most over the last month, in dollars.

    The *Inflows and outflows by blockchain* chart. Sums the one-month TVL
    changes of each chain's vaults, after the same deduplication as
    :py:func:`calculate_tvl_changes`, so a chain's bar is its net change:
    inflows and outflows of its vaults cancel out.

    :param eligible_df:
        Output of :py:func:`filter_eligible_vaults`.

    :param criteria:
        Report thresholds.

    :return:
        The :py:attr:`ReportCriteria.tvl_change_top_n` largest net increases and
        decreases, indexed by chain name, with ``name``, ``address``,
        ``tvl_start``, ``tvl_end`` and ``tvl_change`` columns in USD, sorted
        from the largest increase to the largest decrease.
    """
    by_chain = _one_month_tvl_changes(eligible_df).groupby("chain")[["tvl_start", "tvl_end", "tvl_change"]].sum()
    # The chart labels bars with name, falling back to address; for a chain both are the chain name
    by_chain = by_chain.assign(name=by_chain.index, address=by_chain.index)
    return _largest_changes(by_chain, criteria.tvl_change_top_n)


def _one_month_tvl_changes(eligible_df: pd.DataFrame) -> pd.DataFrame:
    """One-month TVL change of each vault.

    Vaults without one-month TVL data, e.g. too young for the period, are
    dropped. Duplicate listings with the same name, chain and change are
    counted once.

    :param eligible_df:
        Vault metrics with ``period_results``, ``name`` and ``chain`` columns.

    :return:
        The input rows with float ``tvl_start``, ``tvl_end`` and ``tvl_change`` columns, in USD.
    """
    one_month = eligible_df["period_results"].apply(find_period, period="1M")
    df = eligible_df.assign(
        tvl_start=one_month.apply(lambda p: p.get("tvl_start")).astype(float),
        tvl_end=one_month.apply(lambda p: p.get("tvl_end")).astype(float),
    ).dropna(subset=["tvl_start", "tvl_end"])
    return df.assign(tvl_change=df["tvl_end"] - df["tvl_start"]).drop_duplicates(subset=["name", "chain", "tvl_change"])


def _largest_changes(df: pd.DataFrame, top: int) -> pd.DataFrame:
    """The largest increases and decreases, from the largest increase to the largest decrease.

    Unchanged rows are dropped. The decreases are reversed after
    :py:meth:`pandas.DataFrame.nsmallest`, so the result runs from the
    largest increase down to the largest decrease, the bar order of the chart.

    :param df:
        Rows with a ``tvl_change`` column in USD.

    :param top:
        Number of increases and of decreases.

    :return:
        At most ``2 * top`` rows.
    """
    return pd.concat([df.loc[df["tvl_change"] > 0].nlargest(top, "tvl_change"), df.loc[df["tvl_change"] < 0].nsmallest(top, "tvl_change").iloc[::-1]])


def format_return(net: Percent | None, gross: Percent | None) -> str:
    """Format an annualised return, preferring the net value.

    The net return is shown when fee data exists and the gross return
    otherwise, without a net or gross marker, like the website. Values at
    the export's 10,000% cap, :py:data:`CAPPED_ANNUALISED_RETURN`, are shown
    as :py:data:`CAPPED_RETURN_LABEL`, because the capped number itself is
    not the vault's return.

    :param net:
        Net return, 0.01 = 1%, or ``None`` when fees are unknown.

    :param gross:
        Gross return.

    :return:
        E.g. ``12.3%``, ``>9,999%`` for capped values, or ``---``.
    """
    value = net if pd.notna(net) else gross
    if pd.isna(value):
        return "---"
    return CAPPED_RETURN_LABEL if value >= CAPPED_ANNUALISED_RETURN else f"{value:,.1%}"


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


def format_tvl(current: USDollarAmount | None) -> str:
    """Format the current TVL for a table cell.

    Tables show the current TVL only, in dollars with ``k``, ``M`` and ``B``
    suffixes rather than full digits, see the writing rules in
    ``README-blog-post-outline.md``. The bundle's CSV files keep the exact
    values.

    :param current:
        Current TVL.

    :return:
        E.g. ``$275k``, ``$1.2M`` or ``$2.1B``.
    """
    if pd.isna(current):
        return "---"
    # The thresholds sit where rounding would roll over into the next unit,
    # so e.g. $999.7k becomes $1.0M, not $1000k
    if current >= 999_950_000:
        return f"${current / 1e9:,.1f}B"
    if current >= 999_500:
        return f"${current / 1e6:,.1f}M"
    if current >= 1_000:
        return f"${current / 1e3:,.0f}k"
    return f"${current:,.0f}"


def web_link(text: str, url: str | None) -> str:
    """Escaped HTML link, or the escaped text alone when the URL is not a web link.

    Links come from exports and other data: accept only ``https://`` links,
    never e.g. ``javascript:`` URLs.

    :param text:
        Link text.

    :param url:
        Target URL.

    :return:
        HTML.
    """
    content = html.escape(text)
    return f'<a href="{html.escape(url)}">{content}</a>' if isinstance(url, str) and url.startswith("https://") else content


def format_vault_cells(row: pd.Series, sparkline_ids: frozenset[str] = frozenset()) -> dict[str, str]:
    """Format one vault as HTML table cells.

    Every value is HTML-escaped here, because names, symbols and links come
    from onchain data and the export, and the cells are pasted into the
    Ghost post as raw HTML. Vault names link to the vault page, chains and
    protocols to their website pages; unidentified protocols show as
    :py:data:`OTHER_PROTOCOL` without a link.

    :param row:
        Vault metrics row from :py:func:`eth_defi.vault_report.data.prepare_vault_metrics`.

    :param sparkline_ids:
        Vault ids with a published sparkline; other vaults get an empty sparkline cell.

    :return:
        Column label -> escaped cell HTML, for all columns in :py:data:`VAULT_TABLE_COLUMNS`.
    """
    vault_id = row["id"]
    # max-width:none stops the blog theme from shrinking the image inside a narrow table cell
    sparkline = f'<img src="{SPARKLINE_URL.format(vault_id=vault_id)}" width="72" height="18" alt="" style="width:72px;max-width:none;height:18px;vertical-align:middle">' if vault_id in sparkline_ids else ""
    return {
        "Vault": web_link(row["name"] or row["address"], row["trading_strategy_link"]),
        "3M history": sparkline,
        "1M ann.": html.escape(format_return(row["one_month_cagr_net"], row["one_month_cagr"])),
        "3M ann.": html.escape(format_return(row["three_months_cagr_net"], row["three_months_cagr"])),
        "Lifetime ann.": html.escape(format_return(row["cagr_net"], row["cagr"])),
        "3M Sharpe": html.escape(format_sharpe(row["three_months_sharpe_best"])),
        "TVL": html.escape(format_tvl(row["current_nav"])),
        "Age (y)": f"{row['years']:.2f}" if pd.notna(row["years"]) else "---",
        "Token": html.escape(row["denomination"] or ""),
        "Chain": web_link(row["chain"], _get_trading_strategy_chain_link(row["chain"])),
        "Protocol": web_link(row["protocol"], _get_trading_strategy_protocol_link(row["protocol_slug"])) if row["protocol_identified"] else OTHER_PROTOCOL,
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
    # Tables have at most a few dozen rows, so iterrows() is fast enough and keeps the cell code readable
    for rank, (_, vault) in enumerate(section.vaults_df.iterrows(), start=1):
        cells = format_vault_cells(vault, section.sparkline_ids)
        tds = "".join(f'<td style="{_style(c)}">{cells[c]}</td>' for c in columns)
        if section.numbered:
            tds = f'<td style="text-align:right">{rank}</td>' + tds
        rows.append(f"<tr>{tds}</tr>")

    return "<table>\n<thead>\n<tr>" + header + "</tr>\n</thead>\n<tbody>\n" + "\n".join(rows) + "\n</tbody>\n</table>"


def is_blacklisted(vaults_df: pd.DataFrame) -> pd.Series:
    """Find blacklisted vaults.

    A vault is blacklisted when the export rates it ``Blacklisted``, which
    includes vaults with bad flags when the export was made (see
    :py:func:`eth_defi.research.vault_metrics.apply_bad_flag_check`), or when
    this repository's :py:mod:`eth_defi.vault.flag` gives it a bad flag. The
    second rule applies blacklist entries added after the export, e.g. by the
    investability check, right away, without waiting for the next export
    cycle. Only flags in :py:data:`eth_defi.vault.flag.BAD_FLAGS` count:
    ``review_needed`` vaults stay in the report.

    Used by :py:func:`filter_eligible_vaults` and :py:func:`select_tvl_history_vaults`.
    The flag lookup is a Python call per vault, keyed by address and protocol.

    :param vaults_df:
        Vault metrics with ``risk``, ``address`` and ``protocol`` columns.

    :return:
        Boolean series, ``True`` for blacklisted vaults.
    """
    flagged = [isinstance(address, str) and bool(get_vault_special_flags(address, protocol) & BAD_FLAGS) for address, protocol in zip(vaults_df["address"], vaults_df["protocol"], strict=True)]
    return (vaults_df["risk"] == BLACKLISTED_RISK) | pd.Series(flagged, index=vaults_df.index)


def select_tvl_history_vaults(vaults_df: pd.DataFrame) -> pd.DataFrame:
    """Select vaults whose TVL history is counted in the market TVL chart.

    Excludes blacklisted vaults, see :py:func:`is_blacklisted`, like the
    website's historical TVL charts, so the totals match the website.
    Unlike the rankings, stale vaults, unidentified protocols, AMM pools and
    vaults the investability check excluded are kept: the TVL charts report
    where money is, not where to invest, and a stale vault still has a
    history up to when it stopped.

    :param vaults_df:
        All vault metrics, :py:attr:`eth_defi.vault_report.data.VaultReportData.vaults_df`.

    :return:
        Vaults to include.
    """
    return vaults_df.loc[~is_blacklisted(vaults_df)]


def _group_tvl_history(tvl_history: pd.DataFrame, groups: pd.Series, top_n: int, excluded: str | None = None) -> pd.DataFrame:
    """Sum vault TVL history per group, keeping the largest groups and summing the rest.

    Shared by the stacked TVL charts. Groups are ranked by their TVL in the
    latest week, so the legend order reflects where money is now, not where
    it was a year ago. Everything outside the top ``top_n``, and the
    ``excluded`` group, is stacked into one ``Other`` band so the chart
    keeps a readable number of colours while the stack still sums to the
    total TVL.

    :param tvl_history:
        Output of :py:func:`eth_defi.vault_report.data.read_vault_tvl_history`, one column per vault id.

    :param groups:
        Vault id -> group name.

    :param top_n:
        Groups shown separately, by their latest TVL.

    :param excluded:
        Group never shown separately, e.g. unknown protocols.

    :return:
        DataFrame indexed by week with one TVL column per group in USD,
        largest first, then ``Other`` if any vaults remain.
    """
    # Transpose so vault ids are rows and can be grouped by the vault id -> group mapping.
    # Weeks before a group's first vault sum to NaN with min_count=1, and fillna(0) turns them
    # into zero, so the stacked areas have no gaps.
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

    The *Stablecoin TVL by DeFi vault protocol* chart. The caller passes
    DeFi vaults only; tokenised funds have their own NAV chart, because
    their share classes would dominate this one. Grouping by
    ``protocol_label`` sends unidentified protocols to the
    :py:data:`OTHER_PROTOCOL` band, so they count in the total without
    showing as a protocol.

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


def calculate_chain_tvl_history(tvl_history: pd.DataFrame, vaults_df: pd.DataFrame, top_n: int = 7) -> pd.DataFrame:
    """Sum vault TVL history per blockchain.

    The *Stablecoin TVL by blockchain* chart: the same DeFi vaults as
    :py:func:`calculate_protocol_tvl_history`, grouped by where they live.

    :param tvl_history:
        Output of :py:func:`eth_defi.vault_report.data.read_vault_tvl_history`, one column per vault id.

    :param vaults_df:
        Vault metrics with a ``chain`` column, indexed by vault id.

    :param top_n:
        Blockchains shown separately, by their latest TVL. The rest are summed as ``Other``.

    :return:
        DataFrame with one column per blockchain, largest first, then ``Other`` if more blockchains remain.
    """
    return _group_tvl_history(tvl_history, vaults_df["chain"], top_n)


def calculate_fund_nav_history(tvl_history: pd.DataFrame, funds_df: pd.DataFrame, top_n: int = 7) -> pd.DataFrame:
    """Sum tokenised fund NAV history per fund.

    The *Stablecoin NAV by tokenised fund* chart. A fund deployed on several
    chains under the same name is counted as one fund, because readers know
    the fund, not its deployments. A fund without a name falls back to its
    address.

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
