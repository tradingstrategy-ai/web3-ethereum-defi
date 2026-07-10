"""Benchmark native-price parquet merge implementations on production-shaped data.

The benchmark never replaces the configured uncleaned price parquet. Each
candidate writes a temporary sibling file, verifies it, records timing and
output schema compatibility, and removes the file afterwards.

Experiment results (2026-07-10)
---------------------------------

The benchmark used the current production-shaped
``~/.tradingstrategy/vaults/vault-prices-1h.parquet`` with 19,340,511 rows.
Its four native partitions contained 835,020 rows. Native partitions were
read from that same parquet for the timing run, which isolates the full-file
replacement cost from DuckDB export time.

On a warm local filesystem cache, the existing pandas path took 24.19 seconds
and produced a 258.4 MiB canonical parquet. A direct PyArrow table path took
14.63 seconds and produced the same canonical schema and size: a 39.5%
reduction in merge time. DuckDB ``COPY`` took 5.38 seconds, but it rewrote
``timestamp`` and ``written_at`` as ``timestamp[us]`` rather than
``timestamp[ms]`` and changed ``deposit_closed_reason`` from ``large_string``
to ``string``. It is therefore not compatible with the scanner's schema
contract despite its speed.

Conclusion: use PyArrow for the production native-partition replacement. Keep
pandas only for the comparatively small protocol source frames. Re-run this
script after schema or compression changes before considering DuckDB again.

Run with the project's Poetry environment:

.. code-block:: shell

    poetry run python scripts/erc-4626/benchmark-native-price-merge.py

Environment variables:

- ``UNCLEANED_PARQUET_PATH``: Input parquet. Defaults to the vault pipeline.
- ``PIPELINE_DATA_DIR``: Directory containing native metrics DuckDB files.
- ``BENCHMARK_RUNS``: Repetitions per implementation (default: 1).
- ``BENCHMARK_IMPLEMENTATIONS``: Comma-separated subset of ``pandas``,
  ``pyarrow``, and ``duckdb`` (default: all).
- ``NATIVE_SOURCE``: ``parquet`` (default) reuses current native partitions;
  ``duckdb`` rebuilds native frames from their source DuckDB files.
"""

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from tabulate import tabulate

from eth_defi.grvt.constants import GRVT_CHAIN_ID
from eth_defi.grvt.daily_metrics import GRVTDailyMetricsDatabase
from eth_defi.grvt.vault_data_export import build_raw_prices_dataframe as build_grvt_prices_dataframe
from eth_defi.hibachi.constants import HIBACHI_CHAIN_ID
from eth_defi.hibachi.daily_metrics import HibachiDailyMetricsDatabase
from eth_defi.hibachi.vault_data_export import build_raw_prices_dataframe as build_hibachi_prices_dataframe
from eth_defi.hyperliquid.constants import HYPERCORE_CHAIN_ID
from eth_defi.hyperliquid.daily_metrics import HyperliquidDailyMetricsDatabase
from eth_defi.hyperliquid.high_freq_metrics import HyperliquidHighFreqMetricsDatabase
from eth_defi.hyperliquid.vault_data_export import (
    build_raw_prices_dataframe as build_hypercore_daily_prices_dataframe,
)
from eth_defi.hyperliquid.vault_data_export import (
    build_raw_prices_dataframe_hf as build_hypercore_hf_prices_dataframe,
)
from eth_defi.lighter.constants import LIGHTER_CHAIN_ID
from eth_defi.lighter.daily_metrics import LighterDailyMetricsDatabase
from eth_defi.lighter.vault_data_export import build_raw_prices_dataframe as build_lighter_prices_dataframe
from eth_defi.vault.base import VaultHistoricalRead, verify_parquet_file

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BenchmarkResult:
    """One completed merge implementation measurement.

    :param implementation:
        Implementation label.
    :param duration_seconds:
        Wall-clock duration for read, merge, sort, write, and verification.
    :param output_rows:
        Number of rows in the generated parquet.
    :param output_size_mb:
        Generated parquet size in MiB.
    :param schema_matches_canonical_writer:
        Whether the output schema exactly matches the current writer output.
    """

    implementation: str
    duration_seconds: float
    output_rows: int
    output_size_mb: float
    schema_matches_canonical_writer: bool


