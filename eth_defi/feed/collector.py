"""Vault post collection and feed normalisation."""

import calendar
import datetime
import hashlib
import html
import logging
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Sequence

import feedparser
import joblib
import requests
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_fromtimestamp, native_datetime_utc_now
from eth_defi.event_reader.webshare import ProxyRotator, load_proxy_rotator
from eth_defi.feed.database import CollectedPost, VaultPostDatabase
from eth_defi.feed.sources import TrackedPostSource
from eth_defi.feed.twitter_api import TwitterUserCache, fetch_tweets_from_x_list, fetch_user_tweets

logger = logging.getLogger(__name__)


_RETRYABLE_STATUS_CODES = {403, 429, 502, 503, 504}

#: Status codes that indicate the URL itself is broken, not the proxy.
#: These should fail immediately without proxy rotation or retry.
_PERMANENT_FAILURE_STATUS_CODES = {404, 410}


class AllBridgesFailedError(RuntimeError):
    """Raised when every bridge URL for a social feed source fails.

    :param source_label: Human-readable source type label for error messages.
    :param canonical_url: Canonical source URL for diagnostics.
    :param bridge_errors: List of ``(url, http_status_or_none)`` for each attempt.
      ``None`` for the status code indicates a non-HTTP failure such as a timeout.
    """

    def __init__(
        self,
        source_label: str,
        canonical_url: str,
        bridge_errors: list[tuple[str, int | None]],
    ):
        self.bridge_errors = bridge_errors
        error_parts = [f"{url}: HTTP {code}" if code is not None else f"{url}: connection error" for url, code in bridge_errors]
        super().__init__(f"Could not fetch {source_label} feed for {canonical_url}: {'; '.join(error_parts)}")

    @property
    def indicates_auth_block(self) -> bool:
        """Return True when at least one bridge returned HTTP 503 (LinkedIn auth barrier).

        When all bridges fail and at least one specifically returns 503, LinkedIn is most
        likely redirecting unauthenticated requests to the login page for this company.
        Bridges that are simply down (502 or connection error) do not indicate anything
        about LinkedIn's stance on the company page, so they are not required to return 503.
        """
        return bool(self.bridge_errors) and any(code == 503 for _, code in self.bridge_errors)


#: Default Twitter/X RSS bridge URL templates used when no ``TWITTER_FEED_URL_TEMPLATES``
#: environment variable is set.  Each template must contain a ``{handle}`` placeholder.
#: Verified working as of 2026-04-03.
DEFAULT_TWITTER_URL_TEMPLATES: list[str] = [
    "https://xcancel.com/{handle}/rss",
    "https://rss.xcancel.com/{handle}/rss",
]

#: Default LinkedIn RSS bridge URL templates used when no ``LINKEDIN_FEED_URL_TEMPLATES``
#: environment variable is set.  Each template must contain a ``{company_id}`` placeholder.
#: Verified working as of 2026-04-03.
#:
#: .. note::
#:
#:     RSSHub bridges rely on unauthenticated LinkedIn access.  LinkedIn serves public
#:     company post feeds only for large or verified organisations.  Smaller companies
#:     redirect unauthenticated scrapers to the login page, causing every bridge to
#:     return 503 regardless of which instance is used.  When the standard scan detects
#:     this pattern it writes ``linkedin-rss-hub-disabled-at`` to the feeder YAML so
#:     future runs skip the source without retrying.
DEFAULT_LINKEDIN_URL_TEMPLATES: list[str] = [
    "https://rsshub.pseudoyu.com/linkedin/company/{company_id}/posts",
    "https://rss.owo.nz/linkedin/company/{company_id}/posts",
    "https://rsshub.umzzz.com/linkedin/company/{company_id}/posts",
]


@dataclass(slots=True)
class CollectorRunSummary:
    """Summary counters for one collector run."""

    #: Number of configured sources seen at the start of the run.
    sources_loaded: int = 0
    #: Number of sources collected successfully.
    sources_succeeded: int = 0
    #: Number of sources that failed or were skipped with an error.
    sources_failed: int = 0
    #: Number of feeder YAML files where all sources were disabled.
    feeders_skipped: int = 0
    #: Total number of parsed posts returned by all successful source reads.
    posts_fetched: int = 0
    #: Number of newly inserted posts after deduplication.
    posts_inserted: int = 0
    #: Per-source collection results for dashboard rendering.
    source_results: list["CollectedSourceResult"] | None = None


