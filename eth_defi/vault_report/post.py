"""Assemble the monthly vault report blog post HTML.

The post body is Ghost-compatible HTML:

- Tables, the podcast list and the table of contents placeholder are wrapped
  in ``<!--kg-card-begin: html-->`` comments, which Ghost imports as raw HTML
  cards, keeping their markup intact
- Charts are Ghost image cards
- The post has no editor callouts. The parts the editor writes (the month's
  highlight, the report content updates, community news and commentary, the
  feature image) are listed in the editor workflow of ``README-vault-report.md``.

Evergreen sections (*About the report*, *Next steps*) are copied from the
previous report post, so edits made in Ghost carry over month to month. The
*Partners* section is rendered from :py:data:`PARTNERS`, so partners are named
by organisation, not by X handle.

The section order, headings, introductions and notes follow the *Writing
rules* in ``README-blog-post-outline.md``: keep them when changing the
templates below, and update the rules when a decision changes.

The templates are data, not code: :py:data:`BEST_SECTIONS` defines each ranked
vault group once, and :py:data:`SECTION_TEMPLATES` the order and text of every
data section. :py:func:`build_post_html` walks the templates and includes a
section only when its table or one of its charts was generated, so a missing
input (no prices, a failed benchmark download, an empty group) silently
removes a section instead of leaving an empty heading in the post.

:py:func:`build_post_html` is called twice per run by
:py:mod:`eth_defi.vault_report.report`: once with bundle-relative image paths
for ``post.html`` and the local preview, and once with the uploaded Ghost
image URLs for the draft. It is a pure function of :py:class:`PostContext`,
so both renderings have the same text.
"""

import datetime
import html
import itertools
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from eth_defi.vault_report.ghost import BLOG_URL, GhostPost, strip_ghost_ref
from eth_defi.vault_report.podcasts import PODCAST_PAGE_URL, PodcastEpisode, render_podcast_episodes
from eth_defi.vault_report.sections import AMM, CHART_RETURN, LENDING, PERP_DEX, RWA, TOKENISED_FUND, canonical_vault_urls, web_link

#: Slug prefix of the monthly report posts. The Content API finds the previous
#: report by this prefix, so changing it breaks the month-to-month continuity
#: of the evergreen sections and the link to the previous report.
REPORT_SLUG_PREFIX = "the-best-performing-stablecoin-vaults"

#: Vault pages on the website, linked from the section introductions
VAULTS_URL = "https://tradingstrategy.ai/vaults"

#: Glossary on the website, linked from the section introductions
GLOSSARY_URL = "https://tradingstrategy.ai/glossary"

#: Ghost theme placeholder that the theme fills with a table of contents of the post headings, as in the earlier reports
TABLE_OF_CONTENTS = '<!--kg-card-begin: html-->\n<div id="table-of-contents"></div>\n<!--kg-card-end: html-->'


#: Report partners as (organisation name, link), thanked in the *Partners* section.
#: Maintained here rather than copied from the previous post, so the names stay
#: organisation names, not X handles; edit this list when the partners change.
PARTNERS = (
    ("Arbitrum DAO", "https://x.com/@arbitrumdao_gov"),
    ("Envio", "https://x.com/@envio_indexer"),
    ("dRPC", "https://x.com/@dRPCorg"),
    ("IPOR", "https://x.com/@ipor_io"),
    ("Goldsky", "https://x.com/@goldskyio"),
    ("IceCreamSwap", "https://x.com/@icecream_swap"),
    ("GRVT", "https://x.com/@grvt_io"),
    ("Orderly Network", "https://x.com/@OrderlyNetwork"),
    ("Lagoon Finance", "https://x.com/@lagoon_finance"),
    ("Webacy", "https://x.com/mywebacy"),
    ("Quants Spaces", "https://quants.space/"),
)


def render_partners_section(partners: tuple[tuple[str, str], ...] = PARTNERS) -> str:
    """Render the *Partners* section, thanking each partner by name with a link.

    Rendered from :py:data:`PARTNERS` every month rather than copied from the
    previous post like the other evergreen sections, because earlier posts
    named partners by X handle; the writing rules name them by organisation.

    :param partners:
        (organisation name, link) pairs.

    :return:
        Section HTML.
    """
    links = [web_link(name, url) for name, url in partners]
    names = ", ".join(links[:-1]) + f" and {links[-1]}" if len(links) > 1 else "".join(links)
    return f'<h2 id="partners">Partners</h2><p>We want to thank our partners {names} for getting this report together.</p>'


