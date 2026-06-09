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
        """Fake Tweepy client for list member reads and writes."""

        def __init__(self, **_kwargs: object):
            pass

        @staticmethod
        def get_list_members(list_id: str, max_results: int, pagination_token: str | None):
            """Return an empty list so every resolved user needs adding."""

            assert list_id == LIST_ID
            assert max_results == LIST_MEMBER_PAGE_SIZE
            assert pagination_token is None
            return SimpleNamespace(data=[], meta={})

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
        assert db.get_sync_state("twitter_handles_hash") == compute_handles_hash(["alice", "bob"])
    finally:
        db.close()


def test_sync_x_list_members_retries_on_server_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Retry transient X API 503 server errors when reading list members.

    A transient ``503 Service Unavailable`` from X used to propagate out of the
    scan cycle and crash the long-running process, producing a Docker restart
    hot-loop.  The read loop must back off and retry instead.

    1. Stub a client whose ``get_list_members`` raises 503 twice, then succeeds.
    2. Run the sync and capture the backoff sleeps.
    3. Assert the read retried and the sync completed without raising.
    """

    # 1. Stub a client whose get_list_members raises 503 twice, then succeeds
    monkeypatch.setattr(
        twitter_api,
        "resolve_twitter_handles",
        lambda _handles, _bearer_token, _user_cache: {"alice": "1"},
    )

    read_calls: list[int] = []
    sleep_calls: list[float] = []

    class FakeClient:
        """Fake Tweepy client that fails reads transiently before succeeding."""

        def __init__(self, **_kwargs: object):
            pass

        @staticmethod
        def get_list_members(list_id: str, max_results: int, pagination_token: str | None):
            """Raise 503 for the first two calls, then return the member."""

            read_calls.append(1)
            if len(read_calls) <= 2:
                raise tweepy.TwitterServerError(_ServerErrorResponse())
            return SimpleNamespace(data=[SimpleNamespace(id="1")], meta={})

        @staticmethod
        def add_list_member(list_id: str, user_id: str) -> None:
            """Member already present, so no writes should occur."""

            raise AssertionError("add_list_member should not be called")

    monkeypatch.setattr(twitter_api.tweepy, "Client", FakeClient)
    monkeypatch.setattr(twitter_api.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    db = VaultPostDatabase(tmp_path / "posts.duckdb")
    try:
        # 2. Run the sync and capture the backoff sleeps
        added = sync_x_list_members(
            LIST_ID,
            ["alice"],
            "consumer-key",
            "consumer-secret",
            "access-token",
            "access-token-secret",
            TwitterUserCache(tmp_path / "twitter-users.json"),
            "bearer-token",
            db,
            add_delay_seconds=0,
        )

        # 3. The read retried twice and the sync completed without raising
        assert added == 0
        assert len(read_calls) == 3
        assert sleep_calls == [5.0, 10.0]
        assert db.get_sync_state("twitter_handles_hash") == compute_handles_hash(["alice"])
    finally:
        db.close()
