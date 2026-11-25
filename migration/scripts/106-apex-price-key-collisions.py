"""Reconcile duplicate ApeX observations in the combined raw price history.

Purpose: Unblock incremental publication after PR #1812.
Scope: Duplicate millisecond keys in the ApeX partition of vault-prices-1h.parquet.
Branch: plan/native-price-merge-repair
PR title: fix: repair ApeX price key collisions and preserve same-second EVM observations
PR: https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1818
Preserves: EVM block observations, unique ApeX history, other native rows,
source databases, reader state, and unrelated Parquet metadata.
Expected: 109,361 excess rows in the audited 23 November 2026 snapshot.

LLM-generated operational migration. Changes require human review.
Stop the looped scanner and other writers before preview or apply.
"""

import json
import logging
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from tqdm_loggable.auto import tqdm

from eth_defi.apex.constants import APEX_CHAIN_ID, APEX_METRICS_DATABASE
from eth_defi.apex.metrics import ApexMetricsDatabase
from eth_defi.apex.vault_data_export import build_raw_prices_dataframe
from eth_defi.perp_dex.parquet import PERP_VAULT_PARQUET_FIELDS, attach_perp_metrics_to_price_rows, derive_perp_vault_metric_snapshots
from eth_defi.perp_dex.storage import read_perp_vault_observations
from eth_defi.vault.post_processing import apex_price_timestamps
from eth_defi.vault.publication_state import NATIVE_REVISIONS_KEY
from eth_defi.vault_protocols.migration import MigrationContext
from eth_defi.vault_protocols.repairs.price_backfill import PRICE_FILE_NAME, publish_staged_price_backfill

# ruff: noqa: N999 - numbered migration module

logger = logging.getLogger(__name__)

BATCH_ROWS = 1_048_576
ENRICHMENT_FIELDS = ("perp_metrics_observed_at", "perp_quote_asset", "perp_position_data_status")


def _equal_values(left: pa.Array | pa.ChunkedArray, right: pa.Array | pa.ChunkedArray) -> pa.Array | pa.ChunkedArray:
    """Compare typed values, including nulls and valid floating-point NaNs.

    :param left: Original or expected values.
    :param right: Values with the same Arrow type and length.
    :return: Non-null elementwise equality.
    """
    equal = pc.or_(pc.fill_null(pc.equal(left, right), False), pc.and_(pc.is_null(left), pc.is_null(right)))
    if pa.types.is_floating(left.type):
        equal = pc.or_(equal, pc.fill_null(pc.and_(pc.is_nan(left), pc.is_nan(right)), False))
    return equal


def _price_keys(frame: pd.DataFrame) -> pd.MultiIndex:
    """Build identities using the same precision as the recurring ApeX merge.

    :param frame: ApeX addresses and naive UTC timestamps.
    :return: Canonical address and timestamp pairs in input row order.
    """
    return pd.MultiIndex.from_arrays([frame["address"].str.lower(), apex_price_timestamps(frame["timestamp"])])


def _reconcile_pairs(apex: pa.Table, source_path: Path) -> tuple[np.ndarray, pd.DataFrame]:
    """Resolve colliding pairs using frozen source observations and metrics.

    :param apex: Existing raw ApeX partition in original row order.
    :param source_path: Closed ApeX DuckDB archive, opened read-only.
    :return: Partition row offsets to remove and enrichment indexed by kept row offset.
    """
    timeline = apex.select(["chain", "address", "timestamp"]).to_pandas()
    if timeline[["address", "timestamp"]].isna().any().any():
        message = "Stored ApeX history contains a null price key"
        raise ValueError(message)
    keys = _price_keys(timeline)
    duplicate_keys = keys[keys.duplicated(keep=False)]
    if duplicate_keys.empty:
        return np.empty(0, dtype=np.int64), pd.DataFrame()
    oversized = duplicate_keys[duplicate_keys.duplicated(keep="first") & duplicate_keys.duplicated(keep="last")].unique()
    if len(oversized):
        raise ValueError(f"ApeX duplicate groups larger than two: {oversized[:8].tolist()}")

    remove = np.flatnonzero(keys.duplicated(keep="first"))
    first = np.flatnonzero(keys.duplicated(keep="last"))
    second_by_key = pd.Series(remove, index=keys[remove])
    second = second_by_key.loc[keys[first]].to_numpy()
    originals, copies = apex.take(first), apex.take(second)
    differs = np.zeros(len(first), dtype=bool)
    for name in apex.column_names:
        unequal = pc.invert(_equal_values(originals[name], copies[name])).to_numpy()
        if name in ENRICHMENT_FIELDS:
            differs |= unequal
        elif unequal.any():
            raise ValueError(f"ApeX duplicate pairs disagree in {name}: {keys[first[unequal]][:8].tolist()}")

    updates = _rebuild_enrichment(timeline, keys, first[differs], apex, source_path)
    return remove, updates