#: Evergreen sections used when there is no previous post to copy them from, by heading id.
#:
#: Normally :py:func:`build_post_html` copies these sections from the previous
#: report, so the editor's wording in Ghost carries over. The defaults cover a
#: run without the Ghost Content API (local previews, tests) or a previous post
#: that lacks the heading; the editor can then fix the text in Ghost once and
#: later reports inherit it.
DEFAULT_EVERGREEN_SECTIONS = {
    "about-the-report": (f'<h2 id="about-the-report">About the report</h2><p>In this post, we examine the performance of <a href="{GLOSSARY_URL}/decentralised-finance" rel="noreferrer">DeFi</a> vaults. Vaults can be considered "self-custodial investment strategies" in traditional finance: smart contracts that let users deposit funds from their cryptocurrency wallets and execute a predefined strategy with investors\' money.</p>'),
    "next-steps": ('<h2 id="next-steps">Next steps</h2><p>Visit our <a href="https://tradingstrategy.ai/vaults">vaults page</a> for real-time dashboards. If you have any questions, <a href="https://tradingstrategy.ai/community">contact us on Discord, email or Twitter</a>.</p>'),
}


@dataclass(slots=True, frozen=True)
class SectionTemplate:
    """Static content of one data section of the post.

    :py:func:`build_post_html` renders a section as its heading, introduction,
    criteria notes, charts and table, in that order. A section is included
    when its table or any of its charts exists. A grouping heading, see
    :py:attr:`group`, is included when any of its subsections is, so the post
    never shows a heading with nothing under it.

    The writing rules give every section an introduction paragraph that
    links to the matching tradingstrategy.ai page or glossary entry, except
    *The best-performing vaults*, which goes straight to its subsections;
    ``test_every_section_introduces_itself_with_website_links`` enforces it.
    """

    #: Section key: the table key in :py:attr:`PostContext.tables` and the section notes key
    key: str

    #: Heading anchor id
    heading_id: str

    #: Heading text
    heading: str

    #: Introduction paragraph HTML, or empty
    intro: str = ""

    #: (chart key in :py:attr:`PostContext.charts`, alt text) pairs shown above the table
    charts: tuple[tuple[str, str], ...] = ()

    #: Heading level, 2 for sections and 3 for subsections
    level: int = 2

    #: A heading that groups the subsections after it: included when any of them is
    group: bool = False


@dataclass(slots=True, frozen=True)
class BestSection:
    """One ranked vault group of the post: its table, performance chart and section.

    The single definition of a best-performing section. The tables, the chart
    selection, the chart panel, the criteria notes and the post section are all
    derived from it, see :py:data:`BEST_SECTIONS`. Keeping them in one place
    means a new vault group, or a changed ranking metric, cannot leave the
    table, the chart and the investability check candidates
    (:py:func:`eth_defi.vault_report.report.collect_top_lists`) out of step.

    Tables rank by the one-month return like the website's live ranking,
    while the charts rank by the steadier three-month return, see
    :py:attr:`chart_metric`, so the two lists of a section may differ.
    """

    #: Section key, e.g. ``lending``: the table and criteria notes key
    key: str

    #: Vault group, see :py:func:`eth_defi.vault_report.sections.classify_vault`
    group: str

    #: Section heading
    heading: str

    #: Heading anchor id
    heading_id: str

    #: The ranked vaults in chart titles and alt texts, e.g. ``the best-performing lending vaults``
    subject: str

    #: What the chart compares the vaults against
    benchmark: str = "their benchmarks"

    #: Table ranking column
    metric: str = "one_month_cagr_best"

    #: Chart lines: ``equity`` curves, or the rolling ``sharpe`` ratio ranked by :py:attr:`metric`
    measure: Literal["equity", "sharpe"] = "equity"

    #: How the chart subtitle describes the ranking, e.g. ``funds by 3M return``
    ranked_by: str = "by 3M return"

    #: Live page on the website, without ``https://``, for the chart footer
    link: str = "tradingstrategy.ai/vaults"

    #: Introduction paragraph HTML, with links to the website
    intro: str = ""

    @property
    def chart_key(self) -> str:
        """Key of the performance chart in :py:attr:`PostContext.charts`."""
        return f"{self.key}_performance"

    @property
    def chart_metric(self) -> str:
        """Column that picks and ranks the chart vaults: the steadier three-month return, or the Sharpe ratio."""
        return self.metric if self.measure == "sharpe" else CHART_RETURN

    @property
    def template(self) -> "SectionTemplate":
        """The post section of this group."""
        alt = f"90-day performance of {self.subject} against {self.benchmark}"
        return SectionTemplate(key=self.key, heading_id=self.heading_id, heading=self.heading, intro=self.intro, charts=((self.chart_key, alt),), level=3)


