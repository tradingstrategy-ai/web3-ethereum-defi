"""Collect raw Flying Tulip sftUSD reward and supply history with Hypersync.

The collector stores only canonical source events.  The later contextual reader
replays these events into the common ``share_price_equivalence`` curve.
"""

import asyncio
import logging
from collections.abc import Iterator
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

from eth_defi.erc_4626.vault_protocol.flying_tulip.constants import (
    FLYING_TULIP_CURVE_CANONICAL_START_TIMESTAMP,
    FLYING_TULIP_FT_FTUSD_CURVE_POOL,
    FLYING_TULIP_INITIAL_SHARE_PRICE_EQUIVALENCE,
    FLYING_TULIP_MAX_ORACLE_AGE_SECONDS,
    FLYING_TULIP_RATE_RAY,
    FLYING_TULIP_SFTUSD_BY_CHAIN,
)
from eth_defi.hypersync.session import ThrottledHypersyncClient, open_hypersync_stream
from eth_defi.vault.flow_events import decode_hypersync_int
from eth_defi.vault.vaultdb import get_pipeline_data_dir

try:
    import hypersync
    from hypersync import BlockField, LogField
except ImportError:
    hypersync = None

logger = logging.getLogger(__name__)


#: Index of ``epochId`` in ``EpochSettled``.
EPOCH_SETTLED_TOPIC = f"0x{keccak(text='EpochSettled(uint32,uint256,uint256,uint256)').hex()}"

#: Standard ERC-20 transfer event signature.
TRANSFER_TOPIC = f"0x{keccak(text='Transfer(address,address,uint256)').hex()}"

#: Solidity's indexed zero address representation.
ZERO_ADDRESS_TOPIC = f"0x{'0' * 64}"

#: ``EpochSettled`` has signature and indexed epoch topics.
EPOCH_SETTLED_TOPIC_COUNT = 2

#: ERC-20 transfers have signature, sender and recipient topics.
TRANSFER_TOPIC_COUNT = 3

#: A chain-wide source range is split so every successful request is observable.
FLYING_TULIP_SOURCE_CHUNK_SIZE = 10_000_000


@dataclass(slots=True, frozen=True)
class FlyingTulipContextPrefillResult:
    """Summarise one full or bounded Flying Tulip source collection."""

    #: Source EVM chain identifier.
    chain_id: int
    #: Inclusive first processed block.
    start_block: int
    #: Exclusive safe source head.
    end_block: int
    #: Number of decoded ``EpochSettled`` logs.
    epochs_fetched: int
    #: Number of mint or burn transfer logs.
    supply_events_fetched: int
    #: Number of new raw rows committed to the contextual cache.
    rows_inserted: int


@dataclass(slots=True, frozen=True)
class EpochSettlement:
    """One raw ``EpochSettled`` event needed for performance replay."""

    #: Source EVM chain identifier.
    chain_id: int
    #: sftUSD proxy which emitted the event.
    vault_address: HexAddress
    #: Sequential contract settlement identifier.
    epoch_id: int
    #: EVM block containing the event.
    block_number: int
    #: Unix timestamp of :attr:`block_number`.
    block_timestamp: int
    #: Emitting transaction hash.
    transaction_hash: str
    #: Event position in the transaction.
    log_index: int
    #: Raw FT distribution amount.
    raw_reward_amount: int
    #: Raw aggregate stake-seconds.
    raw_stake_time: int
    #: Raw contract reward-rate identity value.
    raw_rate_ray: int


@dataclass(slots=True, frozen=True)
class SupplyChange:
    """One sftUSD mint or burn reconstructed from ``Transfer``."""

    #: Source EVM chain identifier.
    chain_id: int
    #: sftUSD proxy which emitted the transfer.
    vault_address: HexAddress
    #: EVM block containing the transfer.
    block_number: int
    #: Unix timestamp of :attr:`block_number`.
    block_timestamp: int
    #: Emitting transaction hash.
    transaction_hash: str
    #: Event position in the transaction.
    log_index: int
    #: ``True`` for a mint and ``False`` for a burn.
    is_mint: bool
    #: Raw sftUSD supply delta.
    raw_amount: int


