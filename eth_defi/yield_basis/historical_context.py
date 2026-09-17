"""Persist YieldBasis valuation context used by the common price scanner.

The common Parquet schema intentionally remains unchanged. This context table
stores the raw values needed to reproduce the primary redemption-value curve,
its TRD and the complementary fundamental native-asset return. The fixed
generic USD-stablecoin entry and exit costs are metadata assumptions and do
not belong in this historical source table.
"""

import datetime
import logging
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING

import duckdb
from eth_typing import HexAddress
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, Web3Exception

from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.hypersync.hypersync_timestamp import fetch_exact_block_timestamps_using_hypersync_cached
from eth_defi.hypersync.session import ThrottledHypersyncClient
from eth_defi.middleware import ProbablyNodeHasNoBlock
from eth_defi.vault.vaultdb import get_pipeline_data_dir
from eth_defi.yield_basis.addresses import YIELD_BASIS_ACTIVE_MARKETS
from eth_defi.yield_basis.metrics import LT_SHARE_SCALE, MAX_ASSET_DECIMALS, asset_price_per_share, redemption_usd_price_per_share, staked_ratio, temporary_redemption_discount

if TYPE_CHECKING:
    from eth_defi.yield_basis.vault import YieldBasisVault

logger = logging.getLogger(__name__)

#: Number of historical contract-read tasks persisted as one resumable batch.
#: Four active products make this roughly 100 hourly samples per commit.
YIELD_BASIS_CONTEXT_COMMIT_TASKS: int = 400


def get_yield_basis_historical_context_path() -> Path:
    """Return the shared contextual-history DuckDB path.

    YieldBasis owns one table in the database while the common Parquet file
    remains the canonical vault price dataset.

    :return:
        Default path below the configured vault pipeline directory.
    """

    return get_pipeline_data_dir() / "vault-historical-context.duckdb"


@dataclass(frozen=True, slots=True)
class YieldBasisHistoricalObservation:
    """One reproducible source observation for a YieldBasis LT market.

    Raw values are stored instead of pre-rounded derived metrics. This makes the
    valuation basis auditable and lets future reports recompute fundamental
    value, marginal redemption value and TRD without rescanning archive state.
    """

    #: EVM chain ID; currently always Ethereum mainnet.
    chain_id: int
    #: Historical state block used for every value in this observation.
    block_number: int
    #: Naive-UTC-compatible Unix timestamp for ``block_number``.
    block_timestamp: int
    #: LT/yb-LP share-token address.
    lt_address: HexAddress
    #: BTC or ETH underlying-token address.
    asset_address: HexAddress
    #: ERC-20 precision needed to interpret the raw redemption amount.
    #: Stored per row because the supported assets use both 8 and 18 decimals.
    asset_decimals: int
    #: Raw Curve asset/crvUSD oracle value.
    raw_asset_crvusd_price: int
    #: Raw LT fundamental PPS in native-asset units.
    raw_asset_price_per_share: int
    #: Raw LT amount passed to ``preview_withdraw``; at most one whole share.
    raw_preview_shares: int
    #: Raw underlying amount returned by the same-block redemption preview.
    raw_redemption_assets: int
    #: Raw effective LT supply from ``updated_balances()``.
    raw_effective_supply: int
    #: Raw effective LT units held by the staker.
    raw_staked_supply: int

    @property
    def asset_price_per_share(self) -> Decimal:
        """Return fundamental LT PPS in the native asset.

        This diagnostic excludes TRD and BTC or ETH price movement.

        :return:
            Fundamental native-asset value of one LT share.
        """

        return asset_price_per_share(self.raw_asset_price_per_share)

    @property
    def temporary_redemption_discount(self) -> Decimal:
        """Return marginal redemption value relative to fundamental PPS.

        :return:
            Decimal ratio where ``-0.01`` means a 1% discount.
        """

        return temporary_redemption_discount(
            self.raw_preview_shares,
            self.raw_redemption_assets,
            self.raw_asset_price_per_share,
            asset_decimals=self.asset_decimals,
        )

    @property
    def share_price(self) -> Decimal:
        """Return the primary marginal redemption value in USD.

        The same-block redemption preview incorporates TRD once and the Curve
        oracle incorporates BTC or ETH price movement. This product-value
        measure remains gross of the fixed entry and exit conversion costs
        exposed separately by the VaultBase adapter.

        :return:
            Gross marginal redemption-value equivalent per LT share in USD.
        """

        return redemption_usd_price_per_share(
            self.raw_preview_shares,
            self.raw_redemption_assets,
            self.raw_asset_crvusd_price,
            asset_decimals=self.asset_decimals,
        )

    @property
    def effective_supply(self) -> Decimal:
        """Return effective LT supply from ``updated_balances``.

        :return:
            Whole LT shares including the supply represented by staked LT.
        """

        return Decimal(self.raw_effective_supply) / LT_SHARE_SCALE

    @property
    def staked_ratio(self) -> Decimal | None:
        """Return the effective staked-to-total LT supply ratio.

        :return:
            Decimal ratio, or ``None`` for zero effective supply.
        """

        return staked_ratio(self.raw_effective_supply, self.raw_staked_supply)

    @property
    def total_assets(self) -> Decimal:
        """Return redemption-value-equivalent USD equity.

        The multiplication applies a marginal one-share preview to effective
        supply. It is useful for comparable TVL reporting but is not a promise
        that the entire vault could be redeemed at the same marginal price.

        :return:
            Marginal redemption value multiplied by effective LT supply.
        """

        return self.share_price * self.effective_supply


