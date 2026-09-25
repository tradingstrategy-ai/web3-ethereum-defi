"""Assemble the monthly vault report blog post HTML.

The post body is Ghost-compatible HTML:

- Tables are wrapped in ``<!--kg-card-begin: html-->`` comments, which
  Ghost imports as raw HTML cards
- Charts are Ghost image cards
- Parts that need a human editor (intro, community news, commentary) are
  yellow callout cards starting with ``EDITOR:``; delete them before publishing

Evergreen sections (*About the report*, *Partners*, *Next steps*) are copied
from the previous report post, so edits made in Ghost carry over month to month.
"""

import datetime
import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from eth_defi.vault_report.ghost import GhostPost

#: Slug prefix of the monthly report posts
REPORT_SLUG_PREFIX = "the-best-performing-stablecoin-vaults"

#: Public blog base URL. The Ghost API returns ``ghost.io`` URLs; readers use this domain.
BLOG_URL = "https://tradingstrategy.ai/blog"

#: Evergreen sections used when there is no previous post to copy them from, by heading id
DEFAULT_EVERGREEN_SECTIONS = {
    "about-the-report": ('<h2 id="about-the-report">About the report</h2><p>In this post, we examine the performance of DeFi vaults. Vaults can be considered "self-custodial investment strategies" in traditional finance: vaults are smart contracts that enable users to deposit funds from their cryptocurrency wallets and trade a predefined strategy with investors\' money.</p>'),
    "partners": '<h2 id="partners">Partners</h2><p>We want to thank our partners for getting this report together.</p>',
    "next-steps": ('<h2 id="next-steps">Next steps</h2><p>Visit our <a href="https://tradingstrategy.ai/trading-view/vaults">vaults page</a> for real-time dashboards. If you have any questions, <a href="https://tradingstrategy.ai/community">contact us on Discord, email or Twitter</a>.</p>'),
}

#: Ghost adds this tracking parameter to links in its HTML output
GHOST_REF_PARAMETER = re.compile(r"([?&])ref=[a-z0-9.-]+\.ghost\.io(&?)")


@dataclass(slots=True, frozen=True)
class SectionTemplate:
    """Static content of one data section of the post.

    A section is included when its table or any of its charts exists, or
    always when :py:attr:`always` is set, e.g. for a heading that groups subsections.
    """

    #: Section key: the table key in :py:attr:`PostContext.tables` and the criteria notes key
    key: str

    #: Heading anchor id
    heading_id: str

    #: Heading text
    heading: str

    #: Introduction paragraph HTML, or empty
    intro: str = ""

    #: (chart key in :py:attr:`PostContext.charts`, alt text) pairs shown above the table
    charts: tuple[tuple[str, str], ...] = ()

    #: Text of an editor callout, or empty
    editor_note: str = ""

    #: Heading level, 2 for sections and 3 for subsections
    level: int = 2

    #: Include the section even without a table or chart
    always: bool = False


