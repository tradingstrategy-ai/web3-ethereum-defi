"""Vault settlement event storage and price-row annotation helpers.

This module stores sparse asynchronous vault settlement events in a small
DuckDB database. The settlement data is intentionally kept separate from the
raw historical price parquet: price rows remain scanner snapshots, while
settlement events are merged into the in-memory DataFrame before the cleaning
pipeline runs.
"""

import datetime
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from eth_typing import HexAddress
from hexbytes import HexBytes

from eth_defi.compat import native_datetime_utc_now
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)

VAULT_SETTLEMENT_DATABASE_FILENAME = "vault-settlements.duckdb"


def get_default_vault_settlement_database_path() -> Path:
    """Return the default vault settlement DuckDB path.

    The path follows the active pipeline data directory so test and production
    runs can redirect all vault artefacts with ``PIPELINE_DATA_DIR``.

    :return:
        DuckDB file path.
    """
    return get_pipeline_data_dir() / VAULT_SETTLEMENT_DATABASE_FILENAME


@dataclass(slots=True, frozen=True)
class VaultSettlement:
    """One asynchronous vault settlement transaction.

    :param chain_id:
        EVM chain id.
    :param address:
        Vault address.
    :param block_number:
        Settlement block number.
    :param protocol:
        Protocol name, e.g. ``"Lagoon Finance"``.
    :param block_hash:
        Settlement block hash.
    :param timestamp:
        Naive UTC block timestamp.
    :param tx_hash:
        Settlement transaction hash.
    :param event_name:
        Protocol event name, e.g. ``"SettleDeposit"``.
    :param inserted_at:
        Naive UTC timestamp when this row was inserted. ``None`` uses current
        time during database insert.
    """

    chain_id: int
    address: HexAddress | str
    block_number: int
    protocol: str
    block_hash: HexBytes | str
    timestamp: datetime.datetime
    tx_hash: HexBytes | str
    event_name: str = ""
    inserted_at: datetime.datetime | None = None

    def as_db_tuple(self, inserted_at: datetime.datetime) -> tuple:
        """Convert to a DuckDB insert tuple.

        :param inserted_at:
            Insert timestamp to use when this event does not already specify
            one.
        :return:
            Tuple matching the ``vault_settlements`` table schema.
        """
        return (
            self.chain_id,
            str(self.address).lower(),
            self.block_number,
            self.protocol,
            _hex_to_string(self.block_hash),
            self.timestamp,
            _hex_to_string(self.tx_hash),
            self.event_name,
            self.inserted_at or inserted_at,
        )


