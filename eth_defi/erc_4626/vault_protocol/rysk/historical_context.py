"""Collect final Rysk Premium epoch prices from onchain events.

Rysk first emits ``EpochPriceSet`` and may replace the proposal with
``EpochPriceDisputed`` during the dispute window. ``epochExecuted`` is the
finalisation boundary. Only the latest price update preceding that execution
is exposed as a share-price-equivalent observation.
"""

import asyncio
import datetime
import logging
from collections.abc import Collection, Iterable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import TracebackType

import duckdb
from eth_typing import HexAddress
from eth_utils import keccak
from hexbytes import HexBytes
from tqdm_loggable.auto import tqdm
from web3 import Web3

from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.hypersync.hypersync_timestamp import fetch_exact_block_timestamps_using_hypersync_cached
from eth_defi.hypersync.session import ThrottledHypersyncClient, open_hypersync_stream
from eth_defi.vault.flow_events import decode_hypersync_int
from eth_defi.vault.vaultdb import get_pipeline_data_dir

try:
    import hypersync
    from hypersync import LogField
except ImportError:
    hypersync = None

logger = logging.getLogger(__name__)

RYSK_EPOCH_PRICE_SET_TOPIC = f"0x{keccak(text='EpochPriceSet(uint256,uint256,uint256)').hex()}"
RYSK_EPOCH_PRICE_DISPUTED_TOPIC = f"0x{keccak(text='EpochPriceDisputed(uint256,uint256,uint256)').hex()}"
RYSK_EPOCH_EXECUTED_TOPIC = f"0x{keccak(text='epochExecuted(uint256)').hex()}"
RYSK_EPOCH_TOPICS = (RYSK_EPOCH_PRICE_SET_TOPIC, RYSK_EPOCH_PRICE_DISPUTED_TOPIC, RYSK_EPOCH_EXECUTED_TOPIC)
RYSK_HISTORY_CHUNK_SIZE = 10_000_000
RYSK_PRICE_UPDATE_MIN_TOPIC_COUNT = 2


def get_rysk_historical_context_path() -> Path:
    """Return the shared contextual-history database path.

    Rysk owns one table in the pipeline-wide DuckDB context file. Other
    protocol tables and their rows are not modified.

    :return:
        Pipeline ``vault-historical-context.duckdb`` location.
    """

    return get_pipeline_data_dir() / "vault-historical-context.duckdb"


@dataclass(slots=True, frozen=True)
class RyskEpochPriceUpdate:
    """Represent one proposed or disputed onchain epoch price."""

    #: Epoch whose prices were updated.
    epoch: int
    #: EVM block containing the update.
    block_number: int
    #: Event position within the transaction.
    log_index: int
    #: Raw subscription price in collateral-token precision.
    raw_deposit_pps: int
    #: Raw redemption price in collateral-token precision.
    raw_withdrawal_pps: int


@dataclass(slots=True, frozen=True)
class RyskHistoricalSharePriceObservation:
    """Represent one price made final by ``epochExecuted``."""

    #: EVM chain containing the pool.
    chain_id: int
    #: Pool and ERC-20 LP share-token address.
    pool_address: HexAddress
    #: Epoch finalised by the execution.
    epoch: int
    #: Execution block, which is the finalisation boundary.
    block_number: int
    #: Unix timestamp of the execution block.
    block_timestamp: int
    #: Execution transaction hash.
    transaction_hash: str
    #: Execution event position within the transaction.
    log_index: int
    #: Final raw subscription price retained for auditing.
    raw_deposit_pps: int
    #: Final raw redemption price retained for scaling.
    raw_withdrawal_pps: int
    #: Collateral-denominated final redemption price for one share.
    withdrawal_share_price: Decimal | None = None


@dataclass(slots=True, frozen=True)
class RyskHistoricalContextPrefillResult:
    """Summarise one onchain Rysk history refresh."""

    #: Final execution events reconstructed from the requested ranges.
    observations_fetched: int
    #: Previously unseen final observations persisted.
    observations_inserted: int


