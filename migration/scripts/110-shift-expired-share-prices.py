# ruff: noqa: N999
"""Repair Shift price and TVL observations recorded after quote expiry.

Purpose: Recover the published value behind Shift's stale-price sentinel.
Scope: Legacy zero rows for the three reviewed Shift vaults on Base and
    Arbitrum through the fixed 26 November 2026 archive blocks below.
Branch: investigate-shift-lighter-llp-wrapper-vault
PR title: fix: retain Shift valuations through oracle expiry
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1832
Preserves: Genuine zero valuations, existing sample keys, unrelated rows,
    Parquet metadata and price scanner checkpoints.
Expected: Replace only stale-zero observations with positive archived quotes.

The ltLLP sample at Arbitrum block 527445008 on 25 November 2026 produced a
false 100% drawdown. The 13:00:11 quote aged past 70 minutes at 14:10:12 and
the feed recovered at block 527469821 at 16:00:12. See
docs/claude-plans/2026-11-26-shift-stale-share-prices.md.
Earlier valid rows retain their derived TVL and unknown publication time.
Stop all writers before preview or apply on the production volume. Applying
queues the normal scanner to regenerate and publish the corrected datasets.
"""

import datetime
import logging
import os
import shutil
import tempfile
import time
from itertools import groupby
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from eth_typing import HexAddress
from joblib import Parallel, delayed
from tqdm_loggable.auto import tqdm

from eth_defi.compat import native_datetime_utc_fromtimestamp
from eth_defi.erc_4626.vault_protocol.shift.vault import ShiftValuation, ShiftVault
from eth_defi.provider.env import read_json_rpc_url
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.base import VaultHistoricalRead, VaultSpec
from eth_defi.vault.publication_state import read_publication_state, write_publication_state
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.price_backfill import PRICE_FILE_NAME, publish_staged_price_backfill

logger = logging.getLogger(__name__)

# Use the reviewed vault identity and fixed half-open archive range. Newly
# scanned data belongs to the normal adapter and is outside this repair.
TARGETS = (
    (VaultSpec(8453, HexAddress("0xaf69bf9ea9e0166498c0502af5b5945980ed1e0e")), 35_705_986, 62_565_724),
    (VaultSpec(8453, HexAddress("0x4ce3ec1b7b4ffb33a0b70c64a0560a3f341aa2e1")), 38_936_930, 62_565_724),
    (VaultSpec(42161, HexAddress("0x956bdd9c18b786b082fd50c52722d254f0cb6964")), 434_181_142, 527_702_177),
)
KEY_FIELDS = ("chain", "address", "block_number")
VALUE_FIELDS = ("share_price", "total_assets", "nav_timestamp")
BATCH_SIZE = 131_072


def fetch_replacement(vault: ShiftVault, block_number: int) -> ShiftValuation | None:
    """Check that the exact stored block has an expired positive valuation.

    :param vault: Reviewed Shift adapter connected to an archive RPC.
    :param block_number: Stored observation block.
    :return: Verified valuation, or None if the stored zero has another cause.
    """
    raw_price = vault.shift_contract.functions.getSharePrice().call(block_identifier=block_number)
    if raw_price != 0:
        return None
    block = vault.web3.eth.get_block(block_number)
    value, published, supply = vault.tvl_feed.functions.getLastTvl().call(block_identifier=block_number)
    if value == 0 or supply == 0 or published == 0:
        return None
    freshness = vault.shift_contract.functions.freshness().call(block_identifier=block_number)
    if block["timestamp"] - published <= freshness:
        return None
    if vault.fetch_total_supply(block_number) == 0:
        return None
    valuation = vault.decode_valuation((value, published, supply), native_datetime_utc_fromtimestamp(block["timestamp"]))
    return valuation if valuation.share_price > 0 else None


def find_replacements(path: Path, max_workers: int) -> tuple[dict[tuple[int, str, int], ShiftValuation], int]:
    """Inventory candidate legacy zeros and verify each at its stored block.

    :param path: Frozen raw price Parquet.
    :param max_workers: Number of parallel archive block reads.
    :return: Verified replacements keyed by row identity and candidate count.
    """
    replacements = {}
    inspected = 0
    for chain, targets in groupby(TARGETS, key=lambda target: target[0].chain_id):
        web3 = None
        for spec, start, end in targets:
            filters = [("chain", "=", chain), ("address", "=", spec.vault_address), ("block_number", ">=", start), ("block_number", "<", end), ("share_price", "=", 0), ("total_assets", "=", 0)]
            blocks = pq.read_table(path, columns=["block_number"], filters=filters)["block_number"].to_pylist()
            if not blocks:
                continue
            if web3 is None:
                web3 = create_multi_provider_web3(read_json_rpc_url(chain))
            vault = ShiftVault(web3, spec)
            blocks = sorted(set(blocks))
            # The multi-provider transport retries recoverable RPC failures.
            # Exhausted or unverifiable reads abort before any publication.
            observations = Parallel(n_jobs=max_workers, backend="threading", return_as="generator")(delayed(fetch_replacement)(vault, block) for block in blocks)
            progress = tqdm(observations, total=len(blocks), desc=f"Verify {spec.vault_address} Shift quotes", unit="block")
            for block, valuation in zip(blocks, progress, strict=True):
                inspected += 1
                if valuation is not None:
                    replacements[chain, spec.vault_address, block] = valuation
    return replacements, inspected