#: Data sections in display order, see ``README-blog-post-outline.md``
SECTION_TEMPLATES = (
    SectionTemplate(
        key="chain_yields",
        heading_id="average-yield-by-blockchain",
        heading="Average yield by blockchain",
        charts=(("chain_yields", "Stablecoin vault yield on the largest blockchains against the US Treasury bill"),),
        editor_note="Comment on the chains paying the most and the least.",
    ),
    SectionTemplate(
        key="protocol_yields",
        heading_id="average-yield-by-protocol",
        heading="Average yield by protocol",
        charts=(("protocol_yields", "Stablecoin vault yield of the largest protocols against the US Treasury bill"),),
    ),
    SectionTemplate(
        key="protocol_tvl",
        heading_id="stablecoin-tvl-by-defi-vault-protocol",
        heading="Stablecoin TVL by DeFi vault protocol",
        charts=(("protocol_tvl", "Stablecoin TVL by DeFi vault protocol"),),
        editor_note="Comment on the TVL trend: which protocols grew or shrank.",
    ),
    SectionTemplate(
        key="fund_nav",
        heading_id="stablecoin-nav-by-tokenised-fund",
        heading="Stablecoin NAV by tokenised fund",
        charts=(("fund_nav", "Stablecoin NAV by tokenised fund"),),
        editor_note="Comment on the tokenised fund trend: which funds grew or shrank.",
    ),
    SectionTemplate(
        key="tvl_changes",
        heading_id="inflows-and-outflows",
        heading="Inflows and outflows",
        intro="<p>Where the money moved: the vaults whose total value locked grew or shrank the most over the last 30 days.</p>",
        charts=(("tvl_changes", "The largest vault TVL increases and decreases over the last 30 days"),),
        editor_note="Explain the largest moves if known, e.g. a new fund launch or a redemption.",
    ),
    SectionTemplate(
        key="best",
        heading_id="the-best-performing-vaults",
        heading="The best-performing vaults",
        intro="<p>The best-performing vaults of the month in four groups: lending vaults, perpetual futures DEX vaults by return and by risk-adjusted return, and other vaults.</p>",
        editor_note="Comment on the top vaults of the month.",
        always=True,
    ),
    SectionTemplate(
        key="lending",
        heading_id="best-performing-lending-vaults",
        heading="Lending vaults",
        charts=(("lending_performance", "90-day performance of the best-performing lending vaults against benchmarks"),),
        level=3,
    ),
    SectionTemplate(
        key="perp_dex",
        heading_id="best-performing-perp-dex-vaults",
        heading="Perpetual futures DEX vaults by return",
        charts=(("perp_dex_performance", "90-day performance of the best-performing perp DEX vaults against BTC and ETH"),),
        level=3,
    ),
    SectionTemplate(
        key="perp_dex_sharpe",
        heading_id="best-performing-perp-dex-vaults-by-sharpe",
        heading="Perpetual futures DEX vaults by Sharpe ratio",
        charts=(("perp_dex_sharpe_performance", "90-day performance of the perp DEX vaults with the best Sharpe ratio against BTC and ETH"),),
        level=3,
    ),
    SectionTemplate(
        key="other",
        heading_id="best-performing-other-vaults",
        heading="Other vaults",
        charts=(("other_performance", "90-day performance of other best-performing vaults against benchmarks"),),
        level=3,
    ),
    SectionTemplate(
        key="tokenised_funds",
        heading_id="the-best-performing-tokenised-funds",
        heading="The best-performing tokenised funds",
        intro="<p>Tokenised funds bring traditional money market, treasury and credit funds onchain.</p>",
        charts=(("tokenised_funds_performance", "90-day performance of the best-performing tokenised funds against benchmarks"),),
    ),
    SectionTemplate(key="new", heading_id="the-best-performing-new-vaults", heading="The best-performing new vaults"),
    SectionTemplate(
        key="risk_return",
        heading_id="risk-and-return",
        heading="Risk and return",
        intro="<p>Higher returns usually come with higher volatility. Vaults above and to the left of the crowd offer better returns for their risk.</p>",
        charts=(("risk_return", "Risk and return of stablecoin yield vaults"),),
    ),
    SectionTemplate(key="by_chain", heading_id="the-best-performing-vaults-on-each-chain", heading="The best-performing vaults on each chain"),
)


@dataclass(slots=True)
class PostContext:
    """Everything needed to render the post body."""

    #: E.g. ``September 2026``
    month_label: str

    #: Summary statistics bullet points for the report content updates section, plain text
    stats: list[str]

    #: Section key -> rendered HTML table. Sections without vaults are absent.
    tables: dict[str, str]

    #: Chart key -> image URL or relative path. Charts that were not rendered are absent.
    charts: dict[str, str]

    #: Section or chart key -> bullet points describing the selection criteria
    criteria_notes: dict[str, list[str]]

    #: Previous report post, if found
    previous: GhostPost | None = None

    #: Recent changelog entries offered to the editor for the report content updates section
    changelog_entries: list[str] = field(default_factory=list)

    #: Section key -> one data-driven sentence shown after the section introduction, plain text
    captions: dict[str, str] = field(default_factory=dict)


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

    Ghost ``?ref=...ghost.io`` tracking parameters are removed from the links.

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
    # Keep the separator when other query parameters follow: ?ref=x&a=1 -> ?a=1
    return GHOST_REF_PARAMETER.sub(lambda m: m.group(1) if m.group(2) else "", match.group(0)).strip()


