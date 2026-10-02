"""Minimal real authenticated provider checks, without public tweet writes."""

import os

import pytest

from eth_defi.vault_report.ghost import GhostAdminClient
from eth_defi.vault_report.tweet_plan import extract_charts, fetch_image
from eth_defi.vault_report.twitter import TwitterWriter


@pytest.mark.skipif(not all(os.environ.get(name) for name in ("TWITTER_CONSUMER_KEY", "TWITTER_SECRET_KEY", "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_TOKEN_SECRET")), reason="X OAuth user credentials required")
def test_live_publishing_identity() -> None:
    """Verify a real successful authenticated X response for the publishing user.

    This read-only test exercises actual OAuth signing and provider parsing;
    no media or tweet is created. Credentials are never included in output.
    """
    user = TwitterWriter.from_env().fetch_identity()
    assert user["username"].lower() == "tradingprotocol"
    assert user["id"].isdigit()


@pytest.mark.skipif(not os.environ.get("GHOST_ADMIN_API_KEY"), reason="Ghost Admin credentials required")
def test_live_ghost_chart_readback() -> None:
    """Read an edited published report and its actual served chart end-to-end.

    The October report verifies editorial image enumeration and authenticated
    Ghost access without overwriting the post or reading fresh vault metrics.
    """
    client = GhostAdminClient(os.environ.get("GHOST_ADMIN_API_URL") or os.environ["GHOST_CONTENT_API_URL"], os.environ["GHOST_ADMIN_API_KEY"])
    post = client.fetch_post_by_slug("the-best-performing-stablecoin-vaults-october-2026")
    assert post and post.status == "published"
    charts, _ = extract_charts(post.html)
    assert any(c["key"] == "lending_performance" for c in charts)
    assert fetch_image(charts[0]["url"]).startswith(b"\x89PNG")
