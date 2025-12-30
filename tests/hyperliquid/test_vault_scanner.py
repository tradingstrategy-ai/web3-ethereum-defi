"""Test Hyperliquid vault scanner with DuckDB storage.

This test module verifies that we can scan Hyperliquid vaults and store
snapshots in a DuckDB database.
"""

from pathlib import Path

import pandas as pd

from eth_defi.hyperliquid.session import create_hyperliquid_session
from eth_defi.hyperliquid.vault_scanner import (
    ScanDisabled,
    VaultSnapshot,
    VaultSnapshotDatabase,
    scan_vaults,
)


def test_scan_vaults_without_followers(tmp_path: Path):
    """Scan vaults without fetching follower counts for speed.

    - Creates a temporary DuckDB database
    - Scans a limited number of vaults without fetching follower counts (faster)
    - Verifies snapshots are stored correctly
    - Queries the database to verify data
    """
    db_path = tmp_path / "test-vaults.duckdb"

    session = create_hyperliquid_session()

    # Scan without follower counts for speed, limit to 10 vaults for testing
    db = scan_vaults(
        session=session,
        db_path=db_path,
        fetch_follower_counts=False,
        limit=10,
    )

    try:
        # Verify database was created
        assert db_path.exists()

        # Should have exactly 10 vaults (limited)
        vault_count = db.get_vault_count()
        assert vault_count == 10, f"Expected 10 vaults, got {vault_count}"

        # Get latest snapshots
        df = db.get_latest_snapshots()
        assert len(df) == vault_count

        # Verify columns exist
        expected_columns = [
            "snapshot_timestamp",
            "vault_address",
            "name",
            "leader",
            "is_closed",
            "relationship_type",
            "tvl",
            "apr",
            "total_pnl",
            "follower_count",
        ]
        for col in expected_columns:
            assert col in df.columns, f"Missing column: {col}"

        # Verify data types and values
        assert df["vault_address"].iloc[0].startswith("0x")
        assert df["leader"].iloc[0].startswith("0x")

        # Since we didn't fetch follower counts, they should all be None/NaN
        assert df["follower_count"].isna().all()

        # Get snapshot timestamps
        timestamps = db.get_snapshot_timestamps()
        assert len(timestamps) == 1, "Should have exactly one scan timestamp"
    finally:
        db.close()


def test_scan_vaults_with_followers_limited(tmp_path: Path):
    """Scan a limited number of vaults with follower counts.

    This test is slower as it fetches detailed info for each vault.
    We limit to testing that the mechanism works by checking
    the first few vaults have follower counts.
    """
    db_path = tmp_path / "test-vaults-followers.duckdb"

    session = create_hyperliquid_session()

    # Scan with follower counts, limit to 10 vaults for testing
    db = scan_vaults(
        session=session,
        db_path=db_path,
        fetch_follower_counts=True,
        limit=10,
    )

    try:
        # Get latest snapshots
        df = db.get_latest_snapshots()

        # At least some vaults should have follower counts
        # (some may fail to fetch, but most should succeed)
        has_followers = df["follower_count"].notna().sum()
        assert has_followers > 0, "Expected at least some vaults to have follower counts"

        # Verify follower counts are non-negative integers where present
        valid_counts = df[df["follower_count"].notna()]["follower_count"]
        assert (valid_counts >= 0).all(), "Follower counts should be non-negative"
    finally:
        db.close()


def test_vault_snapshot_database_queries(tmp_path: Path):
    """Test VaultSnapshotDatabase query methods."""
    db_path = tmp_path / "test-queries.duckdb"

    session = create_hyperliquid_session()

    # Scan vaults, limit to 10 for testing
    db = scan_vaults(
        session=session,
        db_path=db_path,
        fetch_follower_counts=False,
        limit=10,
    )

    try:
        # Get a vault address to query
        df = db.get_latest_snapshots()
        test_vault_address = df["vault_address"].iloc[0]

        # Test get_vault_history
        history = db.get_vault_history(test_vault_address)
        assert len(history) == 1, "Should have one snapshot for the vault"
        assert history["vault_address"].iloc[0] == test_vault_address

        # Test get_snapshots_at_time
        timestamps = db.get_snapshot_timestamps()
        snapshots_at_time = db.get_snapshots_at_time(timestamps[0])
        assert len(snapshots_at_time) == db.get_vault_count()

        # Test get_count
        total_count = db.get_count()
        assert total_count == db.get_vault_count(), "Total count should equal vault count for single scan"
    finally:
        db.close()


