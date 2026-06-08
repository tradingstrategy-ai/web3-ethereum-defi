"""Test Core3 export helper for vault metrics JSON export."""

import datetime
from pathlib import Path

import pytest

from eth_defi.core3.database import Core3Database
from eth_defi.core3.vault_protocol import build_core3_protocols_for_export


def _make_core3_project_json(slug: str, name: str, rank: int, pol_score: float) -> dict:
    """Build a Core3 project JSON matching the full API payload shape."""
    return {
        "slug": slug,
        "name": name,
        "description": f"{name} is a DeFi protocol.",
        "rank": rank,
        "pol": {"score": pol_score, "rating": "BB", "confidence": "High"},
        "ticker": slug.upper(),
        "coingecko_id": slug,
        "logo": f"https://example.com/{slug}.png",
        "link": f"https://core3.io{slug}",
        "launched_at": None,
        "category": {"name": "Decentralized Finance"},
        "data_coverage": {"percentage": 76.7},
        "market_cap": {"in_usd": "1000000", "change_24h_percentage": -0.5, "change_24h_in_usd": "-5000"},
        "chains": [{"name": "Ethereum"}, {"name": "Base"}],
        "links": {
            "website": f"https://{slug}.org/",
            "legal": None,
            "whitepaper": None,
            "socials": [{"name": "Twitter", "link": f"https://twitter.com/{slug}"}],
        },
        "tags": [],
        "top_risks": [{"content": "Example risk finding.", "date": "2026-01-01T00:00:00.000Z"}],
        "recent_changes": [],
        "seals": {
            "security_measures": {"value": False, "logo": None},
            "independent_certificates": {"value": False, "logo": None},
            "self_regulation": {"value": False, "logo": None},
        },
    }


def test_build_core3_protocols_for_export(tmp_path: Path):
    """Core3 export helper builds a protocol-slug-keyed dict from the DB.

    1. Create a temp Core3 DuckDB with morpho and instadapp (fluid alias) snapshots
    2. Call build_core3_protocols_for_export with morpho, fluid, euler, some-random
    3. Assert morpho is returned with correct PoL score
    4. Assert fluid is keyed as "fluid" with Core3 slug "instadapp"
    5. Assert euler (no DB data) and some-random (no mapping) are absent
    6. Assert fetched_at is an ISO 8601 string, not a datetime
    """
    # 1. Create temp Core3 DB with morpho and instadapp snapshots
    db = Core3Database(tmp_path / "test-core3.duckdb")
    t_fetch = datetime.datetime(2026, 7, 1, 12, 0, 0)

    db.insert_project_snapshot("morpho", t_fetch, _make_core3_project_json("morpho", "Morpho", rank=96, pol_score=32.15))
    db.insert_project_snapshot("instadapp", t_fetch, _make_core3_project_json("instadapp", "Fluid (Instadapp)", rank=150, pol_score=45.0))

    try:
        # 2. Build export for a mix of valid, aliased, missing, and unmapped slugs
        result = build_core3_protocols_for_export(db, ["morpho", "fluid", "euler", "some-random"])
    finally:
        db.close()

    # 3. Morpho is returned with correct data
    assert "morpho" in result
    assert result["morpho"]["slug"] == "morpho"
    assert result["morpho"]["pol"]["score"] == pytest.approx(32.15)
    assert result["morpho"]["rank"] == 96

    # 4. Fluid is keyed as "fluid" but the Core3 record has slug "instadapp"
    assert "fluid" in result
    assert result["fluid"]["slug"] == "instadapp"
    assert result["fluid"]["pol"]["score"] == pytest.approx(45.0)

    # 5. Euler (no DB data) and some-random (no mapping) are absent
    assert "euler" not in result
    assert "some-random" not in result

    # 6. fetched_at is an ISO 8601 string
    assert result["morpho"]["fetched_at"] == "2026-07-01T12:00:00"
    assert isinstance(result["morpho"]["fetched_at"], str)
    assert result["fluid"]["fetched_at"] == "2026-07-01T12:00:00"