class RyskHistoricalContextStore(AbstractContextManager):
    """Persist final Rysk epoch observations without DuckDB ART indexes."""

    def __init__(self, path: Path) -> None:
        """Open the shared cache and create the Rysk event table.

        :param path:
            Shared contextual-history DuckDB path.
        :return:
            None.
        """

        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(path))
        self.connection.execute("SET wal_autocheckpoint = '1TB'")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rysk_premium_epoch_context (
                chain_id UINTEGER NOT NULL,
                pool_address VARCHAR NOT NULL,
                epoch UBIGINT NOT NULL,
                block_number UBIGINT NOT NULL,
                block_timestamp UBIGINT NOT NULL,
                transaction_hash VARCHAR NOT NULL,
                log_index UINTEGER NOT NULL,
                raw_deposit_pps VARCHAR NOT NULL,
                raw_withdrawal_pps VARCHAR NOT NULL,
                source_id VARCHAR NOT NULL
            )
            """
        )

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        """Close the contextual-history connection.

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

    def fetch_next_source_block(self, chain_id: int, pool_address: HexAddress, default_start_block: int) -> int:
        """Return an inclusive restart block for one pool.

        The last finalisation block is intentionally replayed so a price update
        for the next epoch in the same block cannot be skipped. Exact execution
        rows are deduplicated when inserted.

        :param chain_id:
            EVM chain identifier.
        :param pool_address:
            Pool whose cursor is requested.
        :param default_start_block:
            First discovery block when no context exists.
        :return:
            Inclusive source restart block.
        """

        row = self.connection.execute(
            "SELECT max(block_number) FROM rysk_premium_epoch_context WHERE chain_id = ? AND pool_address = ?",
            (chain_id, pool_address.lower()),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else default_start_block

    def fetch_source_ids_at_block(self, chain_id: int, pool_address: HexAddress, block_number: int) -> frozenset[str]:
        """Return stored execution identities at one replay boundary.

        The incremental collector replays the last finalisation block so it
        cannot miss a next-epoch price update emitted later in that block.
        Stored executions at the boundary may refer to a price proposal before
        the replay window and are therefore supplied to the join as known rows.

        :param chain_id:
            EVM chain identifier.
        :param pool_address:
            Pool whose execution rows are inspected.
        :param block_number:
            Inclusive replay block.
        :return:
            Stored source identities at the boundary.
        """

        rows = self.connection.execute(
            "SELECT source_id FROM rysk_premium_epoch_context WHERE chain_id = ? AND pool_address = ? AND block_number = ?",
            (chain_id, pool_address.lower(), block_number),
        ).fetchall()
        return frozenset(row[0] for row in rows)

    def insert_observations(self, observations: Iterable[RyskHistoricalSharePriceObservation]) -> tuple[int, int]:
        """Insert final epoch observations transactionally and idempotently.

        Source identities use the execution log location. No uniqueness index
        is created because large file-backed ART indexes are unsafe with the
        repository's supported DuckDB and Python versions.

        :param observations:
            Finite stream reconstructed from Rysk events.
        :return:
            ``(fetched, inserted)`` counts.
        """

        fetched = inserted = 0
        self.connection.execute("BEGIN TRANSACTION")
        try:
            for observation in observations:
                fetched += 1
                source_id = _make_rysk_source_id(observation.chain_id, observation.transaction_hash, observation.log_index)
                exists = self.connection.execute(
                    "SELECT EXISTS (SELECT 1 FROM rysk_premium_epoch_context WHERE source_id = ?)",
                    (source_id,),
                ).fetchone()[0]
                if exists:
                    continue
                self.connection.execute(
                    "INSERT INTO rysk_premium_epoch_context VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        observation.chain_id,
                        observation.pool_address.lower(),
                        observation.epoch,
                        observation.block_number,
                        observation.block_timestamp,
                        observation.transaction_hash.lower(),
                        observation.log_index,
                        str(observation.raw_deposit_pps),
                        str(observation.raw_withdrawal_pps),
                        source_id,
                    ),
                )
                inserted += 1
        except duckdb.Error:
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
        """Yield final execution prices in chronological order.

        ``raw_withdrawal_pps`` is encoded using the pool collateral token's
        precision. No total-assets or supply value is fabricated.

        :param chain_id:
            EVM chain identifier.
        :param pool_address:
            Pool and share-token address.
        :param start_block:
            Inclusive execution block boundary.
        :param end_block:
            Exclusive execution block boundary.
        :param collateral_decimals:
            Native precision of the collateral token.
        :return:
            Final collateral-denominated exit prices.
        """

        if collateral_decimals < 0:
            raise ValueError(f"collateral_decimals must be non-negative, got {collateral_decimals}")
        rows = self.connection.execute(
            """
            SELECT epoch, block_number, block_timestamp, transaction_hash,
                   log_index, raw_deposit_pps, raw_withdrawal_pps
            FROM rysk_premium_epoch_context
            WHERE chain_id = ? AND pool_address = ?
              AND block_number >= ? AND block_number < ?
            ORDER BY block_number, log_index
            """,
            (chain_id, pool_address.lower(), start_block, end_block),
        ).fetchall()
        scale = Decimal(10**collateral_decimals)
        for epoch, block_number, timestamp, transaction_hash, log_index, deposit_pps, withdrawal_pps in rows:
            raw_withdrawal_pps = int(withdrawal_pps)
            if raw_withdrawal_pps <= 0:
                logger.warning(
                    "Skipping non-positive Rysk withdrawal price for epoch %d on %s at block %d",
                    epoch,
                    pool_address,
                    block_number,
                )
                continue
            yield RyskHistoricalSharePriceObservation(
                chain_id=chain_id,
                pool_address=pool_address,
                epoch=int(epoch),
                block_number=int(block_number),
                block_timestamp=int(timestamp),
                transaction_hash=transaction_hash,
                log_index=int(log_index),
                raw_deposit_pps=int(deposit_pps),
                raw_withdrawal_pps=raw_withdrawal_pps,
                withdrawal_share_price=Decimal(raw_withdrawal_pps) / scale,
            )


