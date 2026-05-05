"""X/Twitter API v2 integration for tweet collection.

Uses tweepy to read tweets from X lists and individual user timelines.
Provides a user metadata cache to avoid repeated handle-to-ID lookups.

Reading requires only a bearer token (``TWITTER_BEARER_TOKEN``).
List membership writes require full OAuth 1.0a credentials.
"""

import datetime
import hashlib
import html
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import tweepy

from eth_defi.compat import native_datetime_utc_now
from eth_defi.feed.database import CollectedPost, VaultPostDatabase

logger = logging.getLogger(__name__)


def _normalise_whitespace(text: str) -> str:
    """Collapse whitespace and strip simple HTML markup."""

    without_tags = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


#: Default path for the Twitter user metadata cache.
DEFAULT_TWITTER_USER_CACHE_PATH = Path("~/.tradingstrategy/vaults/feeds/twitter-users.json").expanduser()


class XApiError(RuntimeError):
    """Raised when the X API returns an unrecoverable error."""


@dataclass(slots=True)
class CachedTwitterUser:
    """Cached user metadata from a handle-to-ID lookup."""

    #: Numeric X user ID (stable identity).
    user_id: str
    #: Display name at the time of lookup.
    name: str
    #: Handle at the time of lookup.
    handle: str
    #: When this entry was last resolved from the API.
    fetched_at: str


class TwitterUserCache:
    """File-backed cache of Twitter handle-to-user-ID mappings.

    Stored at ``~/.tradingstrategy/vaults/feeds/twitter-users.json``.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or DEFAULT_TWITTER_USER_CACHE_PATH
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            with open(self.path) as f:
                self._data = json.load(f)

    def save(self) -> None:
        """Persist the cache to disk."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2, sort_keys=True)

    def get(self, handle: str) -> CachedTwitterUser | None:
        """Look up a cached user entry by handle (case-insensitive)."""

        entry = self._data.get(handle.lower())
        if entry is None:
            return None
        return CachedTwitterUser(
            user_id=entry["user_id"],
            name=entry["name"],
            handle=entry.get("handle", handle),
            fetched_at=entry["fetched_at"],
        )

    def get_by_user_id(self, user_id: str) -> CachedTwitterUser | None:
        """Look up a cached user entry by numeric user ID."""

        for handle, entry in self._data.items():
            if entry["user_id"] == user_id:
                return CachedTwitterUser(
                    user_id=entry["user_id"],
                    name=entry["name"],
                    handle=handle,
                    fetched_at=entry["fetched_at"],
                )
        return None

    def put(self, handle: str, user_id: str, name: str) -> None:
        """Store or update a cache entry."""

        self._data[handle.lower()] = {
            "user_id": user_id,
            "name": name,
            "handle": handle.lower(),
            "fetched_at": native_datetime_utc_now().isoformat(),
        }

    def is_stale(self, handle: str, max_age_days: int = 30) -> bool:
        """Check whether a cache entry is missing or older than ``max_age_days``."""

        entry = self.get(handle)
        if entry is None:
            return True
        fetched = datetime.datetime.fromisoformat(entry.fetched_at)
        return (native_datetime_utc_now() - fetched).days > max_age_days

    def get_all_user_ids(self) -> dict[str, str]:
        """Return a mapping of handle → user_id for all cached entries."""

        return {handle: entry["user_id"] for handle, entry in self._data.items()}