def read_changelog_entries(changelog_path: Path, since: datetime.date, keywords: tuple[str, ...] = ("vault", "protocol")) -> list[str]:
    """Read new integration entries from the changelog after a date.

    Offers the editor a starting point for the *report content updates* section.
    Only ``feat: Add ...`` entries are considered, as refactors and
    performance work are not interesting to report readers.

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


def _editor_note(inner_html: str) -> str:
    """Create a yellow Ghost callout card for editor instructions.

    :param inner_html:
        Instruction HTML.

    :return:
        Callout card HTML.
    """
    return f'<div class="kg-card kg-callout-card kg-callout-card-yellow"><div class="kg-callout-emoji">✏️</div><div class="kg-callout-text"><b>EDITOR:</b> {inner_html}</div></div>'


def _image(src: str, alt: str, caption: str = "") -> str:
    """Create a Ghost image card.

    :param src:
        Image URL or relative path.

    :param alt:
        Alternative text.

    :param caption:
        Caption HTML, or empty.

    :return:
        Image card HTML.
    """
    figcaption = f"<figcaption>{caption}</figcaption>" if caption else ""
    return f'<figure class="kg-card kg-image-card"><img src="{html.escape(src)}" class="kg-image" alt="{html.escape(alt)}" loading="lazy">{figcaption}</figure>'


def _bullets(items: list[str]) -> str:
    """Create a bullet list.

    :param items:
        List item HTML strings.

    :return:
        ``<ul>`` HTML, or empty for no items.
    """
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>" if items else ""


def build_post_html(context: PostContext) -> str:
    """Render the report post body.

    :param context:
        Report content.

    :return:
        Ghost-compatible post HTML.
    """
    month = html.escape(context.month_label)

    def _evergreen(heading_id: str) -> str:
        section = extract_section_html(context.previous.html or "", heading_id) if context.previous else None
        return section or DEFAULT_EVERGREEN_SECTIONS[heading_id]

    parts = [
        f"<p>In this monthly report, we examine the best-performing USD-denominated DeFi vaults across blockchains, {month} edition.</p>",
        _editor_note("Write a one or two sentence intro with the highlight of the month. Delete all EDITOR notes before publishing."),
        _evergreen("about-the-report"),
        '<h2 id="report-content-updates">Report content updates</h2>',
    ]

    if context.previous:
        previous_month = context.previous.published_at.strftime("%B %Y")
        previous_url = f"{BLOG_URL}/{context.previous.slug}"
        parts.append(f'<p>Since the <a href="{html.escape(previous_url)}">previous report</a> is from {previous_month}, we have updated the report as follows:</p>')
    else:
        parts.append("<p>We have updated the report as follows:</p>")

    candidates = f" Candidates from the changelog since the previous report: {_bullets([html.escape(e) for e in context.changelog_entries])}" if context.changelog_entries else ""
    parts += [
        _editor_note(f"Summarise the notable new integrations as bullet points here.{candidates}"),
        "<p>These benchmarks include:</p>",
        _bullets([html.escape(stat) for stat in context.stats]),
        f'<h2 id="defi-vault-community-news">DeFi vault community news, {month}</h2>',
        "<p>Highlights of what happened in the DeFi vault industry in the last month.</p>",
        _editor_note("Add community news as <code>h3</code> subsections: new vault launches, partnerships, incidents, Trading Strategy product news."),
    ]

    for template in SECTION_TEMPLATES:
        if not template.always and template.key not in context.tables and not any(chart_key in context.charts for chart_key, _ in template.charts):
            continue
        parts += [f'<h{template.level} id="{template.heading_id}">{template.heading}</h{template.level}>', template.intro]
        if template.key in context.captions:
            parts.append(f"<p><strong>{html.escape(context.captions[template.key])}</strong></p>")
        if template.editor_note:
            parts.append(_editor_note(template.editor_note))
        parts.append(_bullets(context.criteria_notes.get(template.key, [])))
        parts += [_image(context.charts[chart_key], alt) for chart_key, alt in template.charts if chart_key in context.charts]
        if template.key in context.tables:
            parts.append(f"<!--kg-card-begin: html-->\n{context.tables[template.key]}\n<!--kg-card-end: html-->")

    parts += [_evergreen("partners"), _evergreen("next-steps")]
    return "\n".join(part for part in parts if part)


def build_preview_html(title: str, post_html: str, feature_image: str | None = None) -> str:
    """Wrap the post body into a standalone HTML page for local preview.

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
    body { font-family: Inter, Helvetica, Arial, sans-serif; max-width: 1100px; margin: 2em auto; color: #15171a; line-height: 1.5; }
    img { max-width: 100%; }
    table { border-collapse: collapse; font-size: 13px; margin: 1em 0; }
    th, td { border-bottom: 1px solid #e6e5e1; padding: 4px 8px; }
    .kg-callout-card-yellow { background: #fcf4e3; border-radius: 6px; padding: 1em; display: flex; gap: 0.6em; margin: 1em 0; }
    figcaption { text-align: center; color: #52514e; font-size: 14px; }
    """
    feature = f'<img src="{html.escape(feature_image)}" alt="">' if feature_image else ""
    return f"<!DOCTYPE html>\n<html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{style}</style></head>\n<body>{feature}<h1>{html.escape(title)}</h1>\n{post_html}\n</body></html>\n"