def _resolve_path(name: str, default: Path) -> Path:
    """Resolve an optional path override from the environment.

    :param name:
        Environment variable name.
    :param default:
        Path used when the environment variable is unset.
    :return:
        Expanded configured path.
    """
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


def build_native_price_frames(data_dir: Path) -> dict[int, pd.DataFrame]:
    """Export all native-source prices from their production DuckDB databases.

    The source frames are built once and reused for each candidate. This keeps
    the comparison focused on parquet merging rather than API or DuckDB export
    time, while still using the actual current native data.

    :param data_dir:
        Directory containing the production DuckDB files.
    :return:
        Fresh non-empty native frames keyed by their synthetic chain ID.
    """
    frames: dict[int, pd.DataFrame] = {}
    daily_path = _resolve_path("HYPERLIQUID_DB_PATH", data_dir / "hyperliquid-vaults.duckdb")
    hf_path = _resolve_path("HYPERLIQUID_HF_DB_PATH", data_dir / "hyperliquid-vaults-hf.duckdb")
    daily_db = HyperliquidDailyMetricsDatabase(daily_path) if daily_path.exists() else None
    hf_db = HyperliquidHighFreqMetricsDatabase(hf_path) if hf_path.exists() else None
    try:
        hypercore_parts: list[pd.DataFrame] = []
        if daily_db is not None:
            daily_df = build_hypercore_daily_prices_dataframe(daily_db)
            if not daily_df.empty:
                hypercore_parts.append(daily_df.assign(_source="daily"))
        if hf_db is not None:
            hf_df = build_hypercore_hf_prices_dataframe(hf_db)
            if not hf_df.empty:
                hypercore_parts.append(hf_df.assign(_source="hf"))
        if hypercore_parts:
            hypercore_df = pd.concat(hypercore_parts, ignore_index=True)
            hypercore_df = hypercore_df.sort_values(["address", "timestamp", "_source"])
            hypercore_df = hypercore_df.drop_duplicates(subset=["address", "timestamp"], keep="last")
            frames[HYPERCORE_CHAIN_ID] = hypercore_df.drop(columns=["_source"])
    finally:
        if daily_db is not None:
            daily_db.close()
        if hf_db is not None:
            hf_db.close()

    source_specs = (
        (GRVT_CHAIN_ID, _resolve_path("GRVT_DB_PATH", data_dir / "grvt-vaults.duckdb"), GRVTDailyMetricsDatabase, build_grvt_prices_dataframe),
        (LIGHTER_CHAIN_ID, _resolve_path("LIGHTER_DB_PATH", data_dir / "lighter-pools.duckdb"), LighterDailyMetricsDatabase, build_lighter_prices_dataframe),
        (HIBACHI_CHAIN_ID, _resolve_path("HIBACHI_DB_PATH", data_dir / "hibachi-vaults.duckdb"), HibachiDailyMetricsDatabase, build_hibachi_prices_dataframe),
    )
    for chain_id, db_path, database_class, builder in source_specs:
        db = database_class(db_path)
        try:
            frame = builder(db)
        finally:
            db.close()
        if not frame.empty:
            frames[chain_id] = frame

    if not frames:
        raise RuntimeError(f"No native price frames could be built from {data_dir}")

    logger.info(
        "Prepared %d native frames containing %d rows",
        len(frames),
        sum(len(frame) for frame in frames.values()),
    )
    return frames


def build_native_price_frames_from_parquet(input_path: Path) -> dict[int, pd.DataFrame]:
    """Read the current native partitions from the real uncleaned parquet.

    This is the default benchmark source because it measures the expensive
    replacement operation with production-native data while avoiding repeated
    DuckDB export work. Use ``NATIVE_SOURCE=duckdb`` to include the source
    export path instead.

    :param input_path:
        Current uncleaned price parquet.
    :return:
        Non-empty current native frames keyed by synthetic chain ID.
    """
    chain_ids = [HYPERCORE_CHAIN_ID, GRVT_CHAIN_ID, LIGHTER_CHAIN_ID, HIBACHI_CHAIN_ID]
    native_df = pd.read_parquet(input_path, filters=[("chain", "in", chain_ids)])
    frames = {int(chain_id): frame.copy() for chain_id, frame in native_df.groupby("chain")}
    assert frames, f"No native rows found in {input_path}"
    logger.info(
        "Read %d current native partitions containing %d rows from parquet",
        len(frames),
        len(native_df),
    )
    return frames


