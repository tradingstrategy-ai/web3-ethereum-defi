from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
import tweepy

from eth_defi.feed import twitter_api
from eth_defi.feed.database import VaultPostDatabase
from eth_defi.feed.twitter_api import (
    TwitterUserCache,
    _tweet_to_collected_post,
    compute_handles_hash,
    sync_x_list_members,
)

LIST_ID = "123"
LIST_MEMBER_PAGE_SIZE = 100


def test_tweet_to_collected_post_keeps_full_note_tweet() -> None:
    """Store the full note tweet body, not the 280-char truncated preview.

    The X API v2 caps the ``text`` field at 280 characters for long tweets and
    returns the complete body in ``note_tweet`` only when that field is
    requested.  We must keep the full text so downstream exports are not cut.

    1. Build a tweet whose ``text`` is the truncated 280-char preview and whose
       ``note_tweet.text`` carries the full long body.
    2. Map it to a :class:`CollectedPost`.
    3. Assert ``full_text`` holds the complete note tweet text.
    4. Assert ``short_description`` is the 200-char preview.
    """

    # 1. Build a tweet with both the truncated text and the full note_tweet body
    full_body = "A " + "long " * 200 + "tail."
    tweet_data = {
        "id": "999",
        "text": full_body[:277] + "…",
        "note_tweet": {"text": full_body},
        "created_at": "2026-06-09T12:00:00.000Z",
    }

    # 2. Map it to a CollectedPost
    post = _tweet_to_collected_post(tweet_data, "hyperliquidx")

    # 3. full_text holds the complete note tweet text (whitespace-normalised)
    assert post.full_text == twitter_api._normalise_whitespace(full_body)
    assert len(post.full_text) > 280

    # 4. short_description is the 200-char preview
    assert len(post.short_description) == 200
    assert post.full_text.startswith(post.short_description)


def test_tweet_to_collected_post_falls_back_to_text() -> None:
    """Use the legacy ``text`` field for short tweets without a note tweet.

    Most tweets are under 280 characters and carry no ``note_tweet`` object, so
    the mapper must fall back to ``text`` without error.

    1. Build a short tweet with only a ``text`` field.
    2. Map it to a :class:`CollectedPost`.
    3. Assert ``full_text`` equals the normalised ``text``.
    """

    # 1. Build a short tweet with only a text field
    tweet_data = {"id": "1", "text": "gm", "created_at": "2026-06-09T12:00:00.000Z"}

    # 2. Map it to a CollectedPost
    post = _tweet_to_collected_post(tweet_data, "someone")

    # 3. full_text equals the normalised text
    assert post.full_text == "gm"


class _RateLimitedResponse:
    """Minimal response object for Tweepy rate-limit exceptions."""

    headers: ClassVar[dict[str, str]] = {"retry-after": "2"}
    reason = "Too Many Requests"
    status_code = 429
    text = "Too Many Requests"

    @staticmethod
    def json() -> dict[str, str]:
        """Return a minimal X API error payload."""

        return {"title": "Too Many Requests"}


class _ServerErrorResponse:
    """Minimal response object for Tweepy 503 server errors."""

    headers: ClassVar[dict[str, str]] = {}
    reason = "Service Unavailable"
    status_code = 503
    text = "Service Unavailable"

    @staticmethod
    def json() -> dict[str, str]:
        """Return a minimal X API error payload."""

        return {"title": "Service Unavailable"}