async def _fetch_rysk_epoch_source_chunk(
    hypersync_client: ThrottledHypersyncClient,
    chain_id: int,
    pool_address: HexAddress,
    start_block: int,
    end_block: int,
) -> tuple[list[RyskEpochPriceUpdate], list[RyskHistoricalSharePriceObservation]]:
    """Fetch and decode one bounded range of Rysk epoch events.

    Execution observations returned here do not yet contain prices. The caller
    joins them to the latest proposed or disputed price for the processed epoch.

    :param hypersync_client:
        Configured throttle-aware Hypersync client.
    :param chain_id:
        EVM chain identifier.
    :param pool_address:
        Address-scoped Rysk pool.
    :param start_block:
        Inclusive source block.
    :param end_block:
        Exclusive source block.
    :return:
        Price updates and price-less execution observations.
    """

    assert hypersync is not None, "hypersync package is required for Rysk event history"
    query = hypersync.Query(
        from_block=start_block,
        to_block=end_block,
        logs=[hypersync.LogSelection(address=[pool_address.lower()], topics=[list(RYSK_EPOCH_TOPICS)])],
        field_selection=hypersync.FieldSelection(log=[LogField.BLOCK_NUMBER, LogField.LOG_INDEX, LogField.TRANSACTION_HASH, LogField.TOPIC0, LogField.TOPIC1, LogField.DATA]),
    )
    updates = []
    executions = []
    codec = Web3().codec
    receiver = await open_hypersync_stream(hypersync_client, query)
    try:
        while response := await receiver.recv():
            for log in response.data.logs or []:
                block_number = decode_hypersync_int(log.block_number)
                log_index = decode_hypersync_int(log.log_index)
                transaction_hash = str(log.transaction_hash)
                topic0 = str(log.topics[0]).lower()
                if topic0 in {RYSK_EPOCH_PRICE_SET_TOPIC, RYSK_EPOCH_PRICE_DISPUTED_TOPIC}:
                    if len(log.topics) < RYSK_PRICE_UPDATE_MIN_TOPIC_COUNT or log.topics[1] is None:
                        raise ValueError(f"Rysk epoch price update lacks an indexed epoch at {transaction_hash}:{log_index}")
                    deposit_pps, withdrawal_pps = codec.decode(["uint256", "uint256"], HexBytes(log.data))
                    updates.append(RyskEpochPriceUpdate(decode_hypersync_int(log.topics[1]), block_number, log_index, deposit_pps, withdrawal_pps))
                elif topic0 == RYSK_EPOCH_EXECUTED_TOPIC:
                    (new_epoch,) = codec.decode(["uint256"], HexBytes(log.data))
                    if new_epoch == 0:
                        raise ValueError(f"Rysk epochExecuted emitted zero at {transaction_hash}:{log_index}")
                    executions.append(RyskHistoricalSharePriceObservation(chain_id, pool_address, new_epoch - 1, block_number, 0, transaction_hash, log_index, 0, 0))
    finally:
        receiver.close()
        # Let the native stream deliver its cancellation callback before the
        # short-lived runner closes its event loop.
        await asyncio.sleep(0.05)
    return updates, executions


