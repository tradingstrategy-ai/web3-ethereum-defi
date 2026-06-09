"""X/Twitter API v2 integration for tweet collection.

Uses tweepy to read tweets from X lists and individual user timelines.
Provides a user metadata cache to avoid repeated handle-to-ID lookups.

Reading requires only a bearer token (``TWITTER_BEARER_TOKEN``).
List membership writes require full OAuth 1.0a credentials.

Note tweets and full text
-------------------------

The X API v2 caps the ``text`` field of every tweet at **280 characters**.
Tweets longer than this — so called *note tweets*, available to premium and
verified accounts — are returned with a truncated ``text`` value (ending in an
ellipsis) and the complete body, up to ~25,000 characters, in a separate
``note_tweet`` object.

The ``note_tweet`` object is only present when the ``note_tweet`` field is
explicitly requested via the ``tweet_fields`` request parameter.  Both
:py:func:`fetch_tweets_from_x_list` and :py:func:`fetch_user_tweets` request it,
and :py:func:`_extract_full_tweet_text` prefers ``note_tweet.text`` over the
truncated ``text`` so the full body is preserved.

The full body lands in :py:attr:`~eth_defi.feed.database.CollectedPost.full_text`,
while :py:attr:`~eth_defi.feed.database.CollectedPost.short_description` keeps a
200-character preview for compact listings.  Both are carried through to the
vault JSON bundle as
:py:attr:`~eth_defi.vault.curator_export.CuratorFeedEntry.full_text` and
:py:attr:`~eth_defi.vault.curator_export.CuratorFeedEntry.snippet` respectively.

See the X API v2 note tweet documentation:
https://docs.x.com/x-api/fundamentals/note-tweets
"""

import datetime
import hashlib
import html
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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


class XRateLimitError(XApiError):
    """Raised when the X API rate-limits a list sync operation."""


#: Maximum retries for transient X API server errors (HTTP 5xx).
#:
#: The X API intermittently returns ``503 Service Unavailable`` and other 5xx
#: responses during partial outages.  These are transient and succeed on retry,
#: so we back off and try again rather than treating them as fatal.
X_SERVER_ERROR_MAX_RETRIES = 5

#: Base delay in seconds for exponential backoff on transient 5xx errors.
X_SERVER_ERROR_BACKOFF_BASE_SECONDS = 5.0

#: ``feed_sync_state`` key prefix under which the X list member IDs we have
#: added are stored as a JSON array.
#:
#: We maintain our own record of confirmed list members instead of reading them
#: back from the X API, because ``GET /2/lists/{id}/members`` returns persistent
#: endpoint-wide ``503 Service Unavailable`` errors.  See ``README`` notes and
#: :func:`sync_x_list_members`.
#:
#: The actual key is suffixed with the list ID (see :func:`_member_ids_state_key`)
#: so a single database can sync multiple X lists without their member caches
#: colliding.
X_LIST_MEMBER_IDS_STATE_KEY_PREFIX = "x_list_member_ids"

#: ``feed_sync_state`` key prefix under which the hash of synced Twitter handles
#: is stored, used to skip list sync when the handle set is unchanged.
#:
#: Suffixed with the list ID (see :func:`_handles_hash_state_key`) so the
#: change-detection hash is scoped to each X list.
X_HANDLES_HASH_STATE_KEY_PREFIX = "twitter_handles_hash"


def _member_ids_state_key(list_id: str) -> str:
    """Return the per-list ``feed_sync_state`` key for the member ID cache."""

    return f"{X_LIST_MEMBER_IDS_STATE_KEY_PREFIX}:{list_id}"


def _handles_hash_state_key(list_id: str) -> str:
    """Return the per-list ``feed_sync_state`` key for the handle hash."""

    return f"{X_HANDLES_HASH_STATE_KEY_PREFIX}:{list_id}"


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
        response = _x_api_read_with_retry(
            lambda: client.get_users(usernames=batch, user_fields=["id", "name", "username"]),
            description=f"resolving handles {batch[:3]}...",
        )

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

    me_response = _x_api_read_with_retry(
        lambda: client.get_me(user_auth=True),
        description="resolving authenticated X user",
    )

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

        response = _x_api_read_with_retry(
            lambda: client.get_owned_lists(owner_id, **request_params),
            description=f"fetching owned X lists for user {owner_id}",
        )

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


