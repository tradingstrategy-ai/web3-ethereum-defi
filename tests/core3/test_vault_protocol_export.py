"""Test Core3 export helper for vault metrics JSON export."""

import datetime
from pathlib import Path

import pytest

from eth_defi.core3.database import Core3Database
from eth_defi.core3.vault_protocol import build_core3_protocols_for_export, build_core3_vault_section


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
    2. Insert two days of per-category PoL points for morpho (so latest wins)
    3. Call build_core3_protocols_for_export with morpho, fluid, euler, some-random
    4. Assert morpho is returned with correct PoL score
    5. Assert fluid is keyed as "fluid" with Core3 slug "instadapp"
    6. Assert euler (no DB data) and some-random (no mapping) are absent
    7. Assert fetched_at is an ISO 8601 string, not a datetime
    8. Assert morpho carries the latest per-category sub-scores with an ISO ts
    9. Assert fluid (no category data) has pol_categories set to None
    """
    # 1. Create temp Core3 DB with morpho and instadapp snapshots
    db = Core3Database(tmp_path / "test-core3.duckdb")
    t_fetch = datetime.datetime(2026, 7, 1, 12, 0, 0)

    db.insert_project_snapshot("morpho", t_fetch, _make_core3_project_json("morpho", "Morpho", rank=96, pol_score=32.15))
    db.insert_project_snapshot("instadapp", t_fetch, _make_core3_project_json("instadapp", "Fluid (Instadapp)", rank=150, pol_score=45.0))

    # 2. Insert two daily category points; the later one must win in the export.
    # Unix timestamps: 2026-06-30 and 2026-07-01 (UTC midnight).
    db.insert_pol_category_daily_points(
        "morpho",
        [
            {
                "timestamp": 1782777600,  # 2026-06-30T00:00:00Z (stale)
                "security": {"score": 10.0},
                "financial": {"score": 20.0},
                "operational": {"score": 30.0},
                "reputational": {"score": 40.0},
                "regulatory": {"score": 50.0},
            },
            {
                "timestamp": 1782864000,  # 2026-07-01T00:00:00Z (latest)
                "security": {"score": 11.1},
                "financial": {"score": 22.2},
                "operational": {"score": 33.3},
                "reputational": {"score": 44.4},
                "regulatory": {"score": None},
            },
        ],
        t_fetch,
    )

    try:
        # 3. Build export for a mix of valid, aliased, missing, and unmapped slugs
        result = build_core3_protocols_for_export(db, ["morpho", "fluid", "euler", "some-random"])
    finally:
        db.close()

    # 4. Morpho is returned with correct data
    assert "morpho" in result
    assert result["morpho"]["slug"] == "morpho"
    assert result["morpho"]["pol"]["score"] == pytest.approx(32.15)
    assert result["morpho"]["rank"] == 96

    # 5. Fluid is keyed as "fluid" but the Core3 record has slug "instadapp"
    assert "fluid" in result
    assert result["fluid"]["slug"] == "instadapp"
    assert result["fluid"]["pol"]["score"] == pytest.approx(45.0)

    # 6. Euler (no DB data) and some-random (no mapping) are absent
    assert "euler" not in result
    assert "some-random" not in result

    # 7. fetched_at is an ISO 8601 string
    assert result["morpho"]["fetched_at"] == "2026-07-01T12:00:00"
    assert isinstance(result["morpho"]["fetched_at"], str)
    assert result["fluid"]["fetched_at"] == "2026-07-01T12:00:00"

    # 8. Morpho carries the latest per-category sub-scores with an ISO ts string
    categories = result["morpho"]["pol_categories"]
    assert categories["ts"] == "2026-07-01T00:00:00"
    assert isinstance(categories["ts"], str)
    assert categories["security"] == pytest.approx(11.1)
    assert categories["financial"] == pytest.approx(22.2)
    assert categories["operational"] == pytest.approx(33.3)
    assert categories["reputational"] == pytest.approx(44.4)
    assert categories["regulatory"] is None

    # 9. Fluid has no category rows, so pol_categories is None
    assert result["fluid"]["pol_categories"] is None


def test_build_core3_vault_section():
    """Per-vault Core3 summary flattens the headline risk fields, None when absent.

    1. Build a section from a full Core3 export record and assert each key maps correctly
    2. Assert market_cap is converted from the API string to a float
    3. Assert a None input yields a None section (protocol has no Core3 data)
    4. Assert missing nested fields degrade each key to None rather than raising
    """
    # 1. Full record maps every headline field into the flat section
    record = {
        "slug": "morpho",
        "rank": 96,
        "pol": {"score": 32.15, "rating": "BB", "confidence": "High"},
        "market_cap": {"in_usd": "1246877334", "change_24h_percentage": -0.7, "change_24h_in_usd": "-8000"},
        "data_coverage": {"percentage": 76.7},
    }
    section = build_core3_vault_section(record)
    assert section["risk_score"] == pytest.approx(32.15)
    assert section["core3_ranking"] == 96
    assert section["data_coverage"] == pytest.approx(76.7)
    assert section["confidence"] == "High"
    assert section["risk_rating_label"] == "BB"

    # 2. market_cap string is parsed to a numeric USD float
    assert section["market_cap"] == pytest.approx(1246877334.0)
    assert isinstance(section["market_cap"], float)

    # 3. None record (no Core3 data for the protocol) yields a None section
    assert build_core3_vault_section(None) is None

    # 4. Missing nested fields degrade gracefully to None, no KeyError
    sparse = build_core3_vault_section({"slug": "x"})
    assert sparse["risk_score"] is None
    assert sparse["market_cap"] is None
    assert sparse["core3_ranking"] is None
    assert sparse["data_coverage"] is None
    assert sparse["confidence"] is None
    assert sparse["risk_rating_label"] is None