def resolve_twitter_handles(
    handles: list[str],
    bearer_token: str,
    cache: TwitterUserCache,
    *,
    max_age_days: int = 30,
) -> dict[str, str]:
    """Resolve Twitter handles to user IDs, using the cache for known entries.

    Only looks up handles that are missing from the cache or stale.
    Returns a mapping of handle → user_id.

    The X API ``get_users`` response may include an ``errors`` list
    describing why specific handles could not be resolved (suspended,
    not found, renamed, etc.).  These reasons are logged so operators
    can take corrective action on the corresponding YAML files.
    """

    stale = [h for h in handles if cache.is_stale(h, max_age_days)]
    if not stale:
        return {h: cache.get(h).user_id for h in handles}

    client = tweepy.Client(bearer_token=bearer_token)

    # Collect per-handle error reasons returned by the X API
    error_reasons: dict[str, str] = {}

    # Batch-resolve in groups of 100 (API limit)
    for i in range(0, len(stale), 100):
        batch = stale[i : i + 100]
        try:
            response = client.get_users(usernames=batch, user_fields=["id", "name", "username"])
        except tweepy.TweepyException as e:
            raise XApiError(f"Failed to resolve handles {batch[:3]}...: {e}") from e

        if response.data:
            for user in response.data:
                cache.put(user.username, str(user.id), user.name)

        # Extract per-handle error details from the API response
        if response.errors:
            for err in response.errors:
                # The ``value`` field contains the username that failed
                err_handle = err.get("value", "").lower()
                title = err.get("title", "Unknown error")
                detail = err.get("detail", "")
                reason = f"{title}: {detail}" if detail else title
                if err_handle:
                    error_reasons[err_handle] = reason

    cache.save()

    result = {}
    for h in handles:
        cached = cache.get(h)
        if cached:
            result[h] = cached.user_id
        else:
            reason = error_reasons.get(h.lower(), "handle not present in API response (may be suspended or deleted)")
            logger.warning("Could not resolve Twitter handle @%s to user ID — %s", h, reason)
    return result


def resolve_x_list_id_by_name(
    list_name: str,
    consumer_key: str,
    consumer_secret: str,
    access_token: str,
    access_token_secret: str,
) -> str:
    """Resolve an X list ID by exact list name for the authenticated user.

    Uses OAuth 1.0a user context to read the current X user and enumerate
    the lists owned by that account.  This is intended for operator scripts
    where the production list owner is also the OAuth user.

    :param list_name:
        Exact X list name to find, e.g. ``Best builders in DeFi``.
    :param consumer_key:
        OAuth 1.0a consumer key.
    :param consumer_secret:
        OAuth 1.0a consumer secret.
    :param access_token:
        OAuth 1.0a user access token.
    :param access_token_secret:
        OAuth 1.0a user access token secret.
    :return:
        Numeric X list ID as a string.
    :raise XApiError:
        If the current user cannot be read, or if zero or multiple owned lists
        match the requested name.

    See the X API v2 list lookup endpoints:
    https://docs.x.com/x-api/lists/list-lookup
    """

    client = tweepy.Client(
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        access_token=access_token,
        access_token_secret=access_token_secret,
    )

    try:
        me_response = client.get_me(user_auth=True)
    except tweepy.TweepyException as e:
        message = f"Failed to resolve authenticated X user: {e}"
        raise XApiError(message) from e

    if me_response.data is None:
        message = "Failed to resolve authenticated X user: response did not include user data"
        raise XApiError(message)

    owner_id = str(me_response.data.id)
    matches: list[str] = []
    pagination_token = None

    while True:
        request_params = {
            "max_results": 100,
            "user_auth": True,
        }
        if pagination_token:
            request_params["pagination_token"] = pagination_token

        try:
            response = client.get_owned_lists(
                owner_id,
                **request_params,
            )
        except tweepy.TweepyException as e:
            message = f"Failed to fetch owned X lists for user {owner_id}: {e}"
            raise XApiError(message) from e

        if response.data:
            for item in response.data:
                if item.name == list_name:
                    matches.append(str(item.id))

        meta = response.meta or {}
        pagination_token = meta.get("next_token")
        if not pagination_token:
            break

    if not matches:
        message = f"Could not find an X list named {list_name!r} owned by user {owner_id}"
        raise XApiError(message)
    if len(matches) > 1:
        message = f"Found multiple X lists named {list_name!r} owned by user {owner_id}: {matches}"
        raise XApiError(message)

    logger.info("Resolved X list %r to id %s for user %s", list_name, matches[0], owner_id)
    return matches[0]


