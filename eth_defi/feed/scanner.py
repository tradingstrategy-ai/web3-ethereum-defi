"""Post scan orchestration.

Provides :func:`run_post_scan_cycle` which runs a complete collection cycle
(RSS → LinkedIn → Twitter) and can be called directly from integration tests
without going through the script entry point.
"""

import datetime
import json
import logging
import os
import re
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from strictyaml import YAMLError

from eth_defi.compat import native_datetime_utc_now
from eth_defi.feed.collector import CollectorRunSummary, collect_posts, collect_twitter_list_posts, fetch_feed_proxy_rotator
from eth_defi.feed.constants import DEFAULT_X_LIST_NAME
from eth_defi.feed.database import DEFAULT_VAULT_POST_DATABASE, VaultPostDatabase
from eth_defi.feed.sources import (
    FEEDS_DATA_DIR,
    auto_disable_failed_linkedin_sources,
    build_twitter_source_file_lookup,
    load_post_sources,
    mark_suspended_twitter_handle,
    mark_rss_source_dead,
    mark_rss_source_failure,
    mark_twitter_handle_unknown,
    mark_twitter_source_dead,
)
from eth_defi.feed.stablecoin_rate import StablecoinRateRefreshSummary, refresh_stablecoin_rates
from eth_defi.feed.twitter_api import TwitterUserCache, XApiError, resolve_twitter_handles, resolve_x_list_id_by_name, sync_x_list_members
from eth_defi.stablecoin_metadata import STABLECOINS_DATA_DIR

logger = logging.getLogger(__name__)

