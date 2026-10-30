"""Address-scoped historical vault price backfills for numbered migrations.

When a vault is discovered, repaired or reclassified after it started
operating, the scanner has no price rows for the period before it began
collecting them. A numbered migration closes that gap by reading old onchain
state for the reviewed vaults only, through the common writer
:py:func:`eth_defi.vault.historical.scan_historical_prices_to_parquet`.

:py:func:`run_price_backfill` stages a copy of the shared raw price Parquet,
replaces only the reviewed vaults' rows inside each half-open block range,
validates the row accounting and the preserved later rows, then publishes the
staged file once with a hard-link backup. Preview performs the same reads and
validation on scratch copies and discards them. Scheduled reader state,
metadata and all other vaults are never changed.

Production needs no backfill-specific parameters: chains, addresses and block
ranges are constants frozen in the migration. For a quick local check of a new
backfill, ``MIGRATION_BACKFILL_BLOCKS`` limits each range to its first blocks.
It is accepted only in preview, so a partial history can never be journalled as
a completed migration. See ``.claude/docs/migration-scripts.md``.
"""

import datetime
import hashlib
import logging
import os
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.compat import native_datetime_utc_now
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.historical import MONAD_CHAIN_ID, fetch_monad_state_available, scan_historical_prices_to_parquet
from eth_defi.vault_protocols.migration import MigrationContext

logger = logging.getLogger(__name__)

#: Development-only environment variable limiting each backfill range.
#:
#: Set to a positive block count to scan only the first blocks of every
#: reviewed range. Rejected outside preview.
BACKFILL_BLOCKS_ENV = "MIGRATION_BACKFILL_BLOCKS"

#: Raw hourly price file shared by all chains.
PRICE_FILE_NAME = "vault-prices-1h.parquet"

#: Scheduled reader-state pickle, hashed to prove it is not modified.
READER_STATE_FILE_NAME = "vault-reader-state-1h.pickle"


@dataclass(slots=True, frozen=True)
class PriceBackfillTarget:
    """One reviewed vault and the half-open block range to backfill.

    Choose ``end_block`` as the first existing price observation, so rows the
    scanner already collected stay untouched and a rerun replaces only the
    same historical interval.
    """

    #: Chain of the vault.
    chain_id: int

    #: Vault share-token address, lower case.
    vault_address: HexAddress

    #: Inclusive first block, usually the vault's first-seen block.
    start_block: int

    #: Exclusive end block, usually the first existing price block.
    end_block: int

    def as_spec(self) -> VaultSpec:
        """Return the metadata key of this target.

        :return: Chain and address of the vault.
        """
        return VaultSpec(self.chain_id, self.vault_address)


class PriceBackfillOutcome(TypedDict):
    """JSON-safe report of one backfilled vault."""

    #: ``chain-address`` identifier.
    vault: str

    #: ``written``, ``missing_vault`` or ``monad_state_unavailable``.
    status: str

    #: Planned inclusive start block, after any development limit.
    planned_start_block: int

    #: Planned exclusive end block, after any development limit.
    planned_end_block: int

    #: Actual first scanned block; later than planned when Monad state was evicted.
    scanned_start_block: int | None

    #: Existing rows of this vault replaced inside the scanned range.
    rows_deleted: int

    #: New rows written for this vault.
    rows_written: int

    #: New rows with a share price.
    price_rows_written: int

    #: Existing rows at or after the end block, preserved unchanged.
    preserved_later_rows: int

    #: UTC timestamp of the first new row, ISO formatted.
    first_written_at: str | None

    #: UTC timestamp of the last new row, ISO formatted.
    last_written_at: str | None