def fetch_rysk_finalised_epoch_prices(
    *,
    hypersync_client: ThrottledHypersyncClient,
    chain_id: int,
    pool_address: HexAddress,
    start_block: int,
    end_block: int,
    source_chunk_size: int = RYSK_HISTORY_CHUNK_SIZE,
    timestamp_cache_path: Path = DEFAULT_TIMESTAMP_CACHE_FOLDER,
    known_execution_source_ids: Collection[str] = (),
) -> tuple[RyskHistoricalSharePriceObservation, ...]:
    """Reconstruct final Rysk prices from proposed, disputed and executed events.

    Price updates are ordered by block and log index. An execution selects the
    greatest update for its processed epoch that precedes the execution event.

    :param hypersync_client:
        Configured throttle-aware Hypersync client.
    :param chain_id:
        EVM chain identifier.
    :param pool_address:
        Rysk pool to scan.
    :param start_block:
        Inclusive source boundary.
    :param end_block:
        Exclusive source boundary.
    :param source_chunk_size:
        Maximum blocks per observable Hypersync request.
    :param timestamp_cache_path:
        Persistent cache directory for execution-block timestamps.
    :param known_execution_source_ids:
        Stored execution rows at an inclusive replay boundary. A known row may
        have its price update before the requested range and is skipped instead
        of being treated as corrupt source history.
    :return:
        Final epoch observations ordered by execution location.
    """

    if source_chunk_size <= 0:
        raise ValueError(f"source_chunk_size must be positive, got {source_chunk_size}")
    updates = []
    executions = []
    chunk_count = (end_block - start_block + source_chunk_size - 1) // source_chunk_size
    with asyncio.Runner() as runner, tqdm(total=chunk_count, desc=f"Rysk epochs for {pool_address}", unit="chunk") as progress_bar:
        for chunk_start in range(start_block, end_block, source_chunk_size):
            chunk_end = min(end_block, chunk_start + source_chunk_size)
            logger.info("Fetching Rysk epoch events for %s on chain %d, blocks %d-%d", pool_address, chain_id, chunk_start, chunk_end)
            chunk_updates, chunk_executions = runner.run(_fetch_rysk_epoch_source_chunk(hypersync_client, chain_id, pool_address, chunk_start, chunk_end))
            updates.extend(chunk_updates)
            executions.extend(chunk_executions)
            progress_bar.update()
            progress_bar.set_postfix({"updates": len(updates), "executions": len(executions)})

    executions = [execution for execution in executions if _make_rysk_source_id(execution.chain_id, execution.transaction_hash, execution.log_index) not in known_execution_source_ids]
    if not executions:
        return ()

    matched_prices = []
    for execution in sorted(executions, key=lambda item: (item.block_number, item.log_index)):
        execution_location = (execution.block_number, execution.log_index)
        candidates = (update for update in updates if update.epoch == execution.epoch and (update.block_number, update.log_index) < execution_location)
        price = max(candidates, default=None, key=lambda item: (item.block_number, item.log_index))
        if price is None:
            logger.warning(
                "Skipping Rysk epoch %d execution for %s at block %d because its price update predates the source window",
                execution.epoch,
                pool_address,
                execution.block_number,
            )
            continue
        matched_prices.append((execution, price))

    if not matched_prices:
        return ()

    execution_blocks = sorted({execution.block_number for execution, _price in matched_prices})
    timestamp_slicer = fetch_exact_block_timestamps_using_hypersync_cached(
        client=hypersync_client,
        chain_id=chain_id,
        block_numbers=execution_blocks,
        cache_path=timestamp_cache_path,
        display_progress=False,
    )
    try:
        execution_timestamps = {block_number: int(timestamp_slicer[block_number].replace(tzinfo=datetime.UTC).timestamp()) for block_number in execution_blocks}
    finally:
        timestamp_slicer.close()

    finalised = []
    for execution, price in matched_prices:
        finalised.append(
            RyskHistoricalSharePriceObservation(
                chain_id=execution.chain_id,
                pool_address=execution.pool_address,
                epoch=execution.epoch,
                block_number=execution.block_number,
                block_timestamp=execution_timestamps[execution.block_number],
                transaction_hash=execution.transaction_hash,
                log_index=execution.log_index,
                raw_deposit_pps=price.raw_deposit_pps,
                raw_withdrawal_pps=price.raw_withdrawal_pps,
            )
        )
    return tuple(finalised)