def _extract_full_tweet_text(tweet_data: dict) -> str:
    """Return the full, untruncated text of a tweet.

    The X API v2 caps the ``text`` field at 280 characters.  Tweets longer
    than this (so called *note tweets*, posted by premium accounts) are
    truncated in ``text`` with a trailing ellipsis, and the complete body is
    only returned in the ``note_tweet`` object when the ``note_tweet`` tweet
    field is requested.  Prefer that full body when present, falling back to
    the legacy ``text`` field.

    The returned value is stored, after whitespace normalisation, in
    :py:attr:`~eth_defi.feed.database.CollectedPost.full_text`.  The companion
    :py:attr:`~eth_defi.feed.database.CollectedPost.short_description` keeps a
    200-character preview of the same body.  See :py:func:`_tweet_to_collected_post`
    for the mapping and the module docstring for the note tweet API overview.

    :param tweet_data:
        Raw tweet dict from the X API v2 (``tweepy`` ``Tweet.data``).
        Carries ``note_tweet`` only when the ``note_tweet`` field was requested
        in ``tweet_fields`` — see :py:func:`fetch_tweets_from_x_list`.

    :return:
        The longest available tweet body, raw (not whitespace-normalised).

    See the X API v2 note tweet documentation:
    https://docs.x.com/x-api/fundamentals/note-tweets
    """

    note_tweet = tweet_data.get("note_tweet")
    if note_tweet:
        note_text = note_tweet.get("text")
        if note_text:
            return note_text
    return tweet_data.get("text", "")


def _tweet_to_collected_post(tweet_data: dict, author_handle: str) -> CollectedPost:
    """Map a raw tweet dict from the X API to a :class:`CollectedPost`.

    The full tweet body — including the complete *note tweet* for long tweets,
    see :py:func:`_extract_full_tweet_text` — is stored in
    :py:attr:`~eth_defi.feed.database.CollectedPost.full_text`, while
    :py:attr:`~eth_defi.feed.database.CollectedPost.short_description` keeps the
    first 200 characters as a preview.
    """

    tweet_id = str(tweet_data["id"])
    # Prefer the full note_tweet body over the 280-char truncated text field
    text = _extract_full_tweet_text(tweet_data)
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

    # ``note_tweet`` is required to receive the full body of tweets longer than
    # 280 chars — without it the X API returns only a truncated ``text`` field.
    tweet_fields = ["id", "text", "note_tweet", "created_at", "author_id", "public_metrics", "entities", "referenced_tweets"]
    expansions = ["author_id"]
    user_fields = ["id", "username", "name"]

    while total_fetched < max_tweets:
        page_size = min(100, max_tweets - total_fetched)
        response = _x_api_read_with_retry(
            lambda: client.get_list_tweets(
                list_id,
                max_results=page_size,
                tweet_fields=tweet_fields,
                expansions=expansions,
                user_fields=user_fields,
                pagination_token=pagination_token,
            ),
            description=f"fetching list timeline for list {list_id}",
        )

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
    # ``note_tweet`` is required to receive the full body of tweets longer than
    # 280 chars — without it the X API returns only a truncated ``text`` field.
    tweet_fields = ["id", "text", "note_tweet", "created_at", "author_id", "public_metrics", "entities", "referenced_tweets"]

    kwargs = {}
    if since is not None:
        # X API requires RFC 3339 format with Z suffix
        kwargs["start_time"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    response = _x_api_read_with_retry(
        lambda: client.get_users_tweets(
            user_id,
            max_results=min(max_tweets, 100),
            tweet_fields=tweet_fields,
            **kwargs,
        ),
        description=f"fetching tweets for user {user_id} (@{author_handle})",
    )

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


def _format_rate_limit_reset(exc: tweepy.TooManyRequests) -> str:
    """Format X API rate-limit reset headers for operator logs.

    :param exc:
        Tweepy rate-limit exception.

    :return:
        Human-readable retry hint, or an empty string if no header was present.
    """

    wait_seconds = _get_rate_limit_sleep_seconds(exc)
    if wait_seconds is None:
        return ""
    return f" Retry after {wait_seconds} seconds."


def _get_rate_limit_sleep_seconds(exc: tweepy.TooManyRequests) -> int | None:
    """Read the X API retry delay from rate-limit headers.

    :param exc:
        Tweepy rate-limit exception.

    :return:
        Number of seconds to wait, or ``None`` if the API did not provide
        usable rate-limit reset headers.
    """

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}

    retry_after = headers.get("retry-after")
    if retry_after:
        try:
            return max(1, int(retry_after))
        except ValueError:
            return None

    reset = headers.get("x-rate-limit-reset")
    if not reset:
        return None
    try:
        reset_at = datetime.datetime.fromtimestamp(int(reset), datetime.UTC).replace(tzinfo=None)
    except ValueError:
        return None

    return max(1, int((reset_at - native_datetime_utc_now()).total_seconds()) + 1)


