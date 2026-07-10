"""Vault settlement event storage and price-row annotation helpers.

This module stores sparse asynchronous vault settlement events in a small
DuckDB database. The settlement data is intentionally kept separate from the
price parquets: raw price rows remain scanner snapshots, while settlement
events are merged into the cleaned in-memory price DataFrame during the
cleaning pipeline.
"""

import datetime
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from eth_typing import HexAddress
from hexbytes import HexBytes

from eth_defi.compat import native_datetime_utc_now
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)

VAULT_SETTLEMENT_DATABASE_FILENAME = "vault-settlements.duckdb"
VAULT_SETTLEMENT_COLUMN = "vault_settlement_at"


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
) -> pd.DataFrame:
    """Annotate cleaned price rows with settlement timestamps.

    For each ``(chain, address)`` group, a price row receives the latest
    settlement timestamp in ``(previous_price_timestamp, current_timestamp]``.
    The first row receives the latest settlement timestamp up to its timestamp.

    The production cleaning pipeline calls this after row-level cleaning, so
    raw price parquet rows remain settlement-free.

    :param prices_df:
        Cleaned vault prices DataFrame. Must contain ``chain``, ``address``
        and ``timestamp`` columns, or use ``timestamp`` as the index.
    :param settlements_df:
        Settlement DataFrame from :class:`VaultSettlementDatabase`.
    :return:
        Copy of ``prices_df`` with ``vault_settlement_at`` populated.
    """
    started_at = time.perf_counter()
    logger.info(
        "Starting vault settlement annotation: %d price rows, %d settlement rows",
        len(prices_df),
        len(settlements_df),
    )
    result = prices_df.copy()
    result[VAULT_SETTLEMENT_COLUMN] = pd.NaT
    if result.empty or settlements_df.empty:
        logger.info(
            "Skipped vault settlement annotation: %d price rows, %d settlement rows",
            len(result),
            len(settlements_df),
        )
        return result

    timestamp_is_index = "timestamp" not in result.columns
    if timestamp_is_index:
        assert result.index.name == "timestamp", "Price DataFrame must have a timestamp column or timestamp index"
        result = result.reset_index()

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

    price_groups = result.groupby(["chain", "address"], sort=False).groups
    settlement_groups = settlements.groupby(["chain_id", "address"], sort=False).groups
    logger.info(
        "Prepared vault settlement annotation groups: %d price rows across %d vaults, %d settlement rows across %d vaults",
        len(result),
        len(price_groups),
        len(settlements),
        len(settlement_groups),
    )

    matched_vault_count = 0
    annotated_row_count = 0
    for (chain_id, address), settlement_indexes in settlement_groups.items():
        row_indexes = price_groups.get((chain_id, address))
        if row_indexes is None:
            continue

        matched_vault_count += 1
        vault_settlements = settlements.loc[settlement_indexes].sort_values("timestamp")
        sorted_rows = result.loc[row_indexes, ["timestamp"]].sort_values("timestamp")
        settlement_timestamps = vault_settlements["timestamp"].to_numpy()
        price_timestamps = sorted_rows["timestamp"].to_numpy()
        previous_price_timestamps = pd.Series(sorted_rows["timestamp"]).shift(1).to_numpy()

        settlement_positions = np.searchsorted(settlement_timestamps, price_timestamps, side="right") - 1
        has_previous_settlement = settlement_positions >= 0
        if not has_previous_settlement.any():
            continue

        candidate_indexes = sorted_rows.index[has_previous_settlement]
        candidate_settlements = settlement_timestamps[settlement_positions[has_previous_settlement]]
        candidate_previous_prices = previous_price_timestamps[has_previous_settlement]
        is_in_price_interval = pd.isna(candidate_previous_prices) | (candidate_settlements > candidate_previous_prices)
        if not is_in_price_interval.any():
            continue

        annotated_indexes = candidate_indexes[is_in_price_interval]
        result.loc[annotated_indexes, VAULT_SETTLEMENT_COLUMN] = pd.to_datetime(candidate_settlements[is_in_price_interval])
        annotated_row_count += len(annotated_indexes)

    logger.info(
        "Vault settlement annotation complete: %d price rows annotated across %d vaults in %.1f seconds",
        annotated_row_count,
        matched_vault_count,
        time.perf_counter() - started_at,
    )
    if timestamp_is_index:
        result.set_index("timestamp", inplace=True)
    return result


def merge_vault_settlements_into_cleaned_prices(
    cleaned_prices_df: pd.DataFrame,
    settlement_db_path: Path | None = None,
) -> pd.DataFrame:
    """Merge settlement timestamps into the cleaned price DataFrame.

    The raw ``vault-prices-1h.parquet`` file is not rewritten with settlement
    markers. Settlement events are stored in ``vault-settlements.duckdb`` and
    applied here to the cleaned in-memory price frame before
    ``cleaned-vault-prices-1h.parquet`` is written.

    :param cleaned_prices_df:
        Cleaned scanner price DataFrame produced by the vault price cleaning
        pipeline.
    :param settlement_db_path:
        Optional settlement DuckDB path.
    :return:
        Cleaned price DataFrame with ``vault_settlement_at`` added.
    """
    settlements_df = load_vault_settlements(settlement_db_path)
    annotated = annotate_prices_with_vault_settlements(cleaned_prices_df, settlements_df)
    non_null_count = int(annotated[VAULT_SETTLEMENT_COLUMN].notna().sum())
    logger.info("Merged vault settlement data: %d price rows annotated", non_null_count)
    return annotated


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