#: Chart key of the new vaults performance chart in :py:attr:`PostContext.charts`
NEW_VAULTS_CHART = "new_performance"

#: Best-performing vault groups, each with a table and a performance chart;
#: :py:data:`SECTION_TEMPLATES` sets their display order. Perpetual futures DEX
#: vaults appear twice, ranked by return and by Sharpe ratio, because a raw
#: return ranking of trading vaults favours the most volatile ones.
BEST_SECTIONS = (
    BestSection(
        key="lending",
        group=LENDING,
        heading="Lending vaults",
        heading_id="best-performing-lending-vaults",
        subject="the best-performing lending vaults",
        intro=f'<p>Vaults that supply stablecoins to <a href="{GLOSSARY_URL}/lending-protocol">lending protocols</a> such as Morpho and Euler, and earn the interest borrowers pay. See all <a href="{VAULTS_URL}/strategies/lending">lending vaults</a>.</p>',
    ),
    BestSection(
        key="rwa",
        group=RWA,
        heading="Real-world asset (RWA) vaults",
        heading_id="best-performing-rwa-vaults",
        subject="the best-performing RWA vaults",
        intro=f'<p>Vaults that invest in, lend against or finance <a href="{GLOSSARY_URL}/rwa">real-world assets</a>, such as private credit, trade finance and royalties. See all <a href="{VAULTS_URL}/strategies/rwa">RWA vaults</a>.</p><p>Real-world asset vaults are a new category and may still contain misclassifications, as we are refining the different RWA categories.</p>',
    ),
    BestSection(
        key="perp_dex",
        group=PERP_DEX,
        heading="Perpetual futures DEX vaults by return",
        heading_id="best-performing-perp-dex-vaults",
        subject="the best-performing perp DEX vaults",
        intro=f'<p>Vaults on <a href="{GLOSSARY_URL}/perpetual-future">perpetual futures</a> DEXes such as Hyperliquid, GRVT and Lighter, which are discretionary, directional algorithmic or market making vaults. See all <a href="{VAULTS_URL}/strategies/perpetual-futures">perpetual futures vaults</a>.</p>',
        benchmark="BTC and ETH",
    ),
    BestSection(
        key="perp_dex_sharpe",
        group=PERP_DEX,
        heading="Perpetual futures DEX vaults by Sharpe ratio",
        heading_id="best-performing-perp-dex-vaults-by-sharpe",
        subject="perp DEX vaults with the best Sharpe ratio",
        intro=f'<p>The same vaults ranked by their three-month <a href="{GLOSSARY_URL}/sharpe">Sharpe ratio</a>, the return per unit of volatility. The ranking rewards steady returns over high but volatile ones; see <a href="{GLOSSARY_URL}/risk-adjusted-return">risk-adjusted return</a>.</p>',
        benchmark="BTC and ETH",
        metric="three_months_sharpe_best",
        measure="sharpe",
        ranked_by="by 3M Sharpe ratio",
    ),
    BestSection(
        key="amm",
        group=AMM,
        heading="AMM pools",
        heading_id="best-performing-amm-pools",
        subject="the best-performing AMM pools",
        intro=f'<p><a href="{GLOSSARY_URL}/amm">AMM</a> liquidity pools, such as GMX GM and GLV pools and YieldBasis pools, earn trading fees, but their returns also move with the prices of the pooled crypto assets. See all <a href="{VAULTS_URL}/strategies/amm">AMM vaults</a>.</p>',
    ),
    BestSection(
        key="tokenised_funds",
        group=TOKENISED_FUND,
        heading="Tokenised funds",
        heading_id="best-performing-tokenised-funds",
        subject="the best-performing tokenised funds",
        intro=f'<p>The <a href="{GLOSSARY_URL}/tokenised-fund">tokenised funds</a> with the best returns. Their yields follow money market, treasury and credit rates, and many are open only to qualified or institutional investors. See all <a href="{VAULTS_URL}/funds">tokenised funds</a>.</p><p>Often there is no clear distinction between a vault and a tokenised fund. From the technical point of view, both use exactly the same smart contract technology with whitelisting. For the report, we define a project as a tokenised fund if we have found evidence that they have self-declared themselves as a tokenised fund.</p>',
        link="tradingstrategy.ai/vaults/funds",
    ),
)