def _x_api_read_with_retry(
    fn: Callable[[], Any],
    *,
    description: str,
    rate_limit_sleep_max_seconds: float = 1200.0,
) -> Any:
    """Call an X API read operation, retrying transient failures.

    Wraps a single X API read call (one page) with uniform handling for the two
    transient failure modes the X API exhibits, so every read path in this
    module is resilient rather than each duplicating the logic:

    - ``TwitterServerError`` (HTTP 5xx, e.g. ``503 Service Unavailable``) is
      retried with exponential backoff up to :data:`X_SERVER_ERROR_MAX_RETRIES`
      attempts before being surfaced as :class:`XApiError`.
    - ``TooManyRequests`` (HTTP 429) is retried after sleeping until the
      rate-limit reset window, as long as that wait is within
      ``rate_limit_sleep_max_seconds``; otherwise :class:`XRateLimitError`.

    Any other :class:`tweepy.TweepyException` is treated as a genuine
    client-side error (bad id, auth failure, malformed request) and raised
    immediately as :class:`XApiError` without retry.

    :param fn:
        Zero-argument callable performing exactly one X API read request.
    :param description:
        Present-participle description of the operation used in log and error
        messages, e.g. ``"fetching list timeline for list 123"``.
    :param rate_limit_sleep_max_seconds:
        Maximum automatic sleep honoured for a rate-limit reset window.
    :return:
        Whatever ``fn`` returns on its first successful call.
    :raise XApiError:
        On non-transient errors, or once 5xx retries are exhausted.
    :raise XRateLimitError:
        When a rate-limit wait exceeds ``rate_limit_sleep_max_seconds``.
    """

    server_error_retries = 0

    while True:
        try:
            return fn()
        except tweepy.TooManyRequests as e:
            wait_seconds = _get_rate_limit_sleep_seconds(e)
            if wait_seconds is None or wait_seconds > rate_limit_sleep_max_seconds:
                raise XRateLimitError(f"X API rate limit hit while {description}.{_format_rate_limit_reset(e)} Rerun later to resume.") from e
            logger.warning("X API rate limit hit while %s; sleeping %.0f seconds before retrying", description, wait_seconds)
            time.sleep(wait_seconds)
        except tweepy.TwitterServerError as e:
            # Transient X-side 5xx.  Back off and retry rather than failing the
            # whole scan cycle; an uncaught error here can crash the process and,
            # under a Docker ``restart`` policy, produce a hot restart loop that
            # hammers X while it is already failing.
            server_error_retries += 1
            if server_error_retries > X_SERVER_ERROR_MAX_RETRIES:
                raise XApiError(f"X API still failing after {X_SERVER_ERROR_MAX_RETRIES} retries while {description}: {e}") from e
            backoff = X_SERVER_ERROR_BACKOFF_BASE_SECONDS * 2 ** (server_error_retries - 1)
            logger.warning(
                "Transient X API server error (%s) while %s; retry %d/%d after %.0f seconds",
                e,
                description,
                server_error_retries,
                X_SERVER_ERROR_MAX_RETRIES,
                backoff,
            )
            time.sleep(backoff)
        except tweepy.TweepyException as e:
            raise XApiError(f"Failed while {description}: {e}") from e


def _load_known_member_ids(db: VaultPostDatabase, list_id: str) -> set[str]:
    """Load the set of member IDs we have previously added to an X list.

    :param db:
        Vault post database holding the ``feed_sync_state`` table.
    :param list_id:
        X list ID, used to scope the cache so multiple lists do not collide.
    :return:
        Set of numeric user IDs, empty when no record exists yet.
    """

    raw = db.get_sync_state(_member_ids_state_key(list_id))
    if not raw:
        return set()
    try:
        return {str(member_id) for member_id in json.loads(raw)}
    except (json.JSONDecodeError, TypeError):
        # A corrupt record must not crash the sync — start from empty and let
        # the add loop repopulate it (re-adding existing members is a no-op).
        logger.warning("Corrupt X list member ID cache in feed_sync_state for list %s; rebuilding from empty", list_id)
        return set()


