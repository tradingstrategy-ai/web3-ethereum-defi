"""Persist GMX V2 liquidity-provider value-and-supply observations.

The shared ``vault-historical-context.duckdb`` file contains one table per
protocol.  GMX stores sparse value-and-supply events here before the common
vault price scanner reads them.  Calculated prices remain in the common
Parquet dataset, not in this cache.
"""

import hashlib
import json
import logging
import re
import time
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

import duckdb
from eth_typing import HexAddress
from eth_utils import to_checksum_address
from web3 import Web3

from eth_defi.gmx.constants import GMX_EVENT_EMITTER_ADDRESS
from eth_defi.gmx.historical_oracle import GMXHistoricalSharePriceObservation, fetch_historical_share_price_observations_hypersync
from eth_defi.gmx.vault_catalog import GMX_CHAIN_NAMES_BY_ID
from eth_defi.vault.vaultdb import get_pipeline_data_dir

GMX_HISTORICAL_CONTEXT_SCHEMA_VERSION = 2
GMX_HYPERSYNC_VALUATION_CHUNK_SIZE = 10_000_000
GMX_LP_SHARE_PRICE_VALUATION_CONTEXT = "lp_share_price"
GMX_HYPERSYNC_RATE_LIMIT_RETRIES = 10

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

    :param chain_id:
        GMX deployment chain.
    :param start_block:
        Inclusive source range boundary.
    :param end_block:
        Exclusive source range boundary.
    :param observations_fetched:
        Value-and-supply events decoded from the source range.
    :param observations_inserted:
        New immutable observations written to DuckDB.
    """

    chain_id: int
    start_block: int
    end_block: int
    observations_fetched: int
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
        """Create the GMX observation table.

        The table retains the original generic column names for compatibility
        with existing backfills.  ``context_json`` contains the protocol-owned
        event fields and ``payload_hash`` protects them from silent mutation.
        """

        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS gmx_historical_context (
                chain_id UINTEGER NOT NULL,
                sample_block_number UBIGINT NOT NULL,
                valuation_context VARCHAR NOT NULL,
                source_observation_id VARCHAR NOT NULL,
                token_coverage_hash VARCHAR NOT NULL,
                payload_hash VARCHAR NOT NULL,
                schema_version UINTEGER NOT NULL,
                context_json JSON NOT NULL,
                PRIMARY KEY (
                    chain_id,
                    sample_block_number,
                    valuation_context,
                    source_observation_id,
                    token_coverage_hash
                )
            )
            """
        )

    def insert_share_price(self, observation: GMXHistoricalSharePriceObservation) -> bool:
        """Persist one GM or GLV observation idempotently.

        :param observation:
            Canonical onchain value-and-supply event.
        :return:
            ``True`` when inserted and ``False`` for an identical retry.
        :raises ValueError:
            If an existing observation has a different payload.
        """

        product_address = observation.product_address.lower()
        coverage_hash = hashlib.sha256(product_address.encode("ascii")).hexdigest()
        payload = json.dumps(
            {
                "block_timestamp": observation.block_timestamp,
                "event_name": observation.event_name,
                "log_index": observation.log_index,
                "product_address": product_address,
                "raw_supply": str(observation.raw_supply),
                "raw_value": str(observation.raw_value),
                "transaction_hash": observation.transaction_hash.lower(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        key = (
            observation.chain_id,
            observation.block_number,
            GMX_LP_SHARE_PRICE_VALUATION_CONTEXT,
            observation.source_observation_id,
            coverage_hash,
        )
        existing = self.connection.execute(
            """
            SELECT payload_hash
            FROM gmx_historical_context
            WHERE chain_id = ? AND sample_block_number = ? AND valuation_context = ?
              AND source_observation_id = ? AND token_coverage_hash = ?
            """,
            key,
        ).fetchone()
        if existing is not None:
            if existing[0] != payload_hash:
                raise ValueError(f"GMX historical share-price conflict for {key}")
            return False
        self.connection.execute(
            "INSERT INTO gmx_historical_context VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (*key, payload_hash, GMX_HISTORICAL_CONTEXT_SCHEMA_VERSION, payload),
        )
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
        coverage_hash = hashlib.sha256(product_address.lower().encode("ascii")).hexdigest()
        rows = self.connection.execute(
            """
            SELECT sample_block_number, source_observation_id, payload_hash,
                   schema_version, context_json
            FROM gmx_historical_context
            WHERE chain_id = ? AND sample_block_number >= ?
              AND sample_block_number < ? AND valuation_context = ?
              AND token_coverage_hash = ?
            ORDER BY sample_block_number ASC, source_observation_id ASC
            """,
            (chain_id, start_block, end_block, GMX_LP_SHARE_PRICE_VALUATION_CONTEXT, coverage_hash),
        ).fetchall()
        by_bucket: dict[int, GMXHistoricalSharePriceObservation] = {}
        for sample_block_number, source_observation_id, payload_hash, schema_version, context_json in rows:
            if schema_version != GMX_HISTORICAL_CONTEXT_SCHEMA_VERSION:
                raise ValueError(f"Unknown GMX historical share-price schema version: {schema_version}")
            if hashlib.sha256(context_json.encode("utf-8")).hexdigest() != payload_hash:
                raise ValueError(f"GMX historical share-price payload hash mismatch for {source_observation_id}")
            payload = json.loads(context_json)
            observation = GMXHistoricalSharePriceObservation(
                chain_id=chain_id,
                block_number=int(sample_block_number),
                block_timestamp=int(payload["block_timestamp"]),
                transaction_hash=payload["transaction_hash"],
                log_index=int(payload["log_index"]),
                product_address=to_checksum_address(payload["product_address"]),
                raw_value=int(payload["raw_value"]),
                raw_supply=int(payload["raw_supply"]),
                event_name=payload["event_name"],
            )
            if observation.source_observation_id != source_observation_id:
                raise ValueError(f"GMX historical share-price source mismatch for {source_observation_id}")
            bucket = (observation.block_number - start_block) // step
            previous = by_bucket.get(bucket)
            if previous is None or (observation.block_number, observation.log_index) > (previous.block_number, previous.log_index):
                by_bucket[bucket] = observation
        yield from (by_bucket[bucket] for bucket in sorted(by_bucket))

    def close(self) -> None:
        """Close the underlying DuckDB connection."""

        self.connection.close()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Close the connection on normal and exceptional exits."""

        self.close()


def fetch_and_store_gmx_historical_share_prices(
    *,
    web3: Web3,
    hypersync_client,
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