_STABLECOIN_RATE_SIDE_JOB_ERROR_TYPES = (
    YAMLError,
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    IndexError,
)


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
    #: X list ID. If unset, resolve :py:attr:`x_list_name` for the authenticated X user.
    x_list_id: str | None = None
    #: X list name to resolve when :py:attr:`x_list_id` is not set.
    x_list_name: str = DEFAULT_X_LIST_NAME
    #: Path to the Twitter user metadata cache JSON.
    twitter_user_cache_path: Path | None = None
    #: Enable X list membership sync (production only).
    sync_x_list: bool = False
    #: Delay between X list member write calls.
    x_list_add_delay_seconds: float = 1.0
    #: Maximum automatic sleep after X API list-write rate limits.
    x_list_rate_limit_sleep_max_seconds: float = 1200.0
    #: Use X list timeline reads for Twitter collection when a list ID is available.
    use_x_list_timeline: bool = True
    #: Limit number of sources per type (for test runs).
    limit: int | None = None
    #: Days after which an inactive Twitter account is considered dead.
    death_detection_days: int = 180
    #: Comma-separated RSS bridge base URLs.
    twitter_rss_base_urls: list[str] = field(default_factory=list)
    #: Refresh stablecoin rates as a post-scan side job.
    refresh_stablecoin_rates: bool = True
    #: Force stablecoin refresh even if the scanner-level 24h gate would skip it.
    force_stablecoin_rate_refresh: bool = False
    #: Stablecoin YAML data directory.
    stablecoin_data_dir: Path = field(default_factory=lambda: STABLECOINS_DATA_DIR)
    #: Stablecoin rate HTTP timeout.
    stablecoin_rate_timeout: float = 20.0
    #: Durable state file for scanner-level 24h stablecoin refresh gate.
    stablecoin_rate_gate_path: Path | None = None


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

    all_sources, feeders_skipped, aliases = load_post_sources(mappings_dir=config.mappings_dir)
    if aliases:
        logger.info("Skipped %d alias feeders (canonical-feeder-id set)", len(aliases))

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
        cache_path = config.twitter_user_cache_path
        twitter_user_cache = TwitterUserCache(cache_path)

        # Pre-resolve handles for Twitter sources so the cache is warm
        handles = [s.source_key for s in twitter_sources]
        if handles:
            handle_to_id = resolve_twitter_handles(handles, config.twitter_bearer_token, twitter_user_cache)

            # Stamp unresolvable handles and remove them from the scan
            today_str = native_datetime_utc_now().strftime("%Y-%m-%d")
            unresolved = [s for s in twitter_sources if s.source_key not in handle_to_id]
            for source in unresolved:
                updated = mark_twitter_handle_unknown(source.mapping_file, today_str)
                if updated:
                    logger.info(
                        "Marked @%s as unresolvable handle — added twitter-handle-resolved-unknown-at to %s",
                        source.source_key,
                        source.mapping_file,
                    )
                else:
                    logger.info(
                        "Skipping @%s — already marked as unresolvable in %s",
                        source.source_key,
                        source.mapping_file,
                    )
            twitter_sources = [s for s in twitter_sources if s.source_key in handle_to_id]

    resolved_x_list_id = config.x_list_id
    can_resolve_x_list_id = all(
        (
            config.twitter_consumer_key,
            config.twitter_consumer_secret,
            config.twitter_access_token,
            config.twitter_access_token_secret,
        )
    )
    if not resolved_x_list_id and can_resolve_x_list_id and (config.sync_x_list or config.use_x_list_timeline):
        resolved_x_list_id = resolve_x_list_id_by_name(
            config.x_list_name or DEFAULT_X_LIST_NAME,
            config.twitter_consumer_key,
            config.twitter_consumer_secret,
            config.twitter_access_token,
            config.twitter_access_token_secret,
        )

    # Sync X list membership (production only, change-detected)
    can_sync_x_list = all(
        (
            config.twitter_consumer_key,
            config.twitter_consumer_secret,
            config.twitter_access_token,
            config.twitter_access_token_secret,
            config.twitter_bearer_token,
        )
    )

    if config.sync_x_list and not can_sync_x_list:
        logger.warning("Skipping X list sync because one or more X API credentials are missing")

    if config.sync_x_list and can_sync_x_list:
        handles = [s.source_key for s in twitter_sources]
        source_files_by_handle = build_twitter_source_file_lookup(twitter_sources)
        today_str = native_datetime_utc_now().strftime("%Y-%m-%d")

        assert resolved_x_list_id is not None
        db_for_sync = VaultPostDatabase(config.db_path)
        try:
            sync_x_list_members(
                resolved_x_list_id,
                handles,
                config.twitter_consumer_key,
                config.twitter_consumer_secret,
                config.twitter_access_token,
                config.twitter_access_token_secret,
                twitter_user_cache,
                config.twitter_bearer_token,
                db_for_sync,
                add_delay_seconds=config.x_list_add_delay_seconds,
                rate_limit_sleep_max_seconds=config.x_list_rate_limit_sleep_max_seconds,
                suspended_member_callback=lambda handle, user_id: mark_suspended_twitter_handle(
                    handle,
                    user_id,
                    source_files_by_handle,
                    today_str,
                ),
            )
            db_for_sync.save()
        except XApiError as e:
            # List membership sync is non-essential maintenance that runs before
            # post collection.  The X ``GET /2/lists/{id}/members`` endpoint is
            # known to return persistent ``503 Service Unavailable`` for extended
            # periods (endpoint-wide, not list-specific), while the list timeline
            # read used for actual post collection keeps working.  Degrade
            # gracefully and continue the cycle instead of aborting it — the sync
            # state hash is left unchanged, so the next cycle retries.  This
            # mirrors the timeline-collection fallback below.
            logger.warning(
                "X list membership sync failed for list %s, continuing post collection without it: %s",
                resolved_x_list_id,
                e,
            )
        finally:
            db_for_sync.close()

    proxy_rotator = fetch_feed_proxy_rotator()
    db = VaultPostDatabase(config.db_path)
    combined_summary = CollectorRunSummary(source_results=[], feeders_skipped=feeders_skipped)
    twitter_collection_used_list_timeline = False
    total_start = time.monotonic()

    _run_stablecoin_rate_side_job(config, combined_summary)

    try:
        # Phase 1: RSS sources
        # Cap RSS workers at 2 — most RSS feeds are on medium.com which
        # rate-limits aggressively (~30 req/min).  Higher parallelism triggers
        # 429 Too Many Requests across the batch.
        if rss_sources:
            logger.info("Scanning %d RSS sources", len(rss_sources))
            rss_start = time.monotonic()
            rss_summary = collect_posts(
                db,
                rss_sources,
                max_workers=min(config.max_workers, 2),
                max_posts_per_source=config.max_posts_per_source,
                request_timeout=config.request_timeout,
                request_delay_seconds=max(config.request_delay_seconds, 2.0),
                proxy_rotator=proxy_rotator,
                max_proxy_rotations=config.max_proxy_rotations,
                label="RSS",
            )
            combined_summary.rss_duration_seconds = time.monotonic() - rss_start
            _merge_summary(combined_summary, rss_summary)
            _record_rss_failures(rss_summary, rss_sources)
            _detect_dead_rss_feeds(db, rss_sources)

        # Phase 2: LinkedIn sources
        if linkedin_sources:
            logger.info("Scanning %d LinkedIn sources", len(linkedin_sources))
            linkedin_start = time.monotonic()
            linkedin_summary = collect_posts(
                db,
                linkedin_sources,
                max_workers=min(config.max_workers, 4),
                max_posts_per_source=config.max_posts_per_source,
                request_timeout=config.request_timeout,
                request_delay_seconds=config.request_delay_seconds,
                proxy_rotator=proxy_rotator,
                max_proxy_rotations=config.max_proxy_rotations,
                label="LinkedIn",
            )
            combined_summary.linkedin_duration_seconds = time.monotonic() - linkedin_start
            _merge_summary(combined_summary, linkedin_summary)

            today_str = native_datetime_utc_now().strftime("%Y-%m-%d")
            auto_disable_failed_linkedin_sources(linkedin_summary, linkedin_sources, today_str)

        # Phase 3: Twitter sources
        if twitter_sources:
            logger.info("Scanning %d Twitter sources", len(twitter_sources))
            twitter_start = time.monotonic()
            if config.use_x_list_timeline and config.twitter_bearer_token and twitter_user_cache and resolved_x_list_id:
                logger.info("Collecting Twitter posts through X list timeline %s", resolved_x_list_id)
                try:
                    twitter_summary = collect_twitter_list_posts(
                        db,
                        twitter_sources,
                        list_id=resolved_x_list_id,
                        bearer_token=config.twitter_bearer_token,
                        twitter_user_cache=twitter_user_cache,
                        max_tweets=max(100, config.max_posts_per_source * len(twitter_sources)),
                    )
                    twitter_collection_used_list_timeline = True
                    combined_summary.twitter_method = "list"
                except XApiError as e:
                    logger.warning(
                        "X list timeline collection failed for list %s, falling back to per-source Twitter collection: %s",
                        resolved_x_list_id,
                        e,
                    )
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
                        label="Twitter",
                    )
                    combined_summary.twitter_method = "rss-bridge"
            else:
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
                    label="Twitter",
                )
                combined_summary.twitter_method = "rss-bridge"
            combined_summary.twitter_duration_seconds = time.monotonic() - twitter_start
            _merge_summary(combined_summary, twitter_summary)

        # Detect dead Twitter accounts
        if config.death_detection_days > 0 and not twitter_collection_used_list_timeline:
            dead_count = _detect_dead_twitter_accounts(db, twitter_sources, config.death_detection_days)
            if dead_count:
                logger.info("Marked %d dead Twitter accounts", dead_count)

        # Prune old posts
        pruned = db.prune_posts(max_post_age_days=config.max_post_age_days)
        db.save()
        logger.info("Pruned %d old posts", pruned)

    finally:
        db.close()

    combined_summary.total_duration_seconds = time.monotonic() - total_start
    return combined_summary


