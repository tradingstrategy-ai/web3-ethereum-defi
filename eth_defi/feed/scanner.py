"""Post scan orchestration.

Provides :func:`run_post_scan_cycle` which runs a complete collection cycle
(RSS → LinkedIn → Twitter) and can be called directly from integration tests
without going through the script entry point.
"""

import datetime
import logging
from dataclasses import dataclass, field
from pathlib import Path

from eth_defi.compat import native_datetime_utc_now
from eth_defi.feed.collector import CollectorRunSummary, collect_posts, fetch_feed_proxy_rotator
from eth_defi.feed.database import DEFAULT_VAULT_POST_DATABASE, VaultPostDatabase
from eth_defi.feed.sources import FEEDS_DATA_DIR, auto_disable_failed_linkedin_sources, load_post_sources


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PostScanConfig:
    """Configuration for one post scan cycle."""

    #: Path to the DuckDB database file.
    db_path: Path = field(default_factory=lambda: DEFAULT_VAULT_POST_DATABASE)
    #: Path to the feeder YAML files directory.
    mappings_dir: Path = field(default_factory=lambda: FEEDS_DATA_DIR)
    #: Maximum number of concurrent worker threads.
    max_workers: int = 8
    #: Maximum number of feed entries to inspect per source.
    max_posts_per_source: int = 20
    #: HTTP request timeout in seconds.
    request_timeout: float = 20.0
    #: Delay between source fetches in seconds.
    request_delay_seconds: float = 1.0
    #: Retention window in days for pruning old posts.
    max_post_age_days: int = 365
    #: Maximum proxy rotations before falling back to direct requests.
    max_proxy_rotations: int = 3
    #: X API v2 bearer token for reading tweets.
    twitter_bearer_token: str | None = None
    #: OAuth 1.0a consumer key for X list write operations.
    twitter_consumer_key: str | None = None
    #: OAuth 1.0a consumer secret for X list write operations.
    twitter_consumer_secret: str | None = None
    #: OAuth 1.0a user access token for X list write operations.
    twitter_access_token: str | None = None
    #: OAuth 1.0a user access token secret for X list write operations.
    twitter_access_token_secret: str | None = None
    #: X list ID for the "Best builders in DeFi" list.
    x_list_id: str | None = None
    #: Path to the Twitter user metadata cache JSON.
    twitter_user_cache_path: Path | None = None
    #: Enable X list membership sync (production only).
    sync_x_list: bool = False
    #: Limit number of sources per type (for test runs).
    limit: int | None = None
    #: Days after which an inactive Twitter account is considered dead.
    death_detection_days: int = 180
    #: Comma-separated RSS bridge base URLs.
    twitter_rss_base_urls: list[str] = field(default_factory=list)