#: Post sections of the best-performing vault groups, by key
BEST_TEMPLATES = {section.key: section.template for section in BEST_SECTIONS}

#: Data sections in display order. Follows the *Writing rules* in ``README-blog-post-outline.md``:
#: the best-performing vaults first, with no introduction or caption, then yield by chain
#: and protocol, risk and return, vaults and tokenised funds TVL, and inflows and outflows
#: last, with its by-vault and by-blockchain subsections. Every other section opens with
#: an introduction linking to tradingstrategy.ai.
SECTION_TEMPLATES = (
    SectionTemplate(
        key="best",
        heading_id="the-best-performing-vaults",
        heading="The best-performing vaults",
        # No introduction and no T-bill caption: the heading leads straight to its subsections
        group=True,
    ),
    SectionTemplate(
        key="new",
        heading_id="best-performing-new-vaults",
        heading="New vaults",
        intro=f'<p>The best-performing vaults launched recently. Their short history makes their returns less reliable, so treat them as vaults to watch. See all <a href="{VAULTS_URL}/new-vaults">new vaults</a>.</p>',
        charts=((NEW_VAULTS_CHART, "Performance of the best-performing new vaults since their launch against their benchmarks"),),
        level=3,
    ),
    *(BEST_TEMPLATES[key] for key in ("lending", "rwa", "perp_dex", "perp_dex_sharpe", "amm")),
    SectionTemplate(
        key="by_chain",
        heading_id="best-performing-vaults-on-each-chain",
        heading="Vaults on each chain",
        intro=f'<p>The best-performing vaults on each blockchain, for investors who stay on one chain. The <a href="{VAULTS_URL}/chains">blockchains page</a> lists the vaults of every chain.</p>',
        charts=(("by_chain_best", "The two best-performing vaults on each chain"),),
        level=3,
    ),
    BEST_TEMPLATES["tokenised_funds"],
    SectionTemplate(
        key="average_yields",
        heading_id="yield-by-chain-and-protocol",
        heading="Yield by chain and protocol",
        intro=f'<p>To better understand market competition between blockchains and protocols, we also compare projects by best returns, not just highest TVL.</p><p>In each chart, a small dot is a vault, and the large dot is the <a href="{GLOSSARY_URL}/total-value-locked-tvl">TVL</a>-weighted average annualised three-month return, compared with the 3-month US Treasury bill as the <a href="{GLOSSARY_URL}/risk-free-rate">risk-free rate</a>.</p>',
        group=True,
    ),
    SectionTemplate(
        key="protocol_yields",
        heading_id="average-yield-by-protocol-high-tvl",
        heading="Yield by protocol, high TVL",
        intro=f'<p>The largest vault protocols by TVL, which hold most of the stablecoin deposits. See all <a href="{VAULTS_URL}/protocols">vault protocols</a>.</p>',
        charts=(("protocol_yields", "Stablecoin vault yield of the largest protocols against the US Treasury bill"),),
        level=3,
    ),
    SectionTemplate(
        key="protocol_high_yields",
        heading_id="average-yield-by-protocol-high-yield",
        heading="Yield by protocol, high yield",
        intro=f'<p>The protocols paying the highest average yield. High yields usually come with higher risk, so check where the yield comes from before investing. The <a href="{VAULTS_URL}/yield-protocol">vault yield by protocol</a> chart compares the yields of every protocol.</p>',
        charts=(("protocol_high_yields", "Stablecoin vault yield of the highest-yielding protocols against the US Treasury bill"),),
        level=3,
    ),
    SectionTemplate(
        key="chain_yields",
        heading_id="average-yield-by-blockchain",
        heading="Yield by blockchain",
        intro=f'<p>Which blockchains pay the most on stablecoins. Browse the vaults of each chain on the <a href="{VAULTS_URL}/chains">blockchains page</a>.</p>',
        charts=(("chain_yields", "Stablecoin vault yield on the largest blockchains against the US Treasury bill"),),
        level=3,
    ),
    SectionTemplate(
        key="risk_return",
        heading_id="risk-and-return",
        heading="Risk and return",
        intro=f'<p>Higher returns usually come with higher <a href="{GLOSSARY_URL}/volatility">volatility</a>. Vaults above and to the left of the crowd offer better returns for their risk. The <a href="{VAULTS_URL}/yield-risk">vault yield and risk</a> chart compares the returns of all vaults with their risk ratings.</p>',
        charts=(("risk_return", "Volatility risk and return of stablecoin vaults by strategy"),),
    ),
    SectionTemplate(
        key="tvl",
        heading_id="vaults-and-tokenised-funds-tvl",
        heading="Vaults and tokenised funds TVL",
        intro=f'<p>How much money stablecoin vaults and tokenised funds hold, by protocol, blockchain and fund, over the last 12 months. The <a href="{VAULTS_URL}/historical-tvl-stablecoin">stablecoin TVL</a> chart has the full history.</p>',
        group=True,
    ),
    SectionTemplate(
        key="protocol_tvl",
        heading_id="stablecoin-tvl-by-defi-vault-protocol",
        heading="Stablecoin TVL by DeFi vault protocol",
        intro=f'<p>Where the stablecoins deposited in DeFi vaults are, by protocol, over the last 12 months. Tokenised funds are shown separately below. The <a href="{VAULTS_URL}/historical-tvl-protocol">live chart</a> has the full history.</p>',
        charts=(("protocol_tvl", "Stablecoin TVL by DeFi vault protocol"),),
        level=3,
    ),
    SectionTemplate(
        key="chain_tvl",
        heading_id="stablecoin-tvl-by-blockchain",
        heading="Stablecoin TVL by blockchain",
        intro=f'<p>The same deposits by the blockchain they are on. The <a href="{VAULTS_URL}/historical-tvl-chain">live chart</a> has the full history.</p>',
        charts=(("chain_tvl", "Stablecoin TVL by blockchain"),),
        level=3,
    ),
    SectionTemplate(
        key="fund_nav",
        heading_id="stablecoin-nav-by-tokenised-fund",
        heading="Stablecoin NAV by tokenised fund",
        intro=f'<p>Tokenised funds bring money market, treasury and credit funds onchain. The chart shows their net asset value (NAV) by fund over the last 12 months. See the <a href="{VAULTS_URL}/funds">tokenised funds page</a> for the live data.</p>',
        charts=(("fund_nav", "Stablecoin NAV by tokenised fund"),),
        level=3,
    ),
    SectionTemplate(
        key="flows",
        heading_id="inflows-and-outflows",
        heading="Inflows and outflows",
        intro=f'<p>Where the money moved: the vaults and blockchains whose total value locked grew or shrank the most over the last 30 days. A TVL change includes deposits, redemptions and the returns the vaults earned. Browse all vaults and their current TVL on the <a href="{VAULTS_URL}">vaults page</a>.</p>',
        group=True,
    ),
    SectionTemplate(
        key="tvl_changes",
        heading_id="inflows-and-outflows-by-vault",
        heading="Inflows and outflows by vault",
        intro=f'<p>The vaults with the largest TVL increases and decreases, in dollars. The <a href="{VAULTS_URL}/high-tvl">high TVL vaults</a> page ranks the vaults with at least $2M TVL.</p>',
        charts=(("tvl_changes", "The largest vault TVL increases and decreases over the last 30 days"),),
        level=3,
    ),
    SectionTemplate(
        key="chain_tvl_changes",
        heading_id="inflows-and-outflows-by-blockchain",
        heading="Inflows and outflows by blockchain",
        intro=f'<p>The net TVL change of all vaults on each blockchain, for the blockchains with the largest increases and decreases. The <a href="{VAULTS_URL}/chains">blockchains page</a> shows the TVL of every chain.</p>',
        charts=(("chain_tvl_changes", "The largest net TVL increases and decreases by blockchain over the last 30 days"),),
        level=3,
    ),
)


