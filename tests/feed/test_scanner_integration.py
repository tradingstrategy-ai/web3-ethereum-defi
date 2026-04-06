"""Live integration tests for the post scan pipeline via X API v2.

These tests require ``TWITTER_BEARER_TOKEN`` to be set in the environment
(loaded via ``.local-test.env``).  They make real X API calls and cost
real money (~$0.05 per test run).
"""

import json
import os
from pathlib import Path

import pytest

from eth_defi.feed.scanner import PostScanConfig, run_post_scan_cycle


pytestmark = pytest.mark.skipif(
    not os.environ.get("TWITTER_BEARER_TOKEN"),
    reason="TWITTER_BEARER_TOKEN not set — skipping live X API tests",
)


def test_scan_single_twitter_account(tmp_path: Path) -> None:
    """Scan one Twitter account via X API and verify posts are stored.

    1. Write a minimal YAML feeder for gauntlet_xyz.
    2. Call run_post_scan_cycle() with LIMIT=1 and real TWITTER_BEARER_TOKEN.
    3. Assert posts are stored in DuckDB with raw_payload as valid JSON.
    4. Assert published_at timestamps are naive UTC.
    """

    # 1. Write a minimal YAML feeder for gauntlet_xyz
    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()
    (feeds_dir / "gauntlet.yaml").write_text("feeder-id: gauntlet\nname: Gauntlet\nrole: curator\nwebsite: https://www.gauntlet.xyz/\ntwitter: gauntlet_xyz\n")

    config = PostScanConfig(
        db_path=tmp_path / "test-posts.duckdb",
        mappings_dir=feeds_dir,
        twitter_bearer_token=os.environ["TWITTER_BEARER_TOKEN"],
        twitter_user_cache_path=tmp_path / "twitter-users.json",
        limit=1,
        max_posts_per_source=10,
        request_delay_seconds=0,
        twitter_rss_base_urls=[],
    )

    # 2. Call run_post_scan_cycle()
    summary = run_post_scan_cycle(config)

    # 3. Assert posts are stored in DuckDB with raw_payload
    from eth_defi.feed.database import VaultPostDatabase

    db = VaultPostDatabase(config.db_path)
    try:
        posts_df = db.get_posts_df()
        tracked_df = db.get_tracked_sources_df()

        assert not posts_df.empty, "Expected at least one post from @gauntlet_xyz"
        assert len(posts_df) >= 1

        # Verify raw_payload is valid JSON on every post
        for _, row in posts_df.iterrows():
            if row["raw_payload"] is not None:
                payload = json.loads(row["raw_payload"])
                assert isinstance(payload, dict)
                assert "id" in payload or "text" in payload

        # 4. Assert published_at timestamps are naive UTC
        for ts in posts_df["published_at"].dropna():
            assert ts.tzinfo is None, f"Expected naive UTC timestamp, got {ts}"

        # Verify tracked source was registered
        assert len(tracked_df) == 1
        assert tracked_df.iloc[0]["feeder_id"] == "gauntlet"
        assert tracked_df.iloc[0]["source_type"] == "twitter"
    finally:
        db.close()

    # Verify user cache was populated
    assert config.twitter_user_cache_path.exists()
    with open(config.twitter_user_cache_path) as f:
        cache_data = json.load(f)
    assert "gauntlet_xyz" in cache_data
    assert "user_id" in cache_data["gauntlet_xyz"]
