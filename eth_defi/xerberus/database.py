"""DuckDB persistence for Xerberus risk classification data.

Stores registry entity snapshots, platform vault lists, optional report URLs,
derived daily scores, and sync state. Follows Core3 patterns: no PRIMARY KEY
(DuckDB ART crash workaround), application-level DELETE+INSERT dedupe,
Zstd-compressed JSON payloads, thread lock, and latest storage format.

Default path: ``~/.tradingstrategy/vaults/xerberus/xerberus.duckdb``
"""

import datetime
import json
import logging
import threading
from pathlib import Path

import duckdb
import pandas as pd

from eth_defi.compat import native_datetime_utc_now
from eth_defi.xerberus.constants import XERBERUS_DEFAULT_MAX_SCORE_AGE_DAYS

logger = logging.getLogger(__name__)

#: DuckDB storage version for new Xerberus databases.
XERBERUS_DUCKDB_STORAGE_COMPATIBILITY_VERSION = "latest"


def _normalise_address(address: str | None) -> str | None:
    """Lowercase an EVM address, or return None."""
    if address is None:
        return None
    return str(address).lower()


def _is_fresh(fetched_at: datetime.datetime, max_age_days: int | None) -> bool:
    """Return whether *fetched_at* is within *max_age_days* of now."""
    if max_age_days is None:
        return True
    if fetched_at is None:
        return False
    now = native_datetime_utc_now()
    if fetched_at.tzinfo is not None:
        fetched_at = fetched_at.replace(tzinfo=None)
    return (now - fetched_at) <= datetime.timedelta(days=max_age_days)