@dataclass(slots=True, frozen=True)
class RewardPriceObservation:
    """One canonical FT/ftUSD price attached to a settled epoch.

    The price is deliberately source-chain independent: each settlement uses
    the Ethereum Curve oracle at the greatest Ethereum block no later than the
    settlement timestamp. Raw ``uint256`` units are retained in the context
    cache so replay never depends on float serialisation.
    """

    #: Ethereum block used for the Curve read.
    ethereum_block_number: int
    #: Unix timestamp of :attr:`ethereum_block_number`.
    ethereum_block_timestamp: int
    #: Reviewed Ethereum Curve pool address.
    pool_address: HexAddress
    #: Raw inverse ``price_oracle()`` response.
    raw_oracle: int
    #: Curve's last oracle-update Unix timestamp.
    oracle_updated_at: int
    #: Normalised ftUSD-per-FT price, scaled by 1e18.
    raw_ft_price_in_ftusd: int


@dataclass(slots=True, frozen=True)
class FlyingTulipSharePriceObservation:
    """One replayed, non-redeemable sftUSD price-equivalence observation."""

    #: Source settlement block.
    block_number: int
    #: Source settlement Unix timestamp.
    block_timestamp: int
    #: Source ``EpochSettled`` identifier.
    epoch_id: int
    #: Reconstructed sftUSD principal supply in ftUSD units.
    total_supply: Decimal
    #: Compounded, non-redeemable ftUSD price equivalent.
    share_price: Decimal
    #: Synthetic equivalent value, ``share_price * total_supply``.
    #:
    #: This is a reward-reinvested performance value for the shared
    #: ``share_price_equivalence`` pipeline, not contractual sftUSD TVL.
    total_assets: Decimal


def is_mint_transfer(from_topic: str, to_topic: str) -> bool:
    """Classify a zero-address ERC-20 transfer as a supply mint or burn.

    ERC-20 emits a mint with the zero address as sender and a burn with the
    zero address as recipient.  The caller has already filtered ordinary
    transfers before invoking this helper.

    :param from_topic:
        Normalised indexed ERC-20 transfer sender topic.
    :param to_topic:
        Normalised indexed ERC-20 transfer recipient topic.
    :return:
        ``True`` for a mint and ``False`` for a burn.
    :raises ValueError:
        If the transfer does not alter token supply.
    """

    if from_topic == ZERO_ADDRESS_TOPIC:
        return True
    if to_topic == ZERO_ADDRESS_TOPIC:
        return False
    message = "Flying Tulip supply transfer does not have a zero-address endpoint"
    raise ValueError(message)