@dataclass(slots=True)
class CollectedSourceResult:
    """Detailed collection result for one tracked source."""

    #: Canonical feeder slug for the collected source.
    feeder_id: str
    #: Human-readable feeder name for diagnostics.
    name: str
    #: Feeder role such as protocol, curator, or vault.
    role: str
    #: Source transport type such as rss, twitter, or linkedin.
    source_type: str
    #: Final status for this source, such as success or failed.
    status: str
    #: Number of parsed posts fetched from this source.
    posts_fetched: int = 0
    #: Number of inserted posts after deduplication.
    posts_inserted: int = 0
    #: Last published timestamp seen in this source, if any.
    last_post_published_at: datetime.datetime | None = None
    #: Error message when the source failed or was skipped.
    error: str | None = None
    #: True when all bridge attempts failed and at least one returned HTTP 503 (LinkedIn auth barrier).
    auth_blocked: bool = False


def _deduplicate_urls(urls: Sequence[str]) -> list[str]:
    """Deduplicate URLs while preserving order."""

    seen: set[str] = set()
    result = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _expand_feed_url_templates(url_templates: Sequence[str], **kwargs: str) -> list[str]:
    """Expand URL templates for social feed bridges."""

    return _deduplicate_urls([template.strip().format(**kwargs) for template in url_templates if template.strip()])


def build_twitter_rss_feed_urls(
    handle: str,
    base_urls: Sequence[str],
    *,
    url_templates: Sequence[str] | None = None,
) -> list[str]:
    """Build live feed URLs for a Twitter handle."""

    urls = [f"{base_url.rstrip('/')}/{handle}/rss" for base_url in base_urls if base_url.strip()]
    urls.extend(_expand_feed_url_templates(url_templates or [], handle=handle))
    return _deduplicate_urls(urls)


def build_linkedin_rss_feed_urls(company_id: str, url_templates: Sequence[str]) -> list[str]:
    """Build live feed URLs for a LinkedIn company id."""

    return _expand_feed_url_templates(url_templates, company_id=company_id)


def _normalise_whitespace(text: str) -> str:
    """Collapse whitespace and strip simple HTML markup."""

    without_tags = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def _extract_post_url(entry) -> str | None:
    """Extract a canonical post URL from one feed entry."""

    link = entry.get("link")
    if isinstance(link, str) and link.strip():
        return link.strip()
    return None