def _rebuild_enrichment(timeline: pd.DataFrame, keys: pd.MultiIndex, repair_rows: np.ndarray, apex: pa.Table, source_path: Path) -> pd.DataFrame:
    """Reconstruct conflicting enrichment from the checkpointed ApeX archive.

    Compare the other perp fields with the same selected snapshot before
    combining them with the three reconstructed fields.

    :param timeline: All stored ApeX price times, including duplicates.
    :param keys: Stored millisecond keys in partition order.
    :param repair_rows: Retained offsets with conflicting enrichment.
    :param apex: Original typed ApeX partition.
    :param source_path: Checkpointed source DuckDB beside the raw Parquet.
    :return: Reconstructed fields indexed by retained partition offset.
    """
    if not source_path.is_file() or source_path.with_suffix(".duckdb.wal").exists():
        raise ValueError(f"Expected a checkpointed ApeX source archive: {source_path}")
    database = ApexMetricsDatabase(source_path, read_only=True)
    try:
        logger.info("Reading checkpointed ApeX prices from %s", source_path)
        source = build_raw_prices_dataframe(database)
        if source.empty:
            if len(repair_rows):
                message = "ApeX source has no observations for reconciliation"
                raise ValueError(message)
            return pd.DataFrame()
        source_keys = _price_keys(source)
        colliding = source_keys[source_keys.duplicated(keep=False)]
        if len(colliding):
            sample = source.loc[source_keys.isin(colliding), ["address", "timestamp"]].head(8).to_dict("records")
            raise ValueError(f"ApeX source has colliding observations: {sample}")
        if not len(repair_rows):
            return pd.DataFrame()
        repair_keys = keys[repair_rows]
        if not repair_keys.isin(source_keys).all():
            raise ValueError(f"Missing source observations for ApeX enrichment: {repair_keys[~repair_keys.isin(source_keys)][:8].tolist()}")
        logger.info("Reconstructing snapshots for %d conflicting pairs", len(repair_rows))
        accounts, positions = read_perp_vault_observations(database.con)
        snapshots = derive_perp_vault_metric_snapshots(accounts, positions)
    finally:
        database.close()

    # Keep the full price timeline for each affected vault. The latest-price
    # overlay can only be determined with the source's newer observations and
    # any raw-only history still present in the combined archive.
    addresses = repair_keys.get_level_values(0).unique()
    history = pd.concat([timeline, source[timeline.columns]], ignore_index=True)
    history = history.loc[history["address"].str.lower().isin(addresses)]
    history.index = _price_keys(history)
    history = history.loc[~history.index.duplicated(keep="last")]
    logger.info("Joining ApeX snapshots to %d price observations", len(history))
    enriched = attach_perp_metrics_to_price_rows(history.reset_index(drop=True), snapshots)
    enriched.index = history.index
    updates = enriched.loc[repair_keys, list(ENRICHMENT_FIELDS)].set_axis(repair_rows)
    missing = updates.isna().any(axis=1) | updates["perp_quote_asset"].eq("") | updates["perp_position_data_status"].eq("")
    if missing.any():
        raise ValueError(f"ApeX snapshot history cannot resolve enrichment: {keys[updates.index[missing]][:8].tolist()}")
    _check_snapshot_fields(apex, repair_rows, enriched.loc[repair_keys])
    return updates


