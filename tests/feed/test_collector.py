"""Integration tests for feed collection."""

from dataclasses import dataclass
from pathlib import Path

import requests

from eth_defi.feed.collector import collect_posts, collect_posts_for_source
from eth_defi.feed.database import VaultPostDatabase
from eth_defi.feed.sources import load_post_sources
from eth_defi.feed.testing import make_test_tracked_source


RSS_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Example feed</title>
    <item>
      <guid>rss-post-1</guid>
      <title>Protocol launch</title>
      <link>https://example.com/post-1</link>
      <description><![CDATA[<p>Launch body</p>]]></description>
      <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


ATOM_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom feed</title>
  <entry>
    <id>atom-post-1</id>
    <title>Atom entry</title>
    <link href="https://example.com/atom-1" />
    <updated>2024-01-02T00:00:00Z</updated>
    <content type="html"><![CDATA[<p>Atom content</p>]]></content>
  </entry>
</feed>
"""


GAUNTLET_TWITTER_LIVE_TEMPLATES = [
    "https://xcancel.com/{handle}/rss",
    "https://rss.xcancel.com/{handle}/rss",
]

GAUNTLET_LINKEDIN_LIVE_TEMPLATES = [
    "https://rsshub.pseudoyu.com/linkedin/company/{company_id}/posts",
    "https://rss.owo.nz/linkedin/company/{company_id}/posts",
    "https://rsshub.umzzz.com/linkedin/company/{company_id}/posts",
    "https://rsshub.isrss.com/linkedin/company/{company_id}/posts",
    "https://rss.datuan.dev/linkedin/company/{company_id}/posts",
    "https://rsshub.cups.moe/linkedin/company/{company_id}/posts",
]


@dataclass(slots=True)
class DummyResponse:
    """A small fake requests response for integration-style collector tests."""

    content: bytes = b""
    status_code: int = 200

    def raise_for_status(self) -> None:
        """Raise a ``requests`` error for failing status codes."""

        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def test_end_to_end_collection_with_dedup(tmp_path: Path, monkeypatch) -> None:
    """Collect mixed source types and keep inserts idempotent across repeated runs.

    1. Mock one Twitter bridge feed and one direct RSS feed.
    2. Run the collector twice against the same DuckDB database.
    3. Verify source state, feeder metadata, and post deduplication are preserved.
    """

    def fake_fetch(url: str, **kwargs) -> DummyResponse:
        if url == "https://bridge.example/exampleprotocol/rss":
            return DummyResponse(content=RSS_XML)
        if url == "https://vault.example/feed.xml":
            return DummyResponse(content=ATOM_XML)
        raise AssertionError(f"Unexpected URL {url}")

    db = VaultPostDatabase(tmp_path / "posts.duckdb")
    try:
        # 1. Mock one Twitter bridge feed and one direct RSS feed.
        monkeypatch.setattr("eth_defi.feed.collector.requests.get", fake_fetch)

        protocol_source = make_test_tracked_source(
            feeder_id="morpho",
            name="Morpho",
            role="protocol",
            source_type="twitter",
            source_key="exampleprotocol",
            canonical_url="https://x.com/exampleprotocol",
        )
        curator_source = make_test_tracked_source(
            feeder_id="gauntlet",
            name="Gauntlet",
            role="curator",
            source_type="rss",
            source_key="https://vault.example/feed.xml",
            canonical_url="https://vault.example/feed.xml",
        )

        # 2. Run the collector twice against the same DuckDB database.
        summary_first = collect_posts(
            db,
            [protocol_source, curator_source],
            request_delay_seconds=0,
            twitter_rss_base_urls=["https://bridge.example"],
        )
        summary_second = collect_posts(
            db,
            [protocol_source, curator_source],
            request_delay_seconds=0,
            twitter_rss_base_urls=["https://bridge.example"],
        )

        tracked_df = db.get_tracked_sources_df()
        posts_df = db.get_posts_df()

        # 3. Verify source state, feeder metadata, and post deduplication are preserved.
        assert summary_first.sources_succeeded == 2
        assert summary_first.posts_inserted == 2
        assert summary_second.posts_inserted == 0
        assert len(tracked_df) == 2
        assert len(posts_df) == 2
        assert set(tracked_df["feeder_id"]) == {"morpho", "gauntlet"}
        assert set(tracked_df["role"]) == {"protocol", "curator"}
        assert tracked_df["last_success_at"].notna().all()
        assert set(posts_df["title"].dropna().tolist()) == {"Protocol launch", "Atom entry"}
        assert set(posts_df["ai_summary"].tolist()) == {None}
    finally:
        db.close()


def test_live_gauntlet_collection_and_source_registration(tmp_path: Path) -> None:
    """Read the current Gauntlet feeds and store them in DuckDB.

    1. Load the real Gauntlet feeder YAML from the repository feed folder.
    2. Upsert the real Twitter, LinkedIn, and RSS source rows into DuckDB.
    3. Fetch the current live Gauntlet RSS, Twitter, and LinkedIn feeds and verify posts are stored.
    """

    db = VaultPostDatabase(tmp_path / "posts.duckdb")
    try:
        # 1. Load the real Gauntlet feeder YAML from the repository feed folder.
        data_dir = Path(__file__).resolve().parents[2] / "eth_defi" / "data" / "feeds"
        sources = [source for source in load_post_sources(data_dir) if source.feeder_id == "gauntlet"]

        # 2. Upsert the real Twitter, LinkedIn, and RSS source rows into DuckDB.
        source_ids = db.upsert_tracked_sources(sources)

        rss_source = next(source for source in sources if source.source_type == "rss")
        twitter_source = next(source for source in sources if source.source_type == "twitter")
        linkedin_source = next(source for source in sources if source.source_type == "linkedin")

        # 3. Fetch the current live Gauntlet RSS, Twitter, and LinkedIn feeds and verify posts are stored.
        rss_posts = collect_posts_for_source(
            rss_source,
            max_posts_per_source=5,
            request_timeout=20,
            twitter_rss_base_urls=[],
        )
        twitter_posts = collect_posts_for_source(
            twitter_source,
            max_posts_per_source=5,
            request_timeout=20,
            twitter_rss_base_urls=[],
            twitter_url_templates=GAUNTLET_TWITTER_LIVE_TEMPLATES,
        )
        linkedin_posts = collect_posts_for_source(
            linkedin_source,
            max_posts_per_source=5,
            request_timeout=20,
            twitter_rss_base_urls=[],
            linkedin_url_templates=GAUNTLET_LINKEDIN_LIVE_TEMPLATES,
        )

        inserted_rss = db.insert_posts(source_ids[rss_source.get_logical_key()], rss_posts)
        inserted_twitter = db.insert_posts(source_ids[twitter_source.get_logical_key()], twitter_posts)
        inserted_linkedin = db.insert_posts(source_ids[linkedin_source.get_logical_key()], linkedin_posts)

        tracked_df = db.get_tracked_sources_df()
        posts_df = db.get_posts_df()

        assert set(tracked_df["feeder_id"]) == {"gauntlet"}
        assert set(tracked_df["role"]) == {"curator"}
        assert set(tracked_df["source_type"]) == {"twitter", "linkedin", "rss"}
        assert tracked_df.loc[tracked_df["source_type"] == "twitter"].iloc[0]["canonical_url"] == "https://x.com/gauntlet_xyz"
        assert tracked_df.loc[tracked_df["source_type"] == "linkedin"].iloc[0]["canonical_url"] == "https://www.linkedin.com/company/gauntlet-xyz"
        assert tracked_df.loc[tracked_df["source_type"] == "rss"].iloc[0]["canonical_url"] == "https://medium.com/feed/gauntlet-networks"

        assert inserted_rss > 0
        assert inserted_twitter > 0
        assert inserted_linkedin > 0
        assert not posts_df.empty
        assert posts_df["title"].notna().any()
        assert posts_df["post_url"].notna().any()
        assert posts_df["full_text"].str.len().gt(0).any()
        assert posts_df.loc[posts_df["source_id"] == source_ids[rss_source.get_logical_key()]].shape[0] > 0
        assert posts_df.loc[posts_df["source_id"] == source_ids[twitter_source.get_logical_key()]].shape[0] > 0
        assert posts_df.loc[posts_df["source_id"] == source_ids[linkedin_source.get_logical_key()]].shape[0] > 0
    finally:
        db.close()