@dataclass(slots=True)
class PostContext:
    """Everything needed to render the post body.

    Built by :py:func:`eth_defi.vault_report.report.generate_monthly_vault_report`
    with bundle-relative image paths, and copied by
    :py:func:`eth_defi.vault_report.report.publish_report_draft` with the
    uploaded Ghost image URLs. Only :py:attr:`charts` and
    :py:attr:`podcast_images` differ between the two.
    """

    #: E.g. ``September 2026``
    month_label: str

    #: Summary statistics bullet points for the report content updates section, plain text
    stats: list[str]

    #: Section key -> rendered HTML table, see
    #: :py:func:`eth_defi.vault_report.sections.render_section_table`, which
    #: escapes the vault data. Sections without vaults are absent.
    tables: dict[str, str]

    #: Chart key -> image URL or relative path. Charts that were not rendered are absent.
    charts: dict[str, str]

    #: Section or chart key -> bullet points describing the selection criteria; only what the chart and table do not show
    criteria_notes: dict[str, list[str]]

    #: Previous report post with its HTML body, from the Ghost Content API, if found.
    #: Source of the evergreen sections and of the "previous report" link.
    previous: GhostPost | None = None

    #: Latest podcast episodes, newest first; the section is left out when empty
    podcasts: list[PodcastEpisode] = field(default_factory=list)

    #: Podcast guest logos and service icons -> image URL or relative path,
    #: see :py:func:`eth_defi.vault_report.podcasts.render_podcast_episodes`
    podcast_images: dict[str, str] = field(default_factory=dict)