class VaultSettlementDatabase:
    """Mini DuckDB interface for vault settlement events.

    The schema has no primary key because DuckDB ART indexes have caused
    stability issues elsewhere in this project. Idempotence is handled with a
    delete-then-insert transaction keyed by
    ``(chain_id, address, tx_hash, event_name)``. Different transactions in
    the same block and different settlement events in the same transaction are
    separate rows.

    :param path:
        DuckDB database path.
    """

    def __init__(self, path: Path):
        """Open the database and create the schema if needed.

        :param path:
            DuckDB file path.
        """
        assert isinstance(path, Path), f"Expected Path, got {type(path)}"
        assert not path.is_dir(), f"Expected DuckDB file path, got directory: {path}"

        import duckdb

        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(path))
        self.con.execute("SET wal_autocheckpoint = '1TB'")
        self._init_schema()

    def __del__(self) -> None:
        if hasattr(self, "con") and self.con is not None:
            self.con.close()
            self.con = None

    def _init_schema(self) -> None:
        """Create settlement tables if they do not exist."""
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS vault_settlements (
                chain_id INTEGER NOT NULL,
                address VARCHAR NOT NULL,
                block_number BIGINT NOT NULL,
                protocol VARCHAR NOT NULL,
                block_hash VARCHAR NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                tx_hash VARCHAR NOT NULL,
                event_name VARCHAR,
                inserted_at TIMESTAMP NOT NULL
            )
            """
        )
        existing_columns = {
            row[0]
            for row in self.con.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'vault_settlements'
                """
            ).fetchall()
        }
        if "event_name" not in existing_columns:
            self.con.execute("ALTER TABLE vault_settlements ADD COLUMN event_name VARCHAR")
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS vault_settlement_scan_state (
                chain_id INTEGER NOT NULL,
                address VARCHAR NOT NULL,
                last_scanned_block BIGINT NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
            """
        )

    def close(self) -> None:
        """Close the database connection."""
        if self.con is not None:
            logger.info("Closing vault settlement database at %s", self.path)
            self.con.close()
            self.con = None

    def save(self) -> None:
        """Checkpoint the database."""
        if self.con is not None:
            self.con.execute("CHECKPOINT")

    def upsert_settlements(self, settlements: list[VaultSettlement]) -> int:
        """Insert settlement rows idempotently.

        Existing rows with the same ``(chain_id, address, tx_hash,
        event_name)`` are replaced. This lets protocol readers rescan
        overlapping block ranges without collapsing different settlement
        transactions in the same block or different event logs in the same
        transaction.

        :param settlements:
            Settlement rows to store.
        :return:
            Number of rows inserted.
        """
        if not settlements:
            return 0

        inserted_at = native_datetime_utc_now()
        rows = [settlement.as_db_tuple(inserted_at) for settlement in settlements]
        keys = [(row[0], row[1], row[6], row[7]) for row in rows]

        self.con.execute("BEGIN TRANSACTION")
        try:
            self.con.executemany(
                """
                DELETE FROM vault_settlements
                WHERE chain_id = ? AND address = ? AND tx_hash = ? AND event_name = ?
                """,
                keys,
            )
            self.con.executemany(
                """
                INSERT INTO vault_settlements (
                    chain_id,
                    address,
                    block_number,
                    protocol,
                    block_hash,
                    timestamp,
                    tx_hash,
                    event_name,
                    inserted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self.con.execute("COMMIT")
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        return len(rows)

    def get_settlements(
        self,
        chain_id: int | None = None,
        address: HexAddress | str | None = None,
        protocol: str | None = None,
    ) -> pd.DataFrame:
        """Read settlement rows as a DataFrame.

        :param chain_id:
            Optional chain filter.
        :param address:
            Optional vault address filter.
        :param protocol:
            Optional protocol filter.
        :return:
            DataFrame sorted by ``chain_id``, ``address``, ``timestamp`` and
            ``tx_hash``.
        """
        clauses: list[str] = []
        params: list[object] = []
        if chain_id is not None:
            clauses.append("chain_id = ?")
            params.append(chain_id)
        if address is not None:
            clauses.append("address = ?")
            params.append(str(address).lower())
        if protocol is not None:
            clauses.append("protocol = ?")
            params.append(protocol)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.con.execute(
            f"""
            SELECT
                chain_id,
                address,
                block_number,
                protocol,
                block_hash,
                timestamp,
                tx_hash,
                event_name,
                inserted_at
            FROM vault_settlements
            {where}
            ORDER BY chain_id, address, timestamp, tx_hash
            """,
            params,
        ).fetchdf()

    def get_settlement_count(self) -> int:
        """Return the number of stored settlement rows."""
        result = self.con.execute("SELECT COUNT(*) FROM vault_settlements").fetchone()
        return int(result[0]) if result else 0

    def get_latest_block_number(self, chain_id: int, address: HexAddress | str) -> int | None:
        """Return the latest stored settlement block for a vault.

        :param chain_id:
            Chain id.
        :param address:
            Vault address.
        :return:
            Latest block number, or ``None`` if no settlement is stored.
        """
        result = self.con.execute(
            """
            SELECT MAX(block_number)
            FROM vault_settlements
            WHERE chain_id = ? AND address = ?
            """,
            [chain_id, str(address).lower()],
        ).fetchone()
        return int(result[0]) if result and result[0] is not None else None

    def get_latest_scanned_block_number(self, chain_id: int, address: HexAddress | str) -> int | None:
        """Return the latest successfully scanned settlement block for a vault.

        Settlement events are sparse, so the event table cannot tell whether a
        vault has no new events or whether it has never been scanned. This
        scan-state table records successful empty scans as well.

        :param chain_id:
            Chain id.
        :param address:
            Vault address.
        :return:
            Latest scanned block number, or ``None`` if no scan state exists.
        """
        result = self.con.execute(
            """
            SELECT MAX(last_scanned_block)
            FROM vault_settlement_scan_state
            WHERE chain_id = ? AND address = ?
            """,
            [chain_id, str(address).lower()],
        ).fetchone()
        return int(result[0]) if result and result[0] is not None else None

    def upsert_scan_state(self, scan_states: list[tuple[int, HexAddress | str, int]]) -> int:
        """Update settlement scan watermarks.

        The stored watermark never moves backwards. Forced historical backfills
        may scan old ranges, but they must not erase knowledge of newer ranges
        that already completed successfully.

        :param scan_states:
            Tuples of ``(chain_id, address, last_scanned_block)``.
        :return:
            Number of distinct vault scan-state rows updated.
        """
        if not scan_states:
            return 0

        latest_by_key: dict[tuple[int, str], int] = {}
        for chain_id, address, last_scanned_block in scan_states:
            key = (int(chain_id), str(address).lower())
            latest_by_key[key] = max(latest_by_key.get(key, -1), int(last_scanned_block))

        updated_at = native_datetime_utc_now()
        rows: list[tuple[int, str, int, datetime.datetime]] = []
        for (chain_id, address), last_scanned_block in latest_by_key.items():
            existing_block = self.get_latest_scanned_block_number(chain_id, address)
            if existing_block is not None:
                last_scanned_block = max(last_scanned_block, existing_block)
            rows.append((chain_id, address, last_scanned_block, updated_at))

        keys = [(chain_id, address) for chain_id, address, _last_scanned_block, _updated_at in rows]
        self.con.execute("BEGIN TRANSACTION")
        try:
            self.con.executemany(
                """
                DELETE FROM vault_settlement_scan_state
                WHERE chain_id = ? AND address = ?
                """,
                keys,
            )
            self.con.executemany(
                """
                INSERT INTO vault_settlement_scan_state (
                    chain_id,
                    address,
                    last_scanned_block,
                    updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                rows,
            )
            self.con.execute("COMMIT")
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        return len(rows)


def load_vault_settlements(path: Path | None = None) -> pd.DataFrame:
    """Load settlement rows from DuckDB if the database exists.

    :param path:
        Optional DuckDB path. ``None`` resolves to the default pipeline path.
    :return:
        Settlement DataFrame. Empty when the database does not exist.
    """
    path = path or get_default_vault_settlement_database_path()
    if not path.exists():
        logger.info("Vault settlement database does not exist: %s", path)
        return _create_empty_settlement_dataframe()

    db = VaultSettlementDatabase(path)
    try:
        return db.get_settlements()
    finally:
        db.close()


def checkpoint_vault_settlement_database_if_exists(path: Path | None = None) -> bool:
    """Checkpoint the settlement DuckDB database if it exists.

    DuckDB can keep recent writes in a WAL file while a connection is open.
    Backup and export code copies only ``vault-settlements.duckdb``, so make
    sure the main database file is self-contained before copying it.

    :param path:
        Optional DuckDB path. ``None`` resolves to the default pipeline path.
    :return:
        ``True`` if an existing database was checkpointed, ``False`` if there
        was no database file.
    """
    path = path or get_default_vault_settlement_database_path()
    if not path.exists():
        return False

    db = VaultSettlementDatabase(path)
    try:
        db.save()
        logger.info("Checkpointed vault settlement database at %s", path)
        return True
    finally:
        db.close()


def annotate_prices_with_vault_settlements(
    prices_df: pd.DataFrame,
    settlements_df: pd.DataFrame,
    column_name: str = "vault_settlement_at",
) -> pd.DataFrame:
    """Annotate price rows with settlement timestamps.

    For each ``(chain, address)`` group, a price row receives the latest
    settlement timestamp in ``(previous_price_timestamp, current_timestamp]``.
    The first row receives the latest settlement timestamp up to its timestamp.

    :param prices_df:
        Raw or cleaned vault prices DataFrame. Must contain ``chain`` and
        ``address`` columns. The row timestamp can be either a ``timestamp``
        column or a :class:`~pandas.DatetimeIndex` named ``timestamp``.
    :param settlements_df:
        Settlement DataFrame from :class:`VaultSettlementDatabase`.
    :param column_name:
        Output timestamp column name.
    :return:
        Copy of ``prices_df`` with ``column_name`` populated.
    """
    result = prices_df.copy()
    result[column_name] = pd.NaT
    if result.empty or settlements_df.empty:
        return result

    if "timestamp" not in result.columns and pd.api.types.is_datetime64_any_dtype(result.index):
        result = result.reset_index()
        index_column = result.columns[0]
        if index_column != "timestamp":
            result.rename(columns={index_column: "timestamp"}, inplace=True)

    required_price_columns = {"chain", "address", "timestamp"}
    missing_price_columns = required_price_columns - set(result.columns)
    assert not missing_price_columns, f"Price DataFrame missing columns: {missing_price_columns}"

    required_settlement_columns = {"chain_id", "address", "timestamp"}
    missing_settlement_columns = required_settlement_columns - set(settlements_df.columns)
    assert not missing_settlement_columns, f"Settlement DataFrame missing columns: {missing_settlement_columns}"

    result["address"] = result["address"].astype(str).str.lower()
    result["timestamp"] = pd.to_datetime(result["timestamp"])

    settlements = settlements_df.copy()
    settlements["address"] = settlements["address"].astype(str).str.lower()
    settlements["timestamp"] = pd.to_datetime(settlements["timestamp"])

    for (chain_id, address), row_indexes in result.groupby(["chain", "address"]).groups.items():
        vault_settlements = settlements[(settlements["chain_id"] == chain_id) & (settlements["address"] == address)].sort_values("timestamp")
        if vault_settlements.empty:
            continue

        sorted_rows = result.loc[row_indexes].sort_values("timestamp")
        settlement_timestamps = vault_settlements["timestamp"].to_numpy()
        price_timestamps = sorted_rows["timestamp"].to_numpy()
        previous_price_timestamps = pd.Series(sorted_rows["timestamp"]).shift(1).to_numpy()

        for row_index, price_timestamp, previous_price_timestamp in zip(sorted_rows.index, price_timestamps, previous_price_timestamps, strict=True):
            settlement_pos = settlement_timestamps.searchsorted(price_timestamp, side="right") - 1
            if settlement_pos < 0:
                continue
            settlement_timestamp = settlement_timestamps[settlement_pos]
            if pd.isna(previous_price_timestamp) or settlement_timestamp > previous_price_timestamp:
                result.loc[row_index, column_name] = pd.Timestamp(settlement_timestamp)

    return result


def preserve_vault_settlement_markers(
    raw_prices_df: pd.DataFrame,
    cleaned_prices_df: pd.DataFrame,
    column_name: str = "vault_settlement_at",
) -> pd.DataFrame:
    """Carry settlement markers from annotated raw prices to cleaned prices.

    Cleaning can remove row-level samples, for example inactive lead-time rows
    or rows outside the stablecoin scope. If a vault itself survives cleaning,
    settlement timestamps must not disappear merely because the raw row that
    carried the marker was removed. This helper rebuilds settlement events from
    the annotated raw frame and reapplies normal interval annotation to the
    cleaned frame.

    :param raw_prices_df:
        Raw price DataFrame after ``vault_settlement_at`` has been merged.
    :param cleaned_prices_df:
        Cleaned price DataFrame that should keep or carry forward settlement
        markers.
    :param column_name:
        Settlement marker column name.
    :return:
        Copy of ``cleaned_prices_df`` with settlement markers re-applied.
    """
    result = cleaned_prices_df.copy()
    if column_name not in result.columns:
        result[column_name] = pd.NaT

    if raw_prices_df.empty or result.empty or column_name not in raw_prices_df.columns:
        return result

    required_columns = {"chain", "address", column_name}
    missing_columns = required_columns - set(raw_prices_df.columns)
    assert not missing_columns, f"Raw price DataFrame missing columns: {missing_columns}"

    marker_mask = raw_prices_df[column_name].notna()
    if not marker_mask.any():
        return result

    settlements_df = raw_prices_df.loc[marker_mask, ["chain", "address", column_name]].rename(
        columns={
            "chain": "chain_id",
            column_name: "timestamp",
        }
    )
    settlements_df = settlements_df.drop_duplicates(subset=["chain_id", "address", "timestamp"])
    return annotate_prices_with_vault_settlements(result, settlements_df, column_name=column_name)


def merge_vault_settlement_data(
    prices_df: pd.DataFrame,
    settlement_db_path: Path | None = None,
    column_name: str = "vault_settlement_at",
) -> pd.DataFrame:
    """Merge settlement data from DuckDB into a price DataFrame.

    :param prices_df:
        Raw or cleaned price DataFrame.
    :param settlement_db_path:
        Optional settlement DuckDB path. Missing database means the column is
        still added but left empty.
    :param column_name:
        Output timestamp column name.
    :return:
        Annotated DataFrame.
    """
    settlements_df = load_vault_settlements(settlement_db_path)
    annotated = annotate_prices_with_vault_settlements(prices_df, settlements_df, column_name=column_name)
    non_null_count = int(annotated[column_name].notna().sum()) if column_name in annotated.columns else 0
    logger.info("Merged vault settlement data: %d price rows annotated", non_null_count)
    return annotated


def merge_vault_settlements_into_raw_prices(
    raw_prices_df: pd.DataFrame,
    settlement_db_path: Path | None = None,
) -> pd.DataFrame:
    """Merge settlement timestamps into the raw price DataFrame.

    This is the narrow entry point used by the price cleaning pipeline. It keeps
    all settlement storage and interval annotation logic in this module, while
    ``wrangle_vault_prices`` only decides where in the cleaning flow the merge
    happens.

    :param raw_prices_df:
        Raw scanner price DataFrame loaded from ``vault-prices-1h.parquet``.
    :param settlement_db_path:
        Optional settlement DuckDB path.
    :return:
        Raw price DataFrame with ``vault_settlement_at`` added.
    """
    return merge_vault_settlement_data(
        raw_prices_df,
        settlement_db_path=settlement_db_path,
        column_name="vault_settlement_at",
    )


def _hex_to_string(value: HexBytes | str) -> str:
    """Convert a hex-like value to a normal lowercase string."""
    if isinstance(value, HexBytes):
        return "0x" + value.hex()
    value = str(value).lower()
    return value if value.startswith("0x") else "0x" + value


def _create_empty_settlement_dataframe() -> pd.DataFrame:
    """Create an empty settlement DataFrame with the storage schema."""
    return pd.DataFrame(
        columns=[
            "chain_id",
            "address",
            "block_number",
            "protocol",
            "block_hash",
            "timestamp",
            "tx_hash",
            "event_name",
            "inserted_at",
        ]
    )
