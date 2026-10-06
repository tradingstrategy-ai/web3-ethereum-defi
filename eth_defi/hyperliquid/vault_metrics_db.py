"""Base class for Hyperliquid vault metrics DuckDB databases.

Provides the shared ``vault_metadata`` table schema and common methods
used by both the daily and high-frequency pipelines:

- :py:class:`~eth_defi.hyperliquid.daily_metrics.HyperliquidDailyMetricsDatabase`
- :py:class:`~eth_defi.hyperliquid.high_freq_metrics.HyperliquidHighFreqMetricsDatabase`

Subclasses implement their own price table (``vault_daily_prices`` vs
``vault_high_freq_prices``) and upsert/tombstone methods.
"""

import datetime
import hashlib
import json
import logging
import threading
import uuid
from contextlib import closing
from pathlib import Path

import duckdb
import pandas as pd
from eth_typing import HexAddress
from requests.exceptions import RequestException

from eth_defi.compat import native_datetime_utc_now
from eth_defi.hyperliquid.permission import PermissionObservation, append_permission_observation, build_permission_observation, initialise_permission_schema, read_permission_observations
from eth_defi.hyperliquid.session import HyperliquidSession
from eth_defi.hyperliquid.vault import HyperliquidVault, VaultInfo, VaultSummary
from eth_defi.perp_dex.storage import initialise_perp_vault_observation_schema
from eth_defi.types import Percent

logger = logging.getLogger(__name__)