class Heartbeat:
    """Log progress while a long native merge step has no intermediate output.

    :param label:
        Human-readable operation label.
    :param interval_seconds:
        Logging interval.
    """

    def __init__(self, label: str, interval_seconds: float = 5.0) -> None:
        self.label = label
        self.interval_seconds = interval_seconds
        self._stop_event = Event()
        self._thread: Thread | None = None

    def __enter__(self) -> None:
        """Start progress logging in a daemon thread."""

        def report_progress() -> None:
            while not self._stop_event.wait(self.interval_seconds):
                logger.info("%s is still running", self.label)

        self._thread = Thread(target=report_progress, daemon=True)
        self._thread.start()

    def __exit__(self, *_: object) -> None:
        """Stop progress logging and wait for the thread to exit."""
        self._stop_event.set()
        assert self._thread is not None
        self._thread.join()


def _target_schema(existing_schema: pa.Schema, native_frames: dict[int, pd.DataFrame]) -> pa.Schema:
    """Construct the production writer schema for Arrow candidates.

    Canonical fields use the repository's exact types. Extra native fields are
    unified across the current parquet and fresh source frames.

    :param existing_schema:
        Schema of the current uncleaned parquet.
    :param native_frames:
        Fresh source data keyed by chain ID.
    :return:
        Canonical schema followed by compatible extra native fields.
    """
    canonical = VaultHistoricalRead.to_pyarrow_schema()
    canonical_names = set(canonical.names)
    extra_schemas = [pa.schema(field for field in existing_schema if field.name not in canonical_names)]
    for frame in native_frames.values():
        source_table = pa.Table.from_pandas(frame, preserve_index=False)
        extra_schemas.append(pa.schema(field for field in source_table.schema if field.name not in canonical_names))

    extras = pa.unify_schemas(extra_schemas, promote_options="permissive")
    return pa.schema([*canonical, *extras])


def _align_table_to_schema(table: pa.Table, schema: pa.Schema) -> pa.Table:
    """Add missing fields and cast columns to the target schema.

    :param table:
        Source Arrow table.
    :param schema:
        Required result schema.
    :return:
        Table with all schema fields in schema order.
    """
    arrays: list[pa.ChunkedArray] = []
    for field in schema:
        column_index = table.schema.get_field_index(field.name)
        if column_index == -1:
            arrays.append(pa.chunked_array([pa.nulls(len(table), type=field.type)]))
            continue
        column = table.column(column_index)
        arrays.append(column if column.type == field.type else column.cast(field.type, safe=False))
    return pa.Table.from_arrays(arrays, schema=schema)


def _arrow_native_table(existing_schema: pa.Schema, native_frames: dict[int, pd.DataFrame]) -> tuple[pa.Table, pa.Schema]:
    """Create replacement rows aligned to a shared production schema.

    :param existing_schema:
        Schema of the current uncleaned parquet.
    :param native_frames:
        Fresh source data keyed by chain ID.
    :return:
        Combined native rows and their exact target schema.
    """
    schema = _target_schema(existing_schema, native_frames)
    tables = [_align_table_to_schema(pa.Table.from_pandas(frame, preserve_index=False), schema) for frame in native_frames.values()]
    return pa.concat_tables(tables), schema


def _temporary_output_path(input_path: Path, implementation: str, run: int) -> Path:
    """Return a same-filesystem temporary benchmark path.

    :param input_path:
        Production parquet input path.
    :param implementation:
        Candidate implementation label.
    :param run:
        One-indexed repetition number.
    :return:
        Temporary output path.
    """
    return input_path.with_name(f".{input_path.stem}.benchmark-{implementation}-{run}.parquet")