def _tweet_to_collected_post(tweet_data: dict, author_handle: str) -> CollectedPost:
    """Map a raw tweet dict from the X API to a :class:`CollectedPost`."""

    tweet_id = str(tweet_data["id"])
    text = tweet_data.get("text", "")
    normalised_text = _normalise_whitespace(text)
    created_at_str = tweet_data.get("created_at")
    published_at = None
    if created_at_str:
        published_at = datetime.datetime.fromisoformat(created_at_str.replace("Z", "+00:00")).replace(tzinfo=None)

    return CollectedPost(
        external_post_id=tweet_id,
        title=None,
        post_url=f"https://x.com/{author_handle}/status/{tweet_id}",
        published_at=published_at,
        fetched_at=native_datetime_utc_now(),
        short_description=normalised_text[:200],
        full_text=normalised_text,
        ai_summary=None,
        raw_payload=json.dumps(tweet_data, default=str),
    )


def fetch_tweets_from_x_list(
    list_id: str,
    bearer_token: str,
    user_cache: TwitterUserCache,
    *,
    max_tweets: int = 100,
    known_post_ids: set[str] | None = None,
) -> dict[str, list[CollectedPost]]:
    """Fetch tweets from an X list timeline, grouped by author user_id.

    Uses cursor-based pagination.  Stops when encountering tweets already
    in the database (checked against ``known_post_ids``) or when
    ``max_tweets`` is reached.

    :return: Mapping of user_id → list of :class:`CollectedPost`.
    """

    client = tweepy.Client(bearer_token=bearer_token)
    known = known_post_ids or set()
    result: dict[str, list[CollectedPost]] = {}
    total_fetched = 0
    pagination_token = None

    tweet_fields = ["id", "text", "created_at", "author_id", "public_metrics", "entities", "referenced_tweets"]
    expansions = ["author_id"]
    user_fields = ["id", "username", "name"]

    while total_fetched < max_tweets:
        try:
            page_size = min(100, max_tweets - total_fetched)
            response = client.get_list_tweets(
                list_id,
                max_results=page_size,
                tweet_fields=tweet_fields,
                expansions=expansions,
                user_fields=user_fields,
                pagination_token=pagination_token,
            )
        except tweepy.TweepyException as e:
            raise XApiError(f"Failed to fetch list timeline for list {list_id}: {e}") from e

        if not response.data:
            break

        # Build author_id → handle lookup from includes
        author_map: dict[str, str] = {}
        if response.includes and "users" in response.includes:
            for user in response.includes["users"]:
                author_map[str(user.id)] = user.username

        hit_known = False
        for tweet in response.data:
            tweet_id = str(tweet.id)
            if tweet_id in known:
                hit_known = True
                break

            author_id = str(tweet.author_id)
            author_handle = author_map.get(author_id, "unknown")

            # Update cache with author info from includes
            cached = user_cache.get_by_user_id(author_id)
            if cached is None:
                user_cache.put(author_handle, author_id, author_handle)

            post = _tweet_to_collected_post(tweet.data, author_handle)
            result.setdefault(author_id, []).append(post)
            total_fetched += 1

        if hit_known:
            break

        # Check for next page
        meta = response.meta or {}
        pagination_token = meta.get("next_token")
        if not pagination_token:
            break

    logger.info("Fetched %d tweets from X list %s across %d authors", total_fetched, list_id, len(result))
    return result


