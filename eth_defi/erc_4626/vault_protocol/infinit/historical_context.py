"""Persistent Infinit API evidence and indexed share supply.

The store owns only ``infinit_*`` tables in the shared contextual database.
It uses unconstrained staging and joins, never ART-backed uniqueness indexes.
"""

import datetime
import hashlib
import logging
import zlib
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.vault_protocol.infinit.api import InfinitHistory, InfinitPricePoint, fetch_infinit_history
from eth_defi.erc_4626.vault_protocol.infinit.constants import INFINIT_CHAIN_ID, INFINIT_MANAGER_DEPLOYMENT_BLOCK, InfinitProduct
from eth_defi.erc_4626.vault_protocol.infinit.events import InfinitEvent, fetch_infinit_events
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.hypersync.hypersync_timestamp import fetch_block_timestamps_using_hypersync_cached
from eth_defi.hypersync.session import ThrottledHypersyncClient
from eth_defi.vault.vaultdb import get_pipeline_data_dir

logger = logging.getLogger(__name__)


class InfinitHistoryCoverageError(ValueError):
    """Existing prices need source-context restoration before they can be replaced."""


def get_infinit_historical_context_path() -> Path:
    """Resolve the contextual cache beneath the active pipeline directory.

    :return: Shared history DuckDB path.
    """
    return get_pipeline_data_dir() / "vault-historical-context.duckdb"


def fetch_infinit_timestamp_tail(client: ThrottledHypersyncClient, product: InfinitProduct, end_block: int, cache_path: Path = DEFAULT_TIMESTAMP_CACHE_FOLDER) -> None:
    """Extend an already bootstrapped dense cache before a recurring scan.

    Initial historical coverage remains an explicit operator step. Normal
    scans append recent headers through the shared cache-aware Hypersync API;
    they never silently rebuild a missing or sparse historical cache.

    :param client: Configured Hypersync client.
    :param product: Reviewed deployment defining the cache start.
    :param end_block: Exclusive source boundary.
    :param cache_path: Persistent per-chain timestamp directory.
    :return: None; the shared cache may gain recent headers.
    """
    path = cache_path / f"{INFINIT_CHAIN_ID}-timestamps.duckdb"
    if not path.exists():
        raise ValueError(f"Restore or prepopulate the dense Arbitrum timestamp cache: {path}")
    with duckdb.connect(str(path), read_only=True) as connection:
        count, last = connection.execute(
            "SELECT count(*), max(block_number) FROM block_timestamps WHERE block_number >= ? AND block_number < ?",
            [product.deployment_block, end_block],
        ).fetchone()
    if last is None or count != last - product.deployment_block + 1:
        message = "Restore or prepopulate dense Infinit timestamp history before recurring scans"
        raise ValueError(message)
    if last < end_block - 1:
        logger.info("Infinit extending timestamp cache from %s to %s", last + 1, end_block - 1)
        timestamps = fetch_block_timestamps_using_hypersync_cached(
            client=client,
            chain_id=INFINIT_CHAIN_ID,
            start_block=last + 1,
            end_block=end_block - 1,
            cache_path=cache_path,
        )
        timestamps.close()


