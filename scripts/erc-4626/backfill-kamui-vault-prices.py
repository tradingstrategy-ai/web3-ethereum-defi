"""Backfill only Kamui's three Ethereum Lagoon vault price histories.

The reviewed half-open range starts at the earliest Kamui detection block and
ends at the first price block recorded by production on 2026-09-28. Existing
rows at and after that boundary are preserved. The common stateless writer
receives only the three reviewed addresses and never updates scheduled reader
state. A staged Parquet copy is validated before replacing the production raw
price file under the shared scanner lock.

``DRY_RUN=true`` (the default) runs and validates the complete historical scan
against scratch copies without publishing prices or updating shared state.
Both modes require an archive-capable ``JSON_RPC_ETHEREUM`` and the usual
Hypersync configuration. The mounted dense Ethereum timestamp cache must be
retained in production.
"""

import datetime
import hashlib
import logging
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from eth_typing import HexAddress
from tabulate import tabulate
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.erc_4626.vault_protocol.lagoon.constants import KAMUI_LAGOON_VAULTS
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.event_reader.timestamp_cache import DEFAULT_TIMESTAMP_CACHE_FOLDER
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.broken_provider import get_almost_latest_block_number
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import MultiProviderWeb3Factory, create_multi_provider_web3
from eth_defi.token import TokenDiskCache
from eth_defi.utils import setup_console_logging, wait_other_writers
from eth_defi.vault.historical import ParquetScanResult, pformat_scan_result, scan_historical_prices_to_parquet
from eth_defi.vault.vaultdb import DEFAULT_READER_STATE_DATABASE, VaultDatabase, get_pipeline_data_dir

logger = logging.getLogger(__name__)

#: Exact reviewed Ethereum products and their first detection blocks.
#: These blocks are from production metadata, inspected on 2026-09-29.
KAMUI_FIRST_SEEN_BLOCKS: dict[HexAddress, int] = {
    HexAddress("0xcda323c2df692d989b24ba51d0acca924cf9a344"): 25_888_764,  # Stable
    HexAddress("0xa5ae405242f42c47996a0c6857ff10a77f9bdee6"): 25_897_234,  # Balanced
    HexAddress("0x9e0db8f43bb91e2148b0db920e21370525cf3aab"): 25_897_239,  # Boosted
}

#: First Kamui raw-price observation, kept outside the replacement range.
KAMUI_BACKFILL_END_BLOCK = 26_076_164

#: Chain and sample frequency of the existing production price history.
KAMUI_CHAIN_ID = 1
KAMUI_BACKFILL_FREQUENCY = "1h"

#: Minimum finite observation span needed for a vault sparkline.
KAMUI_MIN_PRICE_HISTORY = datetime.timedelta(days=14)

assert {(KAMUI_CHAIN_ID, address) for address in KAMUI_FIRST_SEEN_BLOCKS} == KAMUI_LAGOON_VAULTS


@dataclass(frozen=True, slots=True)
class KamuiBackfillPlan:
    """Validated, fixed-scope price backfill plan.

    The original Parquet row count and selected-row counts allow the staged
    common-writer output to be checked before it replaces production data.
    """

    #: The three reviewed detection records, ordered by address.
    detections: tuple[ERC4262VaultDetection, ...]

    #: Inclusive earliest reviewed detection block.
    start_block: int

    #: Exclusive first existing Kamui price block.
    end_block: int

    #: Number of all-chain rows in the original raw Parquet file.
    original_row_count: int

    #: Kamui rows already present inside the replacement range.
    rows_to_replace: int

    #: Existing Kamui rows at or after the end boundary.
    forward_rows_by_address: dict[HexAddress, int]


def load_scoped_price_rows(price_database: Path) -> pa.Table:
    """Read only reviewed Kamui rows from the shared raw price Parquet.

    The chain and address filter is identical in the plan and stage validation.
    Unrelated rows are never loaded into the migration's planning frame.

    :param price_database:
        Existing raw hourly price Parquet path.
    :return:
        Arrow table of Kamui rows, including immutable forward observations.
    """

    addresses = sorted(KAMUI_FIRST_SEEN_BLOCKS)
    source = ds.dataset(price_database, format="parquet")
    predicate = (ds.field("chain") == KAMUI_CHAIN_ID) & ds.field("address").isin(addresses)
    return source.to_table(filter=predicate)


