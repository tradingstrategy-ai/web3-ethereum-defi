"""Test GRVT vault scanner with DuckDB storage.

This test module verifies that we can scan GRVT vaults and store
snapshots in a DuckDB database.

No authentication required — all endpoints are public.
"""

import os

import pytest

CI = os.environ.get("CI", None) is not None

pytestmark = pytest.mark.skipif(CI, reason="GRVT endpoints are behind Cloudflare which blocks CI runners")

from pathlib import Path

from eth_defi.grvt.vault_scanner import (
    VaultSnapshotDatabase,
    scan_vaults,
)


def test_scan_vaults(tmp_path: Path):
    """Scan all discoverable GRVT vaults into a temporary DuckDB.

    - Creates a temporary DuckDB database
    - Discovers vaults via the GraphQL API
    - Enriches with live data from market data API
    - Verifies snapshots are stored correctly
    """
    db_path = tmp_path / "test-grvt-vaults.duckdb"

    db = scan_vaults(
        db_path=db_path,
    )

    try:
        assert db_path.exists()

        vault_count = db.get_vault_count()
        assert vault_count > 0, "Expected at least one vault"

        df = db.get_latest_snapshots()
        assert len(df) == vault_count

        expected_columns = [
            "snapshot_timestamp",
            "vault_id",
            "chain_vault_id",
            "name",
            "tvl",
            "share_price",
            "apr",
        ]
        for col in expected_columns:
            assert col in df.columns, f"Missing column: {col}"

        # Vault IDs should be VLT: prefixed strings
        assert df["vault_id"].iloc[0].startswith("VLT:")

        timestamps = db.get_snapshot_timestamps()
        assert len(timestamps) == 1, "Should have exactly one scan timestamp"
    finally:
        db.close()


def test_vault_snapshot_database_queries(tmp_path: Path):
    """Test VaultSnapshotDatabase query methods.

    - Scans vaults
    - Tests get_vault_history, get_count, get_vault_count
    """
    db_path = tmp_path / "test-grvt-queries.duckdb"

    db = scan_vaults(
        db_path=db_path,
    )

    try:
        df = db.get_latest_snapshots()
        test_vault_id = df["vault_id"].iloc[0]

        # Test get_vault_history
        history = db.get_vault_history(test_vault_id)
        assert len(history) == 1, "Should have one snapshot for the vault"
        assert history["vault_id"].iloc[0] == test_vault_id

        # Test get_count
        total_count = db.get_count()
        assert total_count == db.get_vault_count(), "Total count should equal vault count for single scan"
    finally:
        db.close()
