"""Offline tests for Core3Database — no API key required.

Verifies DuckDB insert, deduplication, sync state, and query methods
using synthetic data so these tests always run in CI.
"""

import datetime
import json
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from eth_defi.core3.constants import INDEX_SLUG
from eth_defi.core3.database import CORE3_PAYLOAD_COMPRESSION_QUERIES, Core3Database
from eth_defi.core3.vault_protocol import get_core3_protocol_record


@pytest.fixture()
def db(tmp_path: Path):
    """Create a temporary Core3Database, closed after test."""
    database = Core3Database(tmp_path / "test.duckdb")
    yield database
    database.close()


def _make_project_json(slug: str, rank: int, pol_score: float, market_cap: str | None = None) -> dict:
    """Build a minimal project detail JSON matching the API shape."""
    result = {
        "slug": slug,
        "name": slug.replace("-", " ").title(),
        "rank": rank,
        "pol": {"score": pol_score, "rating": "BBB"},
    }
    if market_cap is not None:
        result["market_cap"] = {"in_usd": market_cap}
    return result


def test_migrate_legacy_database_to_latest_storage_with_zstd_payloads(tmp_path: Path):
    """Migrate all legacy Core3 rows and native-compress their raw JSON.

    The old default DuckDB storage version cannot use Zstandard compression for
    ``VARCHAR`` columns. Opening this fixture through :class:`Core3Database`
    must atomically rebuild it in the latest format without losing snapshots,
    time-series rows or sync watermarks.

    :param tmp_path:
        Pytest-provided directory for the legacy and migrated DuckDB files.
    """
    database_path = tmp_path / "legacy-core3.duckdb"
    fetched_at = datetime.datetime(2026, 7, 23, 12, 0, 0)
    project_payload = json.dumps(
        {
            "slug": "aave",
            "description": "Core3 historical payload. " * 1_000,
            "pol": {"score": 12.5, "rating": "A"},
        }
    )
    section_payload = json.dumps({"audit": "Passed. " * 1_000})

    legacy = duckdb.connect(str(database_path))
    try:
        legacy.execute("""
            CREATE TABLE project_snapshots (
                slug VARCHAR NOT NULL,
                fetched_at TIMESTAMP NOT NULL,
                name VARCHAR,
                rank INTEGER,
                pol_score DOUBLE,
                pol_rating VARCHAR,
                market_cap_usd VARCHAR,
                payload VARCHAR NOT NULL
            )
        """)
        legacy.execute("""
            CREATE TABLE section_snapshots (
                slug VARCHAR NOT NULL,
                section VARCHAR NOT NULL,
                fetched_at TIMESTAMP NOT NULL,
                section_pol_score DOUBLE,
                payload VARCHAR NOT NULL
            )
        """)
        legacy.execute("""
            CREATE TABLE pol_daily (
                slug VARCHAR NOT NULL,
                ts TIMESTAMP NOT NULL,
                pol_score DOUBLE NOT NULL,
                fetched_at TIMESTAMP NOT NULL
            )
        """)
        legacy.execute("""
            CREATE TABLE pol_category_daily (
                slug VARCHAR NOT NULL,
                ts TIMESTAMP NOT NULL,
                security_score DOUBLE,
                financial_score DOUBLE,
                operational_score DOUBLE,
                reputational_score DOUBLE,
                regulatory_score DOUBLE,
                fetched_at TIMESTAMP NOT NULL
            )
        """)
        legacy.execute("""
            CREATE TABLE sync_state (
                slug VARCHAR NOT NULL,
                data_type VARCHAR NOT NULL,
                last_ts BIGINT,
                backfill_done BOOLEAN NOT NULL DEFAULT FALSE,
                last_synced TIMESTAMP NOT NULL
            )
        """)
        legacy.execute(
            "INSERT INTO project_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ["aave", fetched_at, "Aave", 1, 12.5, "A", "1000000", project_payload],
        )
        legacy.execute(
            "INSERT INTO section_snapshots VALUES (?, ?, ?, ?, ?)",
            ["aave", "security", fetched_at, 10.0, section_payload],
        )
        legacy.execute(
            "INSERT INTO pol_daily VALUES (?, ?, ?, ?)",
            ["aave", fetched_at, 12.5, fetched_at],
        )
        legacy.execute(
            "INSERT INTO pol_category_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ["aave", fetched_at, 10.0, 11.0, 12.0, 13.0, 14.0, fetched_at],
        )
        legacy.execute(
            "INSERT INTO sync_state VALUES (?, ?, ?, ?, ?)",
            ["aave", "pol_daily", 1_753_267_200, True, fetched_at],
        )
        legacy.execute("CHECKPOINT")
    finally:
        legacy.close()

    database = Core3Database(database_path)
    try:
        storage_version = database.con.execute("SELECT tags['storage_version'] FROM duckdb_databases() WHERE database_name = current_database()").fetchone()[0]
        assert database._uses_latest_storage_format(storage_version)
        assert database.get_project_count() == 1
        assert database.get_snapshot_count() == 1
        assert database.get_pol_daily_count() == 1

        snapshot_payload = database.con.execute("SELECT payload FROM project_snapshots").fetchone()[0]
        assert snapshot_payload == project_payload
        assert database.con.execute("SELECT COUNT(*) FROM section_snapshots").fetchone()[0] == 1
        assert database.con.execute("SELECT COUNT(*) FROM pol_category_daily").fetchone()[0] == 1
        assert database.con.execute("SELECT COUNT(*) FROM sync_state").fetchone()[0] == 1

        for table_name in ("project_snapshots", "section_snapshots"):
            compression = database.con.execute(CORE3_PAYLOAD_COMPRESSION_QUERIES[table_name]).fetchone()[0]
            assert compression == "ZSTD"
    finally:
        database.close()