def read_backfill_block_limit(*, dry_run: bool) -> int | None:
    """Read the development-only block limit for a local backfill check.

    A limited range proves that the migration's readers and writer work
    without scanning the whole history. Applying it would journal a partial
    history as complete, so the variable is refused outside preview.

    :param dry_run: Whether the migration runs in preview mode.
    :return: Positive block count, or ``None`` when unset.
    :raises ValueError: If the value is not a positive integer or is set during apply.
    """
    value = os.environ.get(BACKFILL_BLOCKS_ENV)
    if value is None or value == "":
        return None
    if not dry_run:
        raise ValueError(f"{BACKFILL_BLOCKS_ENV} is a development preview limit and cannot be used when applying a migration")
    limit = int(value)
    if limit < 1:
        raise ValueError(f"{BACKFILL_BLOCKS_ENV} must be a positive block count, got {value!r}")
    return limit


def _hash_file(path: Path) -> str | None:
    """Hash a file to prove it was not modified.

    :param path: File to hash.
    :return: SHA-256 hex digest, or ``None`` if the file does not exist.
    """
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _load_vault_rows(price_path: Path, chain_id: int, vault_address: str) -> pa.Table:
    """Read one vault's rows, all columns, from the shared raw price Parquet.

    :param price_path: Raw price Parquet file.
    :param chain_id: Vault chain.
    :param vault_address: Lower-case vault address.
    :return: The vault's rows in persisted order.
    """
    source = ds.dataset(price_path, format="parquet")
    predicate = (ds.field("chain") == chain_id) & (ds.field("address") == vault_address)
    return source.to_table(filter=predicate)


def _later_rows(table: pa.Table, end_block: int) -> pa.Table:
    """Select rows at or after the exclusive end block, sorted by block.

    :param table: One vault's rows.
    :param end_block: Exclusive end block of the backfill range.
    :return: Preserved rows in block order.
    """
    later = table.filter(pc.greater_equal(table["block_number"], end_block))
    return later.sort_by("block_number")


def _assert_rows_unchanged(before: pa.Table, after: pa.Table, spec_id: str) -> None:
    """Compare preserved rows column by column, treating matching NaNs as equal.

    PyArrow equality treats a floating NaN as unequal to itself, and raw price
    rows contain NaNs in optional numeric fields. Compare the NaN masks first,
    then the values with matching NaNs replaced by nulls. This still rejects a
    changed value, a NaN-to-null change or a schema change.

    :param before: Preserved rows read before the scan.
    :param after: The same rows read from the staged file.
    :param spec_id: Vault identifier for error messages.
    :return: ``None`` when every preserved row is identical.
    :raises RuntimeError: If the row count, schema or any value differs.
    """
    if before.num_rows != after.num_rows or not before.schema.equals(after.schema, check_metadata=False):
        message = f"Backfill of {spec_id} changed the count or schema of rows at or after its end block"
        raise RuntimeError(message)
    for field in before.schema:
        original = before[field.name]
        staged = after[field.name]
        if pa.types.is_floating(field.type):
            original_nan = pc.fill_null(pc.is_nan(original), False)
            staged_nan = pc.fill_null(pc.is_nan(staged), False)
            if not original_nan.equals(staged_nan):
                message = f"Backfill of {spec_id} changed a NaN in {field.name} at or after its end block"
                raise RuntimeError(message)
            original = pc.if_else(original_nan, pa.scalar(None, type=field.type), original)
            staged = pc.if_else(staged_nan, pa.scalar(None, type=field.type), staged)
        if not original.equals(staged):
            message = f"Backfill of {spec_id} changed rows at or after its end block in {field.name}"
            raise RuntimeError(message)


def _plan_range(target: PriceBackfillTarget, limit: int | None) -> tuple[int, int]:
    """Apply the optional development limit to a reviewed range.

    :param target: Reviewed vault and range.
    :param limit: Optional preview block limit.
    :return: Inclusive start and exclusive end block.
    """
    end_block = target.end_block if limit is None else min(target.end_block, target.start_block + limit)
    return target.start_block, end_block