def _extract_entry_text(entry) -> str:
    """Extract the best available full-text candidate from a feed entry."""

    content_items = entry.get("content")
    if isinstance(content_items, list):
        for item in content_items:
            value = item.get("value") if isinstance(item, dict) else None
            if value:
                return _normalise_whitespace(value)

    for key in ("summary", "description", "title"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return _normalise_whitespace(value)

    return ""


def _extract_title(entry) -> str | None:
    """Extract a normalised title from a feed entry."""

    title = entry.get("title")
    if isinstance(title, str) and title.strip():
        return _normalise_whitespace(title.strip())
    return None


def _extract_short_description(entry, full_text: str, max_length: int = 200) -> str:
    """Create a short description for a feed entry."""

    title = _extract_title(entry)
    if title:
        return title[:max_length]
    return full_text[:max_length]


def _extract_published_at(entry) -> datetime.datetime | None:
    """Convert feedparser time tuples to naive UTC datetimes."""

    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            return native_datetime_utc_fromtimestamp(calendar.timegm(value))
    return None


def _build_fallback_external_post_id(
    *,
    source: TrackedPostSource,
    post_url: str | None,
    published_at: datetime.datetime | None,
    title: str,
) -> str:
    """Build a deterministic external post ID when feeds do not provide one."""

    if post_url and published_at:
        payload = f"url-ts:{post_url}|{published_at.isoformat()}"
    elif post_url:
        payload = f"url:{post_url}"
    else:
        timestamp_part = published_at.isoformat() if published_at else ""
        payload = f"source:{source.source_key}|ts:{timestamp_part}|title:{title}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _extract_external_post_id(
    source: TrackedPostSource,
    entry,
    *,
    post_url: str | None,
    published_at: datetime.datetime | None,
) -> str:
    """Extract or synthesise a stable external post ID."""

    for key in ("id", "guid"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    title = entry.get("title") if isinstance(entry.get("title"), str) else ""
    return _build_fallback_external_post_id(
        source=source,
        post_url=post_url,
        published_at=published_at,
        title=title,
    )


def _parse_feed_entries(source: TrackedPostSource, feed_content: bytes, max_posts: int) -> list[CollectedPost]:
    """Parse RSS or Atom feed content into collected posts."""

    parsed = feedparser.parse(feed_content)
    if getattr(parsed, "bozo", False) and not parsed.entries:
        raise ValueError(f"Feed parse failed for {source.canonical_url}: {parsed.bozo_exception}")

    fetched_at = native_datetime_utc_now()
    posts = []

    for entry in list(parsed.entries)[:max_posts]:
        title = _extract_title(entry)
        post_url = _extract_post_url(entry)
        published_at = _extract_published_at(entry)
        full_text = _extract_entry_text(entry)
        short_description = _extract_short_description(entry, full_text, max_length=200)
        external_post_id = _extract_external_post_id(
            source,
            entry,
            post_url=post_url,
            published_at=published_at,
        )

        posts.append(
            CollectedPost(
                external_post_id=external_post_id,
                title=title,
                post_url=post_url,
                published_at=published_at,
                fetched_at=fetched_at,
                short_description=short_description,
                full_text=full_text,
                ai_summary=None,
            )
        )

    return posts


def _build_proxy_dict(proxy_rotator: ProxyRotator | None) -> dict[str, str] | None:
    """Build a ``requests`` proxy dictionary for the active Webshare proxy."""

    if proxy_rotator is None:
        return None

    proxy_url = proxy_rotator.current().to_proxy_url()
    return {
        "http": proxy_url,
        "https": proxy_url,
    }


def fetch_feed_proxy_rotator() -> ProxyRotator | None:
    """Fetch an optional Webshare proxy rotator for feed fetching."""

    try:
        return load_proxy_rotator()
    except requests.RequestException as e:
        logger.warning("Could not load Webshare proxy rotator, falling back to direct requests: %s", e)
        return None


def load_feed_proxy_rotator() -> ProxyRotator | None:
    """Backwards-compatible alias for :func:`fetch_feed_proxy_rotator`."""

    return fetch_feed_proxy_rotator()


@contextmanager
def _tqdm_joblib_progress(progress_bar) -> Iterator[None]:
    """Patch Joblib batch completion callbacks to update a tqdm progress bar."""

    original_callback = joblib.parallel.BatchCompletionCallBack

    class TqdmBatchCompletionCallback(original_callback):
        """Update tqdm when a Joblib batch completes."""

        def __call__(self, *args, **kwargs):
            progress_bar.update(n=self.batch_size)
            return super().__call__(*args, **kwargs)

    joblib.parallel.BatchCompletionCallBack = TqdmBatchCompletionCallback
    try:
        yield
    finally:
        joblib.parallel.BatchCompletionCallBack = original_callback
        progress_bar.close()


def _fetch_feed_content(
    url: str,
    timeout: float,
    *,
    proxy_rotator: ProxyRotator | None = None,
    max_proxy_rotations: int = 3,
) -> bytes:
    """Fetch one feed URL and return response content."""

    using_proxy = proxy_rotator is not None
    rotations = 0

    while True:
        response = None
        try:
            response = requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": "web3-ethereum-defi/vault-post-database"},
                proxies=_build_proxy_dict(proxy_rotator),
            )
            # 404/410 mean the URL itself is gone — no proxy will fix that
            if response.status_code in _PERMANENT_FAILURE_STATUS_CODES:
                response.raise_for_status()

            if proxy_rotator is not None and response.status_code in _RETRYABLE_STATUS_CODES and rotations < max_proxy_rotations:
                rotations += 1
                proxy_rotator.rotate(failure_reason=f"HTTP {response.status_code}")
                continue

            response.raise_for_status()
            return response.content
        except (requests.ConnectionError, requests.Timeout, OSError) as e:
            if proxy_rotator is not None and rotations < max_proxy_rotations:
                rotations += 1
                proxy_rotator.rotate(failure_reason=str(e)[:80])
                continue

            if using_proxy and proxy_rotator is not None:
                logger.warning("Proxy-backed feed fetch failed for %s, retrying direct: %s", url, e)
                proxy_rotator = None
                continue

            raise
        except requests.HTTPError:
            if proxy_rotator is not None and response is not None and response.status_code in _RETRYABLE_STATUS_CODES and rotations < max_proxy_rotations:
                rotations += 1
                proxy_rotator.rotate(failure_reason=f"HTTP {response.status_code}")
                continue

            if using_proxy and proxy_rotator is not None and response is not None and response.status_code in _RETRYABLE_STATUS_CODES:
                logger.warning(
                    "Proxy-backed feed fetch returned retryable HTTP %d for %s, retrying direct",
                    response.status_code,
                    url,
                )
                proxy_rotator = None
                continue

            raise