def plan_kamui_backfill(vault_database: Path, price_database: Path) -> KamuiBackfillPlan:
    """Validate production inputs and build the exact Kamui-only scan plan.

    The first existing price block is a fixed exclusive boundary. It keeps the
    command idempotent: a rerun replaces only the same historical interval and
    leaves the ordinary scanner's later observations intact.

    :param vault_database:
        Persisted vault metadata pickle containing the reviewed detections.
    :param price_database:
        Shared raw hourly Parquet file to backfill.
    :return:
        Validated plan with existing row counts for staged-output checks.
    :raises RuntimeError:
        If metadata, first-seen blocks or existing forward rows differ from
        the reviewed Kamui scope.
    """

    if not vault_database.is_file() or not price_database.is_file():
        raise RuntimeError(f"Kamui backfill requires existing metadata and raw prices: {vault_database}, {price_database}")

    database = VaultDatabase.read(vault_database)
    expected = set(KAMUI_FIRST_SEEN_BLOCKS)
    selected = {spec.vault_address.lower(): row for spec, row in database.rows.items() if spec.chain_id == KAMUI_CHAIN_ID and spec.vault_address.lower() in expected}
    if set(selected) != expected:
        raise RuntimeError(f"Expected exactly three reviewed Kamui metadata rows; missing {sorted(expected - set(selected))}")

    detections: list[ERC4262VaultDetection] = []
    for address in sorted(expected):
        row = selected[address]
        detection = row["_detection_data"]
        if row.get("Protocol") != "Lagoon Finance" or ERC4626Feature.lagoon_like not in detection.features:
            raise RuntimeError(f"Kamui metadata is not a Lagoon vault: {address}")
        if detection.chain != KAMUI_CHAIN_ID or detection.address.lower() != address:
            raise RuntimeError(f"Kamui detection identity differs from its metadata key: {address}")
        if detection.first_seen_at_block != KAMUI_FIRST_SEEN_BLOCKS[address]:
            raise RuntimeError(f"Kamui first-seen block changed for {address}: {detection.first_seen_at_block}")
        detections.append(detection)

    scoped_rows = load_scoped_price_rows(price_database)
    if scoped_rows.num_rows == 0:
        message = "No existing Kamui raw price rows; the reviewed replacement boundary cannot be verified"
        raise RuntimeError(message)
    observations = scoped_rows.select(["address", "block_number"]).to_pylist()
    start_block = min(KAMUI_FIRST_SEEN_BLOCKS.values())
    rows_to_replace = 0
    forward_rows_by_address: dict[HexAddress, int] = {}
    for address in sorted(expected):
        blocks = [row["block_number"] for row in observations if row["address"] == address]
        if not blocks or not any(block >= KAMUI_BACKFILL_END_BLOCK for block in blocks):
            raise RuntimeError(f"Kamui forward price history is missing at the fixed boundary: {address}")
        if any(block < KAMUI_FIRST_SEEN_BLOCKS[address] for block in blocks):
            raise RuntimeError(f"Kamui raw prices predate the reviewed first-seen block: {address}")
        rows_to_replace += sum(block < KAMUI_BACKFILL_END_BLOCK for block in blocks)
        forward_rows_by_address[address] = sum(block >= KAMUI_BACKFILL_END_BLOCK for block in blocks)

    return KamuiBackfillPlan(
        detections=tuple(detections),
        start_block=start_block,
        end_block=KAMUI_BACKFILL_END_BLOCK,
        original_row_count=pq.ParquetFile(price_database).metadata.num_rows,
        rows_to_replace=rows_to_replace,
        forward_rows_by_address=forward_rows_by_address,
    )