def _check_snapshot_fields(apex: pa.Table, repair_rows: np.ndarray, reconstructed: pd.DataFrame) -> None:
    """Require the retained exposure fields to agree with the rebuilt snapshot.

    :param apex: Original typed partition.
    :param repair_rows: Retained offsets with conflicting enrichment.
    :param reconstructed: Joined source metrics in the same order, indexed by price key.
    :raises ValueError: An exposure field disagrees with its source snapshot.
    """
    for field in PERP_VAULT_PARQUET_FIELDS:
        if field.name in apex.column_names and field.name not in ENRICHMENT_FIELDS:
            stored = apex[field.name].take(repair_rows)
            rebuilt = pa.Array.from_pandas(reconstructed[field.name], type=stored.type)
            both_missing = pc.and_(pc.is_null(stored, nan_is_null=True), pc.is_null(rebuilt, nan_is_null=True))
            matches = pc.or_(_equal_values(stored, rebuilt), both_missing).to_numpy()
            if not matches.all():
                raise ValueError(f"ApeX snapshot disagrees with stored {field.name}: {reconstructed.index[~matches][:8].tolist()}")


def _enrichment_values(series: pd.Series, field_type: pa.DataType) -> pa.Array:
    """Cast reconstructed fields at the precision used by the raw writer.

    :param series: Values reconstructed from source snapshots.
    :param field_type: Existing raw column type.
    :return: Values ready for insertion into the original column.
    """
    return pa.Array.from_pandas(series).cast(field_type, safe=not pa.types.is_timestamp(field_type))


def _repaired_batches(
    parquet: pq.ParquetFile,
    remove: np.ndarray,
    enrichment: pd.DataFrame,
) -> Iterator[pa.RecordBatch]:
    """Apply edits by ApeX partition offset while retaining original row order.

    :param parquet: Original all-chain history.
    :param remove: Sorted offsets into its ApeX partition.
    :param enrichment: Reconstructed fields indexed by retained ApeX offset.
    :return: Bounded batches with the same field types as the original.
    """
    offset = 0
    # Match the usual raw Parquet row-group size to keep compression and
    # predicate pushdown comparable after the one-time rewrite.
    for batch in parquet.iter_batches(batch_size=BATCH_ROWS):
        apex_rows = np.flatnonzero(pc.fill_null(pc.equal(batch.column("chain"), APEX_CHAIN_ID), False).to_numpy(zero_copy_only=False))
        if len(apex_rows):
            end = offset + len(apex_rows)
            begin_remove, end_remove = np.searchsorted(remove, [offset, end])
            keep = np.ones(batch.num_rows, dtype=bool)
            keep[apex_rows[remove[begin_remove:end_remove] - offset]] = False
            if not enrichment.empty:
                updates = enrichment.loc[offset : end - 1]
                if not updates.empty:
                    mask = np.zeros(batch.num_rows, dtype=bool)
                    mask[apex_rows[updates.index - offset]] = True
                    for name in ENRICHMENT_FIELDS:
                        index = batch.schema.get_field_index(name)
                        original = batch.column(index)
                        values = _enrichment_values(updates[name], original.type)
                        batch = batch.set_column(index, batch.schema.field(index), pc.replace_with_mask(original, pa.array(mask), values))
            batch = batch.filter(keep)
            offset = end
        yield batch


def _verify_values(original: pa.Table, written: pa.Table) -> None:
    """Verify original order and values, including nulls and valid NaNs.

    :param original: Expected rows in source order.
    :param written: Rows read back from the staged file.
    :raises ValueError: A value or row count changed.
    """
    if original.num_rows != written.num_rows:
        raise ValueError(f"Staged price row count mismatch: expected {original.num_rows}, found {written.num_rows}")
    if original.num_rows:
        for name in original.column_names:
            if not pc.all(_equal_values(original[name], written[name])).as_py():
                raise ValueError(f"Staged ApeX repair changed unexpected values in {name}")