def make_month_label(data_end_at: datetime.datetime) -> str:
    """Create a human-readable report month.

    :param data_end_at:
        Report data date.

    :return:
        E.g. ``September 2026``.
    """
    return data_end_at.strftime("%B %Y")


def make_report_title(month_label: str) -> str:
    """Create the post title.

    :param month_label:
        E.g. ``September 2026``.

    :return:
        Post title.
    """
    return f"The best-performing stablecoin vaults, {month_label}"


def make_report_slug(data_end_at: datetime.datetime) -> str:
    """Create the post URL slug.

    :param data_end_at:
        Report data date.

    :return:
        E.g. ``the-best-performing-stablecoin-vaults-september-2026``.
    """
    return f"{REPORT_SLUG_PREFIX}-{data_end_at.strftime('%B-%Y').lower()}"


def extract_section_html(post_html: str, heading_id: str) -> str | None:
    """Extract a ``<h2>`` section, up to the next ``<h2>``, from post HTML.

    Used to carry the evergreen sections (*About the report*, *Next steps*)
    over from the previous report, so wording the editor improved in Ghost is
    not lost the next month. The copied HTML is cleaned of what Ghost and
    older posts leave behind: ``?ref=...ghost.io`` tracking parameters,
    vault links at their legacy ``/trading-view/`` paths and empty paragraphs.

    A regular expression is enough here, rather than an HTML parser, because
    Ghost renders headings with the ``id`` as their first attribute and a
    section ends at the next ``<h2`` or at the end of the post (*Next steps*
    is the last section).

    :param post_html:
        Full post HTML from the Ghost API.

    :param heading_id:
        The ``id`` attribute of the section ``<h2>``.

    :return:
        Section HTML including the heading, or ``None`` if not found.
    """
    match = re.search(rf'<h2 id="{re.escape(heading_id)}">.*?(?=<h2 |$)', post_html, flags=re.DOTALL)
    if not match:
        return None
    return re.sub(r"<p>\s*</p>", "", canonical_vault_urls(strip_ghost_ref(match.group(0)))).strip()


def read_changelog_entries(changelog_path: Path, since: datetime.date, keywords: tuple[str, ...] = ("vault", "protocol")) -> list[str]:
    """Read new integration entries from the changelog after a date.

    Offers the editor a starting point for the *report content updates* section.
    Only ``feat: Add ...`` entries are considered, as refactors and
    performance work are not interesting to report readers. The command line
    script reads entries since the previous report's publication date and
    passes them to ``report.json``; the post itself never lists them, because
    the editor picks and rewrites the ones worth telling readers about.

    :param changelog_path:
        Path to ``CHANGELOG.md``, entries formatted as ``- feat: Add something (YYYY-MM-DD)``.

    :param since:
        Include entries dated after this day.

    :param keywords:
        Include only entries mentioning one of these words, case-insensitive.

    :return:
        Entry texts without the ``feat:`` prefix, in changelog order (newest first).
    """
    if not changelog_path.exists():
        return []

    entry_pattern = re.compile(r"^- feat: (Add .*)\((\d{4}-\d{2}-\d{2})\)\.?\s*$")
    entries = []
    for line in changelog_path.read_text().splitlines():
        match = entry_pattern.match(line.strip())
        if not match:
            continue
        text, date = match.group(1).strip().rstrip("."), datetime.date.fromisoformat(match.group(2))
        if date > since and any(k in text.lower() for k in keywords):
            entries.append(f"{text} ({date.isoformat()})")
    return entries