def collect_posts_for_source(
    source: TrackedPostSource,
    *,
    max_posts_per_source: int,
    request_timeout: float,
    twitter_rss_base_urls: Sequence[str],
    twitter_url_templates: Sequence[str] | None = None,
    linkedin_url_templates: Sequence[str] | None = None,
    proxy_rotator: ProxyRotator | None = None,
    max_proxy_rotations: int = 3,
    twitter_bearer_token: str | None = None,
    twitter_user_cache: TwitterUserCache | None = None,
) -> list[CollectedPost]:
    """Collect posts for one tracked source."""

    if source.source_type == "rss":
        feed_content = _fetch_feed_content(
            source.canonical_url,
            timeout=request_timeout,
            proxy_rotator=proxy_rotator,
            max_proxy_rotations=max_proxy_rotations,
        )
        return _parse_feed_entries(source, feed_content, max_posts=max_posts_per_source)

    if source.source_type == "twitter":
        # Try X API v2 first when bearer token is configured
        if twitter_bearer_token and twitter_user_cache:
            try:
                cached = twitter_user_cache.get(source.source_key)
                if cached:
                    posts = fetch_user_tweets(
                        cached.user_id,
                        twitter_bearer_token,
                        source.source_key,
                        max_tweets=max_posts_per_source,
                    )
                    if posts:
                        return posts
            except Exception as e:
                logger.warning(
                    "X API failed for @%s, falling back to RSS bridges: %s",
                    source.source_key,
                    e,
                )

        candidate_urls = build_twitter_rss_feed_urls(
            source.source_key,
            twitter_rss_base_urls,
            url_templates=twitter_url_templates,
        )
    elif source.source_type == "linkedin":
        candidate_urls = build_linkedin_rss_feed_urls(
            source.source_key,
            linkedin_url_templates or [],
        )
    else:
        raise ValueError(f"Unsupported source type: {source.source_type}")

    errors: list[tuple[str, int | None]] = []
    for candidate_url in candidate_urls:
        try:
            # Bridge URLs are third-party RSS bridge instances (xcancel, RSSHub).
            # When a bridge returns 503 it means the upstream platform (LinkedIn/Twitter)
            # is blocking the bridge's scraper — rotating our egress proxy to reach the
            # same bridge won't change the upstream response.  Skip proxy rotation for
            # bridge candidate URLs to avoid wasting time and polluting proxy failure stats.
            feed_content = _fetch_feed_content(
                candidate_url,
                timeout=request_timeout,
            )
            return _parse_feed_entries(source, feed_content, max_posts=max_posts_per_source)
        except requests.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else None
            errors.append((candidate_url, status_code))
        except (requests.RequestException, ValueError, OSError) as e:
            errors.append((candidate_url, None))

    source_label = "Twitter bridge" if source.source_type == "twitter" else "LinkedIn bridge"
    raise AllBridgesFailedError(source_label, source.canonical_url, errors)