def _save_known_member_ids(db: VaultPostDatabase, list_id: str, member_ids: set[str]) -> None:
    """Persist the set of member IDs we have added to an X list.

    :param db:
        Vault post database holding the ``feed_sync_state`` table.
    :param list_id:
        X list ID, used to scope the cache so multiple lists do not collide.
    :param member_ids:
        Numeric user IDs confirmed present in the list.
    """

    db.set_sync_state(_member_ids_state_key(list_id), json.dumps(sorted(member_ids)))


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
    *,
    add_delay_seconds: float = 1.0,
    rate_limit_sleep_max_seconds: float = 1200.0,
) -> int:
    """Sync X list membership with the provided Twitter handles.

    Only runs when the set of handles has changed (hash-based detection
    using ``feed_sync_state`` table).  Returns the number of members added.

    Requires full OAuth 1.0a credentials for list write operations.
    """

    current_hash = compute_handles_hash(twitter_handles)
    stored_hash = db.get_sync_state(_handles_hash_state_key(list_id))
    if stored_hash == current_hash:
        logger.info("Twitter handles unchanged (hash=%s), skipping list sync", current_hash[:12])
        return 0

    logger.info("Twitter handles changed, syncing X list %s (%d handles)", list_id, len(twitter_handles))

    # Resolve handles → user IDs
    handle_to_id = resolve_twitter_handles(twitter_handles, bearer_token, user_cache)

    # Determine which resolved members still need adding.
    #
    # The X API ``GET /2/lists/{id}/members`` endpoint returns persistent,
    # endpoint-wide ``503 Service Unavailable`` errors (it fails for unrelated
    # lists too, with a healthy rate-limit budget) and cannot be relied on to
    # read back current membership.  Instead we keep our own record of the
    # member IDs we have successfully added in ``feed_sync_state`` and diff
    # against that.  Re-adding an existing member is a harmless no-op on X's
    # side (the v2 add endpoint returns ``is_member`` without error), so a cold
    # cache simply re-adds current members once before settling into delta-only
    # syncs.  The list *timeline* endpoint used for actual post collection is
    # unaffected by the members-endpoint outage.
    known_member_ids = _load_known_member_ids(db, list_id)
    resolved_ids = set(handle_to_id.values())
    to_add = resolved_ids - known_member_ids

    if not to_add:
        logger.info("All %d resolved handles already in X list %s (per local member cache)", len(resolved_ids), list_id)
        db.set_sync_state(_handles_hash_state_key(list_id), current_hash)
        return 0

    client_write = tweepy.Client(
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
        access_token=access_token,
        access_token_secret=access_token_secret,
    )

    added = 0
    failed = 0
    id_to_handle = {user_id: handle for handle, user_id in handle_to_id.items()}

    for user_id in sorted(to_add):
        while True:
            try:
                client_write.add_list_member(list_id, user_id)
                added += 1
                # Record confirmed membership so we never re-add this member,
                # even if a later add in this batch fails (see persistence below).
                known_member_ids.add(user_id)
                logger.info(
                    "Added @%s (%s) to X list %s (%d/%d missing members)",
                    id_to_handle.get(user_id, "unknown"),
                    user_id,
                    list_id,
                    added,
                    len(to_add),
                )
                if add_delay_seconds > 0:
                    time.sleep(add_delay_seconds)
                break
            except tweepy.TooManyRequests as e:
                user_cache.save()
                wait_seconds = _get_rate_limit_sleep_seconds(e)
                if wait_seconds is None or wait_seconds > rate_limit_sleep_max_seconds:
                    message = f"X API rate limit hit while adding @{id_to_handle.get(user_id, 'unknown')} ({user_id}) to list {list_id} after {added}/{len(to_add)} missing members were added.{_format_rate_limit_reset(e)} List sync state was not updated; rerun later to resume."
                    raise XRateLimitError(message) from e

                logger.warning(
                    "X API rate limit hit while adding @%s (%s) to list %s after %d/%d missing members; sleeping %.0f seconds before retrying",
                    id_to_handle.get(user_id, "unknown"),
                    user_id,
                    list_id,
                    added,
                    len(to_add),
                    wait_seconds,
                )
                time.sleep(wait_seconds)
            except (tweepy.Unauthorized, tweepy.Forbidden) as e:
                user_cache.save()
                message = f"X API rejected list member writes while adding @{id_to_handle.get(user_id, 'unknown')} ({user_id}) to list {list_id}: {e}. Check OAuth 1.0a app permissions and access token ownership."
                raise XApiError(message) from e
            except tweepy.TweepyException as e:
                failed += 1
                logger.warning(
                    "Failed to add @%s (%s) to list %s: %s",
                    id_to_handle.get(user_id, "unknown"),
                    user_id,
                    list_id,
                    e,
                )
                if add_delay_seconds > 0:
                    time.sleep(add_delay_seconds)
                break

    # Persist confirmed membership even on partial failure so successfully
    # added members are never re-added next cycle.
    _save_known_member_ids(db, list_id, known_member_ids)

    # Only persist the hash when every add succeeded.  When some fail due to
    # transient API errors the next cycle will detect the mismatch and retry.
    if failed == 0:
        db.set_sync_state(_handles_hash_state_key(list_id), current_hash)
    else:
        logger.warning(
            "Skipping hash update — %d/%d list member adds failed, will retry next cycle",
            failed,
            len(to_add),
        )

    user_cache.save()
    logger.info(
        "Added %d members to X list %s (%d known members, %d failed)",
        added,
        list_id,
        len(known_member_ids),
        failed,
    )
    return added