def _image(src: str, alt: str) -> str:
    """Create a Ghost image card.

    :param src:
        Image URL or relative path.

    :param alt:
        Alternative text.

    :return:
        Image card HTML.
    """
    return f'<figure class="kg-card kg-image-card"><img src="{html.escape(src)}" class="kg-image" alt="{html.escape(alt)}" loading="lazy"></figure>'


def _bullets(items: list[str]) -> str:
    """Create a bullet list.

    :param items:
        List item HTML strings.

    :return:
        ``<ul>`` HTML, or empty for no items.
    """
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>" if items else ""


def _bold_numbers(text: str) -> str:
    """Bold the figures in a summary statistics bullet point, as in the earlier reports.

    The statistics are generated as plain text by
    :py:func:`eth_defi.vault_report.report.calculate_report_stats`, so they
    can be logged and stored in ``report.json``; the bolding is presentation
    and happens only here. ISO dates are matched first, so a date is bolded
    as one figure rather than as three numbers.

    :param text:
        HTML-escaped text, e.g. ``29 blockchains and 116 vault protocols``.

    :return:
        HTML with dates, dollar amounts and counts in ``<strong>``.
    """
    return re.sub(r"(\d{4}-\d{2}-\d{2}|\$?\d[\d,.]*[kMB]?)", r"<strong>\1</strong>", text)