def map_source_blocks(points: Sequence[InfinitPricePoint], cache_path: Path, start_block: int, end_block: int) -> dict[datetime.datetime, int]:
    """Match source seconds to existing dense Arbitrum timestamps.

    This is a read-only cache operation. Historical repairs must restore or
    prepopulate the dense cache, not bootstrap sparse timestamp requests.

    :param points: Source observations inside the requested source horizon.
    :param cache_path: Shared timestamp-cache directory.
    :param start_block: Inclusive deployment boundary.
    :param end_block: Exclusive source boundary.
    :return: Greatest cached block at each exact source second.
    :raise ValueError: If cache coverage is incomplete or source seconds lack blocks.
    """
    path = cache_path / f"{INFINIT_CHAIN_ID}-timestamps.duckdb"
    if not path.exists():
        raise ValueError(f"Restore or prepopulate the dense Arbitrum timestamp cache: {path}")
    con = duckdb.connect(str(path), read_only=True)
    try:
        count, first, last = con.execute("SELECT count(*), min(block_number), max(block_number) FROM block_timestamps WHERE block_number >= ? AND block_number < ?", [start_block, end_block]).fetchone()
        if first != start_block or last != end_block - 1 or count != end_block - start_block:
            raise ValueError(f"Infinit needs dense Arbitrum timestamps for [{start_block}, {end_block}); restore/prepopulate that cache before backfilling")
        requested = pd.DataFrame({"timestamp": [int(p.timestamp.replace(tzinfo=datetime.UTC).timestamp()) for p in points]})
        if requested.empty:
            msg = "Infinit source has no observations inside the requested horizon"
            raise ValueError(msg)
        con.register("infinit_requested_seconds", requested)
        rows = con.execute("SELECT b.timestamp, max(b.block_number) FROM block_timestamps b JOIN infinit_requested_seconds r ON b.timestamp=r.timestamp WHERE b.block_number >= ? AND b.block_number < ? GROUP BY b.timestamp", [start_block, end_block]).fetchall()
        mapping = {datetime.datetime.fromtimestamp(t, datetime.UTC).replace(tzinfo=None): b for t, b in rows}
        if any(p.timestamp not in mapping for p in points):
            msg = "Infinit source timestamp has no exact indexed Arbitrum block; no approximate block was invented"
            raise ValueError(msg)
        return mapping
    finally:
        con.close()


def validate_infinit_price_coverage(price_path: Path, context_path: Path, product: InfinitProduct, start_block: int, end_block: int) -> None:
    """Refuse a replacement that would discard previously exported observations.

    A restored or lost context database must not let a shortened API response
    erase older Parquet history. Every existing source timestamp in the selected
    replacement range must still be present in the original source context.
    A newly arrived earlier sample may legitimately become the hourly output
    while the previously exported sample remains retained in that context.
    Unreadable existing Parquet propagates as an error.

    :param price_path: Existing shared raw price Parquet, if any.
    :param context_path: Validated contextual-history database.
    :param product: Reviewed vault whose rows will be replaced.
    :param start_block: Inclusive replacement boundary.
    :param end_block: Exclusive replacement boundary.
    :return: None; raises ``ValueError`` if source coverage would shrink.
    """
    if not price_path.exists():
        return
    previous = pq.read_table(
        price_path,
        columns=["timestamp"],
        filters=[("chain", "=", INFINIT_CHAIN_ID), ("address", "=", product.address), ("block_number", ">=", start_block), ("block_number", "<", end_block)],
    )
    with InfinitHistoricalContextStore(context_path, read_only=True) as store:
        retained = {
            row[0]
            for row in store.connection.execute(
                "SELECT timestamp FROM infinit_prices WHERE vault_address=? AND block_number>=? AND block_number<?",
                [product.address, start_block, end_block],
            ).fetchall()
        }
    missing = set(previous.column("timestamp").to_pylist()) - retained
    if missing:
        raise InfinitHistoryCoverageError(f"Infinit context lacks {len(missing)} existing price observations; restore its backup before replacing history")


