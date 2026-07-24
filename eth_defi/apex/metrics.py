"""DuckDB persistence and scan orchestration for ApeX vault metrics.

The reader stores exact source timestamps without resampling. Historical rows
are append-and-correct: returned timestamps may be corrected, but timestamps
omitted by a later bounded response are never pruned.
"""

# ruff: noqa: EM101

import datetime
import logging
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.apex.config import HistoryMode
from eth_defi.apex.constants import (
    APEX_DEFAULT_HISTORY_DEADLINE,
    APEX_DEFAULT_HISTORY_INTERVAL,
    APEX_DEFAULT_MAX_WORKERS,
    APEX_DEFAULT_RANKING_DEADLINE,
    APEX_METRICS_DATABASE,
    APEX_TERMINAL_STATUS,
)
from eth_defi.apex.session import ApexAPIError, ApexSessionPool
from eth_defi.apex.vault import ApexHistoryPoint, ApexVaultSummary, fetch_stabilised_vaults, fetch_vault_history
from eth_defi.compat import native_datetime_utc_now

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class ApexHistoryFetchResult:
    """Immutable worker result for one history request."""

    #: Platform vault identifier.
    vault_id: str

    #: Parsed history, or ``None`` on failure.
    points: tuple[ApexHistoryPoint, ...] | None

    #: Error text on failure.
    error: str | None


@dataclass(slots=True, frozen=True)
class ApexScanResult:
    """Summary of one completed ApeX scan."""

    #: Common ranking observation timestamp.
    observed_at: datetime.datetime

    #: Complete source vault count before any target filter.
    discovered_vaults: int

    #: Vault count selected for ranking storage.
    selected_vaults: int

    #: Histories selected by the independent maintenance gate.
    attempted_histories: int

    #: Successful history responses, including empty responses.
    successful_histories: int

    #: Failed history responses.
    failed_histories: int