def _detect_dead_rss_feeds(db: VaultPostDatabase, rss_sources: list) -> int:
    """Mark RSS feeds as dead when valid but no posts published for a year.

    Only considers sources that have been successfully fetched at least once
    (``last_success_at`` is set) and whose most recent post is older than
    365 days.
    """

    cutoff = native_datetime_utc_now() - datetime.timedelta(days=365)
    today_str = native_datetime_utc_now().strftime("%Y-%m-%d")
    dead_count = 0

    tracked_df = db.get_tracked_sources_df()
    if tracked_df.empty:
        return 0

    for source in rss_sources:
        matching = tracked_df[(tracked_df["feeder_id"] == source.feeder_id) & (tracked_df["source_type"] == "rss") & (tracked_df["source_key"] == source.source_key)]
        if matching.empty:
            continue

        row = matching.iloc[0]
        # Only consider feeds that have been successfully fetched
        if row["last_success_at"] is None:
            continue

        last_post = row["last_post_published_at"]
        if last_post is not None and last_post < cutoff:
            if mark_rss_source_dead(source.mapping_file, today_str):
                logger.info(
                    "Marked RSS feed %s as dead (last post: %s)",
                    source.source_key,
                    last_post,
                )
                dead_count += 1

    if dead_count:
        logger.info("Marked %d dead RSS feeds", dead_count)
    return dead_count


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


def _record_rss_failures(summary: CollectorRunSummary, rss_sources: list) -> None:
    """Stamp ``rss-failure-at`` and ``rss-failure-status-code`` in YAML for failed RSS sources."""

    results = summary.source_results or []
    today_str = native_datetime_utc_now().strftime("%Y-%m-%d")

    # Build feeder_id → mapping_file lookup from sources
    yaml_lookup: dict[str, "Path"] = {s.feeder_id: s.mapping_file for s in rss_sources if s.source_type == "rss"}

    for result in results:
        if result.source_type != "rss" or result.status != "failed":
            continue
        yaml_path = yaml_lookup.get(result.feeder_id)
        if yaml_path is None:
            continue

        # Extract HTTP status code from error string if present
        status_code = None
        error = result.error or ""
        match = re.search(r"(\d{3}) (?:Client|Server) Error", error)
        if match:
            status_code = int(match.group(1))

        mark_rss_source_failure(yaml_path, today_str, status_code, exception_message=error or None)
        logger.info("Recorded RSS failure for %s: %s", result.feeder_id, status_code)


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