def build_post_html(context: PostContext) -> str:
    """Render the report post body.

    The post follows the structure in the *Writing rules* of
    ``README-blog-post-outline.md``:

    1. Opening paragraph and the Ghost table of contents placeholder
    2. *About the report*, copied from the previous post
    3. *Report content updates*: a link to the previous report and the
       generated statistics; the editor writes the month's integrations
       under the opening sentence
    4. *DeFi vault community news*: an introduction only, the editor writes the news
    5. *Latest podcasts*, when episodes were read
    6. The data sections of :py:data:`SECTION_TEMPLATES`, each only when it has content
    7. *Partners*, rendered from :py:data:`PARTNERS`, and *Next steps*,
       copied from the previous post

    The post has no editor callouts or placeholders: the sections the
    editor completes are listed in the editor workflow of
    ``README-vault-report.md`` instead.

    Tables and the podcast list are wrapped in ``<!--kg-card-begin: html-->``
    comments so Ghost imports each as one raw HTML card and keeps its
    markup, see
    :py:meth:`eth_defi.vault_report.ghost.GhostAdminClient.create_or_update_draft`.
    Charts become Ghost image cards.

    Vault data reaches the HTML already escaped by the table renderer; the
    criteria notes and the templates are trusted HTML from this repository.

    :param context:
        Report content. Image references may be bundle-relative paths or
        uploaded URLs; the text is the same either way.

    :return:
        Ghost-compatible post HTML.
    """
    month = html.escape(context.month_label)

    # Copy from the previous post when it has the section, so the editor's wording carries over
    def _evergreen(heading_id: str) -> str:
        section = extract_section_html(context.previous.html or "", heading_id) if context.previous else None
        return section or DEFAULT_EVERGREEN_SECTIONS[heading_id]

    parts = [
        f"<p>In this monthly report, we examine the best-performing DeFi vaults across blockchains and decentralised exchanges for {month}.</p>",
        TABLE_OF_CONTENTS,
        _evergreen("about-the-report"),
        '<h2 id="report-content-updates">Report content updates</h2>',
    ]

    if context.previous:
        previous_month = context.previous.published_at.strftime("%B %Y")
        # Link the public blog domain, not the ghost.io URL the API returns
        previous_url = f"{BLOG_URL}/{context.previous.slug}"
        parts.append(f'<p>Since the <a href="{html.escape(previous_url)}">previous report</a> is from {previous_month}, we have updated the report as follows:</p>')
    else:
        parts.append("<p>We have updated the report as follows:</p>")

    # The figures are generated; the editor writes what changed under the opening sentence,
    # with the changelog candidates in report.json. The post has no editor callouts.
    parts += [
        "<p>These benchmarks include:</p>",
        _bullets([_bold_numbers(html.escape(stat)) for stat in context.stats]),
        f'<h2 id="defi-vault-community-news">DeFi vault community news, {month}</h2>',
        f'<p>Highlights of what happened in the DeFi vault industry in the last month. Read more news and research on our <a href="{BLOG_URL}">blog</a>.</p>',
    ]

    # News before the data analytics sections; without the Content API there are no episodes and no section
    if context.podcasts:
        parts += [
            '<h2 id="latest-podcasts">Latest podcasts</h2>',
            f'<p>The latest episodes of the <a href="{PODCAST_PAGE_URL}">Trading Strategy podcast</a>, where we talk with DeFi vault protocols and curators.</p>',
            f"<!--kg-card-begin: html-->\n{render_podcast_episodes(context.podcasts, context.podcast_images)}\n<!--kg-card-end: html-->",
        ]

    def _has_content(template: SectionTemplate) -> bool:
        return template.key in context.tables or any(chart_key in context.charts for chart_key, _ in template.charts)

    for index, template in enumerate(SECTION_TEMPLATES):
        if template.group:
            # A grouping heading is shown when any subsection up to the next heading of its level is.
            # Subsections are found by position, not by an explicit parent key, so the flat
            # SECTION_TEMPLATES order is the single source of the post's outline.
            subsections = itertools.takewhile(lambda sub: sub.level > template.level, SECTION_TEMPLATES[index + 1 :])
            if not any(_has_content(sub) for sub in subsections):
                continue
        elif not _has_content(template):
            continue
        parts += [f'<h{template.level} id="{template.heading_id}">{template.heading}</h{template.level}>', template.intro]
        parts.append(_bullets(context.criteria_notes.get(template.key, [])))
        parts += [_image(context.charts[chart_key], alt) for chart_key, alt in template.charts if chart_key in context.charts]
        if template.key in context.tables:
            parts.append(f"<!--kg-card-begin: html-->\n{context.tables[template.key]}\n<!--kg-card-end: html-->")

    parts += [render_partners_section(), _evergreen("next-steps")]
    return "\n".join(part for part in parts if part)


def build_preview_html(title: str, post_html: str, feature_image: str | None = None) -> str:
    """Wrap the post body into a standalone HTML page for local preview.

    Lets the operator and reviewers read the generated post from the bundle
    with a browser, e.g. with ``GHOST_DRAFT=false`` or when the Ghost site is
    private. The minimal light stylesheet only makes tables and images
    readable; it does not reproduce the blog's dark theme, so check the final
    look in the Ghost editor.

    :param title:
        Post title.

    :param post_html:
        Output of :py:func:`build_post_html`.

    :param feature_image:
        Feature image path or URL, shown above the title as Ghost themes do.

    :return:
        HTML document.
    """
    style = """
    body { font-family: Inter, Helvetica, Arial, sans-serif; max-width: 1100px; margin: 2em auto; padding: 0 1em; box-sizing: border-box; color: #15171a; line-height: 1.5; }
    img { max-width: 100%; }
    .table-wrapper { overflow-x: auto; }
    table { border-collapse: collapse; font-size: 13px; margin: 1em 0; }
    th, td { border-bottom: 1px solid #e6e5e1; padding: 4px 8px; }
    figcaption { text-align: center; color: #52514e; font-size: 14px; }
    """
    feature = f'<img src="{html.escape(feature_image)}" alt="">' if feature_image else ""
    # Mirror the frontend's scroll containers without changing the post HTML sent to Ghost.
    preview_body = re.sub(r"(<table(?:\s[^>]*)?>.*?</table>)", r'<div class="table-wrapper">\1</div>', post_html, flags=re.DOTALL)
    return f"<!DOCTYPE html>\n<html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><title>{html.escape(title)}</title><style>{style}</style></head>\n<body>{feature}<h1>{html.escape(title)}</h1>\n{preview_body}\n</body></html>\n"