class HyperliquidMetricsDatabaseBase:
    """Base class for Hyperliquid vault metrics databases.

    Manages the shared ``vault_metadata`` table and provides common
    metadata methods.  Subclasses must set :py:attr:`price_table` and
    :py:attr:`time_column` and implement :py:meth:`_init_price_schema`.

    Thread safety
    ~~~~~~~~~~~~~

    The scanners in :py:mod:`~eth_defi.hyperliquid.daily_metrics` and
    :py:mod:`~eth_defi.hyperliquid.high_freq_metrics` call a shared
    database instance from multiple worker threads via
    ``joblib.Parallel(backend="threading")``.  DuckDB's Python binding
    is only thread-safe when each thread uses its **own cursor** — a
    shared ``self.con`` sees result sets clobbered by interleaved
    ``execute()`` calls and raises ``Invalid Input Error: No open
    result set``.

    Every method reachable from worker threads therefore issues its
    query through ``self.con.cursor()`` so that each call gets an
    isolated result set.  Schema init (``_init_*_schema``) and
    ``save()``/``close()`` stay on the base connection because they
    run single-threaded.
    """

    #: Name of the price table (set by subclass).
    price_table: str = ""

    #: Name of the time column in the price table (``"date"`` or ``"timestamp"``).
    time_column: str = ""

    def __init__(self, path: Path) -> None:
        assert isinstance(path, Path), f"Expected Path, got {type(path)}"
        assert not path.is_dir(), f"Expected file path, got directory: {path}"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.writer_lock = threading.RLock()
        self.con = duckdb.connect(str(path))
        # Refuse a legacy bulk table before initialisation mutates the file.
        constrained = self.con.execute("SELECT count(*) FROM duckdb_constraints() WHERE table_name=? AND constraint_type IN ('PRIMARY KEY','UNIQUE')", [self.price_table]).fetchone()[0]
        if constrained:
            self.con.close()
            self.con = None
            msg = f"{path} requires the backed-up recover-permissions.py migration before starting this collector"
            raise RuntimeError(msg)
        self.con.execute("SET wal_autocheckpoint = '1TB'")
        initialise_permission_schema(self.con)
        self._init_metadata_schema()
        self._init_price_schema()

    def fetch_vault_metadata(self, session: HyperliquidSession, summary: VaultSummary, timeout: float) -> VaultInfo | None:
        """Fetch vault details and persist permission evidence before price checks.

        Both collectors use this path so failed parsing still retains a successful
        response receipt. Database failures propagate instead of being treated as
        a retryable provider failure. See the Hyperliquid
        `info endpoint <https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint>`__.

        :param session: Rate-limited API session.
        :param summary: Vault selected from the bulk catalogue.
        :param timeout: HTTP request timeout in seconds.
        :return: Parsed metadata, or ``None`` after a recorded fetch/parse failure.
        """
        vault = HyperliquidVault(session=session, vault_address=summary.vault_address.lower(), timeout=timeout)
        try:
            info = vault.fetch_metadata()
        except (RequestException, ValueError, KeyError, TypeError, IndexError) as error:
            if vault.permission_payload is not None:
                self.record_permission(vault, None)
            self.record_permission_error(summary.vault_address, error)
            logger.warning("Failed to fetch vault details for %s (%s): %s", summary.name, summary.vault_address, error)
            return None
        self.record_permission(vault, info)
        return info

    def record_permission(self, vault: HyperliquidVault, info: VaultInfo | None) -> None:
        """Persist a successful coherent response before any price processing.

        Unknown flags are observations too and supersede previously known flags.

        :param vault: Client retaining the raw response receipt.
        :param info: Parsed source response.
        :return: ``None``.
        """
        observation = build_permission_observation(vault, info)
        with self.writer_lock, closing(self.con.cursor()) as cursor:
            append_permission_observation(cursor, observation, new_fetch=True)

    def record_bulk_closures(self, summaries: list[VaultSummary], received_at: datetime.datetime) -> None:
        """Record explicit bulk-list closures even for filtered-out drained vaults.

        Only ``isClosed=true`` supplies deny evidence. Bulk open/missing flags
        cannot establish permission or infer ``allowDeposits``. Each record is a
        coherent partial source response with its own availability clock.
        Repeated identical bulk denials do not refresh this clock; append again
        after a later genuine response changes or clears the recorded closure.

        :param summaries: Newly fetched stats-data vault summaries.
        :param received_at: Immediate UTC receipt bound after bulk fetch.
        :return: ``None``.
        """
        closed = [summary for summary in summaries if summary.is_closed is True]
        if not closed:
            return
        endpoint = "GET stats-data /Mainnet/vaults"
        with self.writer_lock, closing(self.con.cursor()) as cursor:
            latest = cursor.execute(
                """SELECT vault_address,is_closed
                FROM vault_permission_observations
                WHERE vault_address IN (SELECT unnest(?))
                  AND provenance IN ('observed','observed_unknown')
                QUALIFY row_number() OVER (PARTITION BY vault_address ORDER BY permission_observed_at DESC,written_at DESC,observation_id DESC)=1""",
                [[summary.vault_address.lower() for summary in closed]],
            ).fetchall()
            already_closed = {address for address, is_closed in latest if is_closed is True}
            for summary in closed:
                if summary.vault_address.lower() in already_closed:
                    continue
                payload = json.dumps({"isClosed": True}, sort_keys=True)
                observation = PermissionObservation(uuid.uuid4().hex, summary.vault_address.lower(), permission_observed_at=received_at, written_at=native_datetime_utc_now(), is_closed=True, provenance="observed_unknown", source_endpoint=endpoint, payload_json=payload, payload_sha256=hashlib.sha256(payload.encode()).hexdigest(), reason="Explicit bulk-list closure; deposit flag and capacity were not supplied")
                append_permission_observation(cursor, observation, new_fetch=True)
                already_closed.add(summary.vault_address.lower())
                cursor.execute("UPDATE vault_metadata SET is_closed=true,allow_deposits=NULL WHERE vault_address=?", [summary.vault_address.lower()])

    def record_permission_error(self, vault_address: HexAddress, error: BaseException) -> None:
        """Record failed fetch/parse attempts separately from successful unknowns.

        A failed HTTP request does not supersede an earlier known permission.

        :param vault_address: Attempted vault address.
        :param error: Fetch or response parsing failure, stored by type only.
        :return: ``None``.
        """
        with self.writer_lock, closing(self.con.cursor()) as cursor:
            cursor.execute("INSERT INTO vault_permission_errors VALUES (?,?,?,?)", [vault_address.lower(), native_datetime_utc_now(), "POST /info vaultDetails", type(error).__name__])

    def get_permission_observations(self) -> pd.DataFrame:
        """Read independent permissions, including explicit unknown responses.

        New source responses retain their receipt clock. Recovered legacy records
        are separately labelled when the price clock is inferred.

        :return: Observation and uncertainty-boundary frame.
        """
        with closing(self.con.cursor()) as cursor:
            return read_permission_observations(cursor)

    def upsert_price_batch(self, rows: list[tuple], columns: list[str]) -> None:
        """Apply a deduplicated staging batch using hash joins, without ART indexes.

        Permission fields in prices are compatibility fields only. Price-only
        updates preserve the original write clock and cannot refresh permissions.
        Existing constrained databases require the backed-up recovery migration.

        :param rows: Database tuples matching ``columns``.
        :param columns: Explicit price column order.
        :return: ``None``.
        """
        if not rows:
            return
        table, time = self.price_table, self.time_column
        with self.writer_lock, closing(self.con.cursor()) as cursor:
            stage = "price_stage_" + uuid.uuid4().hex
            transaction = False
            try:
                constrained = cursor.execute("SELECT count(*) FROM duckdb_constraints() WHERE table_name = ? AND constraint_type IN ('PRIMARY KEY', 'UNIQUE')", [table]).fetchone()[0]
                if constrained:
                    msg = f"{self.path} requires scripts/hyperliquid/recover-permissions.py before ingestion"
                    raise RuntimeError(msg)
                cursor.execute("BEGIN TRANSACTION")
                transaction = True
                cursor.execute(f"CREATE TEMP TABLE {stage} AS SELECT {','.join(columns)} FROM {table} WHERE false")
                cursor.executemany(f"INSERT INTO {stage} VALUES ({','.join('?' for _ in columns)})", rows)
                keys = f"vault_address,{time}"
                sparse = {"cumulative_volume", "follower_count", "apr", "leader_commission", "epoch_reset", "data_source", "daily_deposit_count", "daily_withdrawal_count", "daily_deposit_usd", "daily_withdrawal_usd", "deposit_count", "withdrawal_count", "deposit_usd", "withdrawal_usd"}
                immutable = {"vault_address", time, "is_closed", "allow_deposits", "leader_fraction", "written_at"}
                # Preserve sequential upsert semantics inside one batch: the last
                # non-null sparse value wins, while the first permission/write
                # fields stay immutable. Dense economics use the last row.
                window = f"PARTITION BY {keys} ORDER BY rowid ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"
                replacements = [f"last_value({c} IGNORE NULLS) OVER ({window}) AS {c}" for c in columns if c in sparse]
                replacements.extend(f"first_value({c}) OVER ({window}) AS {c}" for c in columns if c in immutable and c not in {"vault_address", time})
                projection = "* REPLACE (" + ",".join(replacements) + ")" if replacements else "*"
                cursor.execute(f"CREATE TEMP TABLE {stage}_dedup AS SELECT {projection} FROM {stage} QUALIFY row_number() OVER (PARTITION BY {keys} ORDER BY rowid DESC) = 1")
                updates = ",".join(f"{c}=COALESCE(s.{c},p.{c})" if c in sparse else f"{c}=s.{c}" for c in columns if c not in immutable)
                match = f"p.vault_address=s.vault_address AND p.{time}=s.{time}"
                cursor.execute(f"UPDATE {table} p SET {updates} FROM {stage}_dedup s WHERE {match}")
                cursor.execute(f"INSERT INTO {table} ({','.join(columns)}) SELECT {','.join('s.' + c for c in columns)} FROM {stage}_dedup s WHERE NOT EXISTS (SELECT 1 FROM {table} p WHERE {match})")
                cursor.execute(f"DROP TABLE {stage}_dedup")
                cursor.execute(f"DROP TABLE {stage}")
                cursor.execute("COMMIT")
                transaction = False
            except (duckdb.Error, RuntimeError):
                if transaction:
                    cursor.execute("ROLLBACK")
                raise

    def __del__(self) -> None:
        if hasattr(self, "con") and self.con is not None:
            self.con.close()
            self.con = None

    def _init_metadata_schema(self) -> None:
        """Create the shared vault_metadata table."""
        initialise_perp_vault_observation_schema(self.con)
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS vault_metadata (
                vault_address VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                leader VARCHAR NOT NULL,
                description VARCHAR,
                is_closed BOOLEAN,
                allow_deposits BOOLEAN,
                relationship_type VARCHAR NOT NULL,
                create_time TIMESTAMP,
                commission_rate DOUBLE,
                follower_count INTEGER,
                tvl DOUBLE,
                apr DOUBLE,
                last_updated TIMESTAMP NOT NULL,
                flow_data_earliest_date DATE
            )
        """)

        # Both daily and high-frequency databases share this metadata schema.
        # Older databases may lack allow_deposits entirely.
        self.con.execute("ALTER TABLE vault_metadata ADD COLUMN IF NOT EXISTS allow_deposits BOOLEAN")
        self.con.execute("ALTER TABLE vault_metadata ADD COLUMN IF NOT EXISTS flow_data_earliest_date DATE")

        # Allow missing API flags in databases created by the older schema.
        # Existing values cannot distinguish observed flags from old defaults,
        # so this migration preserves them but stops defaulting future rows.
        columns = self.con.execute("PRAGMA table_info('vault_metadata')").fetchall()
        for name in ("is_closed", "allow_deposits"):
            self.con.execute(f"ALTER TABLE vault_metadata ALTER COLUMN {name} DROP DEFAULT")
            if any(column[1] == name and column[3] for column in columns):
                self.con.execute(f"ALTER TABLE vault_metadata ALTER COLUMN {name} DROP NOT NULL")

    def _init_price_schema(self) -> None:
        """Create the price table.  Must be overridden by subclasses."""
        raise NotImplementedError

    # ── Metadata methods ──

    def upsert_vault_metadata(
        self,
        vault_address: HexAddress,
        name: str,
        leader: HexAddress,
        description: str | None,
        is_closed: bool | None,
        relationship_type: str,
        create_time: datetime.datetime | None,
        commission_rate: Percent | None,
        follower_count: int | None,
        tvl: float | None,
        apr: float | None,
        allow_deposits: bool | None = None,
        flow_data_earliest_date: datetime.date | None = None,
    ) -> None:
        """Insert or update a vault's metadata.

        :param vault_address:
            Vault address (will be lowercased).
        :param flow_data_earliest_date:
            Earliest date for which daily deposit/withdrawal flow data
            has been backfilled.  ``None`` means no flow data yet.
        """
        with closing(self.con.cursor()) as cursor:
            cursor.execute(
                """
                INSERT INTO vault_metadata (
                    vault_address, name, leader, description, is_closed,
                    allow_deposits, relationship_type, create_time, commission_rate,
                    follower_count, tvl, apr, last_updated, flow_data_earliest_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (vault_address)
                DO UPDATE SET
                    name = EXCLUDED.name,
                    leader = EXCLUDED.leader,
                    description = EXCLUDED.description,
                    is_closed = EXCLUDED.is_closed,
                    allow_deposits = EXCLUDED.allow_deposits,
                    relationship_type = EXCLUDED.relationship_type,
                    create_time = EXCLUDED.create_time,
                    commission_rate = EXCLUDED.commission_rate,
                    follower_count = EXCLUDED.follower_count,
                    tvl = EXCLUDED.tvl,
                    apr = EXCLUDED.apr,
                    last_updated = EXCLUDED.last_updated,
                    flow_data_earliest_date = CASE
                        WHEN EXCLUDED.flow_data_earliest_date IS NULL THEN vault_metadata.flow_data_earliest_date
                        WHEN vault_metadata.flow_data_earliest_date IS NULL THEN EXCLUDED.flow_data_earliest_date
                        ELSE LEAST(EXCLUDED.flow_data_earliest_date, vault_metadata.flow_data_earliest_date)
                    END
                """,
                [
                    vault_address.lower(),
                    name,
                    leader.lower(),
                    description,
                    is_closed,
                    allow_deposits,
                    relationship_type,
                    create_time,
                    commission_rate,
                    follower_count,
                    tvl,
                    apr,
                    native_datetime_utc_now(),
                    flow_data_earliest_date,
                ],
            )

    def update_vault_tvl_bulk(
        self,
        updates: list[tuple[float, bool | None, Percent | None, HexAddress]],
    ) -> None:
        """Bulk-update TVL and APR without mixing permission source responses.

        Only updates rows that already exist in ``vault_metadata``.

        :param updates:
            List of tuples ``(tvl, is_closed, apr, vault_address)``. The flag is
            retained in the calling contract but permission is recorded independently.
        """
        with closing(self.con.cursor()) as cursor:
            if not updates:
                return
            cursor.executemany(
                """
                UPDATE vault_metadata
                SET tvl = ?,
                    apr = ?,
                    last_updated = CURRENT_TIMESTAMP
                WHERE vault_address = ?
                """,
                [(tvl, apr, address) for tvl, _is_closed, apr, address in updates],
            )

    def get_all_vault_metadata(self) -> pd.DataFrame:
        """Get metadata for all vaults.

        :return:
            DataFrame with one row per vault.
        """
        with closing(self.con.cursor()) as cursor:
            return cursor.execute("SELECT * FROM vault_metadata ORDER BY tvl DESC NULLS LAST").df()

    def get_latest_leader_fractions(self) -> dict[HexAddress, Percent]:
        """Get the latest leader_fraction for each vault.

        Reads the latest genuine permission snapshot per vault. If that response
        omits leader share, the previous value is not carried forwards. Inferred
        legacy permissions never supply capacity. This catalogue lookup does not
        certify freshness; consumers must check the separate capacity clock.

        :return:
            Dict mapping lowercased vault address to leader_fraction.
        """
        with closing(self.con.cursor()) as cursor:
            rows = cursor.execute("""
                SELECT vault_address, leader_fraction FROM (
                    SELECT vault_address, leader_fraction,
                           row_number() OVER (PARTITION BY vault_address ORDER BY permission_observed_at DESC, observation_id DESC) AS position
                    FROM vault_permission_observations
                    WHERE record_kind='observation' AND permission_observed_at IS NOT NULL
                      AND provenance IN ('observed','observed_unknown','restored')
                ) WHERE position=1 AND leader_fraction IS NOT NULL
            """).fetchall()
            return {row[0]: row[1] for row in rows}

    # ── Price query methods (use price_table / time_column) ──

    def get_vault_count(self) -> int:
        """Get the number of unique vaults with price data."""
        with closing(self.con.cursor()) as cursor:
            return cursor.execute(f"SELECT COUNT(DISTINCT vault_address) FROM {self.price_table}").fetchone()[0]

    def get_recently_tracked_addresses(self, within_days: int = 4) -> set[str]:
        """Return vault addresses with price data within the last *within_days* days.

        Uses a pure date cutoff so that whole-day semantics are preserved
        for the daily table (DATE column) and behave identically for the
        HF table (TIMESTAMP column — DuckDB casts DATE to midnight).

        :param within_days:
            Number of days to look back from today.
        :return:
            Set of lowercased vault addresses.
        """
        with closing(self.con.cursor()) as cursor:
            cutoff = native_datetime_utc_now().date() - datetime.timedelta(days=within_days)
            rows = cursor.execute(
                f"SELECT DISTINCT vault_address FROM {self.price_table} WHERE {self.time_column} >= ?",
                [cutoff],
            ).fetchall()
            return {r[0] for r in rows}

    def get_all_tracked_addresses(self) -> set[str]:
        """Return all vault addresses that have any price data.

        :return:
            Set of lowercased vault addresses.
        """
        with closing(self.con.cursor()) as cursor:
            rows = cursor.execute(
                f"SELECT DISTINCT vault_address FROM {self.price_table}",
            ).fetchall()
            return {r[0] for r in rows}

    # ── Lifecycle methods ──

    def mark_vaults_disappeared(self, known_addresses: set[HexAddress]) -> None:
        """Set TVL to zero for vaults that disappeared from the API.

        Also writes tombstone price rows so downstream consumers see a
        fresh row reflecting removal.

        :param known_addresses:
            Lowercased vault addresses currently present in the bulk API.
        """
        with closing(self.con.cursor()) as cursor:
            existing = cursor.execute("SELECT vault_address FROM vault_metadata").fetchall()

            disappeared = [(0.0, addr[0]) for addr in existing if addr[0] not in known_addresses]

            if not disappeared:
                return

            cursor.executemany(
                """
                UPDATE vault_metadata
                SET tvl = ?,
                    last_updated = CURRENT_TIMESTAMP
                WHERE vault_address = ?
                """,
                disappeared,
            )

            disappeared_addrs = [addr for _, addr in disappeared]
            tombstone_count = self._write_tombstone_rows(disappeared_addrs)
            if tombstone_count:
                logger.info(
                    "Wrote %d tombstone price rows (TVL=0) for disappeared vaults",
                    tombstone_count,
                )

    def tombstone_stale_vaults(
        self,
        known_api_addresses: set[str],
        wind_down_days: int = 4,
    ) -> int:
        """Write tombstone rows for vaults whose wind-down window has expired.

        A vault is eligible for tombstoning when:

        1. It has existing price data in the database
        2. It is NOT present in the current bulk API listing
        3. Its most recent price row is older than ``wind_down_days``
        4. It does not already have a tombstone row

        The tombstone carries forward the last known share_price and
        cumulative_pnl so return calculations are not distorted.

        :param known_api_addresses:
            Vaults still in the API (never tombstoned).
        :param wind_down_days:
            Days after last price row before tombstoning.
        :return:
            Number of tombstone rows written.
        """
        # Use pure date cutoff so whole-day semantics match the original
        # daily pipeline.  DuckDB casts DATE to midnight for TIMESTAMP
        # comparisons, so this works for both table types.
        with closing(self.con.cursor()) as cursor:
            cutoff = native_datetime_utc_now().date() - datetime.timedelta(days=wind_down_days)

            candidates = cursor.execute(
                f"""
                SELECT vault_address
                FROM {self.price_table}
                WHERE vault_address NOT IN (
                    SELECT DISTINCT vault_address
                    FROM {self.price_table}
                    WHERE data_source = 'tombstone'
                )
                GROUP BY vault_address
                HAVING MAX({self.time_column}) < ?
                """,
                [cutoff],
            ).fetchall()

            eligible = [addr for (addr,) in candidates if addr not in known_api_addresses]

            count = self._write_tombstone_rows(eligible)
            if count:
                logger.info(
                    "Wrote %d tombstone price rows for vaults that fell out of the pipeline",
                    count,
                )
            return count

    def _write_tombstone_rows(self, vault_addresses: list[HexAddress]) -> int:
        """Write tombstone price rows for the given vaults.

        Carries forward the last known share_price and cumulative_pnl.
        Must be overridden by subclasses to create the correct row type
        and call the correct upsert method.

        :param vault_addresses:
            Lowercased vault addresses to tombstone.
        :return:
            Number of tombstone rows written.
        """
        raise NotImplementedError

    def _get_last_price_row(self, vault_address: HexAddress) -> tuple | None:
        """Fetch the last (share_price, cumulative_pnl) for a vault.

        Used by subclass ``_write_tombstone_rows()`` implementations.
        """
        with closing(self.con.cursor()) as cursor:
            return cursor.execute(
                f"""
                SELECT share_price, cumulative_pnl
                FROM {self.price_table}
                WHERE vault_address = ?
                ORDER BY {self.time_column} DESC
                LIMIT 1
                """,
                [vault_address],
            ).fetchone()

    # ── Persistence ──

    def save(self) -> None:
        """Flush pending writes to disk."""
        if self.con:
            self.con.execute("CHECKPOINT")

    def close(self) -> None:
        """Close the database connection."""
        if self.con:
            self.con.close()
            self.con = None