def repair_batch(batch: pa.RecordBatch, replacements: dict[tuple[int, str, int], ShiftValuation]) -> tuple[pa.Table, int]:
    """Replace only verified legacy zero values and keep all other cells.

    :param batch: Original Parquet row batch.
    :param replacements: Archive valuations keyed by chain, address and block.
    :return: Typed batch table and the number of updated rows.
    """
    table = pa.Table.from_batches([batch])
    if "nav_timestamp" not in table.column_names:
        field = VaultHistoricalRead.to_pyarrow_schema().field("nav_timestamp")
        table = table.append_column(field, pa.nulls(len(table), type=field.type))
    mask = pc.and_(pc.equal(table["share_price"], 0), pc.equal(table["total_assets"], 0))
    offsets = pc.indices_nonzero(mask)
    candidates = table.select(KEY_FIELDS).take(offsets)
    updates = {}
    keys = (candidates[name].to_pylist() for name in KEY_FIELDS)
    for offset, key in zip(offsets.to_pylist(), zip(*keys, strict=True), strict=True):
        if valuation := replacements.get(key):
            updates[offset] = valuation
    if updates:
        fields: dict[str, list[float | datetime.datetime]] = {
            "share_price": [float(value.share_price) for value in updates.values()],
            "total_assets": [float(value.total_assets) for value in updates.values()],
            "nav_timestamp": [value.published_at for value in updates.values()],
        }
        update_mask = pa.array([index in updates for index in range(len(table))])
        for name, values in fields.items():
            index = table.schema.get_field_index(name)
            field = table.schema.field(index)
            table = table.set_column(index, field, pc.replace_with_mask(table[name].combine_chunks(), update_mask, pa.array(values, type=field.type)))
    return table, len(updates)


def migrate(context: MigrationContext) -> None:
    """Preview or atomically replace the raw file with verified repairs.

    :param context: Guarded migration run on the selected data directory.
    :return: None after reporting verified keys and any applied rows.
    """
    max_workers = int(os.environ.get("MAX_WORKERS", "4"))
    if max_workers < 1:
        message = "MAX_WORKERS must be positive"
        raise ValueError(message)
    path = context.data_dir / PRICE_FILE_NAME
    with pq.ParquetFile(path) as original:
        schema = original.schema_arrow
        required = set(KEY_FIELDS) | {"share_price", "total_assets"}
        if not required.issubset(schema.names) or any(not pa.types.is_floating(schema.field(name).type) for name in VALUE_FIELDS[:2]):
            raise ValueError(f"Incompatible Shift price history: {path}")
        logger.info("Checking Shift legacy zeros in %s", path)
        replacements, inspected = find_replacements(path, max_workers)
        context.report(inspected_zero_keys=inspected, verified_expired_keys=len(replacements), changed_rows=0)
        if not replacements:
            return
        if "nav_timestamp" not in schema.names:
            # pandas metadata describes the old column layout. Preserve all
            # other footer metadata, including native publication receipts.
            metadata = {key: value for key, value in (schema.metadata or {}).items() if key != b"pandas"}
            schema = schema.append(VaultHistoricalRead.to_pyarrow_schema().field("nav_timestamp")).with_metadata(metadata or None)
        with tempfile.TemporaryDirectory(dir=context.data_dir, prefix="shift-price-repair-") as directory:
            staged = Path(directory) / path.name
            changed = 0
            with pq.ParquetWriter(staged, schema, compression="zstd") as writer:
                for batch in tqdm(original.iter_batches(batch_size=BATCH_SIZE), desc="Stage Shift price repair", unit="batch"):
                    table, count = repair_batch(batch, replacements)
                    writer.write_table(table)
                    changed += count
            with pq.ParquetFile(staged) as candidate:
                if candidate.metadata.num_rows != original.metadata.num_rows or changed < len(replacements):
                    message = "Shift repair failed row accounting"
                    raise ValueError(message)
                if not candidate.schema_arrow.equals(schema, check_metadata=True):
                    message = "Shift repair changed the staged schema"
                    raise ValueError(message)
            context.report(changed_rows=changed)
            if not context.dry_run:
                shutil.copymode(path, staged)
                with staged.open("rb") as file:
                    os.fsync(file.fileno())
                # Queue cleaning before replacing the raw input so recovery
                # also works after a crash and when no collection is due.
                pending_path = context.data_dir / "post-processing-pending.json"
                pending = read_publication_state(pending_path)
                pending.setdefault("since", time.time())
                pending.setdefault("attempts", 0)
                pending["failed"] = sorted(set(pending.get("failed", ())) | {"clean-prices"})
                write_publication_state(pending_path, pending)
                context.record_modified_file(pending_path)
                publish_staged_price_backfill(context, path, staged)
