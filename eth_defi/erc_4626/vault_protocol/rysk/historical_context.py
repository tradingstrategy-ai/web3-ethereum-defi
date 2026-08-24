"""Persist Rysk Premium epoch-price observations for common vault history.

The Premium app's snapshot stream records all operational events and the two
epoch prices.  Its quoted TVL is intentionally not used as a share valuation:
Rysk's epoch NAV includes marked option liabilities, while the dashboard TVL
is a simplified collateral figure.  The reader publishes final withdrawal PPS
as the exit-equivalent share-price curve and retains deposit PPS for audit.

"""

import hashlib
import json
import logging
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import TracebackType

import duckdb
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.rysk.api import RYSK_FINAL_EPOCH_ACTION, RyskPremiumAPIError, RyskPremiumSnapshot, fetch_rysk_premium_snapshots
from eth_defi.erc_4626.vault_protocol.rysk.constants import RyskPremiumPool
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)

RYSK_HISTORICAL_CONTEXT_COLUMNS = frozenset(
    {
        "chain_id",
        "pool_address",
        "block_number",
        "block_timestamp",
        "transaction_hash",
        "action",
        "epoch",
        "raw_deposit_pps",
        "raw_withdrawal_pps",
        "raw_tvl",
        "source_id",
    }
)


def get_rysk_historical_context_path() -> Path:
    """Return the shared contextual history database used by Rysk Premium.

    Rysk and other contextual readers use protocol-specific tables in one
    pipeline-owned DuckDB file while keeping their schemas independent.

    :return:
        Pipeline ``vault-historical-context.duckdb`` location.
    """

    return get_pipeline_data_dir() / "vault-historical-context.duckdb"


@dataclass(slots=True, frozen=True)
class RyskHistoricalSharePriceObservation:
    """One finalised Rysk Premium withdrawal PPS observation.

    This immutable record is the protocol-specific input converted into the
    common :class:`~eth_defi.vault.base.VaultHistoricalRead` representation.
    """

    #: EVM chain containing the pool.
    chain_id: int
    #: Pool share-token address.
    pool_address: HexAddress
    #: Source EVM block number.
    block_number: int
    #: Source Unix timestamp.
    block_timestamp: int
    #: Source transaction hash.
    transaction_hash: str
    #: Rysk epoch number.
    epoch: int
    #: Collateral-denominated final withdrawal price paid for one share.
    withdrawal_share_price: Decimal


@dataclass(slots=True, frozen=True)
class RyskHistoricalContextPrefillResult:
    """Summarise one Rysk Premium snapshot import.

    Separating fetched and inserted counts makes repeated idempotent refreshes
    observable without treating exact application repeats as new history.
    """

    #: Raw source records fetched from the public Rysk catalogue.
    observations_fetched: int
    #: New raw records persisted after content-level deduplication.
    observations_inserted: int