def _detection(context: MigrationContext, target: PriceBackfillTarget) -> ERC4262VaultDetection | None:
    """Return the persisted detection record of a reviewed target.

    A vault absent from the metadata database has nothing to backfill; it is
    reported, never created. A present row must carry its detection record.

    :param context: Migration context holding the staged metadata.
    :param target: Reviewed vault.
    :return: Detection with features and first-seen block, or ``None`` if the vault row is absent.
    :raises ValueError: If the detection is missing or the range predates detection.
    """
    row = context.metadata().rows.get(target.as_spec())
    if row is None:
        return None
    if row.get("_detection_data") is None:
        raise ValueError(f"Backfill target {target.as_spec().as_string_id()} has no metadata detection record")
    detection = row["_detection_data"]
    if target.start_block < detection.first_seen_at_block:
        raise ValueError(f"Backfill target {target.as_spec().as_string_id()} starts before its first-seen block {detection.first_seen_at_block}")
    if target.end_block <= target.start_block:
        raise ValueError(f"Backfill target {target.as_spec().as_string_id()} has an empty block range")
    return detection


def _backfill_target(
    *,
    target: PriceBackfillTarget,
    detection: ERC4262VaultDetection,
    web3: Web3,
    rpc_url: str,
    staged_path: Path,
    token_cache: TokenDiskCache,
    timestamp_folder: Path,
    hypersync_client: object,
    limit: int | None,
    frequency: str,
    max_workers: int,
) -> PriceBackfillOutcome:
    """Scan one target into the staged Parquet and validate the result.

    :param target: Reviewed vault and range.
    :param detection: Persisted detection with features and first-seen block.
    :param web3: Connection to the target's chain.
    :param rpc_url: Configured RPC URL for worker connections.
    :param staged_path: Scratch copy of the raw price Parquet.
    :param token_cache: Scratch token metadata cache.
    :param timestamp_folder: Dense timestamp cache folder used by the scan.
    :param hypersync_client: Hypersync client for timestamp reads, or ``None``.
    :param limit: Optional preview block limit.
    :param frequency: Sampling frequency of the raw price file.
    :param max_workers: Historical read workers.
    :return: Validated per-vault outcome.
    :raises RuntimeError: If archive state is missing or the write breaks the row accounting.
    """
    spec_id = target.as_spec().as_string_id()
    start_block, end_block = _plan_range(target, limit)
    outcome = PriceBackfillOutcome(
        vault=spec_id,
        status="written",
        planned_start_block=start_block,
        planned_end_block=end_block,
        scanned_start_block=None,
        rows_deleted=0,
        rows_written=0,
        price_rows_written=0,
        preserved_later_rows=0,
        first_written_at=None,
        last_written_at=None,
    )

    # Monad keeps only a provider-dependent recent state window. If even the
    # last block of the gap is evicted, the gap cannot be reconstructed and
    # existing rows are preserved, as required by the repository's Monad rules.
    if target.chain_id == MONAD_CHAIN_ID:
        if not fetch_monad_state_available(web3, end_block - 1):
            logger.warning("Monad state for %s before block %d is no longer readable; existing rows are preserved", spec_id, end_block)
            outcome["status"] = "monad_state_unavailable"
            return outcome
    elif not web3.eth.get_code(Web3.to_checksum_address(target.vault_address), block_identifier=start_block):
        raise RuntimeError(f"No vault bytecode for {spec_id} at block {start_block}; the RPC provider lacks archive state or the range starts before deployment")

    before = _load_vault_rows(staged_path, target.chain_id, target.vault_address)
    before_blocks = before["block_number"].to_pylist()
    later_before = _later_rows(before, end_block)
    total_before = pq.ParquetFile(staged_path).metadata.num_rows

    vault = create_vault_instance(web3, target.vault_address, detection.features, token_cache=token_cache)
    if vault is None:
        raise RuntimeError(f"Could not create a vault reader for {spec_id} with features {sorted(f.value for f in detection.features)}")
    vault.first_seen_at_block = detection.first_seen_at_block

    result = scan_historical_prices_to_parquet(
        output_fname=staged_path,
        web3=web3,
        web3factory=MultiProviderWeb3Factory(rpc_url),
        vaults=[vault],
        token_cache=token_cache,
        start_block=start_block,
        end_block=end_block,
        max_workers=max_workers,
        frequency=frequency,
        hypersync_client=hypersync_client,
        timestamp_cache_file=timestamp_folder,
        vault_addresses={target.vault_address},
    )

    scanned_start = result["start_block"]
    expected_deleted = sum(scanned_start <= block < end_block for block in before_blocks)
    if result["chain_id"] != target.chain_id or result["end_block"] != end_block or scanned_start < start_block:
        raise RuntimeError(f"Backfill of {spec_id} scanned an unexpected chain or range: {result['chain_id']} [{scanned_start}, {result['end_block']})")
    if target.chain_id != MONAD_CHAIN_ID and scanned_start != start_block:
        raise RuntimeError(f"Backfill of {spec_id} did not start at the reviewed block {start_block}")
    if result["rows_deleted"] != expected_deleted or result["existing_row_count"] != total_before - expected_deleted:
        raise RuntimeError(f"Backfill of {spec_id} deleted rows outside its reviewed scope")
    if set(result["rows_written_by_vault"]) - {target.vault_address}:
        raise RuntimeError(f"Backfill of {spec_id} wrote rows for other vaults")
    if result["price_rows_written_by_vault"].get(target.vault_address, 0) == 0:
        raise RuntimeError(f"Backfill of {spec_id} produced no share price in [{scanned_start}, {end_block})")
    if pq.ParquetFile(staged_path).metadata.num_rows != total_before - expected_deleted + result["rows_written"]:
        raise RuntimeError(f"Backfill of {spec_id} left an unexpected staged row count")

    after = _load_vault_rows(staged_path, target.chain_id, target.vault_address)
    _assert_rows_unchanged(later_before, _later_rows(after, end_block), spec_id)
    written = after.filter(pc.and_(pc.greater_equal(after["block_number"], scanned_start), pc.less(after["block_number"], end_block)))
    timestamps: list[datetime.datetime] = written["timestamp"].to_pylist()

    outcome.update(
        scanned_start_block=scanned_start,
        rows_deleted=expected_deleted,
        rows_written=result["rows_written"],
        price_rows_written=result["price_rows_written_by_vault"][target.vault_address],
        preserved_later_rows=later_before.num_rows,
        first_written_at=min(timestamps).isoformat() if timestamps else None,
        last_written_at=max(timestamps).isoformat() if timestamps else None,
    )
    logger.info("Backfilled %s: %d rows in [%d, %d), %d later rows preserved", spec_id, result["rows_written"], scanned_start, end_block, later_before.num_rows)
    return outcome