def test_sync_x_list_members_waits_on_rate_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Wait for X rate-limit reset and retry the same list member.

    A recoverable rate-limit response should not force the operator to manually
    rerun the sync command when X provides a retry delay.
    """

    monkeypatch.setattr(
        twitter_api,
        "resolve_twitter_handles",
        lambda _handles, _bearer_token, _user_cache: {
            "alice": "1",
            "bob": "2",
        },
    )

    add_calls: list[str] = []
    sleep_calls: list[float] = []

    class FakeClient:
        """Fake Tweepy client for list member writes.

        The local member cache starts empty, so both resolved handles need
        adding and the broken ``get_list_members`` endpoint is never touched.
        """

        def __init__(self, **_kwargs: object):
            pass

        @staticmethod
        def add_list_member(list_id: str, user_id: str) -> None:
            """Rate-limit the first write call, then allow retries."""

            assert list_id == LIST_ID
            add_calls.append(user_id)
            if add_calls == ["1"]:
                raise tweepy.TooManyRequests(_RateLimitedResponse())

    monkeypatch.setattr(twitter_api.tweepy, "Client", FakeClient)
    monkeypatch.setattr(twitter_api.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    db = VaultPostDatabase(tmp_path / "posts.duckdb")
    try:
        added = sync_x_list_members(
            LIST_ID,
            ["alice", "bob"],
            "consumer-key",
            "consumer-secret",
            "access-token",
            "access-token-secret",
            TwitterUserCache(tmp_path / "twitter-users.json"),
            "bearer-token",
            db,
            add_delay_seconds=0,
        )

        assert added == 2
        assert add_calls == ["1", "1", "2"]
        assert sleep_calls == [2]
        assert db.get_sync_state(twitter_api._handles_hash_state_key(LIST_ID)) == compute_handles_hash(["alice", "bob"])
    finally:
        db.close()


def test_x_api_read_with_retry_retries_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry transient X API 5xx errors with exponential backoff, then succeed.

    The shared read wrapper protects every X read path (handle resolution, list
    timeline, user timeline) from transient ``503 Service Unavailable`` blips
    that would otherwise abort a scan cycle.

    1. Build a callable that raises 503 twice, then returns a value.
    2. Call the retry wrapper, capturing the backoff sleeps.
    3. Assert it returned the value after exactly two backoff sleeps.
    """

    sleep_calls: list[float] = []
    monkeypatch.setattr(twitter_api.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    # 1. Build a callable that raises 503 twice, then returns a value
    calls: list[int] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) <= 2:
            raise tweepy.TwitterServerError(_ServerErrorResponse())
        return "ok"

    # 2. Call the retry wrapper, capturing the backoff sleeps
    result = twitter_api._x_api_read_with_retry(flaky, description="testing the retry wrapper")

    # 3. It returned after exactly two backoff sleeps
    assert result == "ok"
    assert len(calls) == 3
    assert sleep_calls == [5.0, 10.0]