def _snapshot_source_id(snapshot: RyskPremiumSnapshot) -> str:
    """Create a deterministic content identity without a DuckDB ART index.

    Every source value participates in the fingerprint. Consequently, exact
    repeats deduplicate while distinct corrected records remain available for
    deterministic epoch selection.

    :param snapshot:
        Complete raw Rysk application snapshot.
    :return:
        SHA-256 identity of the complete source record.
    """

    source = {
        "chain_id": snapshot.chain_id,
        "pool": snapshot.pool.lower(),
        "block": snapshot.block_number,
        "timestamp": snapshot.timestamp,
        "tx": snapshot.transaction_hash,
        "action": snapshot.action,
        "epoch": snapshot.epoch,
        "deposit_pps": snapshot.deposit_pps,
        "withdrawal_pps": snapshot.withdrawal_pps,
        "tvl": snapshot.tvl,
    }
    encoded = json.dumps(source, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RyskHistoricalContextStore(AbstractContextManager):
    """Manage Rysk's unconstrained table in shared history DuckDB.

    The table deliberately has no ``PRIMARY KEY`` or ``UNIQUE`` constraint.
    DuckDB ART indexes have corrupted large file-backed history databases under
    Python 3.14. Exact content fingerprints are checked in SQL before insertion;
    the store does not claim to infer conflicts or publication order between
    distinct records.
    """

    def __init__(self, path: Path) -> None:
        """Open the shared cache and validate/create the Rysk table.

        Construction performs the only supported schema migration, widening
        the legacy collateral TVL column without deleting existing rows.

        :param path:
            Shared contextual-history DuckDB path.
        :return:
            None.
        """

        self.path = path
        self.connection = duckdb.connect(str(path))
        self.connection.execute("SET wal_autocheckpoint = '1TB'")
        self._create_schema()

    def _create_schema(self) -> None:
        """Create or migrate the direct-column schema for Rysk source data.

        ``raw_tvl`` uses DuckDB's signed 128-bit integer because the Premium
        API may report token quantities above the 64-bit range. Existing
        early-installation tables are widened in place without changing any
        values.

        :return:
            None.
        """

        existing_columns = {row[0] for row in self.connection.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'rysk_premium_historical_context'").fetchall()}
        if existing_columns and existing_columns != RYSK_HISTORICAL_CONTEXT_COLUMNS:
            raise RuntimeError(f"Unsupported rysk_premium_historical_context columns: {sorted(existing_columns)}")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rysk_premium_historical_context (
                chain_id UINTEGER NOT NULL,
                pool_address VARCHAR NOT NULL,
                block_number UBIGINT NOT NULL,
                block_timestamp UBIGINT NOT NULL,
                transaction_hash VARCHAR NOT NULL,
                action VARCHAR NOT NULL,
                epoch UBIGINT NOT NULL,
                raw_deposit_pps UBIGINT NOT NULL,
                raw_withdrawal_pps UBIGINT NOT NULL,
                raw_tvl HUGEINT,
                source_id VARCHAR NOT NULL
            )
            """
        )
        raw_tvl_type = self.connection.execute("SELECT data_type FROM information_schema.columns WHERE table_name = 'rysk_premium_historical_context' AND column_name = 'raw_tvl'").fetchone()
        if raw_tvl_type and raw_tvl_type[0] == "UBIGINT":
            self.connection.execute("ALTER TABLE rysk_premium_historical_context ALTER raw_tvl SET DATA TYPE HUGEINT")

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        """Close the backing DuckDB connection.

        Transaction ownership stays with the individual write methods; context
        exit only releases the file-backed connection.

        :param exc_type:
            Active exception type, if any.
        :param exc_value:
            Active exception instance, if any.
        :param traceback:
            Active exception traceback, if any.
        :return:
            None.
        """

        self.connection.close()

    def insert_snapshot(self, snapshot: RyskPremiumSnapshot) -> bool:
        """Persist one exact source snapshot, ignoring identical repeats.

        The content fingerprint avoids a DuckDB ART uniqueness index while
        preserving every distinct correction published by the application.

        :param snapshot:
            Rysk Premium public application snapshot.
        :return:
            ``True`` when a new source row was inserted.
        """

        source_id = _snapshot_source_id(snapshot)
        row = (
            snapshot.chain_id,
            snapshot.pool.lower(),
            snapshot.block_number,
            snapshot.timestamp,
            snapshot.transaction_hash,
            snapshot.action,
            snapshot.epoch,
            snapshot.deposit_pps,
            snapshot.withdrawal_pps,
            snapshot.tvl,
        )
        if self.connection.execute("SELECT EXISTS (SELECT 1 FROM rysk_premium_historical_context WHERE source_id = ?)", (source_id,)).fetchone()[0]:
            return False
        self.connection.execute(
            "INSERT INTO rysk_premium_historical_context VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*row, source_id),
        )
        return True

    def insert_snapshots(self, snapshots: Iterable[RyskPremiumSnapshot]) -> tuple[int, int]:
        """Insert a finite source stream transactionally.

        A DuckDB or Rysk API failure rolls back the whole finite batch so a
        partial page sequence is never mistaken for a completed refresh.

        :param snapshots:
            Raw Rysk snapshots to retain.
        :return:
            ``(fetched, inserted)`` counts.
        """

        fetched = inserted = 0
        self.connection.execute("BEGIN TRANSACTION")
        try:
            for snapshot in snapshots:
                fetched += 1
                inserted += int(self.insert_snapshot(snapshot))
        except (duckdb.Error, RyskPremiumAPIError):
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")
        return fetched, inserted

    def iter_finalised_share_prices(
        self,
        *,
        chain_id: int,
        pool_address: HexAddress,
        start_block: int,
        end_block: int,
        collateral_decimals: int,
    ) -> Iterable[RyskHistoricalSharePriceObservation]:
        """Yield final exit-PPS values selected deterministically per epoch.

        The greatest source block wins. Records tied within one block have no
        published update order, so their content fingerprints provide a stable
        tie-break rather than a claim about which record was published last.

        :param chain_id:
            EVM chain id.
        :param pool_address:
            LP share token/pool address.
        :param start_block:
            Inclusive source block bound.
        :param end_block:
            Exclusive source block bound.
        :param collateral_decimals:
            Native precision of the pool's collateral asset. Rysk serialises
            PPS in this precision.
        :return:
            Chronologically ordered final withdrawal-price observations.
        """

        rows = self.connection.execute(
            """
            WITH final_epoch_rows AS (
                SELECT *, row_number() OVER (
                    PARTITION BY chain_id, pool_address, epoch
                    ORDER BY block_number DESC, source_id DESC
                ) AS correction_rank
                FROM rysk_premium_historical_context
                WHERE chain_id = ?
                  AND pool_address = ?
                  AND action = ?
                  AND raw_withdrawal_pps > 0
            )
            SELECT block_number, block_timestamp, transaction_hash, epoch, raw_withdrawal_pps
            FROM final_epoch_rows
            WHERE correction_rank = 1 AND block_number >= ? AND block_number < ?
            ORDER BY block_number, epoch
            """,
            (chain_id, pool_address.lower(), RYSK_FINAL_EPOCH_ACTION, start_block, end_block),
        ).fetchall()
        if collateral_decimals < 0:
            raise ValueError(f"collateral_decimals must be non-negative, got {collateral_decimals}")
        scale = Decimal(10**collateral_decimals)
        for block_number, timestamp, tx_hash, epoch, withdrawal_pps in rows:
            yield RyskHistoricalSharePriceObservation(
                chain_id=chain_id,
                pool_address=pool_address,
                block_number=block_number,
                block_timestamp=timestamp,
                transaction_hash=tx_hash,
                epoch=epoch,
                withdrawal_share_price=Decimal(withdrawal_pps) / scale,
            )


def fetch_and_store_rysk_premium_history(*, pools: Iterable[RyskPremiumPool], context_path: Path) -> RyskHistoricalContextPrefillResult:
    """Fetch and retain Rysk's full published snapshot history for selected pools.

    Each pool and page is logged as it is processed. The application endpoint
    is described by the official `Premium explainer
    <https://docs.rysk.finance/rysk-premium/rysk-premium-explainer>`__.

    :param pools:
        Rysk pools whose public history should be refreshed.
    :param context_path:
        Shared contextual-history DuckDB path.
    :return:
        Aggregate fetched and inserted observation counts.
    """

    context_path.parent.mkdir(parents=True, exist_ok=True)
    fetched = inserted = 0
    with RyskHistoricalContextStore(context_path) as store:
        for pool in pools:
            logger.info("Refreshing Rysk Premium history for %s on chain %d", pool.address, pool.chain_id)
            pool_fetched, pool_inserted = store.insert_snapshots(fetch_rysk_premium_snapshots(pool))
            logger.info("Refreshed Rysk Premium history for %s: %d fetched, %d inserted", pool.address, pool_fetched, pool_inserted)
            fetched += pool_fetched
            inserted += pool_inserted
    return RyskHistoricalContextPrefillResult(fetched, inserted)