class InfinitHistoricalContextStore(AbstractContextManager):
    """Retain original API observations and exact onchain supply changes."""

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        """Open the context store and initialise only this protocol's tables.

        :param path: Contextual-history DuckDB path.
        :param read_only: Prevent schema and data changes for reader use.
        """
        if not read_only:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(path), read_only=read_only)
        if not read_only:
            self.connection.execute("SET wal_autocheckpoint = '1TB'")
            self.connection.execute("CREATE TABLE IF NOT EXISTS infinit_prices (vault_address VARCHAR, timestamp TIMESTAMP, price VARCHAR, block_number UBIGINT)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS infinit_events (vault_address VARCHAR, block_number UBIGINT, log_index UINTEGER, timestamp UBIGINT, kind VARCHAR, amount VARCHAR, transaction_hash VARCHAR)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS infinit_sources (vault_address VARCHAR, digest VARCHAR, fetched_at TIMESTAMP, action_id VARCHAR, payload BLOB)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS infinit_scan_state (vault_address VARCHAR, end_block UBIGINT)")

    def __exit__(self, *args: object) -> None:
        """Close the database even if a caller fails.

        :param args: Context-manager exception information.
        """
        self.connection.close()

    def source_start(self, product: InfinitProduct) -> int:
        """Replay a bounded event tail after complete prior coverage.

        :param product: Reviewed product.
        :return: Inclusive source boundary, initially the manager deployment.
        """
        row = self.connection.execute("SELECT max(end_block) FROM infinit_scan_state WHERE vault_address=?", [product.address]).fetchone()
        return max(INFINIT_MANAGER_DEPLOYMENT_BLOCK, int(row[0]) - 256) if row and row[0] else INFINIT_MANAGER_DEPLOYMENT_BLOCK

    def store(self, product: InfinitProduct, history: InfinitHistory, points: Sequence[InfinitPricePoint], blocks: dict[datetime.datetime, int], events: Sequence[InfinitEvent], start: int, end: int, expected_supply: int | None = None) -> int:  # noqa: PLR0917 - one validated source transaction
        """Commit validated source reads, retaining unmatched older prices.

        Replayed event ranges are replaced for reorg recovery. API history is
        append-and-correct: a shortened response cannot erase omitted prices.

        :param product: Reviewed product.
        :param history: Complete validated API history and provenance.
        :param points: Observations inside the EVM source horizon.
        :param blocks: Exact source-second mapping.
        :param events: Complete indexed events for the covered range.
        :param start: Inclusive event replay boundary.
        :param end: Exclusive event replay boundary.
        :param expected_supply: Optional onchain supply assertion before commit.
        :return: Number of source observations processed.
        """
        con = self.connection
        previous_end = con.execute("SELECT max(end_block) FROM infinit_scan_state WHERE vault_address=?", [product.address]).fetchone()[0]
        staged = pd.DataFrame([(product.address, p.timestamp, str(p.price), blocks[p.timestamp]) for p in points], columns=["vault_address", "timestamp", "price", "block_number"])
        con.register("infinit_price_stage", staged)
        digest = hashlib.sha256(history.raw_response.encode()).hexdigest()
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute("DELETE FROM infinit_prices USING infinit_price_stage s WHERE infinit_prices.vault_address=s.vault_address AND infinit_prices.timestamp=s.timestamp")
            con.execute("INSERT INTO infinit_prices SELECT * FROM infinit_price_stage")
            con.execute("DELETE FROM infinit_events WHERE vault_address=? AND block_number>=? AND block_number<?", [product.address, start, end])
            if events:
                con.executemany("INSERT INTO infinit_events VALUES (?,?,?,?,?,?,?)", [(product.address, e.block_number, e.log_index, e.timestamp, e.kind, str(e.amount), e.transaction_hash) for e in events])
            if expected_supply is not None:
                created = con.execute("SELECT count(*) FROM infinit_events WHERE vault_address=? AND kind='created'", [product.address]).fetchone()[0]
                supply = con.execute("SELECT coalesce(sum(CASE WHEN kind='mint' THEN CAST(amount AS HUGEINT) ELSE -CAST(amount AS HUGEINT) END),0) FROM infinit_events WHERE vault_address=? AND kind IN ('mint','burn') AND block_number<?", [product.address, end]).fetchone()[0]
                if created != 1 or supply != expected_supply:
                    raise ValueError(f"Incomplete Infinit supply provenance: creations={created}, indexed={supply}, onchain={expected_supply}")
            con.execute("INSERT INTO infinit_sources SELECT ?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM infinit_sources WHERE vault_address=? AND digest=?)", [product.address, digest, native_datetime_utc_now(), history.action_id, zlib.compress(history.raw_response.encode()), product.address, digest])
            con.execute("DELETE FROM infinit_scan_state WHERE vault_address=?", [product.address])
            con.execute("INSERT INTO infinit_scan_state VALUES (?,?)", [product.address, max(end, previous_end or end)])
            con.execute("COMMIT")
        except (duckdb.Error, ValueError, TypeError):
            con.execute("ROLLBACK")
            raise
        finally:
            con.unregister("infinit_price_stage")
        return len(points)

    def iter_prices(self, product: InfinitProduct, start_block: int, end_block: int) -> Iterator[tuple[datetime.datetime, Decimal, int, int]]:
        """Join the first actual observation per hour to matching receipt supply.

        Retain the source timestamp, including a publication offset from the
        exact hour. No synthetic timestamps or interpolated prices are created.

        :param product: Reviewed product.
        :param start_block: Inclusive historical-reader boundary.
        :param end_block: Exclusive historical-reader boundary.
        :return: Timestamp, original price, source block and raw receipt supply.
        """
        events = self.connection.execute(
            """SELECT block_number, kind, amount FROM infinit_events
            WHERE vault_address=? AND kind IN ('mint','burn')
            ORDER BY block_number,log_index""",
            [product.address],
        ).fetchall()
        rows = self.connection.execute(
            """SELECT timestamp,price,block_number FROM infinit_prices
            WHERE vault_address=? AND block_number>=? AND block_number<?
            QUALIFY row_number() OVER (PARTITION BY date_trunc('hour', timestamp) ORDER BY timestamp)=1
            ORDER BY timestamp""",
            [product.address, start_block, end_block],
        ).fetchall()
        supply, index = 0, 0
        for timestamp, price, block in rows:
            while index < len(events) and events[index][0] <= block:
                _, kind, amount = events[index]
                supply += int(amount) * (1 if kind == "mint" else -1)
                if supply < 0:
                    msg = "Infinit historical supply became negative; incomplete event history"
                    raise ValueError(msg)
                index += 1
            yield timestamp, Decimal(price), block, supply

    def activity_counts(self, product: InfinitProduct) -> dict[str, int]:
        """Read exact activity counts for manual lead migration.

        :param product: Reviewed product.
        :return: Manager event counts by kind.
        """
        return dict(self.connection.execute("SELECT kind,count(*) FROM infinit_events WHERE vault_address=? GROUP BY kind", [product.address]).fetchall())