def test_insert_and_query_project_snapshots(db: Core3Database):
    """Insert project snapshots and verify query methods return correct data.

    1. Insert two project snapshots with different fetched_at timestamps
    2. Verify get_project_count returns unique slug count
    3. Verify get_snapshot_count returns total row count
    4. Verify get_latest_project_snapshots returns only the most recent per slug
    5. Verify get_project_snapshot_history returns all snapshots for a slug
    """
    t1 = datetime.datetime(2025, 1, 1, 12, 0, 0)
    t2 = datetime.datetime(2025, 1, 2, 12, 0, 0)

    raw_aave = _make_project_json("aave", rank=5, pol_score=15.5, market_cap="1000000")
    raw_uniswap = _make_project_json("uniswap", rank=10, pol_score=20.0)

    # 1. Insert at two timestamps
    db.insert_project_snapshot("aave", t1, raw_aave)
    db.insert_project_snapshot("uniswap", t1, raw_uniswap)
    db.insert_project_snapshot("aave", t2, raw_aave)

    # 2. Unique project count
    assert db.get_project_count() == 2

    # 3. Total snapshot rows
    assert db.get_snapshot_count() == 3

    # 4. Latest snapshots — one per slug
    df_latest = db.get_latest_project_snapshots()
    assert len(df_latest) == 2
    aave_row = df_latest[df_latest["slug"] == "aave"].iloc[0]
    assert aave_row["rank"] == 5
    assert aave_row["pol_score"] == pytest.approx(15.5)
    assert aave_row["market_cap_usd"] == "1000000"

    # uniswap has no market cap
    uni_row = df_latest[df_latest["slug"] == "uniswap"].iloc[0]
    assert uni_row["market_cap_usd"] is None or pd.isna(uni_row["market_cap_usd"])

    # 5. History for aave
    df_history = db.get_project_snapshot_history("aave")
    assert len(df_history) == 2

    # Payload is valid JSON
    payload = json.loads(aave_row["payload"])
    assert payload["slug"] == "aave"


def test_snapshot_upsert_on_conflict(db: Core3Database):
    """Inserting a snapshot with the same (slug, fetched_at) updates existing row.

    1. Insert a snapshot
    2. Insert again with same key but different rank
    3. Verify the row was updated (not duplicated)
    """
    t1 = datetime.datetime(2025, 1, 1, 12, 0, 0)

    # 1. Initial insert
    db.insert_project_snapshot("aave", t1, _make_project_json("aave", rank=5, pol_score=15.0))

    # 2. Upsert with updated rank
    db.insert_project_snapshot("aave", t1, _make_project_json("aave", rank=3, pol_score=12.0))

    # 3. Still one row, with updated values
    assert db.get_snapshot_count() == 1
    df = db.get_latest_project_snapshots()
    assert df.iloc[0]["rank"] == 3
    assert df.iloc[0]["pol_score"] == pytest.approx(12.0)


