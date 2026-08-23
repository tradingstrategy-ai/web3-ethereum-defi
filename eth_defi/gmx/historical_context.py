"""Persist GMX V2 liquidity-provider value-and-supply observations.

The shared ``vault-historical-context.duckdb`` file contains one table per
protocol.  GMX stores sparse value-and-supply events here before the common
vault price scanner reads them.  Calculated prices remain in the common
Parquet dataset, not in this cache.
"""

import logging
import re
import time
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import duckdb
from eth_typing import HexAddress
from eth_utils import to_checksum_address
from web3 import Web3

from eth_defi.gmx.constants import GMX_EVENT_EMITTER_ADDRESS
from eth_defi.gmx.historical_oracle import GMXHistoricalSharePriceObservation, fetch_historical_share_price_observations_hypersync
from eth_defi.gmx.vault_catalog import GMX_CHAIN_NAMES_BY_ID
from eth_defi.hypersync.session import ThrottledHypersyncClient
from eth_defi.vault.vaultdb import get_pipeline_data_dir

#: Maximum Hypersync block range committed in one transaction.
GMX_HYPERSYNC_VALUATION_CHUNK_SIZE: int = 10_000_000

#: Maximum bounded retries for a rate-limited Hypersync request.
GMX_HYPERSYNC_RATE_LIMIT_RETRIES: int = 10

#: Direct columns owned by the GMX context table.
GMX_HISTORICAL_CONTEXT_COLUMNS: frozenset[str] = frozenset(
    {
        "chain_id",
        "block_number",
        "block_timestamp",
        "transaction_hash",
        "log_index",
        "product_address",
        "raw_value",
        "raw_supply",
        "event_name",
    }
)

logger = logging.getLogger(__name__)


def get_gmx_historical_context_path() -> Path:
    """Return the shared contextual-reader DuckDB path.

    :return:
        ``vault-historical-context.duckdb`` under the pipeline data directory.
    """

    return get_pipeline_data_dir() / "vault-historical-context.duckdb"


@dataclass(slots=True, frozen=True)
class GMXHistoricalContextPrefillResult:
    """Summarise one incremental GMX observation fetch.

    Records the source range and event counts for scanner diagnostics and
    operator-facing backfill output.
    """

    #: GMX deployment chain.
    chain_id: int

    #: Inclusive source range boundary.
    start_block: int

    #: Exclusive source range boundary.
    end_block: int

    #: Value-and-supply events decoded from the source range.
    observations_fetched: int

    #: Observations inserted or promoted to the current payload schema.
    observations_inserted: int