def _stablecoin_gate_path(config: PostScanConfig) -> Path:
    """Return the durable stablecoin refresh gate path for this scan config."""
    if config.stablecoin_rate_gate_path is not None:
        return config.stablecoin_rate_gate_path
    return config.db_path.with_suffix(".stablecoin-rate-state.json")


def _read_stablecoin_gate(path: Path) -> dict:
    """Read the stablecoin refresh gate JSON state."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read stablecoin rate gate state %s: %s", path, e)
        return {}


def _write_stablecoin_gate(path: Path, state: dict) -> None:
    """Write the stablecoin refresh gate JSON state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(path, json.dumps(state, indent=2, sort_keys=True))


def _write_text_atomic(path: Path, text: str) -> None:
    """Write text to a file using a same-directory atomic replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing_mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        existing_mode = None

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as tmp_file:
        tmp_file.write(text)
        tmp_path = Path(tmp_file.name)

    try:
        if existing_mode is not None:
            os.chmod(tmp_path, existing_mode)
        os.replace(tmp_path, path)
    except OSError:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def _stablecoin_refresh_due(state: dict, now_: datetime.datetime) -> bool:
    """Return ``True`` if scanner-level 24h stablecoin refresh gate is open."""
    last_succeeded_at = state.get("last_succeeded_at")
    if not last_succeeded_at:
        return True
    try:
        last_succeeded = datetime.datetime.fromisoformat(last_succeeded_at)
    except ValueError:
        return True
    return now_ - last_succeeded >= datetime.timedelta(hours=24)


def _run_stablecoin_rate_side_job(config: PostScanConfig, summary: CollectorRunSummary) -> None:
    """Run the stablecoin rate refresh side job when the durable gate allows it."""
    if not config.refresh_stablecoin_rates:
        summary.stablecoin_rate_status = "disabled"
        return

    now_ = native_datetime_utc_now()
    gate_path = _stablecoin_gate_path(config)
    gate_state = _read_stablecoin_gate(gate_path)

    if not config.force_stablecoin_rate_refresh and not _stablecoin_refresh_due(gate_state, now_):
        summary.stablecoin_rate_status = "skipped_recent"
        return

    try:
        stablecoin_summary: StablecoinRateRefreshSummary = refresh_stablecoin_rates(
            data_dir=config.stablecoin_data_dir,
            now_=now_,
            force=config.force_stablecoin_rate_refresh,
            timeout=config.stablecoin_rate_timeout,
        )
        if _stablecoin_rate_summary_failed(stablecoin_summary):
            _record_stablecoin_rate_side_job_failure(
                gate_path,
                gate_state,
                summary,
                f"stablecoin rate refresh failed for all due entries: failed_count={stablecoin_summary.failed_count}, rates_fetched={stablecoin_summary.rates_fetched}",
            )
            summary.stablecoin_rate_summary = stablecoin_summary
            return

        gate_state["last_succeeded_at"] = native_datetime_utc_now().replace(microsecond=0).isoformat()
        _write_stablecoin_gate(gate_path, gate_state)
    except _STABLECOIN_RATE_SIDE_JOB_ERROR_TYPES as e:
        _record_stablecoin_rate_side_job_failure(gate_path, gate_state, summary, e)
        return

    summary.stablecoin_rate_status = "succeeded"
    summary.stablecoin_rate_summary = stablecoin_summary


def _stablecoin_rate_summary_failed(summary: StablecoinRateRefreshSummary) -> bool:
    """Return ``True`` if a refresh ran but all due entries failed."""
    all_attempts_failed = summary.failed_count > 0 and summary.rates_fetched == 0
    only_failed_attempts_were_skipped = summary.due_count == 0 and summary.rates_fetched == 0 and summary.skipped_failed_today_count > 0 and summary.skipped_succeeded_today_count == 0
    return all_attempts_failed or only_failed_attempts_were_skipped


def _record_stablecoin_rate_side_job_failure(
    gate_path: Path,
    gate_state: dict,
    summary: CollectorRunSummary,
    error: BaseException | str,
) -> None:
    """Record a stablecoin refresh failure without aborting post collection."""
    logger.warning("Stablecoin rate refresh failed, continuing post scan: %s", error)
    gate_state["last_failed_at"] = native_datetime_utc_now().replace(microsecond=0).isoformat()
    try:
        _write_stablecoin_gate(gate_path, gate_state)
    except _STABLECOIN_RATE_SIDE_JOB_ERROR_TYPES as gate_error:
        logger.warning("Could not write stablecoin rate gate failure state %s: %s", gate_path, gate_error)
    summary.stablecoin_rate_status = "failed"
    summary.stablecoin_rate_error = str(error)