def _validate_targets(targets: Sequence[PriceBackfillTarget], frequency: str) -> None:
    """Reject an invalid reviewed target list before any data is read.

    :param targets: Frozen reviewed vaults and ranges.
    :param frequency: Requested sampling frequency.
    :return: ``None`` when the targets are valid.
    :raises ValueError: If the list is empty, duplicated, not lower case or the frequency is unsupported.
    """
    if frequency != "1h":
        message = f"Only the hourly raw price file is supported, got {frequency!r}"
        raise ValueError(message)
    if not targets:
        message = "A price backfill needs at least one reviewed target"
        raise ValueError(message)
    if len({target.as_spec() for target in targets}) != len(targets):
        message = "Price backfill targets must be unique"
        raise ValueError(message)
    if any(target.vault_address != target.vault_address.lower() for target in targets):
        message = "Price backfill target addresses must be lower case"
        raise ValueError(message)


def _missing_outcome(target: PriceBackfillTarget) -> PriceBackfillOutcome:
    """Report a reviewed target whose vault is absent from the metadata database.

    :param target: Reviewed vault and range.
    :return: Outcome with status ``missing_vault``.
    """
    return PriceBackfillOutcome(
        vault=target.as_spec().as_string_id(),
        status="missing_vault",
        planned_start_block=target.start_block,
        planned_end_block=target.end_block,
        scanned_start_block=None,
        rows_deleted=0,
        rows_written=0,
        price_rows_written=0,
        preserved_later_rows=0,
        first_written_at=None,
        last_written_at=None,
    )


