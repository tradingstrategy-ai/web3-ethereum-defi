"""Test Core3 risk intelligence scanner with DuckDB storage.

Verifies that we can scan Core3 projects and store snapshots,
PoL time-series, and category breakdowns in a DuckDB database.

Requires ``CORE3_API_KEY`` environment variable to be set.
"""

import os
from pathlib import Path

import pytest

from eth_defi.core3.constants import INDEX_SLUG
from eth_defi.core3.database import Core3Database
from eth_defi.core3.scanner import scan_projects
from eth_defi.core3.session import create_core3_session

pytestmark = pytest.mark.skipif(
    not os.environ.get("CORE3_API_KEY"),
    reason="CORE3_API_KEY not set",
)


@pytest.fixture()
def core3_session():
    """Create a Core3 API session for testing."""
    return create_core3_session()


def test_scan_projects_snapshot_only(core3_session, tmp_path: Path):
    """Scan a handful of projects without history and verify snapshots are stored.

    1. Scan 5 projects with PoL and category history disabled
    2. Verify project_count matches the limit
    3. Verify expected columns exist in the latest snapshots
    4. Verify payload column contains non-empty JSON
    5. Verify PoL daily table is empty (history not fetched)
    """
    db_path = tmp_path / "test-snapshots.duckdb"

    # 1. Scan 5 projects, snapshots only
    db = scan_projects(
        session=core3_session,
        db_path=db_path,
        fetch_pol_history=False,
        fetch_category_history=False,
        fetch_index_pol=False,
        limit=5,
        max_workers=2,
    )

    try:
        # 2. Verify project count
        project_count = db.get_project_count()
        assert project_count == 5, f"Expected 5 projects, got {project_count}"

        # 3. Verify columns in latest snapshots
        df = db.get_latest_project_snapshots()
        assert len(df) == 5
        for col in ("slug", "name", "rank", "pol_score", "pol_rating", "market_cap_usd", "payload"):
            assert col in df.columns, f"Missing column: {col}"

        # 4. Verify payloads are non-empty JSON
        assert df["payload"].notna().all()
        assert (df["payload"].str.len() > 2).all()

        # 5. Verify no PoL daily rows (history not fetched)
        assert db.get_pol_daily_count() == 0
    finally:
        db.close()


def test_scan_projects_with_pol_history(core3_session, tmp_path: Path):
    """Scan projects with full history enabled and verify time-series data.

    1. Scan 3 projects with PoL history, category history, and index PoL enabled
    2. Verify pol_daily has points for each project with scores in 0-100
    3. Verify pol_category_daily has rows with category scores
    4. Verify INDEX_SLUG rows exist in pol_daily (index-level aggregate)
    """
    db_path = tmp_path / "test-history.duckdb"

    # 1. Scan 3 projects with all history
    db = scan_projects(
        session=core3_session,
        db_path=db_path,
        fetch_pol_history=True,
        fetch_category_history=True,
        fetch_index_pol=True,
        limit=3,
        max_workers=2,
    )

    try:
        # 2. Verify pol_daily has data with scores in valid range
        df_snapshots = db.get_latest_project_snapshots()
        for slug in df_snapshots["slug"].tolist():
            df_pol = db.get_pol_daily(slug)
            assert len(df_pol) > 0, f"Expected PoL history for {slug}"
            assert (df_pol["pol_score"] >= 0).all(), f"PoL scores below 0 for {slug}"
            assert (df_pol["pol_score"] <= 100).all(), f"PoL scores above 100 for {slug}"

        # 3. Verify category daily data
        for slug in df_snapshots["slug"].tolist():
            df_cat = db.get_pol_category_daily(slug)
            assert len(df_cat) > 0, f"Expected category history for {slug}"
            for cat_col in ("security_score", "financial_score", "operational_score"):
                assert cat_col in df_cat.columns, f"Missing {cat_col} for {slug}"

        # 4. Verify index PoL exists
        df_index = db.get_pol_daily(INDEX_SLUG)
        assert len(df_index) > 0, "Expected index-level PoL history"
        assert (df_index["pol_score"] >= 0).all()
        assert (df_index["pol_score"] <= 100).all()
    finally:
        db.close()


def test_scan_idempotent(core3_session, tmp_path: Path):
    """Scan the same projects twice and verify idempotent inserts.

    1. Scan 2 projects with PoL history
    2. Record pol_daily row count and snapshot count
    3. Scan the same 2 projects again
    4. Verify no duplicate (slug, ts) rows in pol_daily
    5. Verify project_snapshots count doubles (two fetched_at values)
    """
    db_path = tmp_path / "test-idempotent.duckdb"

    # 1. First scan
    db = scan_projects(
        session=core3_session,
        db_path=db_path,
        fetch_pol_history=True,
        fetch_category_history=False,
        fetch_index_pol=False,
        limit=2,
        max_workers=2,
    )

    try:
        # 2. Record counts after first scan
        pol_count_1 = db.get_pol_daily_count()
        snapshot_count_1 = db.get_snapshot_count()
        assert pol_count_1 > 0, "First scan should produce PoL rows"
        assert snapshot_count_1 == 2, "First scan should produce 2 snapshots"
    finally:
        db.close()

    # 3. Second scan of same projects
    db = scan_projects(
        session=core3_session,
        db_path=db_path,
        fetch_pol_history=True,
        fetch_category_history=False,
        fetch_index_pol=False,
        limit=2,
        max_workers=2,
    )

    try:
        pol_count_2 = db.get_pol_daily_count()
        snapshot_count_2 = db.get_snapshot_count()

        # 4. PoL daily uses ON CONFLICT DO NOTHING — row count should not
        # significantly increase (may increase slightly if API published
        # new points between the two scans)
        assert pol_count_2 >= pol_count_1, "PoL count should not decrease"
        assert pol_count_2 <= pol_count_1 + 10, f"PoL count grew unexpectedly: {pol_count_1} -> {pol_count_2}"

        # 5. Snapshots use different fetched_at, so count should double
        assert snapshot_count_2 == 4, f"Expected 4 snapshots (2 per scan), got {snapshot_count_2}"
    finally:
        db.close()
