from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
import tweepy

from eth_defi.feed import twitter_api
from eth_defi.feed.database import VaultPostDatabase
from eth_defi.feed.twitter_api import TwitterUserCache, XRateLimitError, sync_x_list_members

LIST_ID = "123"
LIST_MEMBER_PAGE_SIZE = 100


class _RateLimitedResponse:
    """Minimal response object for Tweepy rate-limit exceptions."""

    headers: ClassVar[dict[str, str]] = {}
    reason = "Too Many Requests"
    status_code = 429
    text = "Too Many Requests"

    @staticmethod
    def json() -> dict[str, str]:
        """Return a minimal X API error payload."""

        return {"title": "Too Many Requests"}


def test_sync_x_list_members_stops_on_rate_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Do not continue list writes after X returns a rate-limit error.

    A partial list sync must leave ``twitter_handles_hash`` untouched so that
    the next operator run retries after reading the already-added list members.
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
            """Rate-limit the first write call."""

            assert list_id == LIST_ID
            add_calls.append(user_id)
            raise tweepy.TooManyRequests(_RateLimitedResponse())

    monkeypatch.setattr(twitter_api.tweepy, "Client", FakeClient)

    db = VaultPostDatabase(tmp_path / "posts.duckdb")
    try:
        with pytest.raises(XRateLimitError, match="List sync state was not updated"):
            sync_x_list_members(
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

        assert add_calls == ["1"]
        assert db.get_sync_state("twitter_handles_hash") is None
    finally:
        db.close()