@dataclass(frozen=True, slots=True)
class YieldBasisContextPrefillResult:
    """Summarise one context prefill operation."""

    #: Chain whose context was scanned.
    chain_id: int
    #: Inclusive first requested block.
    start_block: int
    #: Exclusive last requested block.
    end_block: int
    #: Valid observations returned by historical state calls.
    observations_fetched: int
    #: New observations inserted after deduplication.
    observations_inserted: int


def _validate_uint(value: int | str, *, field: str) -> int:
    """Validate one unsigned EVM integer before storing it as text.

    :param value:
        Integer-compatible raw contract value.
    :param field:
        Field name included in validation errors.
    :return:
        Parsed non-negative integer.
    """

    if isinstance(value, bool):
        raise TypeError(f"{field} must be an unsigned integer, got bool")
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f"{field} must be non-negative, got {parsed}")
    return parsed


def _validate_asset_decimals(value: int) -> int:
    """Validate the ERC-20 precision stored with a redemption preview.

    :param value:
        Underlying token decimal precision from the reviewed market record.
    :return:
        Validated precision suitable for DuckDB ``UTINYINT`` storage.
    """

    if isinstance(value, bool) or not 0 <= value <= MAX_ASSET_DECIMALS:
        raise ValueError(f"asset_decimals must be between 0 and {MAX_ASSET_DECIMALS}, got {value!r}")
    return value


