"""Test VaultPostDatabase.fetch_recent_posts_by_feeder() query method."""

import datetime
from pathlib import Path

from eth_defi.feed.database import CollectedPost, VaultPostDatabase
from eth_defi.feed.sources import TrackedPostSource


def test_fetch_recent_posts_by_feeder(tmp_path: Path):
    """Recent posts query returns at most max_per_feeder posts per feeder, newest first.

    1. Create a temp feed DuckDB
    2. Insert tracked sources for two feeders via upsert_tracked_source()
    3. Insert 12 posts for feeder A and 5 posts for feeder B via insert_posts()
    4. Call fetch_recent_posts_by_feeder(max_per_feeder=3)
    5. Assert feeder A returns exactly 3 posts (capped), feeder B returns 5
    6. Assert posts are ordered newest-first
    7. Assert posts without published_at use fetched_at via COALESCE
    8. Assert source_type is included from tracked_sources
    9. Assert empty feeder_ids returns {}
    """
    db = VaultPostDatabase(tmp_path / "test-feed.duckdb")
    try:
        # 2. Insert tracked sources for two feeders
        source_a = TrackedPostSource(
            feeder_id="feeder-a",
            name="Feeder A",
            role="curator",
            website="https://a.example.com",
            source_type="twitter",
            source_key="feeder_a",
            canonical_url="https://x.com/feeder_a",
            mapping_file=Path("fake-a.yaml"),
        )
        source_b = TrackedPostSource(
            feeder_id="feeder-b",
            name="Feeder B",
            role="curator",
            website="https://b.example.com",
            source_type="rss",
            source_key="https://b.example.com/feed",
            canonical_url="https://b.example.com/feed",
            mapping_file=Path("fake-b.yaml"),
        )
        sid_a = db.upsert_tracked_source(source_a)
        sid_b = db.upsert_tracked_source(source_b)

        # 3. Insert 12 posts for feeder A (2 without published_at)
        base_time = datetime.datetime(2026, 6, 1, 12, 0, 0)
        posts_a = []
        for i in range(12):
            published = base_time + datetime.timedelta(hours=i) if i >= 2 else None
            posts_a.append(
                CollectedPost(
                    external_post_id=f"a-post-{i}",
                    title=f"Post A {i}",
                    post_url=f"https://x.com/feeder_a/status/{i}",
                    published_at=published,
                    fetched_at=base_time + datetime.timedelta(hours=i, minutes=30),
                    short_description=f"Description of post A {i}",
                    full_text=f"Full text of post A {i}",
                )
            )
        db.insert_posts(sid_a, posts_a)

        # Insert 5 posts for feeder B
        posts_b = []
        for i in range(5):
            posts_b.append(
                CollectedPost(
                    external_post_id=f"b-post-{i}",
                    title=f"Post B {i}",
                    post_url=f"https://b.example.com/post/{i}",
                    published_at=base_time + datetime.timedelta(hours=i),
                    fetched_at=base_time + datetime.timedelta(hours=i, minutes=15),
                    short_description=f"Description of post B {i}",
                    full_text=f"Full text of post B {i}",
                )
            )
        db.insert_posts(sid_b, posts_b)

        # 4. Call with max_per_feeder=3
        result = db.fetch_recent_posts_by_feeder(["feeder-a", "feeder-b"], max_per_feeder=3)

        # 5. Feeder A capped at 3, feeder B returns all 5 (within limit)
        assert len(result["feeder-a"]) == 3
        # Feeder B has 5 posts but max_per_feeder=3 caps it
        assert len(result["feeder-b"]) == 3

        # 6. Posts are ordered newest-first
        timestamps_a = [p["published_at"] for p in result["feeder-a"]]
        assert timestamps_a == sorted(timestamps_a, reverse=True)

        # 7. Posts without published_at use fetched_at
        # The 2 posts without published_at (i=0, i=1) have fetched_at earlier
        # than posts with published_at, so they won't be in the top 3.
        # Let's verify with a larger limit to see the COALESCE behaviour.
        result_all = db.fetch_recent_posts_by_feeder(["feeder-a"], max_per_feeder=20)
        assert len(result_all["feeder-a"]) == 12
        # All posts should have published_at set (COALESCE fills in fetched_at)
        for post in result_all["feeder-a"]:
            assert post["published_at"] is not None

        # 8. Source type is included
        for post in result["feeder-a"]:
            assert post["source_type"] == "twitter"
        for post in result["feeder-b"]:
            assert post["source_type"] == "rss"

        # 9. Empty feeder_ids returns {}
        assert db.fetch_recent_posts_by_feeder([]) == {}

    finally:
        db.close()
