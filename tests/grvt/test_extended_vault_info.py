"""Test GRVT extended vault info storage, refresh gating, and markdown export.

These tests are network-free: they build :py:class:`GRVTVaultSummary`
objects and a temporary DuckDB directly, so they also run on CI where the
GRVT endpoints are Cloudflare-gated.

Covered behaviour:

1. ``build_vault_description()`` renders the new extended fields (categories,
   manager profile image, cover image) as a markdown section.
2. The ``extended_vault_info`` JSON column is only refreshed when the stored
   copy is older than the max-age window (weekly refresh gate).
3. A ``None`` payload leaves the stored extended info untouched.
4. The schema migration is safe: opening an old-schema database adds the new
   columns without dropping existing rows.
"""

import datetime
import json
from pathlib import Path

import duckdb
import pytest

from eth_defi.grvt.daily_metrics import GRVTDailyMetricsDatabase
from eth_defi.grvt.vault import GRVTVaultSummary, build_vault_description


def _make_summary(**overrides) -> GRVTVaultSummary:
    """Build a minimal GRVTVaultSummary for description tests."""
    base = dict(
        vault_id="VLT:test",
        chain_vault_id=123,
        name="Test Vault",
        description="A test strategy.",
        manager_bio="An experienced trader.",
        investment_philosophy="Buy low, sell high.",
        risk_management_process="Tight stops.",
        vault_type="launchpad",
        discoverable=True,
        status="active",
        manager_name="Test Manager",
        categories=["Market Making", "Mean Reversion"],
        manager_profile_image_url="https://cdn.example/profile.png",
        cover_image_url="https://cdn.example/cover.png",
        raw_metadata={"id": "VLT:test", "managerInfo": {"profileImageURL": "https://cdn.example/profile.png"}},
    )
    base.update(overrides)
    return GRVTVaultSummary(**base)


def test_build_vault_description_includes_extended_fields() -> None:
    """build_vault_description() renders extended metadata as a markdown section.

    1. Build a summary with categories and image URLs.
    2. Render the markdown description.
    3. Assert the existing sections plus a new "Strategy details" section
       with categories and image links are present.
    """

    # 1. Build a summary with extended fields populated
    summary = _make_summary()

    # 2. Render the markdown
    md = build_vault_description(summary)

    # 3. Existing sections still present, plus the new extended section
    assert "## About the vault leader" in md
    assert "## Trading strategy" in md
    assert "## Strategy details" in md
    assert "**Categories:** Market Making, Mean Reversion" in md
    assert "https://cdn.example/profile.png" in md
    assert "https://cdn.example/cover.png" in md


def test_build_vault_description_omits_empty_extended_section() -> None:
    """The extended section is omitted when no extended fields are present.

    1. Build a summary with no categories or image URLs.
    2. Render the markdown.
    3. Assert no "Strategy details" section is emitted.
    """

    # 1. Summary without any extended fields
    summary = _make_summary(categories=[], manager_profile_image_url=None, cover_image_url=None)

    # 2. Render the markdown
    md = build_vault_description(summary)

    # 3. No extended section
    assert "## Strategy details" not in md


def test_extended_vault_info_refresh_gate(tmp_path: Path) -> None:
    """Extended vault info refreshes only when older than the max-age window.

    1. Insert a vault with extended info ``V1`` (new row stores it).
    2. Upsert again with ``V2`` using the default weekly window — kept as ``V1``.
    3. Upsert with ``V3`` and a zero max-age — refreshed to ``V3``.
    4. Upsert with ``None`` payload — stored value is left unchanged.
    """

    db = GRVTDailyMetricsDatabase(tmp_path / "metrics.duckdb")
    try:
        common = dict(
            vault_id="VLT:gate",
            chain_vault_id=1,
            name="Gate Vault",
            description="desc",
            vault_type="launchpad",
            manager_name="mgr",
            tvl=100.0,
            share_price=1.0,
            investor_count=None,
        )

        def stored():
            return db.con.execute("SELECT extended_vault_info, extended_vault_info_metadata_last_updated_at FROM vault_metadata WHERE vault_id = 'VLT:gate'").fetchone()

        # 1. New row stores the payload and stamps the timestamp
        db.upsert_vault_metadata(**common, extended_vault_info="V1")
        info, ts = stored()
        assert info == "V1"
        assert ts is not None

        # 2. Default 7-day window: a just-written row is not refreshed
        db.upsert_vault_metadata(**common, extended_vault_info="V2")
        info, ts2 = stored()
        assert info == "V1", "Fresh extended info must not be overwritten within the window"
        assert ts2 == ts

        # 3. Zero max-age forces refresh because the stored timestamp is older than now
        db.upsert_vault_metadata(**common, extended_vault_info="V3", extended_info_max_age=datetime.timedelta(seconds=0))
        info, ts3 = stored()
        assert info == "V3"
        assert ts3 >= ts

        # 4. None payload never changes the stored value
        db.upsert_vault_metadata(**common, extended_vault_info=None, extended_info_max_age=datetime.timedelta(seconds=0))
        info, _ = stored()
        assert info == "V3"
    finally:
        db.close()


def test_extended_vault_info_migration_is_safe(tmp_path: Path) -> None:
    """Opening an old-schema database adds the new columns without data loss.

    1. Create a database with the pre-migration vault_metadata schema and a row.
    2. Open it through GRVTDailyMetricsDatabase, which runs the migration.
    3. Assert the existing row survives and the new columns exist as NULL.
    """

    db_path = tmp_path / "old.duckdb"

    # 1. Hand-build the old schema (no extended_vault_info columns) and insert a row
    con = duckdb.connect(str(db_path))
    con.execute(
        """
        CREATE TABLE vault_metadata (
            vault_id VARCHAR PRIMARY KEY,
            chain_vault_id INTEGER NOT NULL,
            name VARCHAR NOT NULL,
            description VARCHAR,
            vault_type VARCHAR,
            manager_name VARCHAR,
            tvl DOUBLE,
            share_price DOUBLE,
            investor_count INTEGER,
            management_fee DOUBLE,
            performance_fee DOUBLE,
            last_updated TIMESTAMP NOT NULL
        )
        """
    )
    con.execute(
        "INSERT INTO vault_metadata VALUES ('VLT:old', 7, 'Old Vault', 'desc', 'prime', 'mgr', 9.0, 1.0, 3, 0.01, 0.2, ?)",
        [datetime.datetime(2026, 1, 1)],
    )
    con.close()

    # 2. Open through the production class, which migrates the schema in place
    db = GRVTDailyMetricsDatabase(db_path)
    try:
        # 3. Existing row preserved, new columns present and NULL
        row = db.con.execute("SELECT name, tvl, extended_vault_info, extended_vault_info_metadata_last_updated_at FROM vault_metadata WHERE vault_id = 'VLT:old'").fetchone()
        assert row[0] == "Old Vault"
        assert row[1] == pytest.approx(9.0)
        assert row[2] is None
        assert row[3] is None

        # The migrated database accepts extended info on the next upsert
        db.upsert_vault_metadata(
            vault_id="VLT:old",
            chain_vault_id=7,
            name="Old Vault",
            description="desc",
            vault_type="prime",
            manager_name="mgr",
            tvl=9.0,
            share_price=1.0,
            investor_count=3,
            extended_vault_info=json.dumps({"id": "VLT:old"}),
        )
        info = db.con.execute("SELECT extended_vault_info FROM vault_metadata WHERE vault_id = 'VLT:old'").fetchone()[0]
        assert json.loads(info) == {"id": "VLT:old"}
    finally:
        db.close()
