"""Test curator export builder for vault metrics JSON export."""

import datetime
from pathlib import Path

from eth_defi.feed.database import CollectedPost, VaultPostDatabase
from eth_defi.feed.sources import TrackedPostSource
from eth_defi.vault.curator_export import build_curators_for_export


def test_build_curators_for_export_with_feed(tmp_path: Path):
    """Curator export builder returns metadata and recent posts from the feed DB.

    1. Create temp feed DuckDB with a tracked source for 'gauntlet'
    2. Insert sample posts under the gauntlet source
    3. Call build_curators_for_export with ['gauntlet']
    4. Assert gauntlet has metadata (name, website, twitter URL)
    5. Assert recent_posts has correct shape and published_at is ISO 8601 string
    """
    # 1. Create temp feed DuckDB
    db = VaultPostDatabase(tmp_path / "test-feed.duckdb")
    try:
        # 2. Insert tracked source for gauntlet
        source = TrackedPostSource(
            feeder_id="gauntlet",
            name="Gauntlet",
            role="curator",
            website="https://www.gauntlet.xyz",
            source_type="twitter",
            source_key="gauntlet_xyz",
            canonical_url="https://x.com/gauntlet_xyz",
            mapping_file=Path("curators/gauntlet.yaml"),
        )
        sid = db.upsert_tracked_source(source)

        base_time = datetime.datetime(2026, 6, 1, 12, 0, 0)  # noqa: DTZ001
        post_count = 5
        posts = []
        for i in range(post_count):
            posts.append(
                CollectedPost(
                    external_post_id=f"gauntlet-post-{i}",
                    title=f"Gauntlet update {i}",
                    post_url=f"https://x.com/gauntlet_xyz/status/{1000 + i}",
                    published_at=base_time + datetime.timedelta(hours=i),
                    fetched_at=base_time + datetime.timedelta(hours=i, minutes=10),
                    short_description=f"Risk analysis update {i}",
                    full_text=f"Full risk analysis {i}",
                )
            )
        db.insert_posts(sid, posts)

        # 3. Call builder
        result = build_curators_for_export(["gauntlet"], feed_db=db)
    finally:
        db.close()

    # 4. Assert gauntlet has correct metadata
    assert "gauntlet" in result
    rec = result["gauntlet"]
    assert rec["slug"] == "gauntlet"
    assert rec["name"] == "Gauntlet"
    assert rec["website"] == "https://www.gauntlet.xyz"
    assert rec["short_description"].startswith("Gauntlet provides")
    assert "risk management" in rec["long_description"]
    assert rec["twitter"] == "https://x.com/gauntlet_xyz"
    assert rec["protocol_curator"] is False
    assert rec["canonical_feeder_id"] is None

    # 5. Assert recent_posts
    assert len(rec["recent_posts"]) == post_count
    post = rec["recent_posts"][0]
    assert post["source_type"] == "twitter"
    assert post["link"].startswith("https://x.com/gauntlet_xyz/status/")
    assert post["snippet"].startswith("Risk analysis")
    assert post["full_text"].startswith("Full risk analysis")
    # published_at is ISO 8601 string
    assert isinstance(post["published_at"], str)
    assert "T" in post["published_at"]


def test_build_curators_for_export_without_feed():
    """Curator export builder works without a feed database.

    1. Call build_curators_for_export with feed_db=None for a known curator
    2. Assert metadata is present
    3. Assert recent_posts is an empty list
    """
    # 1. Call with feed_db=None
    result = build_curators_for_export(["gauntlet"], feed_db=None)

    # 2. Assert metadata present
    assert "gauntlet" in result
    rec = result["gauntlet"]
    assert rec["name"] == "Gauntlet"
    assert rec["protocol_curator"] is False

    # 3. Assert recent_posts is empty
    assert rec["recent_posts"] == []


def test_build_curators_for_export_protocol_curator_alias():
    """Protocol curators with an alias curator YAML get descriptions and inherited socials.

    Hyperliquid has a curator alias YAML (curators/hyperliquid.yaml)
    carrying market-making-vault descriptions, with website and social
    URLs inherited from the canonical protocol feeder
    (protocols/hyperliquid.yaml).

    1. Call build_curators_for_export with 'hyperliquid'
    2. Assert protocol_curator is True and canonical_feeder_id resolves
    3. Assert descriptions come from the curator alias YAML
    4. Assert website and social URLs are inherited from protocol YAML
    """
    # 1. Call builder with hyperliquid (protocol curator with alias curator YAML)
    result = build_curators_for_export(["hyperliquid"], feed_db=None)

    # 2. Assert protocol_curator and canonical feeder resolution
    assert "hyperliquid" in result
    rec = result["hyperliquid"]
    assert rec["protocol_curator"] is True
    assert rec["canonical_feeder_id"] == "hyperliquid"

    # 3. Assert descriptions from the curator alias YAML
    assert "Hyperliquidity Provider (HLP)" in rec["short_description"]
    assert "market making" in rec["long_description"]

    # 4. Assert metadata inherited from protocol YAML
    assert rec["name"] == "Hyperliquid"
    assert rec["website"] == "https://hyperliquid.xyz"
    assert rec["twitter"] == "https://x.com/HyperliquidX"
    assert rec["linkedin"] == "https://www.linkedin.com/company/hyperliquid"


