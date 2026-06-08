"""Look up Core3 risk intelligence for our vault protocols.

Resolves our vault protocol slugs to Core3 project slugs via
:py:data:`~eth_defi.core3.mappings.CORE3_MAPPINGS`, then fetches the
latest snapshot from the local DuckDB database.

Example::

    from pathlib import Path
    from eth_defi.core3.database import Core3Database
    from eth_defi.core3.constants import CORE3_DATABASE_PATH
    from eth_defi.core3.vault_protocol import get_core3_protocol_record

    db = Core3Database(CORE3_DATABASE_PATH)
    record = get_core3_protocol_record(db, "morpho")
    if record:
        print(f"PoL score: {record['pol']['score']}")
        print(f"Top risks: {len(record['top_risks'])}")
    db.close()
"""

import datetime
import json
import logging
from collections.abc import Iterable
from typing import NotRequired, TypedDict

from eth_defi.core3.database import Core3Database
from eth_defi.core3.mappings import CORE3_MAPPINGS

logger = logging.getLogger(__name__)


class Core3Seal(TypedDict):
    """A single Core3 trust seal status."""

    #: Whether the seal is currently awarded.
    value: bool

    #: URL to the seal logo image, or ``None`` if not awarded.
    logo: str | None


class Core3Seals(TypedDict):
    """Core3 trust seals for a project.

    Verifiable marks awarded by Core3 based on project practices.
    """

    #: Whether the project demonstrates adequate security measures
    #: (audits, monitoring, bug bounty).
    security_measures: Core3Seal

    #: Whether the project holds independent certifications
    #: (ISO 27001, CCSS, SOC 2).
    independent_certificates: Core3Seal

    #: Whether the project participates in self-regulation initiatives
    #: (KYC, KYT, legal compliance).
    self_regulation: Core3Seal


class Core3PolScore(TypedDict):
    """Core3 Probability of Loss (PoL) score and rating.

    PoL is a data-driven, non-price risk metric. Lower scores indicate
    less risk of loss.
    """

    #: Numeric PoL score from 0 (Exceptional) to 100 (Critical).
    score: float

    #: Credit-style letter rating: ``"AAA"``, ``"AA"``, ``"A"``,
    #: ``"BBB"``, ``"BB"``, ``"B"``, ``"CCC"``, ``"CC"``, ``"C"``,
    #: ``"DDD"``, ``"DD"``, ``"D"``, or ``None`` if not yet rated.
    rating: str | None

    #: Human-readable confidence label: ``"Exceptional"``, ``"High"``,
    #: ``"Medium"``, ``"Low"``, ``"Critical"``, or ``None``.
    confidence: str | None


class Core3MarketCap(TypedDict):
    """Market capitalisation data from Core3.

    Values are returned as strings from the API (not numbers).
    """

    #: Market cap in USD as a string, e.g. ``"1246877334"``.
    in_usd: str | None

    #: 24h percentage change as a float, e.g. ``-0.71437``.
    change_24h_percentage: float | None

    #: 24h absolute change in USD as a string, e.g. ``"-8971443.42"``.
    change_24h_in_usd: str | None


class Core3Chain(TypedDict):
    """A blockchain network where the project is deployed."""

    #: Chain display name, e.g. ``"Ethereum"``, ``"Base"``, ``"Arbitrum One"``.
    name: str


class Core3Social(TypedDict):
    """A social link entry from Core3."""

    #: Link type label: ``"Website"``, ``"Twitter"``, ``"GitHub"``,
    #: ``"YouTube"``, ``"Discord"``, etc.
    name: str

    #: Full URL to the social profile or page.
    link: str


class Core3Links(TypedDict):
    """Project external links from Core3."""

    #: Primary website URL, e.g. ``"https://morpho.org/"``.
    website: str | None

    #: Link to legal documentation or terms of service.
    legal: str | None

    #: Link to the project whitepaper.
    whitepaper: str | None

    #: List of social media and external resource links.
    socials: list[Core3Social]


class Core3TopRisk(TypedDict):
    """A top risk finding from Core3's risk assessment.

    Core3 surfaces the most significant risk factors identified
    across security, financial, operational, reputational, and
    regulatory categories.
    """

    #: Human-readable risk description, e.g.
    #: ``"A treasury composed entirely of the project's own native token..."``.
    content: str

    #: ISO 8601 timestamp when the risk was identified,
    #: e.g. ``"2026-04-02T07:43:02.240Z"``.
    date: str


