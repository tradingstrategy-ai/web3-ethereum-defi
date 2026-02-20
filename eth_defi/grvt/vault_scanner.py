"""GRVT vault scanner with DuckDB storage.

This module provides functionality for scanning all known GRVT vaults and storing
historical snapshots in a DuckDB database for tracking TVL and other metrics
over time.

Vault discovery uses the public GraphQL API at ``https://edge.grvt.io/query``
(which includes per-vault fee data), enriched with live data from the
market data API.

Example usage::

    from pathlib import Path
    from eth_defi.grvt.vault_scanner import scan_vaults

    db = scan_vaults()
    print(db.get_latest_snapshots())
    db.close()

"""

import datetime
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from requests import Session

from eth_defi.compat import native_datetime_utc_now
from eth_defi.grvt.vault import (
    GRVTVaultSummary,
    fetch_vault_details,
    fetch_vault_listing_graphql,
    fetch_vault_performance,
)

logger = logging.getLogger(__name__)

#: Default path for GRVT vault metadata database
GRVT_VAULT_METADATA_DATABASE = Path.home() / ".tradingstrategy" / "grvt" / "vaults.duckdb"


@dataclass(slots=True)
class VaultSnapshot:
    """A point-in-time snapshot of a GRVT vault's state.

    Contains the key metrics we want to track over time for each vault.
    """

    #: When this snapshot was taken
    snapshot_timestamp: datetime.datetime

    #: Vault string ID on the GRVT platform (e.g. ``VLT:xxx``)
    vault_id: str

    #: Numeric on-chain vault ID
    chain_vault_id: int

    #: Vault display name
    name: str

    #: Total Value Locked in USDT
    tvl: float | None

    #: Current share price
    share_price: float | None

    #: Annualised percentage return
    apr: float | None = None

    #: Number of investors in the vault
    investor_count: int | None = None