def test_pol_daily_insert_and_dedup(db: Core3Database):
    """Insert PoL daily points and verify ON CONFLICT DO NOTHING deduplication.

    1. Insert 3 points for a project
    2. Insert overlapping points (2 old + 1 new)
    3. Verify only the new point was added (total 4, not 6)
    4. Verify scores are in expected range
    """
    t_fetch = datetime.datetime(2025, 6, 1, 0, 0, 0)

    # 1. Initial 3 points
    points_1 = [
        {"score": 10.0, "timestamp": 1700000000},
        {"score": 11.0, "timestamp": 1700086400},
        {"score": 12.0, "timestamp": 1700172800},
    ]
    new_count = db.insert_pol_daily_points("aave", points_1, t_fetch)
    assert new_count == 3

    # 2. Overlapping insert (2 existing + 1 new)
    points_2 = [
        {"score": 10.0, "timestamp": 1700000000},
        {"score": 11.0, "timestamp": 1700086400},
        {"score": 13.0, "timestamp": 1700259200},
    ]
    new_count = db.insert_pol_daily_points("aave", points_2, t_fetch)
    assert new_count == 1

    # 3. Total is 4
    assert db.get_pol_daily_count() == 4

    # 4. Query and verify
    df = db.get_pol_daily("aave")
    assert len(df) == 4
    assert df["pol_score"].min() == pytest.approx(10.0)
    assert df["pol_score"].max() == pytest.approx(13.0)


def test_pol_daily_empty_points(db: Core3Database):
    """Inserting an empty points list returns 0 and does not error.

    1. Insert empty list
    2. Verify return value is 0 and table is empty
    """
    t_fetch = datetime.datetime(2025, 6, 1, 0, 0, 0)
    assert db.insert_pol_daily_points("aave", [], t_fetch) == 0
    assert db.get_pol_daily_count() == 0


def test_pol_category_daily_insert(db: Core3Database):
    """Insert category PoL daily points and verify column extraction.

    1. Insert points with category scores
    2. Verify all category columns are populated
    3. Verify deduplication on second insert
    """
    t_fetch = datetime.datetime(2025, 6, 1, 0, 0, 0)

    points = [
        {
            "timestamp": 1700000000,
            "security": {"score": 5.0},
            "financial": {"score": 10.0},
            "operational": {"score": 15.0},
            "reputational": {"score": 20.0},
            "regulatory": {"score": 25.0},
        },
        {
            "timestamp": 1700086400,
            "security": {"score": 6.0},
            "financial": None,
            "operational": {"score": 16.0},
        },
    ]

    # 1. Insert
    new_count = db.insert_pol_category_daily_points("aave", points, t_fetch)
    assert new_count == 2

    # 2. Verify columns
    df = db.get_pol_category_daily("aave")
    assert len(df) == 2
    row0 = df.iloc[0]
    assert row0["security_score"] == pytest.approx(5.0)
    assert row0["regulatory_score"] == pytest.approx(25.0)

    # Second row has None for financial (API returned null)
    row1 = df.iloc[1]
    assert row1["financial_score"] is None or pd.isna(row1["financial_score"])

    # 3. Dedup
    new_count = db.insert_pol_category_daily_points("aave", points, t_fetch)
    assert new_count == 0


def test_sync_state_lifecycle(db: Core3Database):
    """Verify sync state create, read, update cycle.

    1. Initially no state exists
    2. Update sync state with backfill_done=True
    3. Read back and verify fields
    4. Update with new last_ts
    5. Verify updated values
    """
    # 1. No state
    assert db.get_sync_state("aave", "pol_daily") is None

    # 2. Create state
    db.update_sync_state("aave", "pol_daily", last_ts=1700000000, backfill_done=True)

    # 3. Read back
    state = db.get_sync_state("aave", "pol_daily")
    assert state is not None
    assert state["last_ts"] == 1700000000
    assert state["backfill_done"] is True
    assert state["last_synced"] is not None

    # 4. Update
    db.update_sync_state("aave", "pol_daily", last_ts=1700172800, backfill_done=True)

    # 5. Verify update
    state = db.get_sync_state("aave", "pol_daily")
    assert state["last_ts"] == 1700172800


def test_sync_state_null_last_ts(db: Core3Database):
    """Sync state with last_ts=None represents a backfilled project with no data.

    1. Set backfill_done=True with last_ts=None
    2. Verify state distinguishes from never-synced (None return)
    """
    # 1. Backfill done, but no data
    db.update_sync_state("empty-project", "pol_daily", last_ts=None, backfill_done=True)

    # 2. State exists (not None) but last_ts is None
    state = db.get_sync_state("empty-project", "pol_daily")
    assert state is not None
    assert state["last_ts"] is None
    assert state["backfill_done"] is True


def test_section_snapshot_insert(db: Core3Database):
    """Insert a section snapshot and verify extraction.

    1. Insert a security section snapshot
    2. Verify section_pol_score is extracted
    3. Verify payload is stored
    """
    t_fetch = datetime.datetime(2025, 6, 1, 0, 0, 0)
    raw = {"pol": {"score": 8.5}, "details": [{"name": "audit", "status": "passed"}]}

    # 1. Insert
    db.insert_section_snapshot("aave", "security", t_fetch, raw)

    # 2-3. Query via raw SQL (no dedicated query method for sections)
    with db._db_lock:
        row = db.con.execute(
            "SELECT section_pol_score, payload FROM section_snapshots WHERE slug = ? AND section = ?",
            ["aave", "security"],
        ).fetchone()

    assert row[0] == pytest.approx(8.5)
    payload = json.loads(row[1])
    assert payload["details"][0]["name"] == "audit"