def test_x_api_read_with_retry_raises_after_exhausting_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Surface an XApiError once 5xx retries are exhausted.

    A persistently failing endpoint (like ``GET /2/lists/{id}/members``) must
    eventually raise rather than retry forever.

    1. Build a callable that always raises 503.
    2. Call the retry wrapper.
    3. Assert it raises XApiError after the configured maximum retries.
    """

    monkeypatch.setattr(twitter_api.time, "sleep", lambda _seconds: None)

    # 1. Build a callable that always raises 503
    def always_503() -> str:
        raise tweepy.TwitterServerError(_ServerErrorResponse())

    # 2. + 3. The wrapper gives up with XApiError after the retry budget
    with pytest.raises(twitter_api.XApiError):
        twitter_api._x_api_read_with_retry(always_503, description="hitting a dead endpoint")


def test_sync_x_list_members_uses_local_member_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sync membership from a local cache instead of the broken members endpoint.

    Because ``GET /2/lists/{id}/members`` returns persistent 503s, the sync must
    never call it; it tracks added member IDs locally and only writes the delta
    when handles change.

    1. Stub resolution and a client that fails if the members endpoint is read.
    2. Run sync, then add a third handle and run sync again.
    3. Assert the first run adds all members and the second adds only the delta.
    """

    # 1. Stub resolution (mutable so the second run resolves a new handle) and a
    #    client whose read endpoint must never be called.
    handle_map = {"alice": "1", "bob": "2"}
    monkeypatch.setattr(
        twitter_api,
        "resolve_twitter_handles",
        lambda handles, _bearer_token, _user_cache: {h: handle_map[h] for h in handles},
    )

    add_calls: list[str] = []

    class FakeClient:
        """Fake Tweepy client that records writes and forbids member reads."""

        def __init__(self, **_kwargs: object):
            pass

        @staticmethod
        def get_list_members(*_args: object, **_kwargs: object):
            """The members endpoint is broken and must never be called."""

            raise AssertionError("get_list_members must not be called")

        @staticmethod
        def add_list_member(list_id: str, user_id: str) -> None:
            """Record each successful add."""

            assert list_id == LIST_ID
            add_calls.append(user_id)

    monkeypatch.setattr(twitter_api.tweepy, "Client", FakeClient)
    monkeypatch.setattr(twitter_api.time, "sleep", lambda _seconds: None)

    common_args = (
        "consumer-key",
        "consumer-secret",
        "access-token",
        "access-token-secret",
        TwitterUserCache(tmp_path / "twitter-users.json"),
        "bearer-token",
    )

    db = VaultPostDatabase(tmp_path / "posts.duckdb")
    try:
        # 2. First run adds both members from an empty cache
        added_first = sync_x_list_members(LIST_ID, ["alice", "bob"], *common_args, db, add_delay_seconds=0)

        # 2. Second run after a new handle is introduced
        handle_map["carol"] = "3"
        add_calls.clear()
        added_second = sync_x_list_members(LIST_ID, ["alice", "bob", "carol"], *common_args, db, add_delay_seconds=0)

        # 3. First run added all, second run added only the delta
        assert added_first == 2
        assert added_second == 1
        assert add_calls == ["3"]
        assert db.get_sync_state(twitter_api._member_ids_state_key(LIST_ID)) is not None
    finally:
        db.close()


def test_sync_x_list_members_caches_are_scoped_per_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep member caches isolated per X list within one database.

    Two different X lists synced against the same database must not share a
    member cache: members of one list must never be treated as already present
    in another, or handles would silently be left missing from the second list.

    1. Stub a client that records (list_id, user_id) writes.
    2. Sync ``alice,bob`` into one list, then ``alice,bob,carol`` into another.
    3. Assert the second list receives all three members, not just the delta.
    """

    # 1. Stub resolution and a client recording which list each add targets
    handle_map = {"alice": "1", "bob": "2", "carol": "3"}
    monkeypatch.setattr(
        twitter_api,
        "resolve_twitter_handles",
        lambda handles, _bearer_token, _user_cache: {h: handle_map[h] for h in handles},
    )

    add_calls: list[tuple[str, str]] = []

    class FakeClient:
        """Fake Tweepy client recording the list each member is added to."""

        def __init__(self, **_kwargs: object):
            pass

        @staticmethod
        def add_list_member(list_id: str, user_id: str) -> None:
            """Record the (list, member) pair."""

            add_calls.append((list_id, user_id))

    monkeypatch.setattr(twitter_api.tweepy, "Client", FakeClient)
    monkeypatch.setattr(twitter_api.time, "sleep", lambda _seconds: None)

    common_args = (
        "consumer-key",
        "consumer-secret",
        "access-token",
        "access-token-secret",
        TwitterUserCache(tmp_path / "twitter-users.json"),
        "bearer-token",
    )

    db = VaultPostDatabase(tmp_path / "posts.duckdb")
    try:
        # 2. Sync alice,bob into list A, then alice,bob,carol into list B
        sync_x_list_members("list-A", ["alice", "bob"], *common_args, db, add_delay_seconds=0)
        sync_x_list_members("list-B", ["alice", "bob", "carol"], *common_args, db, add_delay_seconds=0)

        # 3. List B must get all three members despite list A caching alice,bob
        list_b_added = sorted(user_id for list_id, user_id in add_calls if list_id == "list-B")
        assert list_b_added == ["1", "2", "3"]
    finally:
        db.close()
