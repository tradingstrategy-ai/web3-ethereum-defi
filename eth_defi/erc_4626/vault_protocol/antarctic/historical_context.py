"""Read Antarctic settlements through Hypersync and persist replayable context.

Only subscriptions supply canonical prices. Redemptions retain their execution
amounts for diagnostics. Source interfaces:
https://arbiscan.io/address/0x98a6aEE58699e4f4E13D8d8d0800e4e9cbBcf8dD#code
"""

import asyncio
import datetime
import logging
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import TracebackType

import duckdb
import pandas as pd
import pyarrow as pa
from eth_typing import HexAddress
from eth_utils import keccak
from hexbytes import HexBytes
from tqdm_loggable.auto import tqdm
from web3 import Web3
from web3._utils.events import get_event_data  # noqa: PLC2701

from eth_defi.abi import get_contract
from eth_defi.erc_4626.vault_protocol.antarctic.constants import ANTARCTIC_BY_ADDRESS, ANTARCTIC_CHAIN_ID, AntarcticDeployment
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER, load_timestamp_cache
from eth_defi.hypersync.hypersync_timestamp import fetch_exact_block_timestamps_using_hypersync_cached
from eth_defi.hypersync.session import ThrottledHypersyncClient, open_hypersync_stream
from eth_defi.vault.flow_events import decode_hypersync_int
from eth_defi.vault.vaultdb import get_pipeline_data_dir

try:
    import hypersync
except ImportError:
    hypersync = None

logger = logging.getLogger(__name__)
ANTARCTIC_ADD_TOPIC = "0x" + keccak(text="AddLiquidity(address,uint256,uint256,uint256)").hex()
ANTARCTIC_REMOVE_TOPIC = "0x" + keccak(text="RemoveLiquidity(address,uint256,uint256,uint256)").hex()
ANTARCTIC_REPLAY_BLOCKS = 512
ANTARCTIC_SOURCE_CHUNK_SIZE = 10_000_000


def get_antarctic_historical_context_path() -> Path:
    """Return Antarctic's shared historical context path.

    Other protocols retain their own tables in this file.

    :return: Pipeline context DuckDB path.
    """
    return get_pipeline_data_dir() / "vault-historical-context.duckdb"


@dataclass(slots=True, frozen=True)
class AntarcticSettlement:
    """One settlement with exact execution amounts and source provenance."""

    #: EVM chain identity.
    chain_id: int
    #: LP token identity, not the manager.
    pool_address: HexAddress
    #: Settlement block.
    block_number: int
    #: Canonical source block hash.
    block_hash: str
    #: Settlement timestamp, Unix seconds.
    block_timestamp: int
    #: Source transaction hash.
    transaction_hash: str
    #: Block-wide event index.
    log_index: int
    #: AddLiquidity or RemoveLiquidity.
    kind: str
    #: Investor whose settlement was processed.
    account: HexAddress
    #: USDT raw amount; serialised as decimal text in DuckDB.
    raw_usdt: int
    #: Raw LP shares; serialised as decimal text in DuckDB.
    raw_shares: int
    #: Handler-reported pre-batch raw TVL.
    raw_tvl: int
    #: Original unindexed log bytes for audit/re-decoding.
    data: str

    @property
    def source_id(self) -> str:
        """Identify a log across replay and database imports.

        Block hash is compared separately so a reorg can replace a location.

        :return: Chain, LP token, transaction and log index identity.
        """
        return f"{self.chain_id}:{self.pool_address.lower()}:{self.transaction_hash.lower()}:{self.log_index}"


@dataclass(slots=True, frozen=True)
class AntarcticPrefillResult:
    """Summarise a completed context refresh, including pending price repairs."""

    #: Number of source events read, including overlap.
    observations_fetched: int
    #: New or replaced source records.
    observations_inserted: int
    #: Earliest outstanding canonical rewrite per selected pool.
    repair_from_blocks: Mapping[HexAddress, int]


