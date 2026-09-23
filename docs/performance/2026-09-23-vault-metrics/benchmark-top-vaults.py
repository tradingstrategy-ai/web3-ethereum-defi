#!/usr/bin/env python3
"""Measure one isolated top-vault export against a production-format copy.

Run once with ``BENCHMARK_PHASE=cold`` in a fresh scratch directory and again
with ``BENCHMARK_PHASE=warm`` using the same directory. The input Parquet and
metadata are read only. Export JSON and freshness state are written under the
scratch directory; R2 publication is not invoked.
"""

import datetime
import json
import logging
import os
import resource
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import pyarrow.parquet as pq

from eth_defi.research import vault_metrics
from eth_defi.vault import top_vaults_json


def main() -> None:  # noqa: PLR0914 - benchmark wiring keeps explicit source and phase inputs local
    """Run one export and save stage timings as JSON.

    ``PIPELINE_DATA_DIR`` identifies the immutable input copy.
    ``BENCHMARK_SCRATCH_DIR`` holds only disposable output and state.
    ``BENCHMARK_RESULT_PATH`` is the saved measurement artefact.

    :return: ``None`` after writing the result and printing stage timings.
    """
    source_dir = Path(os.environ["PIPELINE_DATA_DIR"]).expanduser()
    scratch_dir = Path(os.environ["BENCHMARK_SCRATCH_DIR"]).expanduser()
    result_path = Path(os.environ["BENCHMARK_RESULT_PATH"])
    phase = os.environ["BENCHMARK_PHASE"]
    assert phase in {"cold", "warm"}
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    fixed_now = os.environ.get("BENCHMARK_FIXED_NOW")
    if fixed_now:
        now = datetime.datetime.fromisoformat(fixed_now)
        top_vaults_json.native_datetime_utc_now = lambda: now
        vault_metrics.native_datetime_utc_now = lambda: now
    scratch_dir.mkdir(parents=True, exist_ok=True)
    result_path.parent.mkdir(parents=True, exist_ok=True)

    os.environ["VAULT_EXPORT_STATE_PATH"] = str(scratch_dir / "vault-export-state.json")
    os.environ["VAULT_METRICS_STATE_PATH"] = str(scratch_dir / "vault-metrics-state.json")
    state_paths = (Path(os.environ["VAULT_EXPORT_STATE_PATH"]), Path(os.environ["VAULT_METRICS_STATE_PATH"]))
    if phase == "cold":
        assert not any(path.exists() for path in state_paths), "Cold benchmark needs a fresh scratch directory"
    else:
        assert all(path.exists() for path in state_paths), "Warm benchmark needs both persisted state files"
    measurements: list[dict[str, Any]] = []

    def timed(name: str, operation: Callable[..., Any]) -> Callable[..., Any]:
        """Wrap one existing call with wall and CPU timers.

        :param name: Stable label in the saved artefact.
        :param operation: Unmodified target callable.
        :return: Callable with the same arguments and result.
        """

        def run(*args: Any, **kwargs: Any) -> Any:
            """Invoke the target and record one completed call.

            :param args: Original positional arguments.
            :param kwargs: Original keyword arguments.
            :return: Original call result.
            """
            wall_start = time.perf_counter()
            cpu_start = time.process_time()
            result = operation(*args, **kwargs)
            record = {
                "stage": name,
                "wall_seconds": round(time.perf_counter() - wall_start, 3),
                "cpu_seconds": round(time.process_time() - cpu_start, 3),
            }
            if isinstance(result, pd.DataFrame):
                record["rows"] = len(result)
            elif name == "partition_due_vault_ids":
                record["due_vaults"] = len(result[0])
                record["skipped_vaults"] = len(result[1])
            measurements.append(record)
            print("BENCHMARK_STAGE " + json.dumps(record, sort_keys=True), flush=True)  # noqa: T201 - visible progress for long benchmark
            return result

        return run

    original_read_parquet = pd.read_parquet
    force_all_columns = os.environ.get("BENCHMARK_FORCE_ALL_COLUMNS") == "1"

    def benchmark_read_parquet(*args: Any, **kwargs: Any) -> pd.DataFrame:
        """Optionally restore the old full-width due read for A/B comparison.

        :param args: Original read arguments.
        :param kwargs: Original read keyword arguments.
        :return: The Parquet frame selected by the target exporter.
        """
        if force_all_columns and "filters" in kwargs:
            kwargs.pop("columns", None)
        return original_read_parquet(*args, **kwargs)

    top_vaults_json.pd.read_parquet = timed("read_parquet", benchmark_read_parquet)
    original_json_dump = json.dump
    top_vaults_json.json.dump = timed("json_dump", original_json_dump)
    for function_name in (
        "cross_check_data",
        "free_memory",
        "compute_vault_tvl_observations",
        "partition_due_vault_ids",
        "calculate_hourly_returns_for_all_vaults",
        "calculate_lifetime_metrics",
        "apply_sticky_export_state",
        "build_core3_protocols_for_export",
        "build_xerberus_pool_lookup",
        "build_xerberus_protocols_for_export",
        "build_curators_for_export",
        "append_strategy_categories_to_export",
        "validate_strict_json_serialisable",
        "save_sticky_export_state",
        "save_metrics_state",
    ):
        original = getattr(top_vaults_json, function_name)
        setattr(top_vaults_json, function_name, timed(function_name, original))

    source_prices = source_dir / "cleaned-vault-prices-1h.parquet"
    source_metadata = source_dir / "vault-metadata-db.pickle"
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    output = top_vaults_json.main(
        data_dir=scratch_dir,
        vault_db_path=source_metadata,
        parquet_path=source_prices,
        output_path=scratch_dir / "top_vaults_by_chain.json",
        core3_db_path=scratch_dir / "core3.duckdb",
        xerberus_db_path=scratch_dir / "xerberus.duckdb",
        feed_db_path=scratch_dir / "vault-post-database.duckdb",
    )
    price_metadata = pq.read_metadata(source_prices)
    result = {
        "phase": phase,
        "fixed_now": fixed_now,
        "force_all_columns": force_all_columns,
        "input": {
            "price_path": str(source_prices),
            "price_rows": price_metadata.num_rows,
            "price_size_bytes": source_prices.stat().st_size,
            "price_mtime_ns": source_prices.stat().st_mtime_ns,
            "metadata_size_bytes": source_metadata.stat().st_size,
            "metadata_mtime_ns": source_metadata.stat().st_mtime_ns,
        },
        "output_vaults": len(output["vaults"]),
        "output_size_bytes": (scratch_dir / "top_vaults_by_chain.json").stat().st_size,
        "wall_seconds": round(time.perf_counter() - wall_start, 3),
        "cpu_seconds": round(time.process_time() - cpu_start, 3),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "stages": measurements,
    }
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("BENCHMARK_RESULT " + json.dumps(result, sort_keys=True), flush=True)  # noqa: T201 - standalone benchmark result


if __name__ == "__main__":
    main()