def _collect_posts_for_source_worker(
    idx: int,
    source: TrackedPostSource,
    *,
    max_posts_per_source: int,
    request_timeout: float,
    request_delay_seconds: float,
    twitter_rss_base_urls: Sequence[str],
    twitter_url_templates: Sequence[str],
    linkedin_url_templates: Sequence[str],
    proxy_rotator: ProxyRotator | None,
    max_proxy_rotations: int,
    twitter_bearer_token: str | None = None,
    twitter_user_cache: TwitterUserCache | None = None,
) -> tuple[TrackedPostSource, list[CollectedPost] | None, CollectedSourceResult]:
    """Collect one source in a worker thread and return structured status."""

    # Use the shared proxy rotator directly instead of cloning per-source.
    # rotate() is thread-safe, so each worker call advances to the next proxy
    # and consecutive requests to the same domain (e.g. medium.com) come from
    # different IPs.  Cloning per-source resets the index and causes proxy reuse.
    checked_rotator = proxy_rotator
    if checked_rotator is not None:
        checked_rotator.rotate(failure_reason=None)

    if request_delay_seconds > 0:
        time.sleep(request_delay_seconds)

    if source.source_type == "twitter" and not (twitter_rss_base_urls or twitter_url_templates or twitter_bearer_token):
        error = f"Twitter live feed bridge not configured for {source.canonical_url}"
        return (
            source,
            None,
            CollectedSourceResult(
                feeder_id=source.feeder_id,
                name=source.name,
                role=source.role,
                source_type=source.source_type,
                status="failed",
                error=error,
            ),
        )

    if source.source_type == "linkedin" and not linkedin_url_templates:
        error = f"LinkedIn live feed bridge not configured for {source.canonical_url}"
        return (
            source,
            None,
            CollectedSourceResult(
                feeder_id=source.feeder_id,
                name=source.name,
                role=source.role,
                source_type=source.source_type,
                status="failed",
                error=error,
            ),
        )

    try:
        posts = collect_posts_for_source(
            source,
            max_posts_per_source=max_posts_per_source,
            request_timeout=request_timeout,
            twitter_rss_base_urls=twitter_rss_base_urls,
            twitter_url_templates=twitter_url_templates,
            linkedin_url_templates=linkedin_url_templates,
            proxy_rotator=checked_rotator,
            max_proxy_rotations=max_proxy_rotations,
            twitter_bearer_token=twitter_bearer_token,
            twitter_user_cache=twitter_user_cache,
        )
    except (requests.RequestException, ValueError, RuntimeError) as e:
        auth_blocked = isinstance(e, AllBridgesFailedError) and e.indicates_auth_block
        return (
            source,
            None,
            CollectedSourceResult(
                feeder_id=source.feeder_id,
                name=source.name,
                role=source.role,
                source_type=source.source_type,
                status="failed",
                error=str(e),
                auth_blocked=auth_blocked,
            ),
        )

    latest_post_at = max((post.published_at for post in posts if post.published_at is not None), default=None)
    return (
        source,
        posts,
        CollectedSourceResult(
            feeder_id=source.feeder_id,
            name=source.name,
            role=source.role,
            source_type=source.source_type,
            status="success",
            posts_fetched=len(posts),
            last_post_published_at=latest_post_at,
        ),
    )