class VaultSnapshotDatabase:
    """DuckDB database for storing GRVT vault snapshots over time.

    Stores point-in-time snapshots of vault metrics including TVL and share price.
    Each snapshot is keyed by timestamp and vault ID.

    Example::

        from pathlib import Path
        from eth_defi.grvt.vault_scanner import VaultSnapshotDatabase

        db = VaultSnapshotDatabase(Path("vaults.duckdb"))
        df = db.get_latest_snapshots()
        print(df)
        db.close()

    """

    def __init__(self, path: Path):
        """Initialise the database connection.

        :param path:
            Path to the DuckDB file. Parent directories will be created if needed.
        """
        assert isinstance(path, Path), f"Expected Path for path, got {type(path)}"
        assert not path.is_dir(), f"Expected file path, got directory: {path}"

        # Create folder if needed
        path.parent.mkdir(parents=True, exist_ok=True)

        # Lazy import to avoid import-time dependency
        import duckdb

        self.path = path
        self.con = duckdb.connect(str(path))
        self._init_schema()

    def __del__(self):
        if hasattr(self, "con") and self.con is not None:
            self.con.close()
            self.con = None

    def _init_schema(self):
        """Create the vault_snapshots table if it doesn't exist."""
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS vault_snapshots (
                -- Composite primary key: timestamp + vault ID
                snapshot_timestamp TIMESTAMP NOT NULL,
                vault_id VARCHAR NOT NULL,

                -- Basic vault info
                chain_vault_id INTEGER NOT NULL,
                name VARCHAR NOT NULL,

                -- Key metrics
                tvl DOUBLE,
                share_price DOUBLE,
                apr DOUBLE,
                investor_count INTEGER,

                -- Primary key constraint
                PRIMARY KEY (snapshot_timestamp, vault_id)
            )
        """)

    def insert_snapshot(self, snapshot: VaultSnapshot):
        """Insert a single vault snapshot into the database.

        :param snapshot:
            VaultSnapshot to insert.
        """
        self.con.execute(
            """
            INSERT INTO vault_snapshots (
                snapshot_timestamp, vault_id, chain_vault_id, name,
                tvl, share_price, apr, investor_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (snapshot_timestamp, vault_id)
            DO UPDATE SET
                chain_vault_id = EXCLUDED.chain_vault_id,
                name = EXCLUDED.name,
                tvl = EXCLUDED.tvl,
                share_price = EXCLUDED.share_price,
                apr = EXCLUDED.apr,
                investor_count = EXCLUDED.investor_count
            """,
            [
                snapshot.snapshot_timestamp,
                snapshot.vault_id,
                snapshot.chain_vault_id,
                snapshot.name,
                snapshot.tvl,
                snapshot.share_price,
                snapshot.apr,
                snapshot.investor_count,
            ],
        )

    def insert_snapshots(self, snapshots: list[VaultSnapshot]):
        """Bulk insert vault snapshots into the database.

        :param snapshots:
            List of VaultSnapshot objects to insert.
        """
        if not snapshots:
            return

        rows = [
            (
                s.snapshot_timestamp,
                s.vault_id,
                s.chain_vault_id,
                s.name,
                s.tvl,
                s.share_price,
                s.apr,
                s.investor_count,
            )
            for s in snapshots
        ]

        self.con.executemany(
            """
            INSERT INTO vault_snapshots (
                snapshot_timestamp, vault_id, chain_vault_id, name,
                tvl, share_price, apr, investor_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (snapshot_timestamp, vault_id)
            DO UPDATE SET
                chain_vault_id = EXCLUDED.chain_vault_id,
                name = EXCLUDED.name,
                tvl = EXCLUDED.tvl,
                share_price = EXCLUDED.share_price,
                apr = EXCLUDED.apr,
                investor_count = EXCLUDED.investor_count
            """,
            rows,
        )

    def get_latest_snapshots(self) -> pd.DataFrame:
        """Get the most recent snapshot for each vault.

        :return:
            DataFrame with the latest snapshot for each vault ID.
        """
        return self.con.execute("""
            SELECT * FROM vault_snapshots
            WHERE (vault_id, snapshot_timestamp) IN (
                SELECT vault_id, MAX(snapshot_timestamp)
                FROM vault_snapshots
                GROUP BY vault_id
            )
            ORDER BY tvl DESC NULLS LAST
        """).df()

    def get_vault_history(self, vault_id: str) -> pd.DataFrame:
        """Get all snapshots for a specific vault.

        :param vault_id:
            The vault string ID to query.
        :return:
            DataFrame with all snapshots for the vault, ordered by timestamp.
        """
        return self.con.execute(
            """
            SELECT * FROM vault_snapshots
            WHERE vault_id = ?
            ORDER BY snapshot_timestamp ASC
            """,
            [vault_id],
        ).df()

    def get_snapshot_timestamps(self) -> list[datetime.datetime]:
        """Get all unique snapshot timestamps in the database.

        :return:
            List of snapshot timestamps, ordered from oldest to newest.
        """
        result = self.con.execute("""
            SELECT DISTINCT snapshot_timestamp
            FROM vault_snapshots
            ORDER BY snapshot_timestamp ASC
        """).fetchall()
        return [row[0] for row in result]

    def get_count(self) -> int:
        """Get total number of snapshot records in the database.

        :return:
            Total count of snapshot records.
        """
        return self.con.execute("SELECT COUNT(*) FROM vault_snapshots").fetchone()[0]

    def get_vault_count(self) -> int:
        """Get number of unique vaults in the database.

        :return:
            Count of unique vault IDs.
        """
        return self.con.execute("SELECT COUNT(DISTINCT vault_id) FROM vault_snapshots").fetchone()[0]

    def save(self):
        """Force a checkpoint to ensure data is written to disk."""
        self.con.commit()

    def close(self):
        """Close the database connection."""
        logger.info("Closing GRVT vault snapshot database at %s", self.path)
        if self.con is not None:
            self.con.close()
            self.con = None

    def is_closed(self) -> bool:
        """Check if the database connection is closed."""
        return self.con is None


def scan_vaults(
    session: Session | None = None,
    db_path: Path = GRVT_VAULT_METADATA_DATABASE,
    timeout: float = 30.0,
    only_discoverable: bool = True,
) -> VaultSnapshotDatabase:
    """Scan all GRVT vaults and store snapshots in DuckDB.

    Discovers vaults via the public GraphQL API (includes per-vault fees),
    enriched with live data from the market data API.
    No authentication required.

    Example::

        from eth_defi.grvt.vault_scanner import scan_vaults

        db = scan_vaults()
        df = db.get_latest_snapshots()
        print(f"Scanned {len(df)} vaults")
        db.close()

    :param session:
        HTTP session. If None, one is created via
        :py:func:`~eth_defi.grvt.session.create_grvt_session`.
    :param db_path:
        Path to the DuckDB database file.
    :param timeout:
        HTTP request timeout in seconds.
    :param only_discoverable:
        If True, only scan vaults marked as discoverable.
    :return:
        VaultSnapshotDatabase instance with the newly inserted snapshots.
    """
    if session is None:
        from eth_defi.grvt.session import create_grvt_session

        session = create_grvt_session()

    snapshot_timestamp = native_datetime_utc_now()

    logger.info("Starting GRVT vault scan at %s", snapshot_timestamp)

    # Step 1: Discover vaults via the public GraphQL API (includes per-vault fees).
    vault_summaries = fetch_vault_listing_graphql(
        session,
        only_discoverable=only_discoverable,
        timeout=timeout,
    )
    logger.info("Fetched %d GRVT vault summaries", len(vault_summaries))

    # Step 2: Enrich with TVL and performance
    chain_ids = [s.chain_vault_id for s in vault_summaries]
    details_map: dict = {}
    perf_map: dict = {}

    if chain_ids:
        try:
            details_map = fetch_vault_details(session, chain_ids, timeout=timeout)
        except Exception as e:
            logger.warning("Failed to fetch vault details: %s", e)

        try:
            perf_map = fetch_vault_performance(session, chain_ids, timeout=timeout)
        except Exception as e:
            logger.warning("Failed to fetch vault performance: %s", e)

    # Step 3: Build and insert snapshots
    db = VaultSnapshotDatabase(db_path)

    snapshots = []
    for summary in vault_summaries:
        detail = details_map.get(summary.chain_vault_id, {})
        perf = perf_map.get(summary.chain_vault_id)

        tvl = float(detail.get("total_equity", 0) or 0) if isinstance(detail, dict) else None
        share_price = float(detail.get("share_price", 0) or 0) if isinstance(detail, dict) else None

        snapshot = VaultSnapshot(
            snapshot_timestamp=snapshot_timestamp,
            vault_id=summary.vault_id,
            chain_vault_id=summary.chain_vault_id,
            name=summary.name,
            tvl=tvl,
            share_price=share_price,
            apr=perf.apr if perf else None,
        )
        snapshots.append(snapshot)

    db.insert_snapshots(snapshots)
    db.save()

    logger.info(
        "Scan complete. Inserted %d snapshots into %s",
        len(snapshots),
        db_path,
    )

    return db