def _check_resources(context: MigrationContext, price_path: Path, timestamp_databases: Sequence[Path]) -> None:
    """Check the raw price file, dense timestamp caches and free space.

    :param context: Migration attempt; preview also copies the timestamp caches.
    :param price_path: Raw price Parquet file.
    :param timestamp_databases: Per-chain dense timestamp caches in scope.
    :return: ``None`` when the backfill can be staged.
    :raises RuntimeError: If a file is missing or the disk lacks space for the staged copies.
    """
    if not price_path.is_file():
        message = f"Raw price file is missing: {price_path}"
        raise RuntimeError(message)
    missing_caches = [str(path) for path in timestamp_databases if not path.is_file()]
    if missing_caches:
        message = f"Dense timestamp cache is missing; restore it before a backfill: {missing_caches}"
        raise RuntimeError(message)
    required_space = price_path.stat().st_size * 2
    if context.dry_run:
        required_space += sum(path.stat().st_size for path in timestamp_databases)
    if shutil.disk_usage(context.data_dir).free < required_space:
        message = f"Not enough free space in {context.data_dir} for the staged backfill, need {required_space:,} bytes"
        raise RuntimeError(message)


def _scan_targets(
    *,
    targets: Sequence[PriceBackfillTarget],
    detections: dict[VaultSpec, ERC4262VaultDetection],
    staged_path: Path,
    token_cache: TokenDiskCache,
    timestamp_folder: Path,
    limit: int | None,
    frequency: str,
) -> list[PriceBackfillOutcome]:
    """Scan every target into the staged file, one connection per chain.

    :param targets: Reviewed vaults present in the metadata database.
    :param detections: Detection records keyed by vault.
    :param staged_path: Scratch copy of the raw price Parquet.
    :param token_cache: Scratch token metadata cache.
    :param timestamp_folder: Dense timestamp cache folder used by the scan.
    :param limit: Optional preview block limit.
    :param frequency: Sampling frequency of the raw price file.
    :return: Validated outcomes in chain order.
    :raises RuntimeError: If an RPC connection is wrong or a range exceeds the safe head.
    """
    max_workers = int(os.environ.get("MAX_WORKERS", "4"))
    outcomes: list[PriceBackfillOutcome] = []
    for chain_id in sorted({target.chain_id for target in targets}):
        rpc_url = read_json_rpc_url(chain_id)
        web3 = create_multi_provider_web3(rpc_url)
        if web3.eth.chain_id != chain_id:
            message = f"RPC for chain {chain_id} returned chain {web3.eth.chain_id}"
            raise RuntimeError(message)
        head = get_almost_latest_block_number(web3)
        hypersync = configure_hypersync_from_env(web3)
        for target in (target for target in targets if target.chain_id == chain_id):
            if _plan_range(target, limit)[1] > head:
                message = f"Backfill end block for {target.as_spec().as_string_id()} is beyond the safe head {head}"
                raise RuntimeError(message)
            outcome = _backfill_target(
                target=target,
                detection=detections[target.as_spec()],
                web3=web3,
                rpc_url=rpc_url,
                staged_path=staged_path,
                token_cache=token_cache,
                timestamp_folder=timestamp_folder,
                hypersync_client=hypersync.hypersync_client,
                limit=limit,
                frequency=frequency,
                max_workers=max_workers,
            )
            outcomes.append(outcome)
    return outcomes