def test_vault_scan_disabled(tmp_path: Path):
    """Test that manually disabled vaults are skipped during scanning.

    This test verifies that a vault can be manually disabled and will be
    skipped in subsequent scans.
    """
    db_path = tmp_path / "test-disabled.duckdb"

    session = create_hyperliquid_session()

    # First scan: 10 vaults
    db = scan_vaults(
        session=session,
        db_path=db_path,
        fetch_follower_counts=False,
        limit=10,
    )

    try:
        first_scan_count = db.get_count()
        assert first_scan_count == 10, "First scan should have 10 records"

        # Get count of vaults that were NOT auto-disabled (high TVL vaults)
        df = db.get_latest_snapshots()
        enabled_vaults = df[df["scan_disabled_reason"].isna()]
        auto_disabled_count = len(df) - len(enabled_vaults)

        # Pick a vault that is NOT already auto-disabled to manually disable
        if len(enabled_vaults) == 0:
            # All vaults were auto-disabled due to low TVL, skip the manual disable test
            return

        vault_to_disable = enabled_vaults["vault_address"].iloc[0]

        # Manually disable one vault by updating its snapshot with scan_disabled_reason
        latest_snapshot = db.get_vault_history(vault_to_disable).iloc[-1]
        disabled_snapshot = VaultSnapshot(
            snapshot_timestamp=latest_snapshot["snapshot_timestamp"].to_pydatetime(),
            vault_address=str(vault_to_disable),
            name=str(latest_snapshot["name"]),
            leader=str(latest_snapshot["leader"]),
            is_closed=bool(latest_snapshot["is_closed"]),
            relationship_type=str(latest_snapshot["relationship_type"]),
            create_time=latest_snapshot["create_time"].to_pydatetime() if pd.notna(latest_snapshot["create_time"]) else None,
            tvl=latest_snapshot["tvl"],
            apr=float(latest_snapshot["apr"]) if pd.notna(latest_snapshot["apr"]) else None,
            total_pnl=float(latest_snapshot["total_pnl"]) if pd.notna(latest_snapshot["total_pnl"]) else None,
            follower_count=int(latest_snapshot["follower_count"]) if pd.notna(latest_snapshot["follower_count"]) else None,
            scan_disabled_reason=ScanDisabled.manual,
        )
        db.insert_snapshot(disabled_snapshot)
        db.save()

        # Verify the vault is now in the disabled list
        disabled_vaults = db.get_disabled_vault_addresses()
        assert vault_to_disable in disabled_vaults, "Vault should be in disabled list"

        # Count total disabled (auto + manual)
        total_disabled_before_second_scan = len(disabled_vaults)
        # Should be auto_disabled_count + 1 (the manually disabled one)
        assert total_disabled_before_second_scan == auto_disabled_count + 1

        # Second scan: disabled vaults should be skipped
        db.close()
        db = scan_vaults(
            session=session,
            db_path=db_path,
            fetch_follower_counts=False,
            limit=10,
        )

        # We should have: first_scan_count + (10 - total_disabled_before_second_scan)
        expected_count = first_scan_count + (10 - total_disabled_before_second_scan)
        actual_count = db.get_count()
        assert actual_count == expected_count, f"Expected {expected_count} records, got {actual_count}"

        # Verify the manually disabled vault still only has 1 snapshot (not 2)
        disabled_vault_history = db.get_vault_history(vault_to_disable)
        assert len(disabled_vault_history) == 1, f"Disabled vault should have 1 snapshot, got {len(disabled_vault_history)}"

    finally:
        db.close()