class FlyingTulipHistoricalContextStore(AbstractContextManager):
    """Persist Flying Tulip source events in the shared contextual cache."""

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        """Open the shared DuckDB and initialise Flying Tulip tables.

        :param path:
            Shared ``vault-historical-context.duckdb`` file.
        :param read_only:
            Open an existing context database without schema writes.
        """

        if read_only:
            self.connection = duckdb.connect(str(path), read_only=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(path))
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS flying_tulip_epoch_context (
                chain_id UINTEGER NOT NULL,
                vault_address VARCHAR NOT NULL,
                epoch_id UINTEGER NOT NULL,
                block_number UBIGINT NOT NULL,
                block_timestamp UBIGINT NOT NULL,
                transaction_hash VARCHAR NOT NULL,
                log_index UINTEGER NOT NULL,
                raw_reward_amount VARCHAR NOT NULL,
                raw_stake_time VARCHAR NOT NULL,
                raw_rate_ray VARCHAR NOT NULL,
                PRIMARY KEY (chain_id, transaction_hash, log_index),
                UNIQUE (chain_id, vault_address, epoch_id)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS flying_tulip_reward_price_context (
                ethereum_block_number UBIGINT PRIMARY KEY,
                block_timestamp UBIGINT NOT NULL,
                pool_address VARCHAR NOT NULL,
                raw_oracle VARCHAR NOT NULL,
                oracle_updated_at UBIGINT NOT NULL,
                raw_ft_price_in_ftusd VARCHAR NOT NULL,
                CHECK (pool_address = '0x68102ff5406475881462880a8da3c9bc9181ad6c')
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS flying_tulip_supply_context (
                chain_id UINTEGER NOT NULL,
                vault_address VARCHAR NOT NULL,
                block_number UBIGINT NOT NULL,
                block_timestamp UBIGINT NOT NULL,
                transaction_hash VARCHAR NOT NULL,
                log_index UINTEGER NOT NULL,
                is_mint BOOLEAN NOT NULL,
                raw_amount VARCHAR NOT NULL,
                PRIMARY KEY (chain_id, transaction_hash, log_index)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS flying_tulip_source_scan_state (
                chain_id UINTEGER NOT NULL,
                vault_address VARCHAR NOT NULL,
                end_block UBIGINT NOT NULL,
                PRIMARY KEY (chain_id, vault_address)
            )
            """
        )

    def insert_epoch(self, event: EpochSettlement) -> bool:
        """Insert one epoch event, rejecting any non-identical retry.

        :param event:
            Decoded canonical event.
        :return:
            ``True`` if a new row was inserted.
        """

        values = (
            event.chain_id,
            event.vault_address.lower(),
            event.epoch_id,
            event.block_number,
            event.block_timestamp,
            event.transaction_hash.lower(),
            event.log_index,
            str(event.raw_reward_amount),
            str(event.raw_stake_time),
            str(event.raw_rate_ray),
        )
        existing = self.connection.execute(
            "SELECT * FROM flying_tulip_epoch_context WHERE chain_id = ? AND transaction_hash = ? AND log_index = ?",
            (event.chain_id, event.transaction_hash.lower(), event.log_index),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != values:
                raise ValueError(f"Conflicting Flying Tulip epoch event: {(event.chain_id, event.transaction_hash, event.log_index)}")
            return False
        self.connection.execute("INSERT INTO flying_tulip_epoch_context VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
        return True

    def insert_supply_change(self, event: SupplyChange) -> bool:
        """Insert one mint or burn event, rejecting any non-identical retry.

        :param event:
            Decoded canonical transfer.
        :return:
            ``True`` if a new row was inserted.
        """

        values = (
            event.chain_id,
            event.vault_address.lower(),
            event.block_number,
            event.block_timestamp,
            event.transaction_hash.lower(),
            event.log_index,
            event.is_mint,
            str(event.raw_amount),
        )
        existing = self.connection.execute(
            "SELECT * FROM flying_tulip_supply_context WHERE chain_id = ? AND transaction_hash = ? AND log_index = ?",
            (event.chain_id, event.transaction_hash.lower(), event.log_index),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != values:
                raise ValueError(f"Conflicting Flying Tulip supply event: {(event.chain_id, event.transaction_hash, event.log_index)}")
            return False
        self.connection.execute("INSERT INTO flying_tulip_supply_context VALUES (?, ?, ?, ?, ?, ?, ?, ?)", values)
        return True

    def insert_reward_price(self, observation: RewardPriceObservation) -> bool:
        """Persist one deterministic FT/ftUSD price join idempotently.

        :param observation:
            Price oracle observation for an epoch.
        :return:
            ``True`` if the cache gained a new row.
        """

        if observation.pool_address.lower() != FLYING_TULIP_FT_FTUSD_CURVE_POOL.lower():
            raise ValueError(f"Unsupported Flying Tulip reward price pool: {observation.pool_address}")
        values = (
            observation.ethereum_block_number,
            observation.ethereum_block_timestamp,
            observation.pool_address.lower(),
            str(observation.raw_oracle),
            observation.oracle_updated_at,
            str(observation.raw_ft_price_in_ftusd),
        )
        existing = self.connection.execute(
            "SELECT * FROM flying_tulip_reward_price_context WHERE ethereum_block_number = ?",
            values[:1],
        ).fetchone()
        if existing is not None:
            if tuple(existing) != values:
                raise ValueError(f"Conflicting Flying Tulip reward price at Ethereum block {observation.ethereum_block_number}")
            return False
        self.connection.execute("INSERT INTO flying_tulip_reward_price_context VALUES (?, ?, ?, ?, ?, ?)", values)
        return True

    def fetch_next_source_block(self, chain_id: int, vault_address: HexAddress) -> int | None:
        """Return the first unscanned source block for one reviewed proxy.

        Source coverage is persisted even when a range contains no events. This
        prevents a dormant deployment from being rediscovered from genesis on
        every scheduled scanner cycle.

        :param chain_id:
            sftUSD deployment chain.
        :param vault_address:
            Reviewed source proxy.
        :return:
            Exclusive end of the last completed range, or ``None`` initially.
        """

        row = self.connection.execute(
            "SELECT end_block FROM flying_tulip_source_scan_state WHERE chain_id = ? AND vault_address = ?",
            (chain_id, vault_address.lower()),
        ).fetchone()
        return int(row[0]) if row is not None else None

    def set_source_scan_end_block(self, chain_id: int, vault_address: HexAddress, end_block: int) -> None:
        """Record a fully committed half-open source range.

        The caller invokes this only after every chunk through ``end_block``
        has committed. Monotonic updates make a successful empty range as
        durable as a range containing source events.

        :param chain_id:
            sftUSD deployment chain.
        :param vault_address:
            Reviewed source proxy.
        :param end_block:
            Exclusive end of the fully committed source range.
        :return:
            None.
        """

        self.connection.execute(
            """
            INSERT INTO flying_tulip_source_scan_state VALUES (?, ?, ?)
            ON CONFLICT (chain_id, vault_address) DO UPDATE
            SET end_block = GREATEST(flying_tulip_source_scan_state.end_block, excluded.end_block)
            """,
            (chain_id, vault_address.lower(), end_block),
        )

    def iter_share_price_observations(
        self,
        chain_id: int,
        vault_address: HexAddress,
        asset_decimals: int,
        reward_decimals: int,
        start_block: int,
        end_block: int,
        step: int,
    ) -> Iterator[FlyingTulipSharePriceObservation]:
        """Replay reward epochs as a compounded ftUSD price equivalent.

        Tracking starts at the first settlement after the canonical Ethereum
        Curve FT/ftUSD market was deployed. Earlier rewards cannot be valued
        from this market and are deliberately excluded. The first tracked
        settlement establishes a 1.0 baseline; its reward is not compounded
        because its accrual interval crosses the unsupported boundary. Every
        later epoch validates the onchain rate identity and stake-seconds
        supply invariant before it can extend the compounded suffix.

        :param chain_id:
            sftUSD deployment chain.
        :param vault_address:
            Reviewed sftUSD proxy address.
        :param asset_decimals:
            ftUSD and sftUSD decimal count.
        :param reward_decimals:
            FT decimal count.
        :param start_block:
            Inclusive caller range.
        :param end_block:
            Exclusive caller range.
        :param step:
            Common scanner's approximate block bucket width.
        :return:
            Valid, bucket-downsampled synthetic price observations.
        """

        if asset_decimals < 0 or reward_decimals < 0:
            raise ValueError("Token decimals must be non-negative")
        if step <= 0:
            raise ValueError(f"step must be positive, got {step}")
        address = vault_address.lower()
        epoch_rows = self.connection.execute(
            """
            SELECT epoch_id, block_number, block_timestamp, log_index,
                   raw_reward_amount, raw_stake_time, raw_rate_ray
            FROM flying_tulip_epoch_context
            WHERE chain_id = ? AND vault_address = ? AND block_timestamp >= ?
            ORDER BY block_number, log_index
            """,
            (chain_id, address, FLYING_TULIP_CURVE_CANONICAL_START_TIMESTAMP),
        ).fetchall()
        supply_rows = self.connection.execute(
            """
            SELECT block_number, log_index, is_mint, raw_amount
            FROM flying_tulip_supply_context
            WHERE chain_id = ? AND vault_address = ?
            ORDER BY block_number, log_index
            """,
            (chain_id, address),
        ).fetchall()
        raw_supply = 0
        max_raw_supply_since_previous_epoch = 0
        supply_index = 0
        previous_epoch_id: int | None = None
        previous_timestamp: int | None = None
        share_price = Decimal(FLYING_TULIP_INITIAL_SHARE_PRICE_EQUIVALENCE)
        asset_scale = Decimal(10) ** asset_decimals
        reward_scale = Decimal(10) ** reward_decimals
        price_scale = Decimal(10) ** 18
        replayed: list[FlyingTulipSharePriceObservation] = []
        for epoch_id_raw, block_number_raw, block_timestamp_raw, log_index_raw, reward_raw, stake_time_raw, rate_ray_raw in epoch_rows:
            epoch_id = int(epoch_id_raw)
            block_number = int(block_number_raw)
            block_timestamp = int(block_timestamp_raw)
            log_index = int(log_index_raw)
            while supply_index < len(supply_rows):
                supply_block, supply_log_index, is_mint, raw_amount = supply_rows[supply_index]
                if (int(supply_block), int(supply_log_index)) > (block_number, log_index):
                    break
                amount = int(raw_amount)
                raw_supply = raw_supply + amount if is_mint else raw_supply - amount
                if raw_supply < 0:
                    raise ValueError(f"Flying Tulip supply became negative before epoch {epoch_id}")
                max_raw_supply_since_previous_epoch = max(max_raw_supply_since_previous_epoch, raw_supply)
                supply_index += 1
            if previous_epoch_id is not None and epoch_id != previous_epoch_id + 1:
                raise ValueError(f"Flying Tulip epoch IDs are not contiguous: {previous_epoch_id} then {epoch_id}")
            if previous_timestamp is None:
                previous_epoch_id = epoch_id
                previous_timestamp = block_timestamp
                max_raw_supply_since_previous_epoch = raw_supply
                if start_block <= block_number < end_block:
                    total_supply = Decimal(raw_supply) / asset_scale
                    replayed.append(
                        FlyingTulipSharePriceObservation(
                            block_number=block_number,
                            block_timestamp=block_timestamp,
                            epoch_id=epoch_id,
                            total_supply=total_supply,
                            share_price=share_price,
                            total_assets=share_price * total_supply,
                        )
                    )
                continue
            duration = block_timestamp - previous_timestamp
            if duration <= 0:
                raise ValueError(f"Flying Tulip epoch {epoch_id} has non-positive duration {duration}")
            raw_reward = int(reward_raw)
            raw_stake_time = int(stake_time_raw)
            raw_rate_ray = int(rate_ray_raw)
            if raw_stake_time <= 0 and raw_reward > 0:
                raise ValueError(f"Flying Tulip epoch {epoch_id} has reward without stake-seconds")
            if raw_stake_time > 0 and raw_rate_ray != raw_reward * FLYING_TULIP_RATE_RAY // raw_stake_time:
                raise ValueError(f"Flying Tulip epoch {epoch_id} rateRay identity failed")
            price_row = self.connection.execute(
                """
                SELECT raw_ft_price_in_ftusd
                FROM flying_tulip_reward_price_context
                WHERE block_timestamp <= ?
                  AND oracle_updated_at <= ?
                  AND ? - oracle_updated_at <= ?
                ORDER BY block_timestamp DESC, ethereum_block_number DESC
                LIMIT 1
                """,
                (block_timestamp, block_timestamp, block_timestamp, FLYING_TULIP_MAX_ORACLE_AGE_SECONDS),
            ).fetchone()
            if price_row is None or int(price_row[0]) <= 0:
                # A missing price makes the compounded suffix unknowable.
                logger.warning("Flying Tulip chain %d: ending reward-equivalence replay at epoch %d because no fresh Curve price is available", chain_id, epoch_id)
                break
            price_raw = int(price_row[0])
            average_stake = Decimal(raw_stake_time) / asset_scale / Decimal(duration)
            total_supply = Decimal(raw_supply) / asset_scale
            maximum_supply = Decimal(max_raw_supply_since_previous_epoch) / asset_scale
            if raw_reward > 0 and (average_stake <= 0 or average_stake > maximum_supply):
                raise ValueError(f"Flying Tulip epoch {epoch_id} average stake {average_stake} exceeds maximum reconstructed supply {maximum_supply}")
            reward_value = Decimal(raw_reward) / reward_scale * Decimal(price_raw) / price_scale
            epoch_return = Decimal(0) if raw_reward == 0 else reward_value / average_stake
            if epoch_return < 0:
                raise ValueError(f"Flying Tulip epoch {epoch_id} has a negative reward return")
            share_price *= Decimal(1) + epoch_return
            previous_epoch_id = epoch_id
            previous_timestamp = block_timestamp
            max_raw_supply_since_previous_epoch = raw_supply
            if not start_block <= block_number < end_block:
                continue
            replayed.append(
                FlyingTulipSharePriceObservation(
                    block_number=block_number,
                    block_timestamp=block_timestamp,
                    epoch_id=epoch_id,
                    total_supply=total_supply,
                    share_price=share_price,
                    total_assets=share_price * total_supply,
                )
            )
        by_bucket = {observation.block_number // step: observation for observation in replayed}
        yield from (by_bucket[bucket] for bucket in sorted(by_bucket))

    def close(self) -> None:
        """Close the contextual cache connection."""

        self.connection.close()

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        """Close the cache connection after normal or exceptional use."""

        self.close()


async def fetch_source_chunk(  # noqa: PLR0914
    hypersync_client: ThrottledHypersyncClient,
    chain_id: int,
    vault_address: HexAddress,
    start_block: int,
    end_block: int,
) -> tuple[list[EpochSettlement], list[SupplyChange]]:
    """Stream one source range and decode settlement and supply events.

    The query deliberately requests all sftUSD transfers and filters to zero
    address endpoints locally. This avoids depending on provider-specific
    wildcard-topic syntax while retaining an address-scoped source stream.

    :param hypersync_client:
        Configured client for ``chain_id``.
    :param chain_id:
        EVM source chain.
    :param vault_address:
        Official sftUSD proxy on that chain.
    :param start_block:
        Inclusive source range start.
    :param end_block:
        Exclusive source range end.
    :return:
        Chronologically sorted epoch and supply source events.
    """

    assert hypersync is not None, "hypersync package is required for Flying Tulip source collection"
    query = hypersync.Query(
        from_block=start_block,
        to_block=end_block,
        logs=[
            hypersync.LogSelection(address=[vault_address.lower()], topics=[[EPOCH_SETTLED_TOPIC]]),
            hypersync.LogSelection(address=[vault_address.lower()], topics=[[TRANSFER_TOPIC]]),
        ],
        field_selection=hypersync.FieldSelection(
            block=[BlockField.NUMBER, BlockField.TIMESTAMP],
            log=[LogField.BLOCK_NUMBER, LogField.LOG_INDEX, LogField.TRANSACTION_HASH, LogField.TOPIC0, LogField.TOPIC1, LogField.TOPIC2, LogField.DATA],
        ),
    )
    epochs: list[EpochSettlement] = []
    supply_changes: list[SupplyChange] = []
    receiver = await open_hypersync_stream(hypersync_client, query)
    try:
        while response := await receiver.recv():
            timestamps = {decode_hypersync_int(block.number): decode_hypersync_int(block.timestamp) for block in response.data.blocks or [] if block.number is not None and block.timestamp is not None}
            for log in response.data.logs or []:
                block_number = decode_hypersync_int(log.block_number)
                timestamp = timestamps.get(block_number)
                if timestamp is None:
                    raise ValueError(f"Hypersync did not return a timestamp for Flying Tulip block {block_number}")
                topic0 = str(log.topics[0]).lower()
                transaction_hash = str(log.transaction_hash)
                log_index = decode_hypersync_int(log.log_index)
                if topic0 == EPOCH_SETTLED_TOPIC:
                    if len(log.topics) < EPOCH_SETTLED_TOPIC_COUNT:
                        raise ValueError(f"EpochSettled without epoch topic at {transaction_hash}:{log_index}")
                    reward_amount, stake_time, rate_ray = Web3().codec.decode(["uint256", "uint256", "uint256"], HexBytes(log.data))
                    epochs.append(EpochSettlement(chain_id, vault_address, decode_hypersync_int(log.topics[1]), block_number, timestamp, transaction_hash, log_index, reward_amount, stake_time, rate_ray))
                elif topic0 == TRANSFER_TOPIC and len(log.topics) >= TRANSFER_TOPIC_COUNT:
                    from_topic = str(log.topics[1]).lower()
                    to_topic = str(log.topics[2]).lower()
                    if ZERO_ADDRESS_TOPIC not in {from_topic, to_topic}:
                        continue
                    (amount,) = Web3().codec.decode(["uint256"], HexBytes(log.data))
                    supply_changes.append(SupplyChange(chain_id, vault_address, block_number, timestamp, transaction_hash, log_index, is_mint_transfer(from_topic, to_topic), amount))
    finally:
        receiver.close()
    epochs.sort(key=lambda event: (event.block_number, event.log_index))
    supply_changes.sort(key=lambda event: (event.block_number, event.log_index))
    return epochs, supply_changes


def fetch_flying_tulip_proxy_deployment_block(web3: Web3, vault_address: HexAddress, end_block: int) -> int:
    """Find the first block with runtime code for a reviewed sftUSD proxy.

    The binary search is an archive-state operation for one contract, not a
    historical log or timestamp read. The caller uses it only to establish the
    complete Hypersync source range; all source events themselves remain on
    Hypersync.

    :param web3:
        Archive-capable deployment-chain provider.
    :param vault_address:
        Reviewed sftUSD proxy.
    :param end_block:
        Exclusive safe head whose preceding block contains proxy code.
    :return:
        Inclusive deployment block.
    """

    if not web3.eth.get_code(vault_address, block_identifier=end_block - 1):
        raise ValueError(f"Flying Tulip proxy {vault_address} has no code at safe head {end_block - 1}")
    low = 1
    high = end_block - 1
    while low < high:
        candidate = (low + high) // 2
        if web3.eth.get_code(vault_address, block_identifier=candidate):
            high = candidate
        else:
            low = candidate + 1
    return low


def fetch_and_store_flying_tulip_source_history(
    *,
    web3: Web3,
    hypersync_client: ThrottledHypersyncClient,
    start_block: int,
    end_block: int,
    context_path: Path | None = None,
    source_chunk_size: int = FLYING_TULIP_SOURCE_CHUNK_SIZE,
) -> FlyingTulipContextPrefillResult:
    """Collect every Flying Tulip source event in a half-open block range.

    This function is restartable: completed source rows are inserted
    idempotently and a conflicting duplicate stops the run. It has no JSON-RPC
    log reads and does not construct synthetic prices.

    :param web3:
        RPC connection for the same chain as Hypersync.
    :param hypersync_client:
        Configured throttle-aware Hypersync client.
    :param start_block:
        Inclusive source boundary, normally the sftUSD proxy deployment block.
    :param end_block:
        Exclusive safe source head.
    :param context_path:
        Optional shared contextual-cache path.
    :param source_chunk_size:
        Maximum source blocks per observable Hypersync request.
    :return:
        Counts for the complete requested source range.
    """

    chain_id = web3.eth.chain_id
    vault_address = FLYING_TULIP_SFTUSD_BY_CHAIN.get(chain_id)
    if vault_address is None:
        raise ValueError(f"Flying Tulip source history is unsupported on chain {chain_id}")
    if not 1 <= start_block < end_block:
        raise ValueError(f"Invalid Flying Tulip source range: [{start_block}, {end_block})")
    if source_chunk_size <= 0:
        raise ValueError(f"source_chunk_size must be positive, got {source_chunk_size}")
    path = context_path or get_pipeline_data_dir() / "vault-historical-context.duckdb"
    epoch_count = supply_count = inserted = 0
    chunk_count = (end_block - start_block + source_chunk_size - 1) // source_chunk_size
    with asyncio.Runner() as runner, FlyingTulipHistoricalContextStore(path) as store, tqdm(total=chunk_count, desc=f"Flying Tulip source history on {chain_id}", unit="chunk") as progress_bar:
        for chunk_start in range(start_block, end_block, source_chunk_size):
            chunk_end = min(chunk_start + source_chunk_size, end_block)
            logger.info("Flying Tulip chain %d: fetching blocks %d-%d", chain_id, chunk_start, chunk_end)
            epochs, supply_changes = runner.run(fetch_source_chunk(hypersync_client, chain_id, vault_address, chunk_start, chunk_end))
            store.connection.execute("BEGIN TRANSACTION")
            try:
                inserted += sum(store.insert_epoch(event) for event in epochs)
                inserted += sum(store.insert_supply_change(event) for event in supply_changes)
            except (duckdb.Error, ValueError):
                store.connection.execute("ROLLBACK")
                raise
            else:
                store.connection.execute("COMMIT")
            epoch_count += len(epochs)
            supply_count += len(supply_changes)
            logger.info("Flying Tulip chain %d: blocks %d-%d fetched epochs=%d supply_events=%d", chain_id, chunk_start, chunk_end, len(epochs), len(supply_changes))
            progress_bar.update()
            progress_bar.set_postfix({"epochs": epoch_count, "supply": supply_count})
        store.set_source_scan_end_block(chain_id, vault_address, end_block)
    return FlyingTulipContextPrefillResult(chain_id, start_block, end_block, epoch_count, supply_count, inserted)