def _finish_result(
    implementation: str,
    started_at: float,
    output_path: Path,
    expected_rows: int,
    expected_schema: pa.Schema | None,
) -> BenchmarkResult:
    """Verify a candidate output and convert it to a benchmark result.

    :param implementation:
        Candidate label.
    :param started_at:
        Timer start from :py:func:`time.perf_counter`.
    :param output_path:
        Candidate output file.
    :param expected_rows:
        Expected output row count.
    :param expected_schema:
        Schema expected for compatibility, or ``None`` for a prototype.
    :return:
        Verified benchmark result.
    """
    verify_parquet_file(output_path, expected_rows=expected_rows)
    actual_schema = pq.read_schema(output_path)
    schema_matches = expected_schema is None or actual_schema == expected_schema
    if expected_schema is not None and not schema_matches:
        expected_by_name = {field.name: field.type for field in expected_schema}
        actual_by_name = {field.name: field.type for field in actual_schema}
        differences = [f"{name}: expected {expected_type}, got {actual_by_name.get(name, '<missing>')}" for name, expected_type in expected_by_name.items() if actual_by_name.get(name) != expected_type]
        logger.warning("%s output schema is not production-compatible: %s", implementation, "; ".join(differences))
    return BenchmarkResult(
        implementation=implementation,
        duration_seconds=time.perf_counter() - started_at,
        output_rows=expected_rows,
        output_size_mb=output_path.stat().st_size / 1024 / 1024,
        schema_matches_canonical_writer=schema_matches,
    )


def benchmark_pandas(input_path: Path, native_frames: dict[int, pd.DataFrame], run: int) -> BenchmarkResult:
    """Measure the current pandas plus canonical-writer implementation.

    :param input_path:
        Production parquet input path.
    :param native_frames:
        Fresh source data keyed by chain ID.
    :param run:
        One-indexed repetition number.
    :return:
        Benchmark result.
    """
    output_path = _temporary_output_path(input_path, "pandas", run)
    started_at = time.perf_counter()
    existing_df = pd.read_parquet(input_path)
    existing_df = existing_df[~existing_df["chain"].isin(native_frames)]
    combined_df = pd.concat([existing_df, *native_frames.values()], ignore_index=True)
    combined_df = combined_df.sort_values(["chain", "address", "timestamp"])
    VaultHistoricalRead.write_uncleaned_parquet(combined_df, output_path)
    return _finish_result("pandas", started_at, output_path, len(combined_df), pq.read_schema(output_path))


def benchmark_pyarrow(input_path: Path, native_frames: dict[int, pd.DataFrame], run: int) -> BenchmarkResult:
    """Measure an Arrow-table implementation with canonical schema preservation.

    :param input_path:
        Production parquet input path.
    :param native_frames:
        Fresh source data keyed by chain ID.
    :param run:
        One-indexed repetition number.
    :return:
        Benchmark result.
    """
    output_path = _temporary_output_path(input_path, "pyarrow", run)
    started_at = time.perf_counter()
    existing_table = pq.read_table(input_path)
    native_table, schema = _arrow_native_table(existing_table.schema, native_frames)
    chain_mask = pc.is_in(existing_table["chain"], value_set=pa.array(list(native_frames)))
    retained_table = _align_table_to_schema(existing_table.filter(pc.invert(chain_mask)), schema)
    combined_table = pa.concat_tables([retained_table, native_table])
    sort_indices = pc.sort_indices(combined_table, sort_keys=[("chain", "ascending"), ("address", "ascending"), ("timestamp", "ascending")])
    combined_table = combined_table.take(sort_indices)
    pq.write_table(combined_table, output_path, compression="zstd")
    return _finish_result("pyarrow", started_at, output_path, len(combined_table), schema)