def fetch_user_tweets(
    user_id: str,
    bearer_token: str,
    author_handle: str,
    *,
    max_tweets: int = 10,
    since: datetime.datetime | None = None,
) -> list[CollectedPost]:
    """Fetch recent tweets from a single user timeline.

    ``GET /2/users/:id/tweets`` supports ``start_time`` for incremental reads.
    Used when ``LIMIT`` is set or for backfilling newly added members.
    """

    client = tweepy.Client(bearer_token=bearer_token)
    tweet_fields = ["id", "text", "created_at", "author_id", "public_metrics", "entities", "referenced_tweets"]

    kwargs = {}
    if since is not None:
        # X API requires RFC 3339 format with Z suffix
        kwargs["start_time"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        response = client.get_users_tweets(
            user_id,
            max_results=min(max_tweets, 100),
            tweet_fields=tweet_fields,
            **kwargs,
        )
    except tweepy.TweepyException as e:
        raise XApiError(f"Failed to fetch tweets for user {user_id} (@{author_handle}): {e}") from e

    if not response.data:
        return []

    posts = []
    for tweet in response.data[:max_tweets]:
        post = _tweet_to_collected_post(tweet.data, author_handle)
        posts.append(post)

    logger.info("Fetched %d tweets for @%s (user_id=%s)", len(posts), author_handle, user_id)
    return posts


def compute_handles_hash(handles: list[str]) -> str:
    """Compute a deterministic hash of a sorted set of Twitter handles."""

    normalised = sorted(h.lower() for h in handles)
    payload = "\n".join(normalised)
    return hashlib.sha256(payload.encode()).hexdigest()


def sync_x_list_members(
    list_id: str,
    twitter_handles: list[str],
    consumer_key: str,
    consumer_secret: str,
    access_token: str,
    access_token_secret: str,
    user_cache: TwitterUserCache,
    bearer_token: str,
    db: VaultPostDatabase,
) -> int:
    """Sync X list membership with the provided Twitter handles.

    Only runs when the set of handles has changed (hash-based detection
    using ``feed_sync_state`` table).  Returns the number of members added.

    Requires full OAuth 1.0a credentials for list write operations.
    """

    current_hash = compute_handles_hash(twitter_handles)
    stored_hash = db.get_sync_state("twitter_handles_hash")
    if stored_hash == current_hash:
        logger.info("Twitter handles unchanged (hash=%s), skipping list sync", current_hash[:12])
        return 0

    logger.info("Twitter handles changed, syncing X list %s (%d handles)", list_id, len(twitter_handles))

    # Resolve handles → user IDs
    handle_to_id = resolve_twitter_handles(twitter_handles, bearer_token, user_cache)

    # Get current list members
    client_read = tweepy.Client(bearer_token=bearer_token)
    current_member_ids: set[str] = set()
    pagination_token = None

    while True:
        try:
            response = client_read.get_list_members(
                list_id,
                max_results=100,
                pagination_token=pagination_token,
            )
        except tweepy.TweepyException as e:
            raise XApiError(f"Failed to fetch list members for list {list_id}: {e}") from e

        if response.data:
            for member in response.data:
                current_member_ids.add(str(member.id))

        meta = response.meta or {}
        pagination_token = meta.get("next_token")
        if not pagination_token:
            break

    # Add missing members using OAuth 1.0a user context
    target_ids = set(handle_to_id.values())
    to_add = target_ids - current_member_ids

    if not to_add:
        logger.info("All %d handles already in X list %s", len(twitter_handles), list_id)
        db.set_sync_state("twitter_handles_hash", current_hash)
        return 0

    client_write = tweepy.Client(
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        access_token=access_token,
        access_token_secret=access_token_secret,
    )

    added = 0
    failed = 0
    for user_id in to_add:
        try:
            client_write.add_list_member(list_id, user_id)
            added += 1
        except tweepy.TweepyException as e:
            failed += 1
            logger.warning("Failed to add user %s to list %s: %s", user_id, list_id, e)

    # Only persist the hash when every add succeeded.  When some fail due to
    # transient API errors the next cycle will detect the mismatch and retry.
    if failed == 0:
        db.set_sync_state("twitter_handles_hash", current_hash)
    else:
        logger.warning(
            "Skipping hash update — %d/%d list member adds failed, will retry next cycle",
            failed,
            len(to_add),
        )

    user_cache.save()
    logger.info("Added %d members to X list %s (%d already present, %d failed)", added, list_id, len(current_member_ids), failed)
    return added
