"""Build curator metadata and recent feed entries for the vault JSON export.

Resolves curator slugs from vault records to structured metadata
(name, website, social URLs, logos) and recent feed entries from the
post database.  The result is a dict keyed by curator slug, stored
at the top level of the vault metrics JSON bundle alongside
``core3_protocols``.

Example::

    from eth_defi.feed.database import VaultPostDatabase
    from eth_defi.vault.curator_export import build_curators_for_export

    feed_db = VaultPostDatabase(Path("vault-post-database.duckdb"))
    curators = build_curators_for_export(
        ["gauntlet", "re7-labs", "hyperliquid"],
        feed_db=feed_db,
    )
    feed_db.close()

    # curators["gauntlet"]["recent_posts"] → list of recent feed entries
"""

import datetime
import logging
from collections.abc import Iterable
from typing import TypedDict

from eth_defi.feed.database import VaultPostDatabase
from eth_defi.feed.sources import FEEDS_DATA_DIR, load_feeder_metadata
from eth_defi.vault.curator import (
    ALL_PROTOCOL_CURATOR_SLUGS,
    CURATORS_DATA_DIR,
    PROTOCOL_CURATOR_NAMES,
    CuratorLogos,
    _build_curator_logo_urls,
    build_curator_metadata_json,
    load_curator_map,
)

logger = logging.getLogger(__name__)


class CuratorFeedEntry(TypedDict):
    """A single recent feed entry from a curator's tracked sources.

    Used inside :py:class:`CuratorExportRecord` to surface recent
    social media and blog activity in the vault JSON bundle.
    """

    #: Post title, or ``None`` for untitled posts (e.g. tweets).
    title: str | None

    #: Short preview text (first 200 chars), suitable for compact listings.
    #:
    #: Derived from :py:attr:`~eth_defi.feed.database.CollectedPost.short_description`.
    #: For the complete post body see :py:attr:`full_text`.
    snippet: str

    #: Full untruncated post body.
    #:
    #: For X/Twitter this includes the complete *note tweet* text for tweets
    #: longer than 280 characters, not just the preview — see
    #: :py:func:`eth_defi.feed.twitter_api._extract_full_tweet_text`.  Derived
    #: from :py:attr:`~eth_defi.feed.database.CollectedPost.full_text`.  For the
    #: 200-character preview see :py:attr:`snippet`.
    full_text: str

    #: Canonical URL to the original post, or ``None``.
    link: str | None

    #: Source transport type: ``"twitter"``, ``"linkedin"``, or ``"rss"``.
    source_type: str

    #: ISO 8601 UTC timestamp when the post was published.
    #: Always set — falls back to fetch time when the source
    #: does not provide a publication timestamp.
    published_at: str


class CuratorExportRecord(TypedDict):
    """Serialised curator record for the vault metrics JSON export.

    Contains curator identity metadata and recent feed entries.
    Keyed by curator slug in the top-level ``curators`` dict of
    :py:class:`~eth_defi.research.vault_metrics.VaultMetricsExport`.
    """

    #: Curator slug (e.g. ``"gauntlet"``, ``"re7-labs"``).
    slug: str

    #: Human-readable display name.
    name: str

    #: Company website URL, or ``None``.
    website: str | None

    #: One-line description of the curator.
    short_description: str | None

    #: Multi-paragraph Markdown description of the curator.
    long_description: str | None

    #: Full Twitter/X profile URL (e.g. ``"https://x.com/gauntlet_xyz"``),
    #: or ``None``.
    twitter: str | None

    #: Full LinkedIn company URL, or ``None``.
    linkedin: str | None

    #: RSS or Atom feed URL, or ``None``.
    rss: str | None

    #: Whether this is a protocol-native curator (the protocol itself
    #: acts as curator) rather than a third-party risk manager.
    protocol_curator: bool

    #: When this curator is an alias, the slug of the canonical feeder
    #: whose posts should be used.  ``None`` for non-alias curators.
    canonical_feeder_id: str | None

    #: Logo URLs for available 256x256 PNG variants.
    logos: CuratorLogos

    #: Most recent feed entries, ordered newest first.
    recent_posts: list[CuratorFeedEntry]


def _build_protocol_curator_from_yaml(
    slug: str,
    public_url: str = "",
) -> CuratorExportRecord:
    """Build a curator export record for a protocol curator using its protocol YAML.

    Protocol curators like Hyperliquid, Ostium, Lighter have protocol
    YAML files at ``eth_defi/data/feeds/protocols/{slug}.yaml`` but
    no curator YAML.  This function loads the protocol YAML metadata
    to populate website, twitter, linkedin, and rss fields.

    :param slug:
        Protocol curator slug (e.g. ``"hyperliquid"``).

    :param public_url:
        Public base URL for constructing logo URLs.

    :return:
        Curator export record with metadata from the protocol YAML.
    """
    protocol_yaml = FEEDS_DATA_DIR / "protocols" / f"{slug}.yaml"
    name = PROTOCOL_CURATOR_NAMES.get(slug, slug)

    website = None
    short_description = None
    long_description = None
    twitter_url = None
    linkedin_url = None
    rss = None

    if protocol_yaml.exists():
        meta = load_feeder_metadata(protocol_yaml)
        name = meta.get("name", name)
        website = meta.get("website")
        short_description = meta.get("short_description")
        long_description = meta.get("long_description")
        twitter_handle = meta.get("twitter")
        linkedin_id = meta.get("linkedin")
        rss = meta.get("rss")

        if twitter_handle:
            twitter_url = f"https://x.com/{twitter_handle}"
        if linkedin_id:
            linkedin_url = f"https://www.linkedin.com/company/{linkedin_id}"

    return CuratorExportRecord(
        slug=slug,
        name=name,
        website=website,
        short_description=short_description,
        long_description=long_description,
        twitter=twitter_url,
        linkedin=linkedin_url,
        rss=rss,
        protocol_curator=True,
        canonical_feeder_id=None,
        logos=_build_curator_logo_urls(slug, public_url=public_url),
        recent_posts=[],
    )