class Core3RecentChange(TypedDict):
    """A recent change detected by Core3 monitoring.

    Tracks notable metric movements like traffic drops or
    engagement changes.
    """

    #: Human-readable change description, e.g.
    #: ``"Website traffic has dropped to below-average levels..."``.
    content: str

    #: ISO 8601 timestamp when the change was detected.
    date: str


class Core3Category(TypedDict):
    """Core3 project category classification."""

    #: Category display name, e.g. ``"Decentralized Finance"``,
    #: ``"Lending/Borrowing"``, ``"Decentralized Exchange"``,
    #: ``"Layer 1"``, ``"Layer 2"``, ``"RWA"``.
    name: str


class Core3DataCoverage(TypedDict):
    """Core3 data coverage indicator.

    Shows how much of the project's data Core3 has been able to collect
    and verify.
    """

    #: Percentage of data coverage, 0–100. Higher values mean
    #: more comprehensive risk assessment.
    percentage: float


class Core3Record(TypedDict):
    """Full Core3 project record from the latest DuckDB snapshot.

    Contains the complete JSON payload stored by
    :py:meth:`~eth_defi.core3.database.Core3Database.insert_project_snapshot`,
    plus database metadata fields.

    See :doc:`README-core3` for the API documentation that produces
    this data.
    """

    #: Core3 project slug (CoinGecko-style), e.g. ``"morpho"``,
    #: ``"instadapp"``, ``"syrup"``.
    slug: str

    #: Project display name, e.g. ``"Morpho"``, ``"Fluid"``.
    name: str

    #: Project description text from Core3 (may contain newlines).
    description: str | None

    #: Core3 global rank (1 = lowest risk). ``None`` if unranked.
    rank: int | None

    #: Probability of Loss score, rating, and confidence level.
    pol: Core3PolScore

    #: Token ticker symbol, e.g. ``"MORPHO"``, ``"EUL"``.
    #: Some projects omit this key entirely from the API response.
    ticker: NotRequired[str | None]

    #: CoinGecko ID for cross-referencing, e.g. ``"morpho"``, ``"euler"``.
    #: Some projects omit this key entirely from the API response.
    coingecko_id: NotRequired[str | None]

    #: URL to the project logo image on CoinGecko CDN.
    logo: str | None

    #: Core3 project page link (note: may have malformed URLs
    #: like ``"https://core3.iomorpho"`` — missing ``/``).
    link: str | None

    #: ISO 8601 launch date string, or ``None`` if unknown.
    launched_at: str | None

    #: Project category classification.
    category: Core3Category | None

    #: Data coverage percentage indicator.
    data_coverage: Core3DataCoverage | None

    #: Market capitalisation data.
    market_cap: Core3MarketCap | None

    #: Blockchain networks where the project is deployed.
    chains: list[Core3Chain]

    #: External links (website, legal, whitepaper, socials).
    links: Core3Links | None

    #: Project tags (often empty).
    tags: list[str]

    #: Top risk findings from Core3's assessment.
    #: Typically 10–30 risk items covering security, financial,
    #: operational, reputational, and regulatory concerns.
    top_risks: list[Core3TopRisk]

    #: Recent monitoring changes detected by Core3.
    recent_changes: list[Core3RecentChange]

    #: Trust seals status (security measures, independent certs,
    #: self-regulation).
    seals: Core3Seals | None

    #: Timestamp when this snapshot was fetched from the Core3 API.
    #: Added by the database layer, not present in the original API response.
    fetched_at: datetime.datetime


