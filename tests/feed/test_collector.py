"""Integration tests for feed collection."""

from dataclasses import dataclass
from pathlib import Path

import pytest
import requests

from eth_defi.feed.collector import AllBridgesFailedError, collect_posts, collect_posts_for_source
from eth_defi.feed.database import VaultPostDatabase
from eth_defi.feed.sources import (
    FEEDS_DATA_DIR,
    auto_disable_failed_linkedin_sources,
    load_post_sources,
    mark_linkedin_source_disabled,
)
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
        """Raise a ``requests`` error for failing status codes.

        Passes ``response=self`` so that ``e.response.status_code`` is accessible
        in :class:`~eth_defi.feed.collector.AllBridgesFailedError`.
        """

        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


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
    2. Upsert available source rows into DuckDB (RSS may be dead).
    3. Fetch live Twitter and LinkedIn feeds and verify posts are stored.
    """

    db = VaultPostDatabase(tmp_path / "posts.duckdb")
    try:
        # 1. Load the real Gauntlet feeder YAML from the repository feed folder.
        data_dir = Path(__file__).resolve().parents[2] / "eth_defi" / "data" / "feeds"
        all_sources, _, _ = load_post_sources(data_dir)
        sources = [source for source in all_sources if source.feeder_id == "gauntlet"]

        # 2. Upsert available source rows into DuckDB.
        # At minimum Twitter + LinkedIn; RSS may be marked dead.
        assert len(sources) >= 2
        source_ids = db.upsert_tracked_sources(sources)

        twitter_source = next(source for source in sources if source.source_type == "twitter")
        linkedin_source = next(source for source in sources if source.source_type == "linkedin")
        rss_source = next((source for source in sources if source.source_type == "rss"), None)

        # 3. Fetch live feeds and verify posts are stored.
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

        inserted_twitter = db.insert_posts(source_ids[twitter_source.get_logical_key()], twitter_posts)
        inserted_linkedin = db.insert_posts(source_ids[linkedin_source.get_logical_key()], linkedin_posts)

        if rss_source is not None:
            rss_posts = collect_posts_for_source(
                rss_source,
                max_posts_per_source=5,
                request_timeout=20,
                twitter_rss_base_urls=[],
            )
            db.insert_posts(source_ids[rss_source.get_logical_key()], rss_posts)

        tracked_df = db.get_tracked_sources_df()
        posts_df = db.get_posts_df()

        assert set(tracked_df["feeder_id"]) == {"gauntlet"}
        assert set(tracked_df["role"]) == {"curator"}
        assert "twitter" in set(tracked_df["source_type"])
        assert "linkedin" in set(tracked_df["source_type"])
        assert tracked_df.loc[tracked_df["source_type"] == "twitter"].iloc[0]["canonical_url"] == "https://x.com/gauntlet_xyz"
        assert tracked_df.loc[tracked_df["source_type"] == "linkedin"].iloc[0]["canonical_url"] == "https://www.linkedin.com/company/gauntlet-xyz"

        assert inserted_twitter > 0
        assert inserted_linkedin > 0
        assert not posts_df.empty
        assert posts_df["title"].notna().any()
        assert posts_df["post_url"].notna().any()
        assert posts_df["full_text"].str.len().gt(0).any()
        assert posts_df.loc[posts_df["source_id"] == source_ids[twitter_source.get_logical_key()]].shape[0] > 0
        assert posts_df.loc[posts_df["source_id"] == source_ids[linkedin_source.get_logical_key()]].shape[0] > 0
    finally:
        db.close()


def test_linkedin_disabled_mapping_is_skipped(tmp_path: Path) -> None:
    """Feeder YAML with linkedin-rss-hub-disabled-at skips LinkedIn source creation.

    Verifies that loading a feeder with both linkedin and linkedin-rss-hub-disabled-at set
    produces only non-LinkedIn sources, exercising the schema field and _load_mapping_file logic.

    1. Write a minimal YAML with both linkedin and linkedin-rss-hub-disabled-at fields.
    2. Load post sources from the tmp directory.
    3. Assert only the twitter source is returned — LinkedIn source is not created.
    """

    # 1. Write a minimal YAML with both linkedin and linkedin-rss-hub-disabled-at fields.
    (tmp_path / "apostro.yaml").write_text("feeder-id: apostro\nname: Apostro\nrole: curator\nwebsite: https://apostro.xyz\ntwitter: apostroxyz\nlinkedin: apostro\nlinkedin-rss-hub-disabled-at: 2026-04-04\n")

    # 2. Load post sources from the tmp directory.
    sources, _, _ = load_post_sources(tmp_path)

    # 3. Assert only the twitter source is returned — LinkedIn source is not created.
    assert len(sources) == 1
    assert sources[0].source_type == "twitter"
    assert sources[0].feeder_id == "apostro"


def test_live_apostro_linkedin_auth_blocked_and_yaml_auto_disabled(tmp_path: Path) -> None:
    """Real Apostro LinkedIn bridges must fail with auth_blocked and auto-disable the YAML.

    Apostro is a small company whose LinkedIn page requires authentication for unauthenticated
    scrapers, so all public RSSHub bridges return 5xx errors.  This test verifies the full
    detection and auto-disable pipeline end-to-end with real network calls.

    We use a copy of apostro.yaml in tmp_path so the live repo YAML is not modified.

    1. Copy the real apostro.yaml to tmp_path and load its LinkedIn source.
    2. Collect posts — all bridges fail; verify AllBridgesFailedError with indicates_auth_block.
    3. Run auto_disable_failed_linkedin_sources on the tmp copy and assert the date is appended.
    4. Reload sources from tmp_path and verify LinkedIn source is now absent.
    """
    tmp_yaml = tmp_path / "apostro.yaml"

    # 1. Write a fresh apostro YAML without linkedin-rss-hub-disabled-at so the LinkedIn source is active.
    tmp_yaml.write_text("feeder-id: apostro\nname: Apostro\nrole: curator\nwebsite: https://apostro.xyz\ntwitter: apostroxyz\nlinkedin: apostro\n")
    sources, _, _ = load_post_sources(tmp_path)
    linkedin_sources = [s for s in sources if s.source_type == "linkedin"]
    assert linkedin_sources, "apostro.yaml must have a linkedin entry without a disabled date"
    linkedin_source = linkedin_sources[0]
    # Patch mapping_file so auto-disable writes to the tmp copy, not the real file.
    from dataclasses import replace as dc_replace

    linkedin_source = dc_replace(linkedin_source, mapping_file=tmp_yaml)

    # 2. Collect posts — all bridges fail; verify AllBridgesFailedError with indicates_auth_block.
    from eth_defi.feed.collector import CollectedSourceResult, CollectorRunSummary

    with pytest.raises(AllBridgesFailedError) as exc_info:
        collect_posts_for_source(
            linkedin_source,
            max_posts_per_source=5,
            request_timeout=20,
            twitter_rss_base_urls=[],
            linkedin_url_templates=GAUNTLET_LINKEDIN_LIVE_TEMPLATES,
        )
    err = exc_info.value
    assert err.indicates_auth_block, f"Expected auth block for apostro, got: {err.bridge_errors}"

    # 3. Run auto_disable_failed_linkedin_sources on the tmp copy and assert the date is appended.
    failed_result = CollectedSourceResult(
        feeder_id=linkedin_source.feeder_id,
        name=linkedin_source.name,
        role=linkedin_source.role,
        source_type="linkedin",
        status="failed",
        error=str(err),
        auth_blocked=err.indicates_auth_block,
    )
    summary = CollectorRunSummary(sources_loaded=1, sources_failed=1, source_results=[failed_result])
    disabled_count = auto_disable_failed_linkedin_sources(summary, [linkedin_source], "2026-04-04")
    assert disabled_count == 1
    assert "linkedin-rss-hub-disabled-at: 2026-04-04" in tmp_yaml.read_text()

    # 4. Reload sources from tmp_path and verify LinkedIn source is now absent.
    reloaded, _, _ = load_post_sources(tmp_path)
    assert not any(s.source_type == "linkedin" for s in reloaded)