def build_curators_for_export(
    curator_slugs: Iterable[str],
    feed_db: VaultPostDatabase | None = None,
    max_posts_per_curator: int = 10,
    public_url: str = "",
) -> dict[str, CuratorExportRecord]:
    """Build a curator-slug-keyed dict for the vault metrics JSON export.

    For each curator slug present in the exported vaults:

    1. Load metadata from curator YAML via
       :py:func:`~eth_defi.vault.curator.build_curator_metadata_json`,
       or from the protocol YAML for protocol curators without a
       curator YAML file.
    2. Resolve ``canonical_feeder_id`` for alias curators so that
       feed posts are looked up under the correct feeder.
    3. Batch-query recent posts from the feed database.
    4. Convert timestamps to ISO 8601 strings and assemble the
       final :py:class:`CuratorExportRecord` dicts.

    When *feed_db* is ``None`` (e.g. the database file does not
    exist), all ``recent_posts`` lists will be empty.

    :param curator_slugs:
        Iterable of curator slugs to include (typically extracted
        from ``curator_slug`` fields in the vault records).

    :param feed_db:
        Open vault post database for querying recent posts.
        Pass ``None`` to skip feed enrichment.

    :param max_posts_per_curator:
        Maximum number of recent posts per curator.

    :param public_url:
        Public base URL for constructing logo URLs.

    :return:
        Dict mapping curator slug to :py:class:`CuratorExportRecord`.
    """
    slugs = list(set(curator_slugs))
    if not slugs:
        return {}

    curator_map = load_curator_map()

    # 1. Build metadata for each slug
    records: dict[str, CuratorExportRecord] = {}
    # Maps resolved feeder_id → list of curator slugs that use it
    feeder_to_curators: dict[str, list[str]] = {}

    for slug in slugs:
        if slug in curator_map:
            # Curator has a YAML file — use the existing builder
            yaml_path = CURATORS_DATA_DIR / f"{slug}.yaml"
            meta = build_curator_metadata_json(yaml_path, public_url=public_url)
            canonical = curator_map[slug].get("canonical_feeder_id")
            records[slug] = CuratorExportRecord(
                slug=meta["slug"],
                name=meta["name"],
                website=meta["website"],
                short_description=meta["short_description"],
                long_description=meta["long_description"],
                twitter=meta["twitter"],
                linkedin=meta["linkedin"],
                rss=meta["rss"],
                protocol_curator=meta["protocol_curator"],
                canonical_feeder_id=canonical,
                logos=meta["logos"],
                recent_posts=[],
            )
            # Resolve which feeder_id to use for post lookup
            feeder_id = canonical if canonical else slug
            feeder_to_curators.setdefault(feeder_id, []).append(slug)

        elif slug in ALL_PROTOCOL_CURATOR_SLUGS:
            # Protocol curator without curator YAML — load from protocol YAML
            records[slug] = _build_protocol_curator_from_yaml(slug, public_url=public_url)
            feeder_to_curators.setdefault(slug, []).append(slug)

        else:
            logger.warning("Curator slug %r not found in curator YAML map or protocol curators; skipping", slug)
            continue

    # 2. Batch-query recent posts from the feed database
    if feed_db is not None:
        all_feeder_ids = list(feeder_to_curators.keys())
        posts_by_feeder = feed_db.fetch_recent_posts_by_feeder(
            all_feeder_ids,
            max_per_feeder=max_posts_per_curator,
        )

        # Map posts back to curator slugs
        for feeder_id, posts in posts_by_feeder.items():
            feed_entries = []
            for post in posts:
                published_at = post["published_at"]
                if isinstance(published_at, datetime.datetime):
                    published_at = published_at.isoformat()
                elif published_at is not None:
                    published_at = str(published_at)

                feed_entries.append(
                    CuratorFeedEntry(
                        title=post["title"],
                        snippet=post["short_description"],
                        full_text=post["full_text"],
                        link=post["post_url"],
                        source_type=post["source_type"],
                        published_at=published_at,
                    )
                )

            # Multiple curator slugs may share the same canonical feeder
            for curator_slug in feeder_to_curators.get(feeder_id, []):
                if curator_slug in records:
                    records[curator_slug]["recent_posts"] = feed_entries

    logger.info("Built curator export for %d curators", len(records))
    return records