def collect_posts(
    db: VaultPostDatabase,
    sources: Sequence[TrackedPostSource],
    *,
    max_posts_per_source: int = 20,
    max_workers: int = 8,
    request_timeout: float = 20.0,
    request_delay_seconds: float = 1.0,
    twitter_rss_base_urls: Sequence[str] | None = None,
    twitter_url_templates: Sequence[str] | None = None,
    linkedin_url_templates: Sequence[str] | None = None,
    proxy_rotator: ProxyRotator | None = None,
    max_proxy_rotations: int = 3,
    twitter_bearer_token: str | None = None,
    twitter_user_cache: TwitterUserCache | None = None,
    label: str = "",
) -> CollectorRunSummary:
    """Collect posts for all configured sources and persist them in DuckDB."""

    summary = CollectorRunSummary(sources_loaded=len(sources), source_results=[])
    source_ids = db.upsert_tracked_sources(sources)
    twitter_rss_base_urls = list(twitter_rss_base_urls or [])
    twitter_url_templates = list(twitter_url_templates if twitter_url_templates is not None else DEFAULT_TWITTER_URL_TEMPLATES)
    linkedin_url_templates = list(linkedin_url_templates if linkedin_url_templates is not None else DEFAULT_LINKEDIN_URL_TEMPLATES)
    worker_count = max(1, min(max_workers, len(sources) or 1))
    type_label = f" {label}" if label else ""
    desc = f"Collecting {len(sources)}{type_label} feed sources using {worker_count} workers"
    worker_processor = Parallel(n_jobs=worker_count, backend="threading")

    with tqdm(total=len(sources), desc=desc) as progress_bar, _tqdm_joblib_progress(progress_bar):
        worker_results = worker_processor(
            delayed(_collect_posts_for_source_worker)(
                idx,
                source,
                max_posts_per_source=max_posts_per_source,
                request_timeout=request_timeout,
                request_delay_seconds=request_delay_seconds,
                twitter_rss_base_urls=twitter_rss_base_urls,
                twitter_url_templates=twitter_url_templates,
                linkedin_url_templates=linkedin_url_templates,
                proxy_rotator=proxy_rotator,
                max_proxy_rotations=max_proxy_rotations,
                twitter_bearer_token=twitter_bearer_token,
                twitter_user_cache=twitter_user_cache,
            )
            for idx, source in enumerate(sources)
        )

    for source, posts, source_result in worker_results:
        checked_at = native_datetime_utc_now()
        source_id = source_ids[source.get_logical_key()]
        if posts is None:
            logger.warning("Failed to collect posts for %s: %s", source.canonical_url, source_result.error)
            db.mark_source_failure(source_id, source_result.error or "Unknown error", checked_at=checked_at)
            summary.sources_failed += 1
            summary.source_results.append(source_result)
            continue

        inserted = db.insert_posts(source_id, posts)
        db.mark_source_success(
            source_id,
            checked_at=checked_at,
            last_post_published_at=source_result.last_post_published_at,
        )
        summary.sources_succeeded += 1
        summary.posts_fetched += len(posts)
        summary.posts_inserted += inserted
        source_result.posts_inserted = inserted
        summary.source_results.append(source_result)

    return summary


def collect_twitter_list_posts(
    db: VaultPostDatabase,
    sources: Sequence[TrackedPostSource],
    *,
    list_id: str,
    bearer_token: str,
    twitter_user_cache: TwitterUserCache,
    max_tweets: int,
    label: str = "Twitter list",
) -> CollectorRunSummary:
    """Collect Twitter/X posts through a single X list timeline read.

    The list timeline API returns tweets across all list members in reverse
    chronological order.  This lets production collection avoid one API call
    per tracked account while still storing posts under the account-specific
    tracked source rows.

    :param db:
        Vault post database.
    :param sources:
        Twitter tracked sources whose handles are represented in the X list.
    :param list_id:
        Numeric X list ID.
    :param bearer_token:
        X API bearer token used for list timeline reads.
    :param twitter_user_cache:
        Cache containing handle-to-user-ID mappings.
    :param max_tweets:
        Maximum tweets to read from the list timeline.
    :param label:
        Dashboard label for this collection phase.
    :return:
        Collector run summary with per-source insert counters.
    """

    summary = CollectorRunSummary(sources_loaded=len(sources), source_results=[])
    source_ids = db.upsert_tracked_sources(sources)
    known_post_ids = db.get_known_post_ids()
    posts_by_user_id = fetch_tweets_from_x_list(
        list_id,
        bearer_token,
        twitter_user_cache,
        max_tweets=max_tweets,
        known_post_ids=known_post_ids,
    )
    checked_at = native_datetime_utc_now()

    for source in sources:
        source_id = source_ids[source.get_logical_key()]
        cached = twitter_user_cache.get(source.source_key)
        posts = posts_by_user_id.get(cached.user_id, []) if cached else []
        latest_post_at = max((post.published_at for post in posts if post.published_at is not None), default=None)
        inserted = db.insert_posts(source_id, posts)
        db.mark_source_success(
            source_id,
            checked_at=checked_at,
            last_post_published_at=latest_post_at,
        )

        source_result = CollectedSourceResult(
            feeder_id=source.feeder_id,
            name=source.name,
            role=source.role,
            source_type=source.source_type,
            status="success",
            posts_fetched=len(posts),
            posts_inserted=inserted,
            last_post_published_at=latest_post_at,
        )
        summary.sources_succeeded += 1
        summary.posts_fetched += len(posts)
        summary.posts_inserted += inserted
        summary.source_results.append(source_result)

    logger.info(
        "Collected %d posts via X list %s for %d %s sources",
        summary.posts_fetched,
        list_id,
        len(sources),
        label,
    )
    return summary