def get_core3_protocol_record(
    db: Core3Database,
    vault_protocol_slug: str,
) -> Core3Record | None:
    """Look up the latest Core3 risk record for a vault protocol.

    Resolves our vault protocol slug to a Core3 project slug using
    :py:data:`~eth_defi.core3.mappings.CORE3_MAPPINGS`, then reads
    the most recent project snapshot from the DuckDB database.

    :param db:
        Open Core3 DuckDB database connection.

    :param vault_protocol_slug:
        Our vault protocol slug, e.g. ``"morpho"``, ``"fluid"``,
        ``"maple"``. Must match a key in
        :py:data:`~eth_defi.core3.mappings.CORE3_MAPPINGS`.

    :return:
        The latest :class:`Core3Record` for the protocol, or ``None``
        if the protocol has no Core3 mapping or the Core3 slug is not
        found in the database.
    """
    core3_slug = CORE3_MAPPINGS.get(vault_protocol_slug)
    if core3_slug is None:
        return None

    result = db.get_latest_project_snapshot_raw(core3_slug)
    if result is None:
        return None

    payload_str, fetched_at = result
    payload = json.loads(payload_str)
    payload["fetched_at"] = fetched_at

    return payload


class Core3ExportRecord(TypedDict):
    """Serialised Core3 project record for JSON export.

    Same structure as :class:`Core3Record` but with ``fetched_at``
    as an ISO 8601 string instead of :class:`~datetime.datetime`,
    since the JSON export cannot contain raw datetime objects.
    """

    #: Core3 project slug (CoinGecko-style), e.g. ``"morpho"``,
    #: ``"instadapp"``, ``"syrup"``.
    slug: str

    #: Project display name, e.g. ``"Morpho"``, ``"Fluid"``.
    name: str

    #: Project description text from Core3 (may contain newlines).
    description: str | None

    #: Core3 global rank (1 = lowest risk). ``None`` if unranked.
    rank: int | None

    #: Probability of Loss score, rating, and confidence level.
    pol: Core3PolScore

    #: Token ticker symbol, e.g. ``"MORPHO"``, ``"EUL"``.
    ticker: NotRequired[str | None]

    #: CoinGecko ID for cross-referencing.
    coingecko_id: NotRequired[str | None]

    #: URL to the project logo image on CoinGecko CDN.
    logo: str | None

    #: Core3 project page link.
    link: str | None

    #: ISO 8601 launch date string, or ``None`` if unknown.
    launched_at: str | None

    #: Project category classification.
    category: Core3Category | None

    #: Data coverage percentage indicator.
    data_coverage: Core3DataCoverage | None

    #: Market capitalisation data.
    market_cap: Core3MarketCap | None

    #: Blockchain networks where the project is deployed.
    chains: list[Core3Chain]

    #: External links (website, legal, whitepaper, socials).
    links: Core3Links | None

    #: Project tags (often empty).
    tags: list[str]

    #: Top risk findings from Core3's assessment.
    top_risks: list[Core3TopRisk]

    #: Recent monitoring changes detected by Core3.
    recent_changes: list[Core3RecentChange]

    #: Trust seals status.
    seals: Core3Seals | None

    #: ISO 8601 timestamp when this snapshot was fetched.
    fetched_at: str


def build_core3_protocols_for_export(
    db: Core3Database,
    protocol_slugs: Iterable[str],
) -> dict[str, Core3ExportRecord]:
    """Build a protocol-slug-keyed dict of Core3 records for the JSON export.

    Looks up each vault protocol slug in the Core3 database and returns
    a dict suitable for embedding as the top-level ``core3_protocols``
    key in the vault metrics JSON export. Records are keyed by our
    internal protocol slugs (e.g. ``"fluid"``), not Core3's own slugs
    (e.g. ``"instadapp"``).

    :param db:
        Open Core3 DuckDB database connection.

    :param protocol_slugs:
        Iterable of our vault protocol slugs to look up.

    :return:
        Dict mapping protocol slug to :class:`Core3ExportRecord`.
        Slugs with no Core3 mapping or no database record are excluded.
    """
    result: dict[str, Core3ExportRecord] = {}
    protocol_slugs = set(protocol_slugs)

    for slug in protocol_slugs:
        record = get_core3_protocol_record(db, slug)
        if record is None:
            continue

        # Shallow copy to avoid mutating the original Core3Record in-place
        export_record = {**record}

        # Serialise fetched_at datetime to ISO 8601 string
        fetched_at = export_record["fetched_at"]
        if isinstance(fetched_at, datetime.datetime):
            export_record["fetched_at"] = fetched_at.isoformat()

        result[slug] = export_record

    logger.info("Built Core3 export records for %d / %d protocol slugs", len(result), len(protocol_slugs))
    return result