def decode_antarctic_settlement(log: object, deployment: AntarcticDeployment) -> AntarcticSettlement:
    """Decode a Hypersync log with its verified manager interface.

    Reject source mismatches and non-positive subscription denominators. Request
    events are deliberately excluded by the query and are rejected here too.

    :param log: Hypersync log with source fields selected by the collector.
    :param deployment: Reviewed manager and LP token routing.
    :return: Exact settlement, awaiting cached block timestamp resolution.
    """
    if log.address.lower() != deployment.manager:
        msg = "Antarctic settlement manager mismatch"
        raise ValueError(msg)
    topic = log.topics[0].lower()
    kind = {ANTARCTIC_ADD_TOPIC: "AddLiquidity", ANTARCTIC_REMOVE_TOPIC: "RemoveLiquidity"}.get(topic)
    if kind is None:
        msg = "Not an Antarctic settlement event"
        raise ValueError(msg)
    contract = get_contract(Web3(), f"antarctic/{deployment.product.upper()}Manager.json")
    event = getattr(contract.events, kind)()
    decoded = get_event_data(
        Web3().codec,
        event.abi,
        {
            "address": Web3.to_checksum_address(log.address),
            "topics": [HexBytes(t) for t in log.topics if t is not None],
            "data": HexBytes(log.data),
            "blockNumber": decode_hypersync_int(log.block_number),
            "transactionHash": HexBytes(log.transaction_hash),
            "transactionIndex": decode_hypersync_int(log.transaction_index),
            "logIndex": decode_hypersync_int(log.log_index),
            "blockHash": HexBytes(log.block_hash),
        },
    )
    args = decoded["args"]
    usdt, shares, tvl = args["usdtAmount"], args[f"{deployment.product}Amount"], args["TVL"]
    if shares <= 0 or (kind == "AddLiquidity" and usdt <= 0):
        raise ValueError(f"Non-positive Antarctic settlement amounts at {log.transaction_hash}:{log.log_index}")
    return AntarcticSettlement(ANTARCTIC_CHAIN_ID, deployment.address, decoded["blockNumber"], HexBytes(log.block_hash).to_0x_hex(), 0, HexBytes(log.transaction_hash).to_0x_hex(), decoded["logIndex"], kind, HexAddress(args["account"].lower()), usdt, shares, tvl, HexBytes(log.data).to_0x_hex())