class YieldBasisHistoricalContextStore(AbstractContextManager):
    """Manage YieldBasis observations without DuckDB ART keys."""

    def __init__(self, path: Path | None = None) -> None:
        """Open or create the protocol-owned tables."""

        self.path = path or get_yield_basis_historical_context_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(self.path))
        self.connection.execute("SET wal_autocheckpoint = '1TB'")
        self._create_schema()

    def _create_schema(self) -> None:
        """Create or explicitly migrate the direct-column observation table.

        Logical-key checks are performed by batch joins instead of DuckDB
        ``PRIMARY KEY`` or ``UNIQUE`` constraints. New schema columns are added
        as nullable, as required for an append-only production migration.
        Reviewed asset precision can be reconstructed from the immutable
        allow-list. Legacy rows for currently reviewed products without a
        redemption preview cannot reproduce TRD and are therefore removed for
        the backfill to rebuild. Other products' rows remain untouched.
        A legacy ``redemption_missing_reason`` column may remain in an existing
        DuckDB table; it is deliberately ignored rather than destructively
        rewriting production storage.

        :return:
            None.
        """

        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS yield_basis_historical_context (
                chain_id UINTEGER NOT NULL,
                block_number UBIGINT NOT NULL,
                block_timestamp UBIGINT NOT NULL,
                lt_address VARCHAR NOT NULL,
                asset_address VARCHAR NOT NULL,
                asset_decimals UTINYINT,
                raw_asset_crvusd_price VARCHAR NOT NULL,
                raw_asset_price_per_share VARCHAR NOT NULL,
                raw_preview_shares VARCHAR,
                raw_redemption_assets VARCHAR,
                raw_effective_supply VARCHAR NOT NULL,
                raw_staked_supply VARCHAR NOT NULL
            )
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info('yield_basis_historical_context')").fetchall()}
        if "asset_decimals" not in columns:
            # Add a nullable field first and populate only reviewed LT/asset
            # tuples without guessing precision for other historical products.
            self.connection.execute("ALTER TABLE yield_basis_historical_context ADD COLUMN asset_decimals UTINYINT")
        for review in YIELD_BASIS_ACTIVE_MARKETS.values():
            self.connection.execute(
                """
                UPDATE yield_basis_historical_context
                SET asset_decimals = ?
                WHERE asset_decimals IS NULL
                  AND chain_id = 1
                  AND lower(lt_address) = ?
                  AND lower(asset_address) = ?
                """,
                (review.asset_decimals, review.lt_address.lower(), review.asset_address.lower()),
            )
        reviewed_lt_addresses = [review.lt_address.lower() for review in YIELD_BASIS_ACTIVE_MARKETS.values()]
        unresolved = self.connection.execute(
            """
            SELECT chain_id, lt_address, asset_address, block_number
            FROM yield_basis_historical_context
            WHERE asset_decimals IS NULL
              AND lower(lt_address) = ANY(?)
            LIMIT 1
            """,
            (reviewed_lt_addresses,),
        ).fetchone()
        if unresolved:
            raise RuntimeError(f"YieldBasis context has unresolved asset precision for a reviewed market at {tuple(unresolved)}")
        incomplete_accounting = self.connection.execute(
            """
            SELECT count(*), min(block_number), max(block_number)
            FROM yield_basis_historical_context
            WHERE (raw_preview_shares IS NULL
               OR raw_redemption_assets IS NULL)
              AND lower(lt_address) = ANY(?)
            """,
            (reviewed_lt_addresses,),
        ).fetchone()
        incomplete_count, first_incomplete_block, last_incomplete_block = incomplete_accounting
        if incomplete_count:
            # These observations are re-derivable archive-node data. Keeping a
            # legacy row would omit TRD and mix fundamental and redemption
            # accounting bases in the same equity curve.
            logger.warning(
                "Removing %d legacy YieldBasis context rows without complete redemption inputs from blocks %d-%d; rebuild them with scripts/erc-4626/backfill-yield-basis-vault-prices.py",
                incomplete_count,
                first_incomplete_block,
                last_incomplete_block,
            )
            self.connection.execute(
                """
                DELETE FROM yield_basis_historical_context
                WHERE (raw_preview_shares IS NULL
                   OR raw_redemption_assets IS NULL)
                  AND lower(lt_address) = ANY(?)
                """,
                (reviewed_lt_addresses,),
            )

    @staticmethod
    def _values(observation: YieldBasisHistoricalObservation) -> tuple[object, ...]:
        """Convert and validate an observation for direct table storage.

        The preview input/output form one required accounting unit.
        Observations with a reverted preview are omitted before storage;
        fundamental PPS is never substituted into a historical row.

        :param observation:
            Exact same-block YieldBasis source values.
        :return:
            Values in the direct DuckDB column order.
        """

        effective_supply = _validate_uint(observation.raw_effective_supply, field="raw_effective_supply")
        staked_supply = _validate_uint(observation.raw_staked_supply, field="raw_staked_supply")
        if staked_supply > effective_supply:
            message = "raw_staked_supply must not exceed raw_effective_supply"
            raise ValueError(message)
        preview_shares = _validate_uint(observation.raw_preview_shares, field="raw_preview_shares")
        if preview_shares == 0:
            message = "raw_preview_shares must be positive"
            raise ValueError(message)
        redemption_assets = _validate_uint(observation.raw_redemption_assets, field="raw_redemption_assets")
        if redemption_assets == 0:
            message = "raw_redemption_assets must be positive"
            raise ValueError(message)
        return (
            observation.chain_id,
            observation.block_number,
            observation.block_timestamp,
            observation.lt_address.lower(),
            observation.asset_address.lower(),
            _validate_asset_decimals(observation.asset_decimals),
            str(_validate_uint(observation.raw_asset_crvusd_price, field="raw_asset_crvusd_price")),
            str(_validate_uint(observation.raw_asset_price_per_share, field="raw_asset_price_per_share")),
            str(preview_shares),
            str(redemption_assets),
            str(effective_supply),
            str(staked_supply),
        )

    def insert_observations(self, observations: Iterable[YieldBasisHistoricalObservation]) -> int:
        """Insert observations idempotently and reject conflicting blocks.

        One unconstrained temporary table supports both conflict detection and
        deduplication without the DuckDB ART indexes avoided by this pipeline.
        Every source value is immutable for a logical block, so one comparison
        covers fundamental accounting, the redemption preview and token scale.

        :param observations:
            Source observations to insert as one batch.
        :return:
            Number of new logical rows inserted.
        """

        values = tuple(self._values(observation) for observation in observations)
        if not values:
            return 0
        self.connection.execute("DROP TABLE IF EXISTS yield_basis_context_batch")
        self.connection.execute(
            """
            CREATE TEMP TABLE yield_basis_context_batch AS
            SELECT chain_id, block_number, block_timestamp, lt_address,
                   asset_address, asset_decimals, raw_asset_crvusd_price,
                   raw_asset_price_per_share, raw_preview_shares,
                   raw_redemption_assets, raw_effective_supply,
                   raw_staked_supply
            FROM yield_basis_historical_context
            LIMIT 0
            """
        )
        try:
            self.connection.execute("BEGIN TRANSACTION")
            self.connection.executemany("INSERT INTO yield_basis_context_batch VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
            conflict = self.connection.execute(
                """
                SELECT chain_id, lt_address, block_number
                FROM (
                    SELECT chain_id, block_number, block_timestamp, lt_address,
                           asset_address, asset_decimals, raw_asset_crvusd_price,
                           raw_asset_price_per_share, raw_preview_shares,
                           raw_redemption_assets, raw_effective_supply,
                           raw_staked_supply
                    FROM yield_basis_historical_context
                    UNION ALL
                    SELECT * FROM yield_basis_context_batch
                ) rows
                GROUP BY chain_id, lt_address, block_number
                HAVING count(DISTINCT (block_timestamp, asset_address,
                    asset_decimals, raw_asset_crvusd_price,
                    raw_asset_price_per_share, raw_preview_shares,
                    raw_redemption_assets, raw_effective_supply,
                    raw_staked_supply)) > 1
                LIMIT 1
                """
            ).fetchone()
            if conflict:
                raise ValueError(f"YieldBasis context conflict for {tuple(conflict)}")
            before = self.connection.execute("SELECT count(*) FROM yield_basis_historical_context").fetchone()[0]
            self.connection.execute(
                """
                INSERT INTO yield_basis_historical_context (
                    chain_id, block_number, block_timestamp, lt_address,
                    asset_address, asset_decimals, raw_asset_crvusd_price,
                    raw_asset_price_per_share, raw_preview_shares,
                    raw_redemption_assets, raw_effective_supply,
                    raw_staked_supply
                )
                SELECT DISTINCT batch.*
                FROM yield_basis_context_batch batch
                WHERE NOT EXISTS (
                    SELECT 1 FROM yield_basis_historical_context existing
                    WHERE existing.chain_id = batch.chain_id
                      AND lower(existing.lt_address) = lower(batch.lt_address)
                      AND existing.block_number = batch.block_number
                )
                """
            )
            after = self.connection.execute("SELECT count(*) FROM yield_basis_historical_context").fetchone()[0]
            self.connection.execute("COMMIT")
            return int(after - before)
        except (duckdb.Error, ValueError):
            self.connection.execute("ROLLBACK")
            raise
        finally:
            self.connection.execute("DROP TABLE IF EXISTS yield_basis_context_batch")

    def iter_observations(
        self,
        *,
        chain_id: int,
        lt_address: HexAddress,
        start_block: int,
        end_block: int,
        step: int,
    ) -> Iterator[YieldBasisHistoricalObservation]:
        """Yield the latest observation inside each requested block bucket.

        The common writer requests a regular block grid. When more than one
        exact observation falls into a bucket, only the newest one is exposed.

        :param chain_id:
            Chain to query.
        :param lt_address:
            LT share token to query.
        :param start_block:
            Inclusive range boundary.
        :param end_block:
            Exclusive range boundary.
        :param step:
            Bucket width in blocks.
        :return:
            Iterator of bucketed source observations.
        """

        if step <= 0:
            raise ValueError(f"YieldBasis reader step must be positive, got {step}")
        rows = self.connection.execute(
            """
            SELECT block_number, block_timestamp, lt_address, asset_address,
                   asset_decimals, raw_asset_crvusd_price,
                   raw_asset_price_per_share, raw_preview_shares,
                   raw_redemption_assets, raw_effective_supply,
                   raw_staked_supply
            FROM yield_basis_historical_context
            WHERE chain_id = ? AND lower(lt_address) = ?
              AND block_number >= ? AND block_number < ?
            ORDER BY block_number, rowid
            """,
            (chain_id, lt_address.lower(), start_block, end_block),
        ).fetchall()
        buckets: dict[int, YieldBasisHistoricalObservation] = {}
        for row in rows:
            observation = YieldBasisHistoricalObservation(
                chain_id=chain_id,
                block_number=int(row[0]),
                block_timestamp=int(row[1]),
                lt_address=row[2],
                asset_address=row[3],
                asset_decimals=int(row[4]),
                raw_asset_crvusd_price=int(row[5]),
                raw_asset_price_per_share=int(row[6]),
                raw_preview_shares=int(row[7]),
                raw_redemption_assets=int(row[8]),
                raw_effective_supply=int(row[9]),
                raw_staked_supply=int(row[10]),
            )
            bucket = (observation.block_number - start_block) // step
            if bucket not in buckets or observation.block_number >= buckets[bucket].block_number:
                buckets[bucket] = observation
        yield from (buckets[index] for index in sorted(buckets))

    def count_observations(self, *, chain_id: int, lt_address: HexAddress) -> int:
        """Return the number of stored observations for one LT.

        :param chain_id:
            Chain to query.
        :param lt_address:
            LT share-token address to query.
        :return:
            Number of matching context rows.
        """

        return int(self.connection.execute("SELECT count(*) FROM yield_basis_historical_context WHERE chain_id = ? AND lower(lt_address) = ?", (chain_id, lt_address.lower())).fetchone()[0])

    def checkpoint(self) -> None:
        """Flush the deliberately large DuckDB WAL checkpoint."""

        self.connection.execute("CHECKPOINT")

    def close(self) -> None:
        """Checkpoint and close the database connection."""

        try:
            self.checkpoint()
        finally:
            self.connection.close()

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        """Close the context store."""

        self.close()


def fetch_yield_basis_observation(vault: "YieldBasisVault", block_number: int, block_timestamp: int) -> YieldBasisHistoricalObservation | None:
    """Read one reproducible YieldBasis valuation snapshot.

    All contract values come from ``block_number`` and its timestamp comes
    from the shared cache-aware Hypersync timestamp pipeline. A zero-supply
    market returns no observation until it has investment value to report.

    :param vault:
        Reviewed YieldBasis LT adapter.
    :param block_number:
        Historical state block to read.
    :param block_timestamp:
        Unix timestamp already resolved for the block.
    :return:
        Complete source observation, or ``None`` for a zero-supply market.
    """

    required = vault.fetch_historical_observation(block_number)
    if required is None:
        return None
    return YieldBasisHistoricalObservation(
        chain_id=vault.chain_id,
        block_number=block_number,
        block_timestamp=block_timestamp,
        **required,
    )


def _fetch_optional_yield_basis_observation(vault: "YieldBasisVault", block_number: int, block_timestamp: int) -> YieldBasisHistoricalObservation | None:
    """Read one observation while isolating expected unavailable state.

    A contract-state error is ignored only before the reviewed deployment
    block. Once the product exists, RPC and provider errors propagate so a
    transient failure cannot create an unnoticed hole in history. A
    deterministic redemption-preview revert is handled by the adapter and
    produces a logged missing sample instead.

    :param vault:
        Reviewed YieldBasis LT adapter.
    :param block_number:
        Historical Ethereum state block.
    :param block_timestamp:
        Cache-resolved Unix timestamp for the same block.
    :return:
        Source observation, or ``None`` when no source state exists yet.
    """

    try:
        observation = fetch_yield_basis_observation(vault, block_number, block_timestamp)
    except (BadFunctionCallOutput, ContractLogicError, ProbablyNodeHasNoBlock, Web3Exception) as error:
        if vault.first_seen_at_block is not None and block_number < vault.first_seen_at_block:
            logger.debug("YieldBasis state unavailable before deployment for %s at block %d: %s", vault.address, block_number, error)
            return None
        raise
    if observation is None:
        logger.debug("YieldBasis LT %s has no complete valuation observation at block %d", vault.address, block_number)
    return observation


def fetch_and_store_yield_basis_historical_context(
    *,
    web3: Web3,
    vaults: Iterable["YieldBasisVault"],
    start_block: int,
    end_block: int,
    step: int,
    max_workers: int,
    hypersync_client: ThrottledHypersyncClient,
    context_path: Path | None = None,
    blocks: Iterable[int] | None = None,
    timestamp_cache_path: Path | None = None,
) -> YieldBasisContextPrefillResult:
    """Read and persist bounded YieldBasis historical state.

    All valuation calls, including the redemption preview, are required for a
    stored observation. Samples before an LT's reviewed deployment block are
    not scheduled, and a deterministic preview revert leaves a logged gap.
    Successful observations are persisted in bounded batches, so a later
    provider failure does not discard the completed part of the backfill.

    :param web3:
        Archive-capable Ethereum connection.
    :param vaults:
        Reviewed LT adapters to sample.
    :param start_block:
        Inclusive first block.
    :param end_block:
        Exclusive last block.
    :param step:
        Sampling interval in blocks.
    :param max_workers:
        Maximum threaded archive-read workers.
    :param hypersync_client:
        Configured client used by the shared timestamp cache.
    :param context_path:
        Optional context DuckDB override.
    :param blocks:
        Optional explicit block sequence inside the requested range.
    :param timestamp_cache_path:
        Optional dense block-timestamp cache override.
    :return:
        Fetch and insertion counts for the bounded operation.
    """

    if web3.eth.chain_id != 1:
        raise ValueError(f"YieldBasis historical context is supported on Ethereum only, got {web3.eth.chain_id}")
    if not 0 <= start_block < end_block:
        raise ValueError(f"Invalid YieldBasis context range [{start_block}, {end_block})")
    if step <= 0:
        raise ValueError(f"YieldBasis context step must be positive, got {step}")
    if max_workers <= 0:
        raise ValueError(f"YieldBasis context max_workers must be positive, got {max_workers}")
    if hypersync_client is None:
        message = "YieldBasis historical context requires a configured Hypersync client"
        raise RuntimeError(message)
    selected_vaults = tuple(vaults)
    if not selected_vaults:
        return YieldBasisContextPrefillResult(1, start_block, end_block, 0, 0)
    sample_blocks = tuple(blocks) if blocks is not None else tuple(range(start_block, end_block, step))
    invalid_block = next((block_number for block_number in sample_blocks if not start_block <= block_number < end_block), None)
    if invalid_block is not None:
        raise ValueError(f"Context sample block {invalid_block} is outside [{start_block}, {end_block})")
    timestamp_slicer = fetch_exact_block_timestamps_using_hypersync_cached(
        client=hypersync_client,
        chain_id=web3.eth.chain_id,
        block_numbers=sample_blocks,
        cache_path=timestamp_cache_path or DEFAULT_TIMESTAMP_CACHE_FOLDER,
        display_progress=True,
    )
    try:
        timestamps = {block_number: int(timestamp_slicer[block_number].replace(tzinfo=datetime.UTC).timestamp()) for block_number in sample_blocks}
    finally:
        timestamp_slicer.close()
    task_inputs = tuple((vault, block_number) for block_number in sample_blocks for vault in selected_vaults if vault.first_seen_at_block is None or block_number >= vault.first_seen_at_block)
    tasks = (delayed(_fetch_optional_yield_basis_observation)(vault, block_number, timestamps[block_number]) for vault, block_number in task_inputs)
    fetched_count = 0
    inserted = 0
    pending: list[YieldBasisHistoricalObservation] = []
    with YieldBasisHistoricalContextStore(context_path) as store:
        with Parallel(n_jobs=max_workers, backend="threading", return_as="generator") as parallel:
            with tqdm(parallel(tasks), total=len(task_inputs), desc="Fetching YieldBasis context", unit="observation", mininterval=30) as observations:
                for processed, observation in enumerate(observations, start=1):
                    if observation is not None:
                        pending.append(observation)
                        fetched_count += 1
                    if processed % YIELD_BASIS_CONTEXT_COMMIT_TASKS == 0:
                        inserted += store.insert_observations(pending)
                        pending.clear()
                        logger.info("Stored YieldBasis context batch: processed %d/%d tasks, fetched %d observations, inserted %d rows", processed, len(task_inputs), fetched_count, inserted)
        inserted += store.insert_observations(pending)
    return YieldBasisContextPrefillResult(selected_vaults[0].chain_id, start_block, end_block, fetched_count, inserted)