class XerberusDatabase:
    """DuckDB database for Xerberus registry and vault risk scores.

    Thread safety: all operations are protected by :py:attr:`_db_lock`.

    Example::

        from pathlib import Path
        from eth_defi.xerberus.database import XerberusDatabase

        db = XerberusDatabase(Path("/tmp/xerberus.duckdb"))
        db.save()
        db.close()
    """

    def __init__(self, path: Path, *, read_only: bool = False):
        """Initialise the database connection.

        :param path:
            Path to the DuckDB file. Parent directories are created if needed
            (unless ``read_only``).
        :param read_only:
            Open existing database read-only (export path). Requires the
            file to exist.
        """
        assert isinstance(path, Path), f"Expected Path for path, got {type(path)}"
        assert not path.is_dir(), f"Expected file path, got directory: {path}"

        self.path = path
        self.read_only = read_only

        if read_only:
            assert path.exists(), f"Xerberus database not found for read-only open: {path}"
            self.con = duckdb.connect(str(path), read_only=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.con = duckdb.connect(
                str(path),
                config={"storage_compatibility_version": XERBERUS_DUCKDB_STORAGE_COMPATIBILITY_VERSION},
            )
            # Defensive: Core3 ART crash workaround pattern
            self.con.execute("SET wal_autocheckpoint = '1TB'")
            self._init_schema(self.con)

        self._db_lock = threading.Lock()

    def __del__(self):
        if hasattr(self, "con") and self.con is not None:
            try:
                self.con.close()
            except Exception:  # noqa: S110 — destructor best-effort
                pass
            self.con = None

    @staticmethod
    def _init_schema(connection: duckdb.DuckDBPyConnection) -> None:
        """Create Xerberus tables without PRIMARY KEY constraints.

        Deduplication is application-level DELETE + INSERT.

        :param connection:
            DuckDB connection for the live database.
        """
        connection.execute("""
            CREATE TABLE IF NOT EXISTS registry_snapshots (
                entity_type VARCHAR NOT NULL,
                entity_id VARCHAR NOT NULL,
                name VARCHAR,
                chain VARCHAR,
                address VARCHAR,
                chain_id INTEGER,
                score DOUBLE,
                fetched_at TIMESTAMP NOT NULL,
                payload VARCHAR USING COMPRESSION 'zstd' NOT NULL
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS vault_list_snapshots (
                platform VARCHAR NOT NULL,
                vault_id VARCHAR NOT NULL,
                name VARCHAR,
                chain VARCHAR,
                address VARCHAR,
                chain_id INTEGER,
                score DOUBLE,
                fetched_at TIMESTAMP NOT NULL,
                payload VARCHAR USING COMPRESSION 'zstd' NOT NULL
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS report_urls (
                chain_id INTEGER NOT NULL,
                address VARCHAR NOT NULL,
                entity_id VARCHAR,
                report_url VARCHAR,
                fetched_at TIMESTAMP NOT NULL,
                error VARCHAR
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS score_daily (
                entity_type VARCHAR NOT NULL,
                entity_id VARCHAR NOT NULL,
                day DATE NOT NULL,
                score DOUBLE,
                fetched_at TIMESTAMP NOT NULL,
                chain_id INTEGER,
                address VARCHAR
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                data_type VARCHAR NOT NULL,
                last_synced TIMESTAMP NOT NULL,
                meta VARCHAR
            )
        """)

    def close(self) -> None:
        """Close the database connection."""
        logger.info("Closing Xerberus database at %s", self.path)
        if self.con is not None:
            self.con.close()
            self.con = None

    def save(self) -> None:
        """Force a checkpoint to persist data to disk."""
        if self.read_only:
            return
        with self._db_lock:
            if self.con is not None:
                self.con.execute("CHECKPOINT")

    # ------------------------------------------------------------------
    # Insert methods
    # ------------------------------------------------------------------

    def insert_registry_snapshot_batch(
        self,
        entities: list[dict],
        fetched_at: datetime.datetime,
    ) -> int:
        """Insert a batch of registry entity snapshots.

        Normalises addresses to lowercase. Skips pool rows missing
        ``chainId`` or ``address`` with a warning.

        :param entities:
            Raw entity dicts from ``/registry/scores``.
        :param fetched_at:
            Poll timestamp (naive UTC).
        :return:
            Number of rows inserted.
        """
        if not entities:
            return 0

        rows: list[tuple] = []
        for raw in entities:
            entity_type = raw.get("type")
            entity_id = raw.get("id")
            if not entity_type or not entity_id:
                logger.warning("Skipping registry entity missing type/id: %s", raw)
                continue

            chain_id = raw.get("chainId")
            address = _normalise_address(raw.get("address"))
            if entity_type == "pool" and (chain_id is None or address is None):
                logger.warning("Skipping pool entity missing chainId/address: %s", entity_id)
                continue

            rows.append(
                (
                    entity_type,
                    entity_id,
                    raw.get("name"),
                    raw.get("chain"),
                    address,
                    int(chain_id) if chain_id is not None else None,
                    raw.get("score"),
                    fetched_at,
                    json.dumps(raw),
                )
            )

        if not rows:
            return 0

        with self._db_lock:
            self.con.executemany(
                """
                INSERT INTO registry_snapshots
                (entity_type, entity_id, name, chain, address, chain_id, score, fetched_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def insert_vault_list_snapshot_batch(
        self,
        platform: str,
        vaults: list[dict],
        fetched_at: datetime.datetime,
    ) -> int:
        """Insert vault list rows for one platform.

        :param platform:
            Platform slug.
        :param vaults:
            Raw vault dicts from ``/vault/list``.
        :param fetched_at:
            Poll timestamp.
        :return:
            Number of rows inserted.
        """
        if not vaults:
            return 0

        rows: list[tuple] = []
        for raw in vaults:
            vault_id = raw.get("id")
            chain_id = raw.get("chainId")
            address = _normalise_address(raw.get("address"))
            if not vault_id or chain_id is None or address is None:
                logger.warning("Skipping vault list row missing id/chainId/address: %s", raw)
                continue
            rows.append(
                (
                    platform,
                    vault_id,
                    raw.get("name"),
                    raw.get("chain"),
                    address,
                    int(chain_id),
                    raw.get("score"),
                    fetched_at,
                    json.dumps(raw),
                )
            )

        if not rows:
            return 0

        with self._db_lock:
            self.con.executemany(
                """
                INSERT INTO vault_list_snapshots
                (platform, vault_id, name, chain, address, chain_id, score, fetched_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def upsert_score_daily_points(
        self,
        points: list[dict],
    ) -> int:
        """Upsert daily scores with DELETE + INSERT on ``(entity_type, entity_id, day)``.

        Each point dict must contain: ``entity_type``, ``entity_id``, ``day``
        (date or datetime), ``score``, ``fetched_at``, and optionally
        ``chain_id`` / ``address``.

        :param points:
            Daily score rows for the current scan.
        :return:
            Number of rows written.
        """
        if not points:
            return 0

        prepared: list[tuple] = []
        for p in points:
            day = p["day"]
            if isinstance(day, datetime.datetime):
                day = day.date()
            address = _normalise_address(p.get("address"))
            prepared.append(
                (
                    p["entity_type"],
                    p["entity_id"],
                    day,
                    p.get("score"),
                    p["fetched_at"],
                    p.get("chain_id"),
                    address,
                )
            )

        with self._db_lock:
            self.con.execute(
                """
                CREATE TEMP TABLE IF NOT EXISTS _tmp_score_daily (
                    entity_type VARCHAR,
                    entity_id VARCHAR,
                    day DATE,
                    score DOUBLE,
                    fetched_at TIMESTAMP,
                    chain_id INTEGER,
                    address VARCHAR
                )
                """
            )
            self.con.execute("DELETE FROM _tmp_score_daily")
            self.con.executemany("INSERT INTO _tmp_score_daily VALUES (?, ?, ?, ?, ?, ?, ?)", prepared)
            self.con.execute(
                """
                DELETE FROM score_daily s
                USING _tmp_score_daily t
                WHERE s.entity_type = t.entity_type
                  AND s.entity_id = t.entity_id
                  AND s.day = t.day
                """
            )
            self.con.execute("INSERT INTO score_daily SELECT * FROM _tmp_score_daily")
        return len(prepared)

    def upsert_report_url(
        self,
        chain_id: int,
        address: str,
        report_url: str | None,
        fetched_at: datetime.datetime,
        entity_id: str | None = None,
        error: str | None = None,
    ) -> None:
        """Store or replace a report URL for ``(chain_id, address)``.

        :param chain_id:
            EIP-155 chain id.
        :param address:
            Vault address (will be lowercased).
        :param report_url:
            Deep link URL, or ``None`` on error.
        :param fetched_at:
            Fetch timestamp.
        :param entity_id:
            Registry pool id when known.
        :param error:
            Error message when fetch failed.
        """
        address = _normalise_address(address)
        assert address is not None
        with self._db_lock:
            self.con.execute(
                "DELETE FROM report_urls WHERE chain_id = ? AND address = ?",
                [chain_id, address],
            )
            self.con.execute(
                """
                INSERT INTO report_urls (chain_id, address, entity_id, report_url, fetched_at, error)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [chain_id, address, entity_id, report_url, fetched_at, error],
            )

    def update_sync_state(self, data_type: str, meta: dict | None = None) -> None:
        """Update sync watermark for a data type.

        :param data_type:
            e.g. ``registry``, ``vault_list:morpho``.
        :param meta:
            Optional metadata dict serialised as JSON.
        """
        now = native_datetime_utc_now()
        meta_str = json.dumps(meta) if meta is not None else None
        with self._db_lock:
            self.con.execute("DELETE FROM sync_state WHERE data_type = ?", [data_type])
            self.con.execute(
                "INSERT INTO sync_state (data_type, last_synced, meta) VALUES (?, ?, ?)",
                [data_type, now, meta_str],
            )

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_latest_registry_entities(
        self,
        entity_type: str | None = None,
        max_age_days: int | None = None,
    ) -> pd.DataFrame:
        """Return the latest snapshot per ``(entity_type, entity_id)``.

        :param entity_type:
            Optional filter (``pool``, ``protocol``, …).
        :param max_age_days:
            Optional age filter applied in Python after the SQL latest-row join.
        :return:
            DataFrame of latest entity rows.
        """
        with self._db_lock:
            if entity_type is None:
                df = self.con.execute(
                    """
                    SELECT r.*
                    FROM registry_snapshots r
                    INNER JOIN (
                        SELECT entity_type, entity_id, MAX(fetched_at) AS max_fetched_at
                        FROM registry_snapshots
                        GROUP BY entity_type, entity_id
                    ) latest
                      ON r.entity_type = latest.entity_type
                     AND r.entity_id = latest.entity_id
                     AND r.fetched_at = latest.max_fetched_at
                    """
                ).df()
            else:
                df = self.con.execute(
                    """
                    SELECT r.*
                    FROM registry_snapshots r
                    INNER JOIN (
                        SELECT entity_type, entity_id, MAX(fetched_at) AS max_fetched_at
                        FROM registry_snapshots
                        WHERE entity_type = ?
                        GROUP BY entity_type, entity_id
                    ) latest
                      ON r.entity_type = latest.entity_type
                     AND r.entity_id = latest.entity_id
                     AND r.fetched_at = latest.max_fetched_at
                    WHERE r.entity_type = ?
                    """,
                    [entity_type, entity_type],
                ).df()

        if max_age_days is not None and len(df) > 0:
            df = df[df["fetched_at"].apply(lambda ts: _is_fresh(ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts, max_age_days))]
        return df

    def get_latest_vault_list(
        self,
        max_age_days: int | None = None,
    ) -> pd.DataFrame:
        """Return the latest vault-list row per ``(chain_id, address)``.

        :param max_age_days:
            Optional age filter.
        :return:
            DataFrame of latest vault list rows.
        """
        with self._db_lock:
            df = self.con.execute(
                """
                SELECT v.*
                FROM vault_list_snapshots v
                INNER JOIN (
                    SELECT chain_id, address, MAX(fetched_at) AS max_fetched_at
                    FROM vault_list_snapshots
                    WHERE chain_id IS NOT NULL AND address IS NOT NULL
                    GROUP BY chain_id, address
                ) latest
                  ON v.chain_id = latest.chain_id
                 AND v.address = latest.address
                 AND v.fetched_at = latest.max_fetched_at
                """
            ).df()

        if max_age_days is not None and len(df) > 0:
            df = df[df["fetched_at"].apply(lambda ts: _is_fresh(ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts, max_age_days))]
        return df

    def get_latest_pool_score(
        self,
        chain_id: int,
        address: str,
        max_age_days: int | None = XERBERUS_DEFAULT_MAX_SCORE_AGE_DAYS,
    ) -> dict | None:
        """Get the latest registry pool score for a vault.

        :param chain_id:
            EIP-155 chain id.
        :param address:
            Vault address (case-insensitive).
        :param max_age_days:
            Max age filter; ``None`` disables.
        :return:
            Dict of column values, or ``None``.
        """
        address = _normalise_address(address)
        with self._db_lock:
            row = self.con.execute(
                """
                SELECT entity_type, entity_id, name, chain, address, chain_id, score, fetched_at
                FROM registry_snapshots
                WHERE entity_type = 'pool' AND chain_id = ? AND address = ?
                ORDER BY fetched_at DESC
                LIMIT 1
                """,
                [chain_id, address],
            ).fetchone()
        if row is None:
            return None
        result = {
            "entity_type": row[0],
            "entity_id": row[1],
            "name": row[2],
            "chain": row[3],
            "address": row[4],
            "chain_id": row[5],
            "score": row[6],
            "fetched_at": row[7],
        }
        if not _is_fresh(result["fetched_at"], max_age_days):
            return None
        return result

    def get_latest_protocol_by_id(
        self,
        entity_id: str,
        max_age_days: int | None = XERBERUS_DEFAULT_MAX_SCORE_AGE_DAYS,
    ) -> dict | None:
        """Get the latest registry protocol snapshot by Xerberus id.

        :param entity_id:
            Xerberus protocol entity id.
        :param max_age_days:
            Max age filter.
        :return:
            Dict of column values, or ``None``.
        """
        with self._db_lock:
            row = self.con.execute(
                """
                SELECT entity_type, entity_id, name, chain, address, chain_id, score, fetched_at, payload
                FROM registry_snapshots
                WHERE entity_type = 'protocol' AND entity_id = ?
                ORDER BY fetched_at DESC
                LIMIT 1
                """,
                [entity_id],
            ).fetchone()
        if row is None:
            return None
        result = {
            "entity_type": row[0],
            "entity_id": row[1],
            "name": row[2],
            "chain": row[3],
            "address": row[4],
            "chain_id": row[5],
            "score": row[6],
            "fetched_at": row[7],
            "payload": row[8],
        }
        if not _is_fresh(result["fetched_at"], max_age_days):
            return None
        return result

    def get_report_url(self, chain_id: int, address: str) -> str | None:
        """Return a cached report URL for ``(chain_id, address)`` if successful.

        :param chain_id:
            EIP-155 chain id.
        :param address:
            Vault address.
        :return:
            URL string or ``None``.
        """
        address = _normalise_address(address)
        with self._db_lock:
            row = self.con.execute(
                """
                SELECT report_url, error
                FROM report_urls
                WHERE chain_id = ? AND address = ?
                ORDER BY fetched_at DESC
                LIMIT 1
                """,
                [chain_id, address],
            ).fetchone()
        if row is None or row[1]:
            return None
        return row[0]

    def get_entity_counts(self) -> dict[str, int]:
        """Return simple table row counts.

        :return:
            Dict of count metrics.
        """
        with self._db_lock:
            registry = self.con.execute("SELECT COUNT(*) FROM registry_snapshots").fetchone()[0]
            pools = self.con.execute("SELECT COUNT(DISTINCT entity_id) FROM registry_snapshots WHERE entity_type = 'pool'").fetchone()[0]
            protocols = self.con.execute("SELECT COUNT(DISTINCT entity_id) FROM registry_snapshots WHERE entity_type = 'protocol'").fetchone()[0]
            vault_list = self.con.execute("SELECT COUNT(*) FROM vault_list_snapshots").fetchone()[0]
            score_daily = self.con.execute("SELECT COUNT(*) FROM score_daily").fetchone()[0]
        return {
            "registry_snapshots": int(registry),
            "distinct_pools": int(pools),
            "distinct_protocols": int(protocols),
            "vault_list_snapshots": int(vault_list),
            "score_daily": int(score_daily),
        }