def fetch_and_store_rysk_premium_history(
    *,
    web3: Web3,
    hypersync_client: ThrottledHypersyncClient,
    pool_start_blocks: Mapping[HexAddress, int],
    end_block: int,
    context_path: Path,
    timestamp_cache_path: Path = DEFAULT_TIMESTAMP_CACHE_FOLDER,
) -> RyskHistoricalContextPrefillResult:
    """Incrementally collect final onchain epochs for selected Rysk pools.

    Each pool resumes from its last stored execution block, or its own supplied
    discovery block when no history exists. Rysk event and timestamp reads use
    Hypersync; JSON-RPC ``eth_getLogs`` is never used.

    :param web3:
        RPC connection used only for chain identity.
    :param hypersync_client:
        Configured client for the same chain.
    :param pool_start_blocks:
        Rysk pool addresses mapped to their individual inclusive discovery
        boundaries.
    :param end_block:
        Exclusive safe source head.
    :param context_path:
        Shared contextual-history DuckDB path.
    :param timestamp_cache_path:
        Persistent per-chain timestamp-cache directory.
    :return:
        Aggregate fetched and inserted observation counts.
    """

    fetched = inserted = 0
    chain_id = web3.eth.chain_id
    with RyskHistoricalContextStore(context_path) as store:
        for pool_address, default_start_block in pool_start_blocks.items():
            pool_start = store.fetch_next_source_block(chain_id, pool_address, default_start_block)
            if pool_start >= end_block:
                continue
            known_execution_source_ids = store.fetch_source_ids_at_block(chain_id, pool_address, pool_start)
            observations = fetch_rysk_finalised_epoch_prices(
                hypersync_client=hypersync_client,
                chain_id=chain_id,
                pool_address=pool_address,
                start_block=pool_start,
                end_block=end_block,
                timestamp_cache_path=timestamp_cache_path,
                known_execution_source_ids=known_execution_source_ids,
            )
            pool_fetched, pool_inserted = store.insert_observations(observations)
            logger.info("Refreshed Rysk epoch history for %s: %d final executions, %d inserted", pool_address, pool_fetched, pool_inserted)
            fetched += pool_fetched
            inserted += pool_inserted
    return RyskHistoricalContextPrefillResult(fetched, inserted)


def _make_rysk_source_id(chain_id: int, transaction_hash: str, log_index: int) -> str:
    """Build the stable identity of one Rysk execution log.

    :param chain_id:
        EVM chain identifier.
    :param transaction_hash:
        Execution transaction hash.
    :param log_index:
        Execution log position.
    :return:
        Context-table source identity.
    """

    return f"{chain_id}:{transaction_hash.lower()}:{log_index}"