def run_post_scan_cycle(config: PostScanConfig) -> CollectorRunSummary:
    """Run one full post scan cycle.

    1. Load sources from YAML, apply limit per type if set.
    2. Sync X list membership only if ``sync_x_list=True`` and handles changed.
    3. Scan RSS sources (parallel).
    4. Scan LinkedIn sources (parallel), auto-disable auth-blocked.
    5. Scan Twitter sources (individual timeline mode via X API or RSS bridges).
    6. Prune old posts, save.
    7. Return summary.
    """

    all_sources = load_post_sources(mappings_dir=config.mappings_dir)

    # Split sources by type
    rss_sources = [s for s in all_sources if s.source_type == "rss"]
    linkedin_sources = [s for s in all_sources if s.source_type == "linkedin"]
    twitter_sources = [s for s in all_sources if s.source_type == "twitter"]

    # Apply limit per type if set
    if config.limit is not None:
        rss_sources = rss_sources[: config.limit]
        linkedin_sources = linkedin_sources[: config.limit]
        twitter_sources = twitter_sources[: config.limit]

    # Set up Twitter user cache
    twitter_user_cache = None
    if config.twitter_bearer_token:
        from eth_defi.feed.twitter_api import TwitterUserCache, resolve_twitter_handles

        cache_path = config.twitter_user_cache_path
        twitter_user_cache = TwitterUserCache(cache_path)

        # Pre-resolve handles for Twitter sources so the cache is warm
        handles = [s.source_key for s in twitter_sources]
        if handles:
            resolve_twitter_handles(handles, config.twitter_bearer_token, twitter_user_cache)

    # Sync X list membership (production only, change-detected)
    if config.sync_x_list and config.x_list_id and config.twitter_consumer_key:
        from eth_defi.feed.twitter_api import sync_x_list_members

        handles = [s.source_key for s in twitter_sources]
        db_for_sync = VaultPostDatabase(config.db_path)
        try:
            sync_x_list_members(
                config.x_list_id,
                handles,
                config.twitter_consumer_key,
                config.twitter_consumer_secret,
                config.twitter_access_token,
                config.twitter_access_token_secret,
                twitter_user_cache,
                config.twitter_bearer_token,
                db_for_sync,
            )
            db_for_sync.save()
        finally:
            db_for_sync.close()

    proxy_rotator = fetch_feed_proxy_rotator()
    db = VaultPostDatabase(config.db_path)
    combined_summary = CollectorRunSummary(source_results=[])

    try:
        # Phase 1: RSS sources
        if rss_sources:
            logger.info("Scanning %d RSS sources", len(rss_sources))
            rss_summary = collect_posts(
                db,
                rss_sources,
                max_workers=config.max_workers,
                max_posts_per_source=config.max_posts_per_source,
                request_timeout=config.request_timeout,
                request_delay_seconds=config.request_delay_seconds,
                proxy_rotator=proxy_rotator,
                max_proxy_rotations=config.max_proxy_rotations,
            )
            _merge_summary(combined_summary, rss_summary)

        # Phase 2: LinkedIn sources
        if linkedin_sources:
            logger.info("Scanning %d LinkedIn sources", len(linkedin_sources))
            linkedin_summary = collect_posts(
                db,
                linkedin_sources,
                max_workers=min(config.max_workers, 4),
                max_posts_per_source=config.max_posts_per_source,
                request_timeout=config.request_timeout,
                request_delay_seconds=config.request_delay_seconds,
                proxy_rotator=proxy_rotator,
                max_proxy_rotations=config.max_proxy_rotations,
            )
            _merge_summary(combined_summary, linkedin_summary)

            today_str = native_datetime_utc_now().strftime("%Y-%m-%d")
            auto_disable_failed_linkedin_sources(linkedin_summary, linkedin_sources, today_str)

        # Phase 3: Twitter sources
        if twitter_sources:
            logger.info("Scanning %d Twitter sources", len(twitter_sources))
            twitter_summary = collect_posts(
                db,
                twitter_sources,
                max_workers=config.max_workers,
                max_posts_per_source=config.max_posts_per_source,
                request_timeout=config.request_timeout,
                request_delay_seconds=config.request_delay_seconds,
                twitter_rss_base_urls=config.twitter_rss_base_urls,
                proxy_rotator=proxy_rotator,
                max_proxy_rotations=config.max_proxy_rotations,
                twitter_bearer_token=config.twitter_bearer_token,
                twitter_user_cache=twitter_user_cache,
            )
            _merge_summary(combined_summary, twitter_summary)

        # Detect dead Twitter accounts
        if config.death_detection_days > 0:
            dead_count = _detect_dead_twitter_accounts(db, twitter_sources, config.death_detection_days)
            if dead_count:
                logger.info("Marked %d dead Twitter accounts", dead_count)

        # Prune old posts
        pruned = db.prune_posts(max_post_age_days=config.max_post_age_days)
        db.save()
        logger.info("Pruned %d old posts", pruned)

    finally:
        db.close()

    return combined_summary


def _detect_dead_twitter_accounts(
    db: VaultPostDatabase,
    twitter_sources: list,
    death_detection_days: int,
) -> int:
    """Mark Twitter accounts with no recent posts as dead in their YAML files.

    Checks ``last_post_published_at`` in the tracked_sources table.  When a
    source has been checked at least once (``last_checked_at`` is set) and has
    either never had a post or its most recent post is older than
    ``death_detection_days``, the function stamps ``twitter-dead-at`` in the
    feeder YAML so future loads skip it.
    """

    from eth_defi.feed.sources import mark_twitter_source_dead

    cutoff = native_datetime_utc_now() - datetime.timedelta(days=death_detection_days)
    today_str = native_datetime_utc_now().strftime("%Y-%m-%d")
    dead_count = 0

    tracked_df = db.get_tracked_sources_df()
    if tracked_df.empty:
        return 0

    for source in twitter_sources:
        matching = tracked_df[(tracked_df["feeder_id"] == source.feeder_id) & (tracked_df["source_type"] == "twitter") & (tracked_df["source_key"] == source.source_key)]
        if matching.empty:
            continue

        row = matching.iloc[0]
        # Only consider sources that have been checked at least once
        if row["last_checked_at"] is None:
            continue

        last_post = row["last_post_published_at"]
        if last_post is None or last_post < cutoff:
            if mark_twitter_source_dead(source.mapping_file, today_str):
                logger.info(
                    "Marked @%s as dead (last post: %s, cutoff: %s)",
                    source.source_key,
                    last_post,
                    cutoff,
                )
                dead_count += 1

    return dead_count


def _merge_summary(target: CollectorRunSummary, source: CollectorRunSummary) -> None:
    """Merge counters from ``source`` into ``target``."""

    target.sources_loaded += source.sources_loaded
    target.sources_succeeded += source.sources_succeeded
    target.sources_failed += source.sources_failed
    target.posts_fetched += source.posts_fetched
    target.posts_inserted += source.posts_inserted
    if source.source_results:
        if target.source_results is None:
            target.source_results = []
        target.source_results.extend(source.source_results)