class ApexMetricsDatabase:
    """Single-owner DuckDB database for ApeX vault metadata and time series.

    The three tables use application-enforced logical keys and deliberately
    have no primary or unique constraints. This avoids DuckDB ART indexes on
    affected Python 3.14/macOS ARM64 environments.

    ``vault_metadata`` columns contain one current record per ``vault_id``.
    ``vault_prices`` contains actual naive UTC timestamps, source ``DOUBLE``
    values and the source discriminator. ``history_sync`` tracks response and
    canonical retained-history bounds separately.

    :param path:
        File-backed DuckDB path.
    """

    def __init__(self, path: Path = APEX_METRICS_DATABASE) -> None:
        """Open or create an owner-thread ApeX metrics database.

        The parent directory is created as needed, automatic WAL checkpoints
        are disabled and the forward-compatible schema is initialised.

        :param path:
            File-backed DuckDB path.
        """
        if not isinstance(path, Path):
            raise TypeError(f"Expected Path, got {type(path)}")
        if path.is_dir():
            raise ValueError(f"Expected DuckDB file path, got directory: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._owner_thread_id = threading.get_ident()
        self.con: duckdb.DuckDBPyConnection | None = duckdb.connect(str(path))
        self.con.execute("SET wal_autocheckpoint = '1TB'")
        self._init_schema()

    def _assert_owner(self) -> duckdb.DuckDBPyConnection:
        """Return the connection after enforcing creating-thread ownership."""
        if threading.get_ident() != self._owner_thread_id:
            raise RuntimeError("ApexMetricsDatabase may only be used by its creating thread")
        if self.con is None:
            raise RuntimeError("ApexMetricsDatabase is closed")
        return self.con

    def _init_schema(self) -> None:
        """Create the forward-compatible ART-index-free schema."""
        con = self._assert_owner()
        con.execute("""
            CREATE TABLE IF NOT EXISTS vault_metadata (
                vault_id VARCHAR NOT NULL,
                synthetic_address VARCHAR NOT NULL,
                reported_ethereum_address VARCHAR,
                name VARCHAR NOT NULL,
                description VARCHAR,
                status VARCHAR NOT NULL,
                vault_type VARCHAR,
                created_at TIMESTAMP,
                source_updated_at TIMESTAMP,
                finished_at TIMESTAMP,
                max_amount DOUBLE,
                purchase_fee_rate_raw VARCHAR,
                share_profit_ratio_raw VARCHAR,
                current_nav DOUBLE,
                current_tvl DOUBLE,
                current_share_count DOUBLE,
                first_seen TIMESTAMP NOT NULL,
                last_seen TIMESTAMP NOT NULL,
                missing_since TIMESTAMP
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS vault_prices (
                vault_id VARCHAR NOT NULL,
                synthetic_address VARCHAR NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                share_price DOUBLE,
                total_assets DOUBLE,
                total_supply DOUBLE,
                source VARCHAR NOT NULL,
                source_updated_at TIMESTAMP,
                written_at TIMESTAMP NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS history_sync (
                vault_id VARCHAR NOT NULL,
                latest_attempt_at TIMESTAMP,
                latest_attempt_success BOOLEAN,
                latest_attempt_row_count BIGINT,
                latest_attempt_min_at TIMESTAMP,
                latest_attempt_max_at TIMESTAMP,
                last_successful_attempt_at TIMESTAMP,
                latest_nonempty_success_at TIMESTAMP,
                latest_nonempty_row_count BIGINT,
                latest_nonempty_min_at TIMESTAMP,
                latest_nonempty_max_at TIMESTAMP,
                canonical_history_row_count BIGINT NOT NULL DEFAULT 0,
                canonical_history_min_at TIMESTAMP,
                canonical_history_max_at TIMESTAMP,
                terminal_observed_at TIMESTAMP,
                final_history_sync_at TIMESTAMP,
                missing_observed_at TIMESTAMP,
                final_missing_history_sync_at TIMESTAMP,
                latest_error_text VARCHAR,
                latest_error_at TIMESTAMP
            )
        """)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """Run a write group as one DuckDB transaction.

        Staged data is validated before entering this helper. Any exception or
        process interruption is rolled back before being propagated so the
        connection cannot retain an abandoned open transaction.

        :return:
            Context manager yielding while the transaction is open.
        """
        con = self._assert_owner()
        con.execute("BEGIN TRANSACTION")
        try:
            yield
        except BaseException:
            try:
                con.execute("ROLLBACK")
            finally:
                raise
        else:
            con.execute("COMMIT")

    @staticmethod
    def _rows_as_dicts(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, object]]:
        """Convert the current result set to dictionaries."""
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def _metadata_by_id(self) -> dict[str, dict[str, object]]:
        """Read all logical metadata records."""
        con = self._assert_owner()
        rows = self._rows_as_dicts(con.execute("SELECT * FROM vault_metadata"))
        return {str(row["vault_id"]): row for row in rows}

    def _sync_by_id(self) -> dict[str, dict[str, object]]:
        """Read all logical history state records."""
        con = self._assert_owner()
        rows = self._rows_as_dicts(con.execute("SELECT * FROM history_sync"))
        return {str(row["vault_id"]): row for row in rows}

    @staticmethod
    def _new_sync(vault_id: str) -> dict[str, object]:
        """Create one empty history state record."""
        return {
            "vault_id": vault_id,
            "latest_attempt_at": None,
            "latest_attempt_success": None,
            "latest_attempt_row_count": None,
            "latest_attempt_min_at": None,
            "latest_attempt_max_at": None,
            "last_successful_attempt_at": None,
            "latest_nonempty_success_at": None,
            "latest_nonempty_row_count": None,
            "latest_nonempty_min_at": None,
            "latest_nonempty_max_at": None,
            "canonical_history_row_count": 0,
            "canonical_history_min_at": None,
            "canonical_history_max_at": None,
            "terminal_observed_at": None,
            "final_history_sync_at": None,
            "missing_observed_at": None,
            "final_missing_history_sync_at": None,
            "latest_error_text": None,
            "latest_error_at": None,
        }

    @staticmethod
    def _metadata_values(row: dict[str, object]) -> list[object]:
        """Serialise one metadata row in table column order."""
        columns = (
            "vault_id",
            "synthetic_address",
            "reported_ethereum_address",
            "name",
            "description",
            "status",
            "vault_type",
            "created_at",
            "source_updated_at",
            "finished_at",
            "max_amount",
            "purchase_fee_rate_raw",
            "share_profit_ratio_raw",
            "current_nav",
            "current_tvl",
            "current_share_count",
            "first_seen",
            "last_seen",
            "missing_since",
        )
        return [row[column] for column in columns]

    @staticmethod
    def _sync_values(row: dict[str, object]) -> list[object]:
        """Serialise one history state row in table column order."""
        columns = (
            "vault_id",
            "latest_attempt_at",
            "latest_attempt_success",
            "latest_attempt_row_count",
            "latest_attempt_min_at",
            "latest_attempt_max_at",
            "last_successful_attempt_at",
            "latest_nonempty_success_at",
            "latest_nonempty_row_count",
            "latest_nonempty_min_at",
            "latest_nonempty_max_at",
            "canonical_history_row_count",
            "canonical_history_min_at",
            "canonical_history_max_at",
            "terminal_observed_at",
            "final_history_sync_at",
            "missing_observed_at",
            "final_missing_history_sync_at",
            "latest_error_text",
            "latest_error_at",
        )
        return [row[column] for column in columns]

    def _replace_metadata(self, rows: Iterable[dict[str, object]]) -> None:
        """Replace logical metadata rows inside the caller's transaction."""
        con = self._assert_owner()
        materialised = list(rows)
        if not materialised:
            return
        identifiers = [str(row["vault_id"]) for row in materialised]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Duplicate logical vault_metadata keys in staged batch")
        con.executemany("DELETE FROM vault_metadata WHERE vault_id = ?", [(vault_id,) for vault_id in identifiers])
        con.executemany(
            """
            INSERT INTO vault_metadata VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [self._metadata_values(row) for row in materialised],
        )

    def _replace_sync(self, rows: Iterable[dict[str, object]]) -> None:
        """Replace logical history state rows inside the caller's transaction."""
        con = self._assert_owner()
        materialised = list(rows)
        if not materialised:
            return
        identifiers = [str(row["vault_id"]) for row in materialised]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Duplicate logical history_sync keys in staged batch")
        con.executemany("DELETE FROM history_sync WHERE vault_id = ?", [(vault_id,) for vault_id in identifiers])
        con.executemany(
            "INSERT INTO history_sync VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [self._sync_values(row) for row in materialised],
        )

    def apply_ranking(  # noqa: PLR0914
        self,
        vaults: tuple[ApexVaultSummary, ...],
        observed_at: datetime.datetime,
        *,
        manage_disappearance: bool,
    ) -> None:
        """Atomically store ranking metadata, observations and lifecycle state.

        Every supplied vault is present and selected. An unfiltered scan passes
        ``manage_disappearance=True`` so previously known absent vaults start a
        missing generation. Any supplied present vault clears its old missing
        generation, including during targeted scans.

        :param vaults:
            Selected ranking records from the stabilised second pass.
        :param observed_at:
            Common naive UTC ranking observation timestamp.
        :param manage_disappearance:
            Whether absent known vaults should be marked missing.
        :return:
            None.
        """
        con = self._assert_owner()
        identifiers = [vault.vault_id for vault in vaults]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Duplicate logical vault IDs in ranking batch")
        existing_metadata = self._metadata_by_id()
        existing_sync = self._sync_by_id()
        current_ids = set(identifiers)
        affected_missing_ids = set(existing_metadata) - current_ids if manage_disappearance else set()
        affected_sync_ids = current_ids | affected_missing_ids

        latest_ranking_rows = self._rows_as_dicts(
            con.execute("""
                SELECT vault_id, share_price, total_assets, total_supply, source_updated_at
                FROM vault_prices
                WHERE source = 'ranking_snapshot'
                QUALIFY ROW_NUMBER() OVER (PARTITION BY vault_id ORDER BY timestamp DESC) = 1
            """)
        )
        latest_ranking = {str(row["vault_id"]): row for row in latest_ranking_rows}
        historical_at_observation = {
            str(row[0])
            for row in con.execute(
                "SELECT vault_id FROM vault_prices WHERE timestamp = ? AND source = 'fund_net_values'",
                [observed_at],
            ).fetchall()
        }

        metadata_rows: list[dict[str, object]] = []
        sync_rows = {vault_id: dict(existing_sync.get(vault_id, self._new_sync(vault_id))) for vault_id in affected_sync_ids}
        ranking_rows: list[list[object]] = []

        for vault in vaults:
            old = existing_metadata.get(vault.vault_id)
            old_status = str(old["status"]) if old is not None else None
            metadata_rows.append(
                {
                    "vault_id": vault.vault_id,
                    "synthetic_address": vault.synthetic_address,
                    "reported_ethereum_address": (str(vault.reported_ethereum_address) if vault.reported_ethereum_address is not None else None),
                    "name": vault.name,
                    "description": vault.description,
                    "status": vault.status,
                    "vault_type": vault.vault_type,
                    "created_at": vault.created_at,
                    "source_updated_at": vault.source_updated_at,
                    "finished_at": vault.finished_at,
                    "max_amount": vault.max_amount,
                    "purchase_fee_rate_raw": vault.purchase_fee_rate_raw,
                    "share_profit_ratio_raw": vault.share_profit_ratio_raw,
                    "current_nav": vault.share_price,
                    "current_tvl": vault.tvl,
                    "current_share_count": vault.share_count,
                    "first_seen": old["first_seen"] if old is not None else observed_at,
                    "last_seen": observed_at,
                    "missing_since": None,
                }
            )

            sync = sync_rows[vault.vault_id]
            sync["missing_observed_at"] = None
            sync["final_missing_history_sync_at"] = None
            terminal_transition = vault.status == APEX_TERMINAL_STATUS and old_status != APEX_TERMINAL_STATUS
            if vault.status == APEX_TERMINAL_STATUS:
                if terminal_transition or sync["terminal_observed_at"] is None:
                    sync["terminal_observed_at"] = observed_at
                    sync["final_history_sync_at"] = None
            else:
                sync["terminal_observed_at"] = None
                sync["final_history_sync_at"] = None

            previous = latest_ranking.get(vault.vault_id)
            terminal_changed = previous is None or any(
                (
                    previous["share_price"] != vault.share_price,
                    previous["total_assets"] != vault.tvl,
                    previous["total_supply"] != vault.share_count,
                    previous["source_updated_at"] != vault.source_updated_at,
                )
            )
            should_write = vault.status != APEX_TERMINAL_STATUS or terminal_transition or terminal_changed
            if should_write and vault.vault_id not in historical_at_observation:
                ranking_rows.append(
                    [
                        vault.vault_id,
                        vault.synthetic_address,
                        observed_at,
                        vault.share_price,
                        vault.tvl,
                        vault.share_count,
                        "ranking_snapshot",
                        vault.source_updated_at,
                        observed_at,
                    ]
                )

        for vault_id in affected_missing_ids:
            old = dict(existing_metadata[vault_id])
            if old["missing_since"] is None:
                old["missing_since"] = observed_at
            metadata_rows.append(old)
            sync = sync_rows[vault_id]
            if sync["missing_observed_at"] is None:
                sync["missing_observed_at"] = observed_at
                sync["final_missing_history_sync_at"] = None

        with self._transaction():
            self._replace_metadata(metadata_rows)
            self._replace_sync(sync_rows.values())
            if ranking_rows:
                keys = [(row[0], row[2]) for row in ranking_rows]
                con.executemany(
                    "DELETE FROM vault_prices WHERE vault_id = ? AND timestamp = ? AND source = 'ranking_snapshot'",
                    keys,
                )
                con.executemany("INSERT INTO vault_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", ranking_rows)

    def select_history_candidates(
        self,
        present_vault_ids: set[str],
        now: datetime.datetime,
        *,
        mode: HistoryMode,
        refresh_interval: datetime.timedelta,
        include_missing: bool,
    ) -> tuple[str, ...]:
        """Select histories due under the independent maintenance gate.

        Present, terminal and disappeared vault generations use separate
        persisted success markers so an empty response remains retryable where
        finalisation requires data.

        :param present_vault_ids:
            IDs present in the selected stabilised ranking.
        :param now:
            Naive UTC eligibility timestamp.
        :param mode:
            Incremental, forced refresh or disabled history mode.
        :param refresh_interval:
            Positive non-terminal history refresh interval.
        :param include_missing:
            Whether disappeared vault generations may be selected.
        :return:
            Sorted due vault IDs.
        """
        self._assert_owner()
        if mode == "none":
            return ()
        metadata = self._metadata_by_id()
        sync = self._sync_by_id()
        candidates: list[str] = []
        for vault_id, row in metadata.items():
            is_present = vault_id in present_vault_ids
            is_missing = row["missing_since"] is not None
            if not is_present and not (include_missing and is_missing):
                continue
            state = sync.get(vault_id, self._new_sync(vault_id))
            terminal = row["status"] == APEX_TERMINAL_STATUS
            if is_missing:
                due = state["final_history_sync_at"] is None if terminal else state["final_missing_history_sync_at"] is None
            elif mode == "refresh":
                due = True
            elif terminal:
                due = state["final_history_sync_at"] is None
            else:
                last_success = state["last_successful_attempt_at"]
                due = last_success is None or now - last_success >= refresh_interval
            if due:
                candidates.append(vault_id)
        return tuple(sorted(candidates))

    def apply_history_success(
        self,
        vault_id: str,
        points: tuple[ApexHistoryPoint, ...],
        attempted_at: datetime.datetime,
    ) -> None:
        """Atomically append/correct one history and update its sync state.

        Only timestamps returned by the source are replaced. Omitted existing
        timestamps remain intact, and the canonical retained range is updated
        in the same transaction as attempt and lifecycle state.

        :param vault_id:
            Existing ApeX platform vault ID.
        :param points:
            Fully parsed source history, possibly empty.
        :param attempted_at:
            Naive UTC completion timestamp.
        :return:
            None.
        """
        con = self._assert_owner()
        timestamps = [point.timestamp for point in points]
        if len(timestamps) != len(set(timestamps)):
            raise ValueError(f"Duplicate logical history keys for vault {vault_id}")
        metadata = self._metadata_by_id().get(vault_id)
        if metadata is None:
            raise KeyError(f"ApeX metadata is missing for vault {vault_id}")
        state = dict(self._sync_by_id().get(vault_id, self._new_sync(vault_id)))
        previous_nonempty_count = state["latest_nonempty_row_count"]
        previous_nonempty_min = state["latest_nonempty_min_at"]
        previous_nonempty_max = state["latest_nonempty_max_at"]
        minimum = min(timestamps) if timestamps else None
        maximum = max(timestamps) if timestamps else None

        if points and previous_nonempty_count is not None and len(points) < previous_nonempty_count:
            logger.debug(
                "ApeX history response shrank for %s from %d to %d rows",
                vault_id,
                previous_nonempty_count,
                len(points),
            )
        if points and previous_nonempty_max is not None and maximum < previous_nonempty_max:
            logger.warning(
                "ApeX history maximum timestamp moved backwards for %s: %s to %s",
                vault_id,
                previous_nonempty_max,
                maximum,
            )
        if points and previous_nonempty_min is not None and minimum > previous_nonempty_min:
            logger.info(
                "ApeX history minimum timestamp advanced for %s: %s to %s",
                vault_id,
                previous_nonempty_min,
                minimum,
            )

        with self._transaction():
            if points:
                con.executemany(
                    "DELETE FROM vault_prices WHERE vault_id = ? AND timestamp = ?",
                    [(vault_id, point.timestamp) for point in points],
                )
                con.executemany(
                    "INSERT INTO vault_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            vault_id,
                            f"apex-vault-{vault_id}",
                            point.timestamp,
                            point.net_value,
                            point.total_value,
                            point.total_supply,
                            "fund_net_values",
                            None,
                            attempted_at,
                        )
                        for point in points
                    ],
                )
            canonical = con.execute(
                """
                SELECT COUNT(*), MIN(timestamp), MAX(timestamp)
                FROM vault_prices
                WHERE vault_id = ? AND source = 'fund_net_values'
                """,
                [vault_id],
            ).fetchone()
            state["latest_attempt_at"] = attempted_at
            state["latest_attempt_success"] = True
            state["latest_attempt_row_count"] = len(points)
            state["latest_attempt_min_at"] = minimum
            state["latest_attempt_max_at"] = maximum
            state["last_successful_attempt_at"] = attempted_at
            if points:
                state["latest_nonempty_success_at"] = attempted_at
                state["latest_nonempty_row_count"] = len(points)
                state["latest_nonempty_min_at"] = minimum
                state["latest_nonempty_max_at"] = maximum
            state["canonical_history_row_count"] = canonical[0]
            state["canonical_history_min_at"] = canonical[1]
            state["canonical_history_max_at"] = canonical[2]
            state["latest_error_text"] = None
            state["latest_error_at"] = None
            if points and metadata["status"] == APEX_TERMINAL_STATUS and state["terminal_observed_at"] is not None and state["final_history_sync_at"] is None:
                state["final_history_sync_at"] = attempted_at
            if points and metadata["missing_since"] is not None and metadata["status"] != APEX_TERMINAL_STATUS:
                state["final_missing_history_sync_at"] = attempted_at
            self._replace_sync([state])

    def record_history_error(self, vault_id: str, error: str, attempted_at: datetime.datetime) -> None:
        """Record one retryable history API failure without touching prices.

        The isolated state transaction preserves every retained price row and
        non-empty response diagnostic.

        :param vault_id:
            Existing ApeX platform vault ID.
        :param error:
            Human-readable bounded API failure.
        :param attempted_at:
            Naive UTC failure timestamp.
        :return:
            None.
        """
        state = dict(self._sync_by_id().get(vault_id, self._new_sync(vault_id)))
        state["latest_attempt_at"] = attempted_at
        state["latest_attempt_success"] = False
        state["latest_attempt_row_count"] = None
        state["latest_attempt_min_at"] = None
        state["latest_attempt_max_at"] = None
        state["latest_error_text"] = error
        state["latest_error_at"] = attempted_at
        with self._transaction():
            self._replace_sync([state])

    def checkpoint(self) -> None:
        """Run one explicit file-backed database checkpoint.

        Automatic WAL checkpoints are disabled for this database, so callers
        invoke this once after a complete successful scan.

        :return:
            None.
        """
        self._assert_owner().execute("CHECKPOINT")

    def close(self) -> None:
        """Close the owner-thread database connection.

        The same thread that created the database must close it after all
        writes and the final checkpoint have completed.

        :return:
            None.
        """
        self._assert_owner()
        logger.info("Closing ApeX metrics database at %s", self.path)
        assert self.con is not None
        self.con.close()
        self.con = None

    def get_vault_metadata(self) -> pd.DataFrame:
        """Return current metadata ordered by vault ID.

        :return:
            Dataframe containing all ``vault_metadata`` columns.
        """
        return self._assert_owner().execute("SELECT * FROM vault_metadata ORDER BY vault_id").df()

    def get_price_count(self) -> int:
        """Return the number of retained ApeX price observations.

        This scalar query avoids materialising the complete price history when
        an orchestration caller only needs a progress metric.

        :return:
            Number of rows in ``vault_prices``.
        """
        return int(self._assert_owner().execute("SELECT COUNT(*) FROM vault_prices").fetchone()[0])

    def get_vault_prices(self, vault_id: str | None = None) -> pd.DataFrame:
        """Return actual-timestamp price rows.

        :param vault_id:
            Optional platform vault identifier.
        :return:
            Dataframe containing all ``vault_prices`` columns.
        """
        con = self._assert_owner()
        if vault_id is None:
            return con.execute("SELECT * FROM vault_prices ORDER BY vault_id, timestamp").df()
        return con.execute(
            "SELECT * FROM vault_prices WHERE vault_id = ? ORDER BY timestamp",
            [vault_id],
        ).df()

    def get_history_sync(self) -> pd.DataFrame:
        """Return history maintenance state ordered by vault ID.

        :return:
            Dataframe containing all ``history_sync`` columns.
        """
        return self._assert_owner().execute("SELECT * FROM history_sync ORDER BY vault_id").df()