def hash_file(path: Path) -> str:
    """Hash the scheduled reader-state pickle before or after the backfill.

    :param path:
        Existing production reader-state pickle.
    :return:
        SHA-256 hexadecimal digest.
    """

    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_forward_rows(before: pa.Table, after: pa.Table) -> None:
    """Compare retained rows exactly, treating matching floating NaNs as equal.

    PyArrow table equality treats a floating NaN as unequal to itself. The
    production Kamui rows have NaNs in several optional numeric fields, so a
    direct ``Table.equals()`` rejects a lossless Parquet rewrite. Compare the
    NaN masks first, then compare values with only matching NaNs replaced by
    nulls. This still rejects a changed value, a NaN-to-null change, a changed
    row order or a schema change.

    :param before:
        Original forward Kamui rows, in persisted order.
    :param after:
        Staged forward Kamui rows, in persisted order.
    :return:
        None; raises if any retained row differs.
    :raises RuntimeError:
        If row count, schema, NaN positions or column values differ.
    """

    if before.num_rows != after.num_rows or not before.schema.equals(after.schema, check_metadata=False):
        message = "Kamui staged scan changed an existing forward price row count or schema"
        raise RuntimeError(message)
    for field in before.schema:
        original = before[field.name]
        staged = after[field.name]
        if pa.types.is_floating(field.type):
            original_nan = pc.is_nan(original)
            staged_nan = pc.is_nan(staged)
            if not original_nan.equals(staged_nan):
                raise RuntimeError(f"Kamui staged scan changed an existing forward price row NaN in {field.name}")
            original = pc.if_else(pc.fill_null(original_nan, False), pa.scalar(None, type=field.type), original)
            staged = pc.if_else(pc.fill_null(staged_nan, False), pa.scalar(None, type=field.type), staged)
        if not original.equals(staged):
            raise RuntimeError(f"Kamui staged scan changed an existing forward price row value in {field.name}")


def validate_staged_prices(plan: KamuiBackfillPlan, before: pa.Table, staged_database: Path, result: ParquetScanResult) -> list[tuple[HexAddress, int, datetime.datetime, datetime.datetime, datetime.timedelta]]:
    """Reject a staged write that changed forward rows or missed a vault.

    The common writer applies an address and block filter to its copied input.
    Its row counts and the untouched forward Kamui rows are checked before the
    staged file can replace the shared production Parquet file.

    :param plan:
        Reviewed scan range and original Parquet row counts.
    :param before:
        Kamui rows read from the original Parquet file under the writer lock.
    :param staged_database:
        Scanned copy of the raw Parquet file.
    :param result:
        Common historical writer result for the staged scan.
    :return:
        Per-vault finite price counts and UTC observation spans; raises on any
        failed preservation or coverage check.
    :raises RuntimeError:
        If writer scope, row accounting, forward rows or vault price coverage
        differs from the plan.
    """

    expected = set(KAMUI_FIRST_SEEN_BLOCKS)
    if result["chain_id"] != KAMUI_CHAIN_ID or result["start_block"] != plan.start_block or result["end_block"] != plan.end_block:
        message = "Kamui staged scan returned a different chain or block range"
        raise RuntimeError(message)
    if result["rows_deleted"] != plan.rows_to_replace or result["existing_row_count"] != plan.original_row_count - plan.rows_to_replace:
        message = "Kamui staged scan deleted rows outside the reviewed replacement count"
        raise RuntimeError(message)
    if set(result["rows_written_by_vault"]) != expected:
        message = "Kamui staged scan did not write exactly the three reviewed vault addresses"
        raise RuntimeError(message)
    if any(result["price_rows_written_by_vault"].get(address, 0) == 0 for address in expected):
        message = "Kamui staged scan produced no share price for at least one reviewed vault"
        raise RuntimeError(message)

    after = load_scoped_price_rows(staged_database)
    historical = after.filter(pc.less(after["block_number"], plan.end_block))
    historical_rows = historical.select(["address", "share_price"]).to_pylist()
    if any(not any(row["address"] == address and row["share_price"] is not None and math.isfinite(row["share_price"]) for row in historical_rows) for address in expected):
        message = "Kamui staged scan produced no finite historical share price for at least one reviewed vault"
        raise RuntimeError(message)
    finite_rows = [row for row in after.select(["address", "timestamp", "share_price"]).to_pylist() if row["share_price"] is not None and math.isfinite(row["share_price"])]
    coverage_rows = []
    for address in sorted(expected):
        observed_at = [row["timestamp"] for row in finite_rows if row["address"] == address]
        first_at = min(observed_at)
        last_at = max(observed_at)
        if last_at - first_at < KAMUI_MIN_PRICE_HISTORY:
            message = f"Kamui staged scan has less than 14 days of finite price history for {address}: {first_at} to {last_at}"
            raise RuntimeError(message)
        coverage_rows.append((address, len(observed_at), first_at, last_at, last_at - first_at))
    before_forward = before.filter(pc.greater_equal(before["block_number"], plan.end_block))
    after_forward = after.filter(pc.greater_equal(after["block_number"], plan.end_block))
    validate_forward_rows(before_forward, after_forward)
    expected_total = plan.original_row_count - plan.rows_to_replace + result["rows_written"]
    if pq.ParquetFile(staged_database).metadata.num_rows != expected_total:
        message = "Kamui staged Parquet row count does not match the scoped writer result"
        raise RuntimeError(message)
    return coverage_rows


