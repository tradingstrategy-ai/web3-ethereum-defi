"""Hyperliquid vault scanner with DuckDB storage.

This module provides functionality for scanning all Hyperliquid vaults and storing
historical snapshots in a DuckDB database for tracking TVL, PnL, and other metrics
over time.

Example usage::

    from pathlib import Path
    from eth_defi.hyperliquid.vault_scanner import scan_vaults

    # Scan all vaults and store in database (uses default path)
    scan_vaults()

"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Iterator

import pandas as pd
import requests
from eth_typing import HexAddress
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_now
from eth_defi.hyperliquid.session import HyperliquidSession
from eth_defi.hyperliquid.vault import HYPERLIQUID_STATS_URL, HyperliquidVault, VaultSummary, fetch_all_vaults
from eth_defi.types import Percent

logger = logging.getLogger(__name__)

#: Default path for Hyperliquid vault metadata database
HYPERLIQUID_VAULT_METADATA_DATABASE = Path.home() / ".tradingstrategy" / "hyperliquid" / "vaults.duckdb"

#: Minimum TVL threshold in USD for scanning vaults
#: Vaults below this threshold AND older than AGE_THRESHOLD will be marked as disabled
#: with `ScanDisabled.not_enough_tvl`
MIN_TVL_THRESHOLD = Decimal("1000")

#: Age threshold for disabling low TVL vaults
#: Only vaults older than this threshold can be disabled for low TVL
AGE_THRESHOLD = datetime.timedelta(days=30)


class ScanDisabled(Enum):
    """Reasons why a vault may be excluded from scanning.

    Stored as VARCHAR in DuckDB using the enum value (snake_case string).
    """

    #: Vault TVL is below the threshold for scanning
    not_enough_tvl = "not_enough_tvl"

    #: Vault has been manually disabled from scanning
    manual = "manual"


@dataclass(slots=True)
class VaultSnapshot:
    """A point-in-time snapshot of a Hyperliquid vault's state.

    Contains the key metrics we want to track over time for each vault.
    """

    #: When this snapshot was taken
    snapshot_timestamp: datetime.datetime

    #: Vault's blockchain address
    vault_address: HexAddress

    #: Vault display name
    name: str

    #: Vault manager/operator address
    leader: HexAddress

    #: Whether vault is closed for deposits
    is_closed: bool

    #: Vault relationship type (normal, child, parent)
    relationship_type: str

    #: Vault creation timestamp
    create_time: datetime.datetime | None

    #: Total Value Locked (USD)
    tvl: Decimal

    #: Annual Percentage Rate (as decimal, e.g., 0.15 = 15%)
    apr: Percent | None

    #: All-time PnL (sum of pnl_all_time array)
    total_pnl: Decimal | None

    #: Number of followers/depositors in the vault
    #: Note: Hyperliquid API returns at most 100 followers, so this value maxes out at 100
    follower_count: int | None

    #: Reason why this vault is disabled from future scans, or None if enabled
    scan_disabled_reason: ScanDisabled | None = None


def calculate_total_pnl(pnl_all_time: list[str] | None) -> Decimal | None:
    """Calculate the total PnL from the all-time PnL history array.

    The pnl_all_time array contains cumulative PnL values.
    The last value represents the current total all-time PnL.

    :param pnl_all_time:
        List of PnL values as strings from VaultSummary.pnl_all_time
    :return:
        Total all-time PnL as Decimal, or None if no data
    """
    if not pnl_all_time:
        return None

    # The last value in the array is the current total
    try:
        return Decimal(pnl_all_time[-1])
    except (IndexError, ValueError):
        return None


class VaultSnapshotDatabase:
    """DuckDB database for storing Hyperliquid vault snapshots over time.

    Stores point-in-time snapshots of vault metrics including TVL, PnL,
    APR, and follower count. Each snapshot is keyed by timestamp and
    vault address.

    Example::

        from pathlib import Path
        from eth_defi.hyperliquid.vault_scanner import VaultSnapshotDatabase

        db = VaultSnapshotDatabase(Path("vaults.duckdb"))

        # Query recent snapshots
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
                -- Composite primary key: timestamp + vault address
                snapshot_timestamp TIMESTAMP NOT NULL,
                vault_address VARCHAR NOT NULL,

                -- Basic vault info
                name VARCHAR NOT NULL,
                leader VARCHAR NOT NULL,
                is_closed BOOLEAN NOT NULL,
                relationship_type VARCHAR NOT NULL,
                create_time TIMESTAMP,

                -- Key metrics
                tvl DECIMAL(18, 6) NOT NULL,
                apr DECIMAL(10, 6),
                total_pnl DECIMAL(18, 6),
                follower_count INTEGER,

                -- Scan control
                scan_disabled_reason VARCHAR,

                -- Primary key constraint
                PRIMARY KEY (snapshot_timestamp, vault_address)
            )
        """)

        # Add scan_disabled_reason column if it doesn't exist (migration for existing databases)
        try:
            self.con.execute("""
                ALTER TABLE vault_snapshots ADD COLUMN scan_disabled_reason VARCHAR
            """)
        except Exception:
            # Column already exists
            pass

    def insert_snapshot(self, snapshot: VaultSnapshot):
        """Insert a single vault snapshot into the database.

        :param snapshot:
            VaultSnapshot to insert
        """
        self.con.execute(
            """
            INSERT INTO vault_snapshots (
                snapshot_timestamp, vault_address, name, leader, is_closed,
                relationship_type, create_time, tvl, apr, total_pnl, follower_count,
                scan_disabled_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (snapshot_timestamp, vault_address)
            DO UPDATE SET
                name = EXCLUDED.name,
                leader = EXCLUDED.leader,
                is_closed = EXCLUDED.is_closed,
                relationship_type = EXCLUDED.relationship_type,
                create_time = EXCLUDED.create_time,
                tvl = EXCLUDED.tvl,
                apr = EXCLUDED.apr,
                total_pnl = EXCLUDED.total_pnl,
                follower_count = EXCLUDED.follower_count,
                scan_disabled_reason = EXCLUDED.scan_disabled_reason
            """,
            [
                snapshot.snapshot_timestamp,
                snapshot.vault_address,
                snapshot.name,
                snapshot.leader,
                snapshot.is_closed,
                snapshot.relationship_type,
                snapshot.create_time,
                float(snapshot.tvl),
                float(snapshot.apr) if snapshot.apr is not None else None,
                float(snapshot.total_pnl) if snapshot.total_pnl is not None else None,
                snapshot.follower_count,
                snapshot.scan_disabled_reason.value if snapshot.scan_disabled_reason is not None else None,
            ],
        )

    def insert_snapshots(self, snapshots: Iterator[VaultSnapshot]):
        """Bulk insert vault snapshots into the database.

        :param snapshots:
            Iterator of VaultSnapshot objects to insert
        """
        # Convert to list of tuples for bulk insert
        rows = []
        for snapshot in snapshots:
            rows.append(
                (
                    snapshot.snapshot_timestamp,
                    snapshot.vault_address,
                    snapshot.name,
                    snapshot.leader,
                    snapshot.is_closed,
                    snapshot.relationship_type,
                    snapshot.create_time,
                    float(snapshot.tvl),
                    float(snapshot.apr) if snapshot.apr is not None else None,
                    float(snapshot.total_pnl) if snapshot.total_pnl is not None else None,
                    snapshot.follower_count,
                    snapshot.scan_disabled_reason.value if snapshot.scan_disabled_reason is not None else None,
                )
            )

        if not rows:
            return

        # Use executemany for bulk insert
        self.con.executemany(
            """
            INSERT INTO vault_snapshots (
                snapshot_timestamp, vault_address, name, leader, is_closed,
                relationship_type, create_time, tvl, apr, total_pnl, follower_count,
                scan_disabled_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (snapshot_timestamp, vault_address)
            DO UPDATE SET
                name = EXCLUDED.name,
                leader = EXCLUDED.leader,
                is_closed = EXCLUDED.is_closed,
                relationship_type = EXCLUDED.relationship_type,
                create_time = EXCLUDED.create_time,
                tvl = EXCLUDED.tvl,
                apr = EXCLUDED.apr,
                total_pnl = EXCLUDED.total_pnl,
                follower_count = EXCLUDED.follower_count,
                scan_disabled_reason = EXCLUDED.scan_disabled_reason
            """,
            rows,
        )

    def get_latest_snapshots(self) -> pd.DataFrame:
        """Get the most recent snapshot for each vault.

        :return:
            DataFrame with the latest snapshot for each vault address
        """
        return self.con.execute("""
            SELECT * FROM vault_snapshots
            WHERE (vault_address, snapshot_timestamp) IN (
                SELECT vault_address, MAX(snapshot_timestamp)
                FROM vault_snapshots
                GROUP BY vault_address
            )
            ORDER BY tvl DESC
        """).df()

    def get_vault_history(self, vault_address: HexAddress) -> pd.DataFrame:
        """Get all snapshots for a specific vault.

        :param vault_address:
            The vault's blockchain address
        :return:
            DataFrame with all snapshots for the vault, ordered by timestamp
        """
        return self.con.execute(
            """
            SELECT * FROM vault_snapshots
            WHERE vault_address = ?
            ORDER BY snapshot_timestamp ASC
            """,
            [vault_address],
        ).df()

    def get_snapshots_at_time(self, timestamp: datetime.datetime) -> pd.DataFrame:
        """Get all vault snapshots at a specific timestamp.

        :param timestamp:
            The snapshot timestamp to query
        :return:
            DataFrame with all vault snapshots at that timestamp
        """
        return self.con.execute(
            """
            SELECT * FROM vault_snapshots
            WHERE snapshot_timestamp = ?
            ORDER BY tvl DESC
            """,
            [timestamp],
        ).df()

    def get_snapshot_timestamps(self) -> list[datetime.datetime]:
        """Get all unique snapshot timestamps in the database.

        :return:
            List of snapshot timestamps, ordered from oldest to newest
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
            Total count of snapshot records
        """
        return self.con.execute("SELECT COUNT(*) FROM vault_snapshots").fetchone()[0]

    def get_vault_count(self) -> int:
        """Get number of unique vaults in the database.

        :return:
            Count of unique vault addresses
        """
        return self.con.execute("SELECT COUNT(DISTINCT vault_address) FROM vault_snapshots").fetchone()[0]

    def get_disabled_vault_addresses(self) -> set[HexAddress]:
        """Get vault addresses that have scan_disabled_reason set in their latest snapshot.

        :return:
            Set of vault addresses that should be skipped during scanning
        """
        result = self.con.execute("""
            SELECT vault_address FROM vault_snapshots
            WHERE (vault_address, snapshot_timestamp) IN (
                SELECT vault_address, MAX(snapshot_timestamp)
                FROM vault_snapshots
                GROUP BY vault_address
            )
            AND scan_disabled_reason IS NOT NULL
        """).fetchall()
        return {row[0] for row in result}

    def save(self):
        """Force a checkpoint to ensure data is written to disk."""
        self.con.commit()

    def close(self):
        """Close the database connection."""
        logger.info("Closing vault snapshot database at %s", self.path)
        if self.con is not None:
            self.con.close()
            self.con = None

    def is_closed(self) -> bool:
        """Check if the database connection is closed."""
        return self.con is None


def scan_vaults(
    session: HyperliquidSession,
    db_path: Path = HYPERLIQUID_VAULT_METADATA_DATABASE,
    stats_url: str = HYPERLIQUID_STATS_URL,
    fetch_follower_counts: bool = True,
    timeout: float = 30.0,
    limit: int | None = None,
    max_workers: int = 16,
) -> VaultSnapshotDatabase:
    """Scan all Hyperliquid vaults and store snapshots in DuckDB.

    This function fetches all vault summaries from the Hyperliquid API,
    calculates key metrics (TVL, PnL, etc.), and stores a timestamped
    snapshot for each vault in the database.

    Example::

        from eth_defi.hyperliquid.session import create_hyperliquid_session
        from eth_defi.hyperliquid.vault_scanner import scan_vaults

        session = create_hyperliquid_session()
        db = scan_vaults(session)

        # Get latest snapshot for each vault
        df = db.get_latest_snapshots()
        print(f"Scanned {len(df)} vaults")

        db.close()

    :param session:
        HTTP session for API requests.
        Use :py:func:`eth_defi.hyperliquid.session.create_hyperliquid_session` to create one.
    :param db_path:
        Path to the DuckDB database file.
        Defaults to ``~/.tradingstrategy/hyperliquid/vaults.duckdb``.
    :param stats_url:
        Hyperliquid stats-data API URL for vault listing
    :param fetch_follower_counts:
        If True, fetch detailed vault info to get follower counts.
        This requires an additional API call per vault and is slower.
    :param timeout:
        HTTP request timeout in seconds
    :param limit:
        Limit the number of vaults to scan. Internal testing only.
    :param max_workers:
        Maximum number of parallel workers for fetching vault details.
        Defaults to 16.
    :return:
        VaultSnapshotDatabase instance with the newly inserted snapshots

    .. note::

        The session's rate limiter restricts requests to 1/second by default.
        Having many parallel workers does not speed up processing - they will
        queue behind the rate limiter. With ~8000 vaults and 1 req/sec,
        a full scan takes approximately 2-3 hours. If you encounter 429 errors
        after retries are exhausted, the Hyperliquid API is rate limiting you
        beyond what the client-side limiter can prevent.

    """
    # Use a single timestamp for all snapshots in this scan
    snapshot_timestamp = native_datetime_utc_now()

    logger.info("Starting vault scan at %s", snapshot_timestamp)

    # Open/create the database
    db = VaultSnapshotDatabase(db_path)

    # Get disabled vaults to skip
    disabled_vaults = db.get_disabled_vault_addresses()
    if disabled_vaults:
        logger.info("Skipping %d disabled vaults", len(disabled_vaults))

    # Fetch all vault summaries.
    # Seems like we get flakiness from somewhere.
    vault_summaries = None
    for attempt in range(3):
        try:
            vault_summaries = list(fetch_all_vaults(session, stats_url, timeout))
            break
        except requests.exceptions.ChunkedEncodingError as e:
            logger.warning("Error fetching vault summaries (attempt %d/3): %s", attempt + 1, str(e))
            continue

    if vault_summaries is None:
        raise RuntimeError("Failed to fetch vault summaries after 3 attempts")

    if limit is not None:
        vault_summaries = vault_summaries[:limit]
    logger.info("Fetched %d vault summaries", len(vault_summaries))

    # Filter out disabled vaults before processing
    summaries_to_process = []
    skipped_count = 0
    for summary in vault_summaries:
        if summary.vault_address in disabled_vaults:
            skipped_count += 1
        else:
            summaries_to_process.append(summary)

    def process_vault_summary(summary: VaultSummary) -> VaultSnapshot:
        """Process a single vault summary into a snapshot."""
        # Fetch follower count if requested
        follower_count = None
        if fetch_follower_counts:
            vault = HyperliquidVault(
                session=session,
                vault_address=summary.vault_address,
                timeout=timeout,
            )
            info = vault.fetch_info()
            follower_count = len(info.followers)

        # Automatically disable vaults with TVL below threshold AND older than age threshold
        scan_disabled_reason = None
        if summary.tvl < MIN_TVL_THRESHOLD:
            # Only disable if vault is old enough (give new vaults time to grow)
            if summary.create_time is not None:
                vault_age = snapshot_timestamp - summary.create_time
                if vault_age > AGE_THRESHOLD:
                    scan_disabled_reason = ScanDisabled.not_enough_tvl

        return VaultSnapshot(
            snapshot_timestamp=snapshot_timestamp,
            vault_address=summary.vault_address,
            name=summary.name,
            leader=summary.leader,
            is_closed=summary.is_closed,
            relationship_type=summary.relationship_type,
            create_time=summary.create_time,
            tvl=summary.tvl,
            apr=summary.apr,
            total_pnl=calculate_total_pnl(summary.pnl_all_time),
            follower_count=follower_count,
            scan_disabled_reason=scan_disabled_reason,
        )

    # Process vault summaries in parallel using threading backend
    desc = "Scanning Hyperliquid vaults"
    snapshots = Parallel(n_jobs=max_workers, backend="threading")(delayed(process_vault_summary)(summary) for summary in tqdm(summaries_to_process, desc=desc))

    # Bulk insert all snapshots
    db.insert_snapshots(iter(snapshots))
    db.save()

    logger.info(
        "Scan complete. Inserted %d snapshots into %s (skipped %d disabled)",
        len(snapshots),
        db_path,
        skipped_count,
    )

    return db