def test_build_curators_for_export_frankencoin_protocol_curator_alias():
    """Frankencoin protocol curator uses its curator alias descriptions."""
    result = build_curators_for_export(["frankencoin"], feed_db=None)

    assert "frankencoin" in result
    rec = result["frankencoin"]
    assert rec["protocol_curator"] is True
    assert rec["canonical_feeder_id"] == "frankencoin"
    assert rec["short_description"] == "Frankencoin is a stablecoin maintaining 1:1 value with the Swiss franc."
    assert "savings module" in rec["long_description"]
    assert rec["website"] == "https://frankencoin.com/"
    assert rec["twitter"] == "https://x.com/frankencoinzchf"
    assert rec["linkedin"] == "https://www.linkedin.com/company/frankencoin"


def test_build_curators_for_export_d2_finance_protocol_curator():
    """D2 Finance protocol curator metadata uses its protocol feeder and logos."""

    result = build_curators_for_export(
        ["d2-finance"],
        feed_db=None,
        public_url="https://example.com",
    )

    rec = result["d2-finance"]
    assert rec["name"] == "D2 Finance"
    assert rec["website"] == "https://d2.finance/"
    assert rec["twitter"] == "https://x.com/D2_Finance"
    assert rec["linkedin"] == "https://www.linkedin.com/company/d2finance"
    assert rec["protocol_curator"] is True
    assert rec["canonical_feeder_id"] is None
    assert rec["logos"] == {
        "generic": "https://example.com/curator-metadata/d2-finance/generic.png",
        "dark": "https://example.com/curator-metadata/d2-finance/dark.png",
        "light": "https://example.com/curator-metadata/d2-finance/light.png",
    }


def test_build_curators_for_export_canonical_feeder(tmp_path: Path):
    """Alias curators fetch posts from the canonical feeder.

    1. Create temp feed DuckDB with posts stored under feeder_id 'usde'
    2. Call build_curators_for_export with ['ethena']
    3. Assert canonical_feeder_id == 'usde'
    4. Assert recent_posts contains the posts from the 'usde' feeder

    We use 'ethena' which is an alias curator pointing to canonical
    feeder 'usde' (a stablecoin feeder).
    """
    # 1. Create temp feed DuckDB with posts under 'usde'
    db = VaultPostDatabase(tmp_path / "test-feed.duckdb")
    try:
        source = TrackedPostSource(
            feeder_id="usde",
            name="Ethena USDe",
            role="stablecoin",
            website="https://ethena.fi/",
            source_type="twitter",
            source_key="ethena",
            canonical_url="https://x.com/ethena",
            mapping_file=Path("stablecoins/usde.yaml"),
        )
        sid = db.upsert_tracked_source(source)

        base_time = datetime.datetime(2026, 6, 1, 12, 0, 0)  # noqa: DTZ001
        post_count = 3
        posts = []
        for i in range(post_count):
            posts.append(
                CollectedPost(
                    external_post_id=f"usde-post-{i}",
                    title=f"Ethena update {i}",
                    post_url=f"https://x.com/ethena/status/{2000 + i}",
                    published_at=base_time + datetime.timedelta(hours=i),
                    fetched_at=base_time + datetime.timedelta(hours=i, minutes=5),
                    short_description=f"USDe news {i}",
                    full_text=f"Full USDe text {i}",
                )
            )
        db.insert_posts(sid, posts)

        # 2. Call builder with ethena (alias → usde)
        result = build_curators_for_export(["ethena"], feed_db=db)
    finally:
        db.close()

    # 3. Assert canonical_feeder_id
    assert "ethena" in result
    rec = result["ethena"]
    assert rec["canonical_feeder_id"] == "usde"

    # 4. Assert recent_posts come from usde feeder
    assert len(rec["recent_posts"]) == post_count
    assert rec["recent_posts"][0]["snippet"].startswith("USDe news")