def _publish_staged(context: MigrationContext, price_path: Path, staged_path: Path) -> None:
    """Replace the raw price file with the validated staged copy.

    The original is kept as a hard link, which costs no copy, and both paths
    are journalled for recovery.

    :param context: Migration attempt in apply mode.
    :param price_path: Production raw price Parquet file.
    :param staged_path: Validated staged copy on the same filesystem.
    :return: ``None`` after the durable replacement.
    """
    backup_path = context.data_dir / f"{PRICE_FILE_NAME}.bak-{context.script_name}-{native_datetime_utc_now():%Y%m%dT%H%M%S}-{os.getpid()}"
    os.link(price_path, backup_path)
    context.record_modified_file(backup_path, backup=True)
    os.replace(staged_path, price_path)
    directory = os.open(context.data_dir, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    context.record_modified_file(price_path)


def run_price_backfill(
    context: MigrationContext,
    targets: Sequence[PriceBackfillTarget],
    *,
    frequency: str = "1h",
) -> list[PriceBackfillOutcome]:
    """Backfill reviewed vaults' historical prices and publish them atomically.

    All targets are scanned into one staged copy of the raw price Parquet,
    grouped by chain so each chain gets one RPC and Hypersync connection. The
    staged file replaces production data only after every target validates,
    and only in apply mode; the original is kept as a hard-link backup and
    recorded in the migration journal. Preview copies the dense timestamp
    caches so no provider cache is written. A target absent from the metadata
    database is reported as ``missing_vault``.

    Infrastructure configuration is read from the environment: ``JSON_RPC_*``,
    Hypersync credentials, ``MAX_WORKERS`` (default 4) and ``TIMESTAMP_CACHE``.
    The dense per-chain timestamp cache must already exist.

    :param context: Guarded migration attempt; the runner holds the writer lock.
    :param targets: Frozen reviewed vaults and half-open block ranges.
    :param frequency: Sampling frequency; only the hourly raw file is supported.
    :return: Per-vault outcomes, also recorded with :py:meth:`MigrationContext.report`.
    :raises ValueError: If targets, metadata or configuration are invalid.
    :raises RuntimeError: If required data is missing or validation fails.
    """
    _validate_targets(targets, frequency)
    limit = read_backfill_block_limit(dry_run=context.dry_run)
    detections = {target.as_spec(): _detection(context, target) for target in targets}
    outcomes = [_missing_outcome(target) for target in targets if detections[target.as_spec()] is None]
    present = [target for target in targets if detections[target.as_spec()] is not None]
    if not present:
        logger.warning("No reviewed backfill target exists in the metadata database")
        context.report(price_backfill=outcomes, backfill_block_limit=limit, price_backfill_published=False)
        return outcomes

    timestamp_cache = Path(os.environ.get("TIMESTAMP_CACHE", DEFAULT_TIMESTAMP_CACHE_FOLDER)).expanduser()
    timestamp_databases = [timestamp_cache / f"{chain_id}-timestamps.duckdb" for chain_id in sorted({target.chain_id for target in present})]
    price_path = context.data_dir / PRICE_FILE_NAME
    reader_state_path = context.data_dir / READER_STATE_FILE_NAME
    _check_resources(context, price_path, timestamp_databases)

    state_digest = _hash_file(reader_state_path)
    with tempfile.TemporaryDirectory(prefix=f"price-backfill-{context.script_name}-", dir=context.data_dir) as scratch_name:
        scratch = Path(scratch_name)
        staged_path = scratch / PRICE_FILE_NAME
        logger.info("Staging %s for the backfill of %d vaults", price_path, len(present))
        shutil.copy2(price_path, staged_path)
        timestamp_folder = timestamp_cache
        if context.dry_run:
            timestamp_folder = scratch / "block-timestamp"
            timestamp_folder.mkdir()
            for path in timestamp_databases:
                shutil.copy2(path, timestamp_folder / path.name)
        token_cache = TokenDiskCache(scratch / "tokens.sqlite")
        try:
            outcomes += _scan_targets(targets=present, detections=detections, staged_path=staged_path, token_cache=token_cache, timestamp_folder=timestamp_folder, limit=limit, frequency=frequency)
        finally:
            token_cache.close()

        if _hash_file(reader_state_path) != state_digest:
            message = "Scheduled reader state changed during the price backfill"
            raise RuntimeError(message)
        published = not context.dry_run and any(outcome["status"] == "written" for outcome in outcomes)
        if published:
            _publish_staged(context, price_path, staged_path)

    context.report(price_backfill=outcomes, backfill_block_limit=limit, price_backfill_published=published)
    return outcomes