def run_kamui_backfill(
    plan: KamuiBackfillPlan,
    price_database: Path,
    reader_state_database: Path,
    timestamp_cache: Path,
    max_workers: int,
    *,
    dry_run: bool,
) -> ParquetScanResult:
    """Stage and validate the fixed historical range for three Kamui vaults.

    The caller holds the shared pipeline writer lock. A scratch copy, isolated
    token cache and stateless historical reader keep unrelated price rows and
    scheduled reader state intact. Dry runs also copy the timestamp cache and
    discard the staged result; apply mode atomically publishes it. Historical
    bytecode availability is checked with Ethereum's `eth_getCode
    <https://ethereum.org/en/developers/docs/apis/json-rpc/#eth_getcode>`__.

    :param plan:
        Exact reviewed chain, addresses and half-open block range.
    :param price_database:
        Shared raw hourly Parquet file to replace after validation.
    :param reader_state_database:
        Scheduled reader-state pickle, hashed but never modified.
    :param timestamp_cache:
        Mounted dense Ethereum block-timestamp cache directory.
    :param max_workers:
        Maximum historical RPC read workers.
    :param dry_run:
        Validate the complete scan but leave production prices and caches intact.
    :return:
        Validated common-writer result from the staged scan.
    :raises RuntimeError:
        If the provider is not archival, a vault cannot be instantiated or
        staged output fails validation.
    """

    if not reader_state_database.is_file():
        raise RuntimeError(f"Scheduled reader-state pickle is missing: {reader_state_database}")
    timestamp_database = timestamp_cache / f"{KAMUI_CHAIN_ID}-timestamps.duckdb"
    if not timestamp_database.is_file():
        raise RuntimeError(f"Dense Ethereum timestamp cache is missing: {timestamp_database}")
    state_digest = hash_file(reader_state_database)
    rpc_url = read_json_rpc_url(KAMUI_CHAIN_ID)
    web3 = create_multi_provider_web3(rpc_url)
    if web3.eth.chain_id != KAMUI_CHAIN_ID:
        raise RuntimeError(f"Kamui backfill requires Ethereum mainnet, got chain {web3.eth.chain_id}")
    if get_almost_latest_block_number(web3) < plan.end_block:
        message = "Ethereum safe head precedes the fixed Kamui backfill boundary"
        raise RuntimeError(message)

    # Fail before copying the large Parquet if historical contract state is
    # unavailable from the configured archive-capable provider.
    for detection in plan.detections:
        if not web3.eth.get_code(Web3.to_checksum_address(detection.address), block_identifier=detection.first_seen_at_block):
            raise RuntimeError(f"No Kamui vault bytecode at reviewed first-seen block {detection.first_seen_at_block}: {detection.address}")

    before = load_scoped_price_rows(price_database)
    with tempfile.TemporaryDirectory(prefix="kamui-price-backfill-", dir=price_database.parent) as scratch_name:
        scratch = Path(scratch_name)
        staged_database = scratch / price_database.name
        shutil.copy2(price_database, staged_database)
        scan_timestamp_cache = timestamp_cache
        if dry_run:
            scan_timestamp_cache = scratch / "block-timestamp"
            scan_timestamp_cache.mkdir()
            shutil.copy2(timestamp_database, scan_timestamp_cache / timestamp_database.name)
        token_cache = TokenDiskCache(scratch / "tokens.sqlite")
        try:
            vaults: list[LagoonVault] = []
            for detection in plan.detections:
                vault = create_vault_instance(web3, detection.address, detection.features, token_cache=token_cache)
                if not isinstance(vault, LagoonVault):
                    raise RuntimeError(f"Could not construct the Kamui Lagoon reader for {detection.address}")
                vault.first_seen_at_block = detection.first_seen_at_block
                vaults.append(vault)

            hypersync = configure_hypersync_from_env(web3)
            result = scan_historical_prices_to_parquet(
                output_fname=staged_database,
                web3=web3,
                web3factory=MultiProviderWeb3Factory(rpc_url),
                vaults=vaults,
                token_cache=token_cache,
                start_block=plan.start_block,
                end_block=plan.end_block,
                max_workers=max_workers,
                frequency=KAMUI_BACKFILL_FREQUENCY,
                hypersync_client=hypersync.hypersync_client,
                timestamp_cache_file=scan_timestamp_cache,
                vault_addresses=set(KAMUI_FIRST_SEEN_BLOCKS),
            )
        finally:
            token_cache.close()

        print(tabulate(validate_staged_prices(plan, before, staged_database, result), headers=("Vault address", "Finite rows", "First price at UTC", "Last price at UTC", "History span"), tablefmt="rounded_outline"))
        if hash_file(reader_state_database) != state_digest:
            message = "Scheduled reader-state pickle changed while Kamui backfill was running"
            raise RuntimeError(message)
        if dry_run:
            logger.info("Kamui full dry run passed; discarding validated staged price file without changing production state")
        else:
            os.replace(staged_database, price_database)
            parent_fd = os.open(price_database.parent, os.O_DIRECTORY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        return result


def main() -> None:
    """Print the reviewed dry-run plan or apply it under the scanner lock.

    Storage paths and worker count may be overridden for testing; the chain,
    three addresses, block range and hourly sampling policy are fixed.

    :return:
        None.
    """

    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "info"))
    pipeline_dir = get_pipeline_data_dir()
    vault_database = Path(os.environ.get("VAULT_DATABASE", pipeline_dir / "vault-metadata-db.pickle")).expanduser()
    price_database = Path(os.environ.get("UNCLEANED_PRICE_DATABASE", pipeline_dir / "vault-prices-1h.parquet")).expanduser()
    reader_state_database = Path(os.environ.get("READER_STATE_DATABASE", DEFAULT_READER_STATE_DATABASE)).expanduser()
    timestamp_cache = Path(os.environ.get("TIMESTAMP_CACHE", DEFAULT_TIMESTAMP_CACHE_FOLDER)).expanduser()
    dry_run_setting = os.environ.get("DRY_RUN", "true").lower()
    if dry_run_setting not in {"true", "false"}:
        message = f"DRY_RUN must be true or false, got {dry_run_setting!r}"
        raise ValueError(message)
    dry_run = dry_run_setting == "true"
    max_workers = int(os.environ.get("MAX_WORKERS", "4"))
    if max_workers < 1:
        message = "MAX_WORKERS must be positive"
        raise ValueError(message)

    lock_timeout = int(os.environ.get("PIPELINE_LOCK_TIMEOUT", "60"))
    if lock_timeout < 1:
        message = "PIPELINE_LOCK_TIMEOUT must be positive"
        raise ValueError(message)
    with wait_other_writers(pipeline_dir / "scan-pipeline", timeout=lock_timeout):
        plan = plan_kamui_backfill(vault_database, price_database)
        logger.info("%s Kamui-only backfill, blocks [%d, %d), %d existing rows to replace", "Validating" if dry_run else "Applying", plan.start_block, plan.end_block, plan.rows_to_replace)
        result = run_kamui_backfill(plan, price_database, reader_state_database, timestamp_cache, max_workers, dry_run=dry_run)
        logger.info("Kamui backfill %s: %s", "validated" if dry_run else "complete", pformat_scan_result(result))
        counts = [(address, result["price_rows_written_by_vault"][address]) for address in sorted(KAMUI_FIRST_SEEN_BLOCKS)]
        print(tabulate(counts, headers=("Vault address", "Historical price rows"), tablefmt="rounded_outline"))

    rows = [(address, KAMUI_FIRST_SEEN_BLOCKS[address], plan.end_block, plan.forward_rows_by_address[address]) for address in sorted(KAMUI_FIRST_SEEN_BLOCKS)]
    print(tabulate(rows, headers=("Vault address", "First seen block", "Exclusive end block", "Preserved later rows"), tablefmt="rounded_outline"))
    print(f"Mode={'validated dry run' if dry_run else 'applied'}; existing rows in replacement range={plan.rows_to_replace}; unrelated vaults and reader state preserved")


if __name__ == "__main__":
    main()