def fetch_and_store_infinit_history(*, web3: Web3, hypersync_client: ThrottledHypersyncClient, product: InfinitProduct, end_block: int, context_path: Path, timestamp_cache_path: Path = DEFAULT_TIMESTAMP_CACHE_FOLDER) -> int:
    """Prefill source observations before the common historical writer runs.

    All network reads and cache-coverage checks precede the data transaction.
    The end block is exclusive; current source observations beyond it remain
    in the compressed raw response but are not mapped to invented blocks.

    :param web3: Configured Arbitrum connection.
    :param hypersync_client: Configured indexed event client.
    :param product: Reviewed product.
    :param end_block: Exclusive safe EVM head.
    :param context_path: Protocol-owned tables in the shared context file.
    :param timestamp_cache_path: Existing dense per-chain timestamps.
    :return: Number of source observations processed.
    """
    if web3.eth.chain_id != INFINIT_CHAIN_ID:
        msg = "Infinit history requires Arbitrum"
        raise ValueError(msg)
    horizon = datetime.datetime.fromtimestamp(web3.eth.get_block(end_block - 1)["timestamp"], datetime.UTC).replace(tzinfo=None)
    history = fetch_infinit_history(product)
    points = tuple(p for p in history.points if p.timestamp <= horizon)
    blocks = map_source_blocks(points, timestamp_cache_path, product.deployment_block, end_block)
    with InfinitHistoricalContextStore(context_path) as store:
        start = min(store.source_start(product), end_block)
        events = tuple(fetch_infinit_events(hypersync_client, product, end_block, timestamp_cache_path, start))
        receipt = get_deployed_contract(web3, "infinit/TokenVault.json", product.share_token)
        expected_supply = receipt.functions.totalSupply().call(block_identifier=end_block - 1)
        count = store.store(product, history, points, blocks, events, start, end_block, expected_supply=expected_supply)
    logger.info("Infinit stored %d original prices and %d indexed events through block %s", count, len(events), end_block)
    return count
