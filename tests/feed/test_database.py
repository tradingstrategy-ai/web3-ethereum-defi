"""Integration tests for feed DuckDB storage."""

from pathlib import Path

from eth_defi.feed.database import VaultPostDatabase
from eth_defi.feed.sources import load_post_sources


def test_real_gauntlet_feeder_sources_are_stored_in_database(tmp_path: Path) -> None:
    """Store the real configured Gauntlet sources in DuckDB.

    1. Load the real Gauntlet feeder YAML from the repository feed folder.
    2. Upsert all resulting tracked sources into an empty DuckDB database.
    3. Verify the database contains valid Twitter, LinkedIn, and RSS source rows plus feeder website metadata.
    """

    db = VaultPostDatabase(tmp_path / "posts.duckdb")
    try:
        # 1. Load the real Gauntlet feeder YAML from the repository feed folder.
        data_dir = Path(__file__).resolve().parents[2] / "eth_defi" / "data" / "feeds"
        sources = [source for source in load_post_sources(data_dir) if source.feeder_id == "gauntlet"]

        # 2. Upsert all resulting tracked sources into an empty DuckDB database.
        db.upsert_tracked_sources(sources)
        tracked_df = db.get_tracked_sources_df()

        # 3. Verify the database contains valid Twitter, LinkedIn, and RSS source rows plus feeder website metadata.
        assert len(sources) == 3
        assert len(tracked_df) == 3
        assert set(tracked_df["feeder_id"]) == {"gauntlet"}
        assert set(tracked_df["role"]) == {"curator"}
        assert set(tracked_df["website"]) == {"https://www.gauntlet.xyz/"}
        assert set(tracked_df["source_type"]) == {"twitter", "linkedin", "rss"}

        twitter_row = tracked_df.loc[tracked_df["source_type"] == "twitter"].iloc[0]
        linkedin_row = tracked_df.loc[tracked_df["source_type"] == "linkedin"].iloc[0]
        rss_row = tracked_df.loc[tracked_df["source_type"] == "rss"].iloc[0]

        assert twitter_row["source_key"] == "gauntlet_xyz"
        assert twitter_row["canonical_url"] == "https://x.com/gauntlet_xyz"
        assert linkedin_row["source_key"] == "gauntlet-xyz"
        assert linkedin_row["canonical_url"] == "https://www.linkedin.com/company/gauntlet-xyz"
        assert rss_row["canonical_url"] == "https://medium.com/feed/gauntlet-networks"
    finally:
        db.close()