def _fetch_history_worker(
    session_pool: ApexSessionPool,
    vault_id: str,
    operation_timeout: float,
) -> ApexHistoryFetchResult:
    """Fetch and parse one history without accessing DuckDB."""
    with session_pool.history_worker_scope():
        try:
            points = fetch_vault_history(session_pool, vault_id, operation_timeout=operation_timeout)
            return ApexHistoryFetchResult(vault_id=vault_id, points=points, error=None)
        except ApexAPIError as exc:
            return ApexHistoryFetchResult(vault_id=vault_id, points=None, error=str(exc))


def run_scan(
    session_pool: ApexSessionPool,
    database: ApexMetricsDatabase,
    *,
    vault_ids: tuple[str, ...] | None = None,
    max_workers: int = APEX_DEFAULT_MAX_WORKERS,
    history_mode: HistoryMode = "incremental",
    history_refresh_interval: datetime.timedelta = APEX_DEFAULT_HISTORY_INTERVAL,
    ranking_timeout: float = APEX_DEFAULT_RANKING_DEADLINE,
    history_timeout: float = APEX_DEFAULT_HISTORY_DEADLINE,
) -> ApexScanResult:
    """Run one complete ApeX ranking observation and due history maintenance.

    Ranking is always fetched and validated in full before a target filter is
    applied. The command scheduler owns ranking cadence; this function records
    all selected non-terminal vaults whenever called. Historical maintenance is
    independently gated by persisted success timestamps.

    :param session_pool:
        Configured worker-local HTTP session pool.
    :param database:
        Already-open owner-thread database.
    :param vault_ids:
        Optional exact targeted IDs.
    :param max_workers:
        Threaded history reader count.
    :param history_mode:
        ``incremental``, ``refresh`` or ``none``.
    :param history_refresh_interval:
        Positive independent historical refresh cadence.
    :param ranking_timeout:
        Whole two-pass ranking deadline in seconds.
    :param history_timeout:
        Per-vault history deadline in seconds.
    :return:
        Typed completed-scan summary.
    """
    with session_pool.scan_scope():
        return _run_scan(
            session_pool,
            database,
            vault_ids=vault_ids,
            max_workers=max_workers,
            history_mode=history_mode,
            history_refresh_interval=history_refresh_interval,
            ranking_timeout=ranking_timeout,
            history_timeout=history_timeout,
        )