def benchmark_duckdb(input_path: Path, native_frames: dict[int, pd.DataFrame], run: int) -> BenchmarkResult:
    """Measure a DuckDB Parquet SQL merge using the aligned native Arrow table.

    This is a prototype measurement. It deliberately reports whether DuckDB's
    Parquet writer preserves the exact production Arrow schema; a faster result
    is not eligible for production use when that flag is false.

    :param input_path:
        Production parquet input path.
    :param native_frames:
        Fresh source data keyed by chain ID.
    :param run:
        One-indexed repetition number.
    :return:
        Benchmark result.
    """
    output_path = _temporary_output_path(input_path, "duckdb", run)
    started_at = time.perf_counter()
    existing_schema = pq.read_schema(input_path)
    native_table, schema = _arrow_native_table(existing_schema, native_frames)
    parquet_file = pq.ParquetFile(input_path)
    chain_table = pq.read_table(input_path, columns=["chain"])
    replaced_rows = pc.sum(pc.is_in(chain_table["chain"], value_set=pa.array(list(native_frames)))).as_py() or 0
    expected_rows = parquet_file.metadata.num_rows - replaced_rows + len(native_table)
    chain_ids = ", ".join(str(chain_id) for chain_id in native_frames)
    escaped_input = str(input_path).replace("'", "''")
    escaped_output = str(output_path).replace("'", "''")
    connection = duckdb.connect(":memory:")
    try:
        connection.register("native_rows", native_table)
        query = f"""
            COPY (
                SELECT * FROM read_parquet('{escaped_input}') WHERE chain NOT IN ({chain_ids})
                UNION ALL BY NAME
                SELECT * FROM native_rows
                ORDER BY chain, address, timestamp
            ) TO '{escaped_output}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """  # noqa: S608 - local paths are escaped before interpolation.
        connection.execute(query)
    finally:
        connection.close()
    return _finish_result("duckdb", started_at, output_path, expected_rows, schema)


def main() -> None:
    """Run each merge implementation and display comparable results."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(message)s")
    data_dir = _resolve_path("PIPELINE_DATA_DIR", Path("~/.tradingstrategy/vaults").expanduser())
    input_path = _resolve_path("UNCLEANED_PARQUET_PATH", data_dir / "vault-prices-1h.parquet")
    runs = int(os.environ.get("BENCHMARK_RUNS", "1"))
    assert input_path.exists(), f"Missing input parquet: {input_path}"
    assert runs > 0, "BENCHMARK_RUNS must be positive"

    source_started_at = time.perf_counter()
    source_mode = os.environ.get("NATIVE_SOURCE", "parquet").strip().lower()
    assert source_mode in {"parquet", "duckdb"}, f"Unsupported NATIVE_SOURCE: {source_mode}"
    with Heartbeat(f"native source preparation ({source_mode})"):
        native_frames = build_native_price_frames_from_parquet(input_path) if source_mode == "parquet" else build_native_price_frames(data_dir)
    source_duration = time.perf_counter() - source_started_at
    logger.info("Native source preparation took %.2f seconds", source_duration)

    results: list[BenchmarkResult] = []
    candidates_by_name = {
        "pandas": benchmark_pandas,
        "pyarrow": benchmark_pyarrow,
        "duckdb": benchmark_duckdb,
    }
    implementation_names = [name.strip().lower() for name in os.environ.get("BENCHMARK_IMPLEMENTATIONS", "pandas,pyarrow,duckdb").split(",") if name.strip()]
    unknown_names = set(implementation_names) - set(candidates_by_name)
    assert not unknown_names, f"Unsupported BENCHMARK_IMPLEMENTATIONS values: {sorted(unknown_names)}"
    candidates = [candidates_by_name[name] for name in implementation_names]
    try:
        for run in range(1, runs + 1):
            for candidate in candidates:
                logger.info("Running %s benchmark %d/%d", candidate.__name__, run, runs)
                with Heartbeat(candidate.__name__):
                    results.append(candidate(input_path, native_frames, run))
    finally:
        for result in results:
            output_path = _temporary_output_path(input_path, result.implementation, 1)
            if output_path.exists():
                output_path.unlink()
        for run in range(1, runs + 1):
            for implementation in implementation_names:
                output_path = _temporary_output_path(input_path, implementation, run)
                if output_path.exists():
                    output_path.unlink()

    table = [
        [
            result.implementation,
            f"{result.duration_seconds:.2f}",
            f"{result.output_rows:,}",
            f"{result.output_size_mb:.1f}",
            result.schema_matches_canonical_writer,
        ]
        for result in results
    ]
    print(f"Native source preparation: {source_duration:.2f}s")
    print(tabulate(table, headers=["implementation", "merge seconds", "rows", "MiB", "canonical schema"], tablefmt="github"))


if __name__ == "__main__":
    main()