class AntarcticHistoricalContextStore(AbstractContextManager):
    """Persist exact settlement history and successful range cursors.

    File-backed bulk tables intentionally have no ART primary/unique indexes.
    A complete overlap replacement removes orphaned logs, including removed-only
    reorgs. Pending price repairs survive a subsequent writer failure.
    """

    def __init__(self, path: Path) -> None:
        """Open the shared store without altering foreign protocol tables.

        :param path: Shared context DuckDB path.
        :return: None.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(path))
        self.connection.execute("SET wal_autocheckpoint = '1TB'")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS antarctic_settlements (
            chain_id UINTEGER, pool_address VARCHAR, block_number UBIGINT,
            block_hash VARCHAR, block_timestamp UBIGINT, transaction_hash VARCHAR,
            log_index UINTEGER, kind VARCHAR, account VARCHAR, raw_usdt VARCHAR,
            raw_shares VARCHAR, raw_tvl VARCHAR, data VARCHAR, source_id VARCHAR)""")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS antarctic_cursors (
            chain_id UINTEGER, pool_address VARCHAR, end_block UBIGINT, repair_from UBIGINT)""")

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        """Close the file-backed connection on success or failure.

        :param exc_type: Active exception type.
        :param exc_value: Active exception value.
        :param traceback: Active traceback.
        :return: None.
        """
        self.connection.close()

    def fetch_cursor(self, address: HexAddress) -> tuple[int | None, int | None]:
        """Read successful range coverage and outstanding price repair.

        Empty successful ranges have cursors too, avoiding repeated full scans.

        :param address: Reviewed LP token address.
        :return: Exclusive completed block and earliest pending price repair.
        """
        row = self.connection.execute("SELECT end_block, repair_from FROM antarctic_cursors WHERE chain_id=? AND pool_address=?", [ANTARCTIC_CHAIN_ID, address.lower()]).fetchone()
        return row if row else (None, None)

    def replace_range(self, address: HexAddress, start_block: int, end_block: int, observations: Iterator[AntarcticSettlement]) -> tuple[int, int]:
        """Commit a complete source range and cursor transactionally.

        Hash joins compare exact staged records before replacing the target
        interval. All validation completes before changing persistent tables.

        :param address: LP identity to replace, never a chain-wide scope.
        :param start_block: Inclusive complete source boundary.
        :param end_block: Exclusive complete source boundary.
        :param observations: Decoded, timestamp-resolved settlements.
        :return: Fetched and inserted/replaced counts.
        """
        if address.lower() not in ANTARCTIC_BY_ADDRESS or end_block <= start_block:
            msg = "Invalid Antarctic replacement scope"
            raise ValueError(msg)
        address = HexAddress(address.lower())
        unique = {}
        fetched = 0
        for event in observations:
            fetched += 1
            if event.chain_id != ANTARCTIC_CHAIN_ID or event.pool_address.lower() != address or not start_block <= event.block_number < end_block or event.block_timestamp <= 0 or not event.block_hash:
                msg = "Antarctic source range or identity mismatch"
                raise ValueError(msg)
            if event.source_id in unique and unique[event.source_id] != event:
                msg = "Conflicting Antarctic source records"
                raise ValueError(msg)
            unique[event.source_id] = event
        rows = []
        for event in unique.values():
            row = asdict(event) | {"source_id": event.source_id}
            for name in ("raw_usdt", "raw_shares", "raw_tvl"):
                row[name] = str(row[name])
            rows.append(row)
        connection = self.connection
        connection.execute("CREATE OR REPLACE TEMP TABLE incoming AS SELECT * FROM antarctic_settlements LIMIT 0")
        if rows:
            connection.register("incoming_arrow", pa.Table.from_pylist(rows))
            try:
                connection.execute("INSERT INTO incoming SELECT * FROM incoming_arrow")
            finally:
                connection.unregister("incoming_arrow")
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute("CREATE OR REPLACE TEMP TABLE previous AS SELECT * FROM antarctic_settlements WHERE chain_id=? AND pool_address=? AND block_number>=? AND block_number<?", [ANTARCTIC_CHAIN_ID, address, start_block, end_block])
            inserted = connection.execute("SELECT count(*) FROM (SELECT * FROM incoming EXCEPT SELECT * FROM previous)").fetchone()[0]
            changed_at = connection.execute("""SELECT min(block_number) FROM (
                (SELECT * FROM incoming WHERE kind='AddLiquidity' EXCEPT SELECT * FROM previous WHERE kind='AddLiquidity')
                UNION ALL
                (SELECT * FROM previous WHERE kind='AddLiquidity' EXCEPT SELECT * FROM incoming WHERE kind='AddLiquidity'))""").fetchone()[0]
            old_end, old_repair = self.fetch_cursor(address)
            repairs = [b for b in (old_repair, changed_at) if b is not None]
            connection.execute("DELETE FROM antarctic_settlements WHERE chain_id=? AND pool_address=? AND block_number>=? AND block_number<?", [ANTARCTIC_CHAIN_ID, address, start_block, end_block])
            connection.execute("INSERT INTO antarctic_settlements SELECT * FROM incoming")
            connection.execute("DELETE FROM antarctic_cursors WHERE chain_id=? AND pool_address=?", [ANTARCTIC_CHAIN_ID, address])
            connection.execute("INSERT INTO antarctic_cursors VALUES (?, ?, ?, ?)", [ANTARCTIC_CHAIN_ID, address, max(old_end or end_block, end_block), min(repairs) if repairs else None])
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return fetched, inserted

    def acknowledge_repair(self, address: HexAddress, end_block: int) -> None:
        """Clear a pending repair after a successful complete price rewrite.

        The caller must have written the pool through its completed source end.

        :param address: LP token whose price rewrite succeeded.
        :param end_block: Exclusive successful price writer boundary.
        :return: None.
        """
        cursor, _ = self.fetch_cursor(address)
        if cursor is not None and end_block >= cursor:
            self.connection.execute("UPDATE antarctic_cursors SET repair_from=NULL WHERE chain_id=? AND pool_address=?", [ANTARCTIC_CHAIN_ID, address.lower()])

    def iter_settlements(self, address: HexAddress, start_block: int, end_block: int, *, canonical_only: bool = False) -> Iterator[AntarcticSettlement]:
        """Yield exact source records or one canonical subscription per block.

        Raw context retains every event; common price storage collapses a block
        to the last canonical log, deterministically.

        :param address: LP token identity.
        :param start_block: Inclusive range boundary.
        :param end_block: Exclusive range boundary.
        :param canonical_only: Select last AddLiquidity per block when true.
        :return: Ordered immutable source records.
        """
        extra = "AND kind='AddLiquidity' QUALIFY row_number() OVER (PARTITION BY block_number ORDER BY log_index DESC)=1" if canonical_only else ""
        query = """SELECT * EXCLUDE (source_id) FROM antarctic_settlements
            WHERE chain_id=? AND pool_address=? AND block_number>=? AND block_number<? <canonical_clause>
            ORDER BY block_number, log_index"""
        rows = self.connection.execute(
            query.replace("<canonical_clause>", extra),
            [ANTARCTIC_CHAIN_ID, address.lower(), start_block, end_block],
        ).fetchall()
        for row in rows:
            values = list(row)
            for index in (9, 10, 11):
                values[index] = int(values[index])
            yield AntarcticSettlement(*values)


async def _fetch_source_chunk(client: ThrottledHypersyncClient, deployment: AntarcticDeployment, start_block: int, end_block: int) -> list[AntarcticSettlement]:
    """Read one complete indexed source range with the configured stream.

    Partial results and stream failures never become completed cursor coverage.

    :param client: Configured Arbitrum Hypersync client.
    :param deployment: Reviewed manager/token pair.
    :param start_block: Inclusive source block.
    :param end_block: Exclusive source block.
    :return: Decoded records awaiting cached timestamps.
    """
    assert hypersync is not None, "Install the hypersync extra to read Antarctic history"
    query = hypersync.Query(from_block=start_block, to_block=end_block, logs=[hypersync.LogSelection(address=[deployment.manager], topics=[[ANTARCTIC_ADD_TOPIC, ANTARCTIC_REMOVE_TOPIC]])], field_selection=hypersync.FieldSelection(block=[hypersync.BlockField.NUMBER, hypersync.BlockField.HASH, hypersync.BlockField.TIMESTAMP], log=[hypersync.LogField.BLOCK_NUMBER, hypersync.LogField.BLOCK_HASH, hypersync.LogField.ADDRESS, hypersync.LogField.TRANSACTION_HASH, hypersync.LogField.TRANSACTION_INDEX, hypersync.LogField.LOG_INDEX, hypersync.LogField.TOPIC0, hypersync.LogField.TOPIC1, hypersync.LogField.TOPIC2, hypersync.LogField.TOPIC3, hypersync.LogField.DATA]))
    receiver = await open_hypersync_stream(client, query)
    events = []
    reached = start_block
    headers = {}
    try:
        while True:
            response = await asyncio.wait_for(receiver.recv(), timeout=55)
            if response is None:
                break
            reached = response.next_block
            headers.update({decode_hypersync_int(block.number): (str(block.hash).lower(), decode_hypersync_int(block.timestamp)) for block in response.data.blocks or []})
            events.extend(decode_antarctic_settlement(log, deployment) for log in response.data.logs or [])
            logger.info("Antarctic %s source scanned to block %s: %d settlements", deployment.product, reached, len(events))
        if reached < end_block:
            raise RuntimeError(f"Incomplete Antarctic source range: {reached} < {end_block}")
    finally:
        receiver.close()
        await asyncio.sleep(0.05)
    resolved = []
    for event in events:
        block_hash, timestamp = headers[event.block_number]
        if block_hash != event.block_hash:
            msg = "Antarctic event/header block hash mismatch"
            raise ValueError(msg)
        resolved.append(replace(event, block_timestamp=timestamp))
    return resolved


def fetch_antarctic_settlements(*, hypersync_client: ThrottledHypersyncClient, deployment: AntarcticDeployment, start_block: int, end_block: int, timestamp_cache_path: Path = DEFAULT_TIMESTAMP_CACHE_FOLDER) -> Iterator[AntarcticSettlement]:
    """Fetch exact settlement observations, with cached source timestamps.

    Uses only indexed Hypersync logs and exact block headers, never eth_getLogs
    or a dense timestamp backfill. Reorg block hashes come from the source logs.

    :param hypersync_client: Configured Arbitrum client.
    :param deployment: Reviewed manager/token routing.
    :param start_block: Inclusive source boundary.
    :param end_block: Exclusive source boundary.
    :param timestamp_cache_path: Persistent per-chain timestamp cache root.
    :return: Source observations in block/log order.
    """
    events = []
    with asyncio.Runner() as runner, tqdm(total=max(0, end_block - start_block), desc=f"Antarctic {deployment.product}", unit="block") as progress:
        for start in range(start_block, end_block, ANTARCTIC_SOURCE_CHUNK_SIZE):
            end = min(end_block, start + ANTARCTIC_SOURCE_CHUNK_SIZE)
            events.extend(runner.run(_fetch_source_chunk(hypersync_client, deployment, start, end)))
            progress.update(end - start)
    if not events:
        return
    # Reuse event-associated headers in the persistent timestamp cache. This
    # avoids one additional stream per sparse event and corrects timestamps at
    # replayed reorg heights. The cache-aware helper verifies exact coverage.
    cache = load_timestamp_cache(ANTARCTIC_CHAIN_ID, timestamp_cache_path)
    try:
        cache.import_chain_data(ANTARCTIC_CHAIN_ID, pd.Series({event.block_number: event.block_timestamp for event in events}))
        cache.save()
    finally:
        cache.close()
    stamps = fetch_exact_block_timestamps_using_hypersync_cached(client=hypersync_client, chain_id=ANTARCTIC_CHAIN_ID, block_numbers=sorted({e.block_number for e in events}), cache_path=timestamp_cache_path, display_progress=False)
    try:
        for event in sorted(events, key=lambda e: (e.block_number, e.log_index)):
            timestamp = int(stamps[event.block_number].replace(tzinfo=datetime.UTC).timestamp())
            yield replace(event, block_timestamp=timestamp)
    finally:
        stamps.close()


def fetch_and_store_antarctic_history(*, web3: Web3, hypersync_client: ThrottledHypersyncClient, pool_start_blocks: Mapping[HexAddress, int], end_block: int, context_path: Path, timestamp_cache_path: Path = DEFAULT_TIMESTAMP_CACHE_FOLDER, force_backfill: bool = False) -> AntarcticPrefillResult:
    """Prefill selected pools before any common price writer replacement.

    Successful quiet ranges advance independent per-pool cursors. A bounded
    replay replaces orphaned logs and records repairs until the writer succeeds.

    :param web3: Connection used for chain identity only.
    :param hypersync_client: Configured indexed event client.
    :param pool_start_blocks: Selected LP tokens and inclusive source bounds.
    :param end_block: Exclusive reorg-safe scan boundary.
    :param context_path: Shared historical context database.
    :param timestamp_cache_path: Exact-block timestamp cache root.
    :param force_backfill: Re-read the full requested range regardless of cursor,
        for an explicit operator migration or a deeper historical repair.
    :return: Read/write counts and persistent pending repairs.
    """
    if web3.eth.chain_id != ANTARCTIC_CHAIN_ID:
        msg = "Antarctic history is only supported on Arbitrum"
        raise ValueError(msg)
    fetched = inserted = 0
    repairs = {}
    with AntarcticHistoricalContextStore(context_path) as store:
        for address, requested_start in pool_start_blocks.items():
            deployment = ANTARCTIC_BY_ADDRESS[address.lower()]
            cursor, repair = store.fetch_cursor(address)
            lower = max(requested_start, deployment.manager_deployment_block)
            start = max(lower, cursor - ANTARCTIC_REPLAY_BLOCKS) if cursor and not force_backfill else lower
            if start < end_block:
                events = fetch_antarctic_settlements(hypersync_client=hypersync_client, deployment=deployment, start_block=start, end_block=end_block, timestamp_cache_path=timestamp_cache_path)
                count, added = store.replace_range(address, start, end_block, events)
                fetched += count
                inserted += added
                _, repair = store.fetch_cursor(address)
                logger.info("Antarctic %s: blocks %s-%s, fetched=%s inserted=%s repair=%s", deployment.product, start, end_block, count, added, repair)
            if repair is not None:
                repairs[address] = repair
    return AntarcticPrefillResult(fetched, inserted, repairs)