def _verify_candidate(parquet: pq.ParquetFile, staged: Path, target_schema: pa.Schema, apex: pa.Table, *, remove: np.ndarray, enrichment: pd.DataFrame) -> None:
    """Check both partitions against the original file before publication.

    :param parquet: Original combined raw file.
    :param staged: Candidate on the same data volume.
    :param target_schema: Expected schema and footer after repair.
    :param apex: Original ApeX partition in file order.
    :param remove: Partition offsets of excess rows.
    :param enrichment: Authorised field edits indexed by retained partition offset.
    :raises ValueError: The candidate differs from the authorised changes.
    """
    with pq.ParquetFile(staged) as candidate:
        if candidate.metadata.num_rows != parquet.metadata.num_rows - len(remove) or not candidate.schema_arrow.equals(target_schema, check_metadata=True):
            message = "Staged ApeX repair has unexpected row count or schema"
            raise ValueError(message)
        # Each input batch produced one row group. Compare retained chains
        # directly to their original values, independently of the edit logic.
        for i, batch in enumerate(tqdm(parquet.iter_batches(batch_size=BATCH_ROWS), total=candidate.num_row_groups, desc="Verify retained prices", unit="batch")):
            original = pa.Table.from_batches([batch])
            written = candidate.read_row_group(i)
            original = original.filter(pc.fill_null(pc.not_equal(original["chain"], APEX_CHAIN_ID), True))
            written = written.filter(pc.fill_null(pc.not_equal(written["chain"], APEX_CHAIN_ID), True))
            _verify_values(original, written)

    # Verify all ApeX fields against the original partition and the small
    # set of authorised edits, independently of their all-chain row offsets.
    if not enrichment.empty:
        mask = np.zeros(apex.num_rows, dtype=bool)
        mask[enrichment.index] = True
        for name in ENRICHMENT_FIELDS:
            field = target_schema.field(name)
            expected = pc.replace_with_mask(apex[name].combine_chunks(), mask, _enrichment_values(enrichment[name], field.type))
            apex = apex.set_column(target_schema.get_field_index(name), field, expected)
    distinct = apex.filter(~np.isin(np.arange(apex.num_rows), remove))
    _verify_values(distinct, pq.read_table(staged, filters=[("chain", "=", APEX_CHAIN_ID)]))


def migrate(context: MigrationContext) -> None:
    """Stage and verify the ApeX repair under the runner's writer guard.

    Preview validates a temporary candidate. Apply retains the original
    inode as a journalled backup and then publishes the verified replacement.

    :param context: The guarded preview or apply attempt and its data directory.
    """
    path = context.data_dir / PRICE_FILE_NAME
    parquet = pq.ParquetFile(path)
    schema = parquet.schema_arrow
    if schema.field("timestamp").type != pa.timestamp("ms") or not pa.types.is_string(schema.field("address").type):
        raise ValueError(f"Incompatible raw price key schema: {path}")
    logger.info("Inventorying ApeX price history in %s", path)
    apex = pq.read_table(path, filters=[("chain", "=", APEX_CHAIN_ID)])
    remove, enrichment = _reconcile_pairs(apex, context.data_dir / APEX_METRICS_DATABASE.name)
    before = parquet.metadata.num_rows
    context.report(inspected_rows=apex.num_rows, changed_rows=len(remove), enriched_rows=len(enrichment), price_rows_before=before, price_rows_after=before - len(remove))
    if not len(remove):
        return

    metadata = dict(schema.metadata or {})
    revisions = json.loads(metadata.get(NATIVE_REVISIONS_KEY, b"{}"))
    if "apex" in revisions:
        # The repaired raw partition needs a fresh receipt even when the
        # source database revision matches the one in the original footer.
        del revisions["apex"]
        metadata[NATIVE_REVISIONS_KEY] = json.dumps(revisions, sort_keys=True).encode()
    target_schema = schema.with_metadata(metadata)
    with tempfile.TemporaryDirectory(dir=path.parent, prefix="apex-price-repair-") as temp:
        staged = Path(temp) / path.name
        if shutil.disk_usage(staged.parent).free < path.stat().st_size * 2:
            message = "Insufficient space to stage the ApeX price repair"
            raise ValueError(message)
        with pq.ParquetWriter(staged, target_schema, compression="zstd") as writer:
            for batch in tqdm(_repaired_batches(parquet, remove, enrichment), desc="Stage ApeX price repair", unit="batch"):
                writer.write_batch(batch, row_group_size=BATCH_ROWS)

        _verify_candidate(parquet, staged, target_schema, apex, remove=remove, enrichment=enrichment)
        if not context.dry_run:
            shutil.copymode(path, staged)
            with staged.open("rb") as staged_file:
                os.fsync(staged_file.fileno())
            publish_staged_price_backfill(context, path, staged)