def _run_scan(
    session_pool: ApexSessionPool,
    database: ApexMetricsDatabase,
    *,
    vault_ids: tuple[str, ...] | None,
    max_workers: int,
    history_mode: HistoryMode,
    history_refresh_interval: datetime.timedelta,
    ranking_timeout: float,
    history_timeout: float,
) -> ApexScanResult:
    """Execute a scan while the caller holds exclusive session-pool ownership.

    The public wrapper prevents another scan from creating or closing sessions
    in the same pool until this complete database and network cycle returns.

    :param session_pool:
        Exclusively owned worker-local HTTP session pool.
    :param database:
        Already-open owner-thread database.
    :param vault_ids:
        Optional exact targeted IDs.
    :param max_workers:
        Threaded history reader count.
    :param history_mode:
        ``incremental``, ``refresh`` or ``none``.
    :param history_refresh_interval:
        Positive independent historical refresh cadence.
    :param ranking_timeout:
        Whole two-pass ranking budget in seconds.
    :param history_timeout:
        Per-vault history budget in seconds.
    :return:
        Typed completed-scan summary.
    """
    if max_workers <= 0:
        raise ValueError("max_workers must be positive")
    if history_mode not in {"incremental", "refresh", "none"}:
        raise ValueError(f"Invalid history mode: {history_mode}")
    if history_refresh_interval.total_seconds() <= 0:
        raise ValueError("history_refresh_interval must be positive")

    all_vaults = fetch_stabilised_vaults(session_pool, operation_timeout=ranking_timeout)
    if not all_vaults and not database.get_vault_metadata().empty:
        raise ApexAPIError("ApeX returned an empty all-vault ranking for a non-empty database; refusing to mark every vault missing")
    by_id = {vault.vault_id: vault for vault in all_vaults}
    if vault_ids is None:
        selected = all_vaults
    else:
        requested = set(vault_ids)
        missing = requested - set(by_id)
        if missing:
            raise ValueError(f"Requested ApeX vault IDs are absent from the complete ranking: {sorted(missing)}")
        selected = tuple(by_id[vault_id] for vault_id in vault_ids)

    observed_at = native_datetime_utc_now()
    database.apply_ranking(selected, observed_at, manage_disappearance=vault_ids is None)
    selected_ids = {vault.vault_id for vault in selected}
    candidates = database.select_history_candidates(
        selected_ids,
        observed_at,
        mode=history_mode,
        refresh_interval=history_refresh_interval,
        include_missing=vault_ids is None,
    )

    results: tuple[ApexHistoryFetchResult, ...] = ()
    if candidates:
        try:
            with Parallel(n_jobs=max_workers, backend="threading", return_as="generator_unordered") as parallel:
                result_iterator = parallel(delayed(_fetch_history_worker)(session_pool, vault_id, history_timeout) for vault_id in candidates)
                results = tuple(tqdm(result_iterator, total=len(candidates), desc="Fetching ApeX vault histories"))
        finally:
            session_pool.close_worker_sessions()
    successful = 0
    failed = 0
    for result in results:
        attempted_at = native_datetime_utc_now()
        if result.points is not None:
            database.apply_history_success(result.vault_id, result.points, attempted_at)
            successful += 1
        else:
            database.record_history_error(result.vault_id, result.error or "Unknown ApeX history error", attempted_at)
            failed += 1
    database.checkpoint()
    return ApexScanResult(
        observed_at=observed_at,
        discovered_vaults=len(all_vaults),
        selected_vaults=len(selected),
        attempted_histories=len(candidates),
        successful_histories=successful,
        failed_histories=failed,
    )