def test_index_slug_isolation(db: Core3Database):
    """Index-level PoL rows use INDEX_SLUG and are isolated from project rows.

    1. Insert points for a project and for the index
    2. Verify get_pol_daily returns only the requested slug's rows
    """
    t_fetch = datetime.datetime(2025, 6, 1, 0, 0, 0)

    project_points = [{"score": 20.0, "timestamp": 1700000000}]
    index_points = [{"score": 50.0, "timestamp": 1700000000}]

    db.insert_pol_daily_points("aave", project_points, t_fetch)
    db.insert_pol_daily_points(INDEX_SLUG, index_points, t_fetch)

    # Total is 2
    assert db.get_pol_daily_count() == 2

    # Each query returns only its own rows
    df_aave = db.get_pol_daily("aave")
    assert len(df_aave) == 1
    assert df_aave.iloc[0]["pol_score"] == pytest.approx(20.0)

    df_index = db.get_pol_daily(INDEX_SLUG)
    assert len(df_index) == 1
    assert df_index.iloc[0]["pol_score"] == pytest.approx(50.0)


def _make_full_project_json(slug: str, name: str, rank: int, pol_score: float) -> dict:
    """Build a Core3 project JSON matching the full API payload shape.

    Used by tests that exercise :func:`get_core3_protocol_record` which
    expects the complete payload structure.
    """
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


def test_get_core3_protocol_record_mapped(db: Core3Database):
    """Look up a vault protocol that has a Core3 mapping.

    We insert a snapshot under the Core3 slug "morpho" (which is the
    mapping target for our vault protocol slug "morpho") and verify
    that get_core3_protocol_record resolves and returns the full record.

    1. Insert a full project snapshot for "morpho"
    2. Call get_core3_protocol_record with our slug "morpho"
    3. Verify returned record contains all expected fields
    4. Verify fetched_at is populated from the database layer
    """
    t_fetch = datetime.datetime(2025, 7, 1, 12, 0, 0)
    raw = _make_full_project_json("morpho", "Morpho", rank=96, pol_score=32.15)

    # 1. Insert snapshot
    db.insert_project_snapshot("morpho", t_fetch, raw)

    # 2. Look up via vault protocol slug
    record = get_core3_protocol_record(db, "morpho")

    # 3. Verify record fields
    assert record is not None
    assert record["slug"] == "morpho"
    assert record["name"] == "Morpho"
    assert record["rank"] == 96
    assert record["pol"]["score"] == pytest.approx(32.15)
    assert record["pol"]["rating"] == "BB"
    assert record["pol"]["confidence"] == "High"
    assert record["category"]["name"] == "Decentralized Finance"
    assert record["market_cap"]["in_usd"] == "1000000"
    assert len(record["chains"]) == 2
    assert record["top_risks"][0]["content"] == "Example risk finding."
    assert record["description"] == "Morpho is a DeFi protocol."

    # 4. fetched_at is set by the database layer
    assert record["fetched_at"] == t_fetch


def test_get_core3_protocol_record_aliased(db: Core3Database):
    """Look up a vault protocol whose Core3 slug differs from ours.

    Our "fluid" maps to Core3 "instadapp". We insert under
    "instadapp" and verify lookup via "fluid" resolves correctly.

    1. Insert a snapshot under Core3 slug "instadapp"
    2. Call get_core3_protocol_record with our slug "fluid"
    3. Verify the record is returned with the Core3 slug
    """
    t_fetch = datetime.datetime(2025, 7, 1, 12, 0, 0)
    raw = _make_full_project_json("instadapp", "Fluid", rank=288, pol_score=48.97)

    # 1. Insert under Core3 slug
    db.insert_project_snapshot("instadapp", t_fetch, raw)

    # 2. Look up via our vault protocol slug
    record = get_core3_protocol_record(db, "fluid")

    # 3. Returns the record with Core3's slug
    assert record is not None
    assert record["slug"] == "instadapp"
    assert record["name"] == "Fluid"
    assert record["pol"]["score"] == pytest.approx(48.97)


def test_get_core3_protocol_record_unmapped(db: Core3Database):
    """Look up a vault protocol with no Core3 mapping returns None.

    1. Call get_core3_protocol_record with an unmapped slug
    2. Verify None is returned
    """
    # 1-2. No mapping for ipor-fusion
    assert get_core3_protocol_record(db, "ipor-fusion") is None
    assert get_core3_protocol_record(db, "lagoon-finance") is None