class GMXHistoricalContextStore(AbstractContextManager):
    """Manage GMX's table in the shared historical-context DuckDB."""

    def __init__(self, path: Path) -> None:
        """Open the cache and create the GMX table when needed.

        :param path:
            Shared contextual-cache DuckDB filename.
        """

        self.path = path
        self.connection = duckdb.connect(str(path))
        self._create_schema()

    def _create_schema(self) -> None:
        """Create the minimal GMX observation table or migrate its legacy layout.

        Early development caches stored GMX fields inside a generic JSON
        envelope. The migration copies only the current deposit-context rows
        into direct protocol-owned columns and replaces the old table in one
        DuckDB transaction.

        :return:
            None.
        """

        existing_columns = {
            row[0]
            for row in self.connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'gmx_historical_context'
                """
            ).fetchall()
        }
        if existing_columns and existing_columns != GMX_HISTORICAL_CONTEXT_COLUMNS:
            if "context_json" not in existing_columns:
                raise RuntimeError(f"Unsupported gmx_historical_context columns: {sorted(existing_columns)}")
            self._migrate_legacy_schema()
            return

        self._create_direct_table("gmx_historical_context")

    def _create_direct_table(self, table_name: str) -> None:
        """Create a direct-column GMX observation table.

        The internal table name is allowlisted because DuckDB identifiers
        cannot be passed as query parameters.

        :param table_name:
            Valid internal table name chosen by this module.
        :return:
            None.
        """

        if table_name not in {"gmx_historical_context", "gmx_historical_context_direct"}:
            raise ValueError(f"Unsupported GMX context table name: {table_name}")

        self.connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                chain_id UINTEGER NOT NULL,
                block_number UBIGINT NOT NULL,
                block_timestamp UBIGINT NOT NULL,
                transaction_hash VARCHAR NOT NULL,
                log_index UINTEGER NOT NULL,
                product_address VARCHAR NOT NULL,
                raw_value UHUGEINT NOT NULL,
                raw_supply UHUGEINT NOT NULL,
                event_name VARCHAR NOT NULL,
                PRIMARY KEY (chain_id, transaction_hash, log_index)
            )
            """
        )

    def _migrate_legacy_schema(self) -> None:
        """Replace the legacy generic JSON envelope with direct GMX columns.

        The replacement is transactional, so an interrupted or invalid legacy
        cache leaves the original table available for operator recovery.

        :return:
            None.
        """

        self.connection.execute("BEGIN TRANSACTION")
        try:
            self._create_direct_table("gmx_historical_context_direct")
            self.connection.execute(
                """
                INSERT INTO gmx_historical_context_direct
                SELECT chain_id,
                       sample_block_number,
                       CAST(json_extract_string(context_json, '$.block_timestamp') AS UBIGINT),
                       lower(json_extract_string(context_json, '$.transaction_hash')),
                       CAST(json_extract_string(context_json, '$.log_index') AS UINTEGER),
                       lower(json_extract_string(context_json, '$.product_address')),
                       CAST(json_extract_string(context_json, '$.raw_value') AS UHUGEINT),
                       CAST(json_extract_string(context_json, '$.raw_supply') AS UHUGEINT),
                       json_extract_string(context_json, '$.event_name')
                FROM gmx_historical_context
                WHERE valuation_context = 'lp_share_price' AND schema_version = 3
                """
            )
            self.connection.execute("DROP TABLE gmx_historical_context")
            self.connection.execute("ALTER TABLE gmx_historical_context_direct RENAME TO gmx_historical_context")
        except duckdb.Error:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def insert_share_price(self, observation: GMXHistoricalSharePriceObservation) -> bool:
        """Persist one GM or GLV observation idempotently.

        The chain transaction hash and log index identify the source event.
        Repeating an identical event is a no-op; conflicting values are rejected.

        :param observation:
            Canonical onchain value-and-supply event.
        :return:
            ``True`` when inserted and ``False`` for an identical retry.
        :raises ValueError:
            If an existing observation has a different payload.
        """

        values = (
            observation.chain_id,
            observation.block_number,
            observation.block_timestamp,
            observation.transaction_hash.lower(),
            observation.log_index,
            observation.product_address.lower(),
            observation.raw_value,
            observation.raw_supply,
            observation.event_name,
        )
        existing = self.connection.execute(
            """
            SELECT chain_id, block_number, block_timestamp, transaction_hash,
                   log_index, product_address, raw_value, raw_supply, event_name
            FROM gmx_historical_context
            WHERE chain_id = ? AND transaction_hash = ? AND log_index = ?
            """,
            (observation.chain_id, observation.transaction_hash.lower(), observation.log_index),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != values:
                key = (observation.chain_id, observation.transaction_hash.lower(), observation.log_index)
                raise ValueError(f"GMX historical share-price conflict for {key}")
            return False
        self.connection.execute("INSERT INTO gmx_historical_context VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
        return True

    def iter_share_prices(
        self,
        *,
        chain_id: int,
        product_address: HexAddress,
        start_block: int,
        end_block: int,
        step: int,
    ) -> Iterable[GMXHistoricalSharePriceObservation]:
        """Yield at most one observation per common block bucket.

        :param chain_id:
            GMX deployment chain.
        :param product_address:
            GM market token or GLV share token.
        :param start_block:
            Inclusive archive boundary.
        :param end_block:
            Exclusive archive boundary.
        :param step:
            Positive common price-scan bucket width.
        :return:
            Verified observations ordered by block and log index.
        """

        assert step > 0
        rows = self.connection.execute(
            """
            SELECT block_number, block_timestamp, transaction_hash, log_index,
                   product_address, raw_value, raw_supply, event_name
            FROM gmx_historical_context
            WHERE chain_id = ? AND block_number >= ? AND block_number < ?
              AND product_address = ?
            ORDER BY block_number ASC, log_index ASC
            """,
            (chain_id, start_block, end_block, product_address.lower()),
        ).fetchall()
        by_bucket: dict[int, GMXHistoricalSharePriceObservation] = {}
        for block_number, block_timestamp, transaction_hash, log_index, stored_product_address, raw_value, raw_supply, event_name in rows:
            observation = GMXHistoricalSharePriceObservation(
                chain_id=chain_id,
                block_number=int(block_number),
                block_timestamp=int(block_timestamp),
                transaction_hash=transaction_hash,
                log_index=int(log_index),
                product_address=to_checksum_address(stored_product_address),
                raw_value=int(raw_value),
                raw_supply=int(raw_supply),
                event_name=event_name,
            )
            bucket = (observation.block_number - start_block) // step
            previous = by_bucket.get(bucket)
            if previous is None or (observation.block_number, observation.log_index) > (previous.block_number, previous.log_index):
                by_bucket[bucket] = observation
        yield from (by_bucket[bucket] for bucket in sorted(by_bucket))

    def close(self) -> None:
        """Close the underlying DuckDB connection."""

        self.connection.close()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connection on normal and exceptional exits."""

        self.close()


def fetch_and_store_gmx_historical_share_prices(
    *,
    web3: Web3,
    hypersync_client: ThrottledHypersyncClient,
    start_block: int,
    end_block: int,
    context_path: Path | None = None,
    source_chunk_size: int = GMX_HYPERSYNC_VALUATION_CHUNK_SIZE,
    product_addresses: Iterable[HexAddress] | None = None,
) -> GMXHistoricalContextPrefillResult:
    """Cache GM and GLV value-and-supply events.

    Each source chunk is committed before the next Hypersync request, making a
    lifetime backfill restartable.

    :param web3:
        Web3 connection for Arbitrum One or Avalanche.
    :param hypersync_client:
        Configured Hypersync client for the same chain.
    :param start_block:
        Inclusive requested source boundary.
    :param end_block:
        Exclusive requested source boundary.
    :param context_path:
        Optional shared contextual-cache DuckDB path.
    :param source_chunk_size:
        Maximum half-open Hypersync range committed at once.
    :param product_addresses:
        Optional current catalogue addresses to filter at the log level.
    :return:
        Source observation and insertion counts.
    """

    chain_id = web3.eth.chain_id
    chain_name = GMX_CHAIN_NAMES_BY_ID.get(chain_id)
    if chain_name is None:
        raise ValueError(f"GMX historical share prices are unsupported on chain {chain_id}")
    if hypersync_client is None:
        message = "A configured Hypersync client is required for GMX historical observation collection"
        raise RuntimeError(message)
    if not 0 <= start_block < end_block:
        raise ValueError(f"Invalid GMX historical share-price range: [{start_block}, {end_block})")
    if source_chunk_size <= 0:
        raise ValueError(f"GMX source chunk size must be positive, got {source_chunk_size}")
    products = tuple(product_addresses) if product_addresses is not None else None
    path = context_path or get_gmx_historical_context_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    observations_fetched = 0
    observations_inserted = 0
    with GMXHistoricalContextStore(path) as store:
        for chunk_start in range(start_block, end_block, source_chunk_size):
            chunk_end = min(chunk_start + source_chunk_size, end_block)
            for attempt in range(1, GMX_HYPERSYNC_RATE_LIMIT_RETRIES + 1):
                try:
                    observations = fetch_historical_share_price_observations_hypersync(
                        hypersync_client=hypersync_client,
                        web3=web3,
                        chain_id=chain_id,
                        event_emitter_address=to_checksum_address(GMX_EVENT_EMITTER_ADDRESS[chain_name]),
                        start_block=chunk_start,
                        end_block=chunk_end,
                        product_addresses=products,
                    )
                    break
                except RuntimeError as error:
                    if "rate limited by server" not in str(error) or attempt == GMX_HYPERSYNC_RATE_LIMIT_RETRIES:
                        raise
                    match = re.search(r"resets_in=(\d+)s", str(error))
                    delay = min(60, int(match.group(1)) + 2) if match else 35
                    logger.warning(
                        "GMX Hypersync rate limit in blocks %d-%d; retry %d/%d in %d seconds",
                        chunk_start,
                        chunk_end,
                        attempt,
                        GMX_HYPERSYNC_RATE_LIMIT_RETRIES,
                        delay,
                    )
                    time.sleep(delay)
            observations_fetched += len(observations)
            store.connection.execute("BEGIN TRANSACTION")
            try:
                observations_inserted += sum(store.insert_share_price(observation) for observation in observations)
            except (duckdb.Error, ValueError):
                store.connection.execute("ROLLBACK")
                raise
            else:
                store.connection.execute("COMMIT")
            logger.info(
                "GMX historical observation chunk chain %d blocks %d-%d: fetched %d, inserted %d",
                chain_id,
                chunk_start,
                chunk_end,
                len(observations),
                observations_inserted,
            )
    return GMXHistoricalContextPrefillResult(chain_id, start_block, end_block, observations_fetched, observations_inserted)
