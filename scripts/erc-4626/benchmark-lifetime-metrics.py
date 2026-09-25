"""Benchmark calculate_lifetime_metrics() and check output parity between versions.

The script mirrors the stablecoin top-vault export: it reads the same metric
columns from the cleaned hourly Parquet, prepares daily returns and then times
:py:func:`eth_defi.research.vault_metrics.calculate_lifetime_metrics`. It reads
local files only and never writes pipeline state or uploads anything.

Every run can save its exported rows. A later run, typically on a changed
branch, compares its rows against that file. Rows are compared after
:py:func:`~eth_defi.research.vault_metrics.export_lifetime_row`, which is the
form published as JSON, so the check catches both value changes and type
changes that alter JSON bytes, such as ``0`` becoming ``0.0``.

Environment variables:

- ``PIPELINE_DATA_DIR``: Pipeline data directory. Defaults to ``~/.tradingstrategy``.
- ``VAULT_DATABASE``: Vault metadata pickle override.
- ``LIFETIME_BENCHMARK_PRICE_DATABASE``: Cleaned hourly Parquet override.
- ``LIFETIME_BENCHMARK_N``: Number of stablecoin vaults, chosen deterministically
  by SHA-256 of the vault ID. ``0`` means every stablecoin vault. Defaults to ``1000``.
- ``LIFETIME_BENCHMARK_OUTPUT``: Optional pickle path for this run's exported rows.
- ``LIFETIME_BENCHMARK_COMPARE_WITH``: Optional pickle from an earlier run to compare against.

Example:

.. code-block:: shell

    LIFETIME_BENCHMARK_OUTPUT=/tmp/before.pickle poetry run python scripts/erc-4626/benchmark-lifetime-metrics.py
    # ... change code ...
    LIFETIME_BENCHMARK_COMPARE_WITH=/tmp/before.pickle poetry run python scripts/erc-4626/benchmark-lifetime-metrics.py
"""

import collections
import gc
import hashlib
import logging
import math
import os
import pickle  # noqa: S403 - reads only result files this script wrote locally
import resource
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
from tabulate import tabulate

from eth_defi.research.vault_metrics import UNUSED_METRIC_PRICE_COLUMNS, calculate_hourly_returns_for_all_vaults, calculate_lifetime_metrics, export_lifetime_row
from eth_defi.stablecoin_metadata import is_stablecoin_like
from eth_defi.utils import setup_console_logging
from eth_defi.vault.vaultdb import VaultDatabase

logger = logging.getLogger(__name__)

#: Relative tolerance for float comparison. Reordered floating-point
#: summation (pandas versus NumPy reductions) changes only the last few bits.
FLOAT_REL_TOLERANCE = 1e-9

#: Absolute tolerance for values that should be zero but carry rounding dust.
FLOAT_ABS_TOLERANCE = 1e-12

#: Output columns that legitimately differ between any two runs.
IGNORED_FIELDS = frozenset({"generated_at"})


def _choose_vault_ids(ids: set[str], limit: int) -> list[str]:
    """Choose a stable vault sample independent of Parquet row order.

    Sorting by SHA-256 gives a sample that is spread across chains and
    protocols, and that stays the same between runs and code versions.

    :param ids:
        Candidate vault IDs.
    :param limit:
        Sample size, or zero or less for every ID.
    :return:
        Selected vault IDs.
    """
    ordered = sorted(ids, key=lambda vault_id: hashlib.sha256(vault_id.encode()).hexdigest())
    return ordered if limit <= 0 else ordered[:limit]


def _normalise_path(path: str) -> str:
    """Collapse list positions so differences aggregate per field.

    :param path:
        Field path such as ``period_results[3].sharpe``.
    :return:
        Path with list indexes removed, such as ``period_results[].sharpe``.
    """
    out = []
    skipping = False
    for char in path:
        if char == "[":
            skipping = True
            out.append("[")
        elif char == "]":
            skipping = False
            out.append("]")
        elif not skipping:
            out.append(char)
    return "".join(out)


def _collect_differences(before: Any, after: Any, path: str, differences: list[tuple[str, Any, Any]]) -> None:
    """Recursively compare two exported JSON values.

    Types must match exactly: JSON serialises ``0`` and ``0.0`` differently,
    and ``None`` is not the same as a number. Floats are compared with a
    tight relative tolerance because the optimised code may sum values in a
    different order.

    :param before:
        Value from the reference run.
    :param after:
        Value from the current run.
    :param path:
        Dotted path of this value, used in the report.
    :param differences:
        Output list receiving ``(path, before, after)`` tuples.
    """
    if type(before) is not type(after):
        differences.append((path, before, after))
    elif isinstance(before, float):
        if not math.isclose(before, after, rel_tol=FLOAT_REL_TOLERANCE, abs_tol=FLOAT_ABS_TOLERANCE):
            differences.append((path, before, after))
    elif isinstance(before, dict):
        for key in before.keys() | after.keys():
            if key in IGNORED_FIELDS and not path:
                continue
            child = f"{path}.{key}" if path else str(key)
            if key not in before or key not in after:
                differences.append((child, before.get(key, "<missing>"), after.get(key, "<missing>")))
            else:
                _collect_differences(before[key], after[key], child, differences)
    elif isinstance(before, list):
        if len(before) != len(after):
            differences.append((f"{path}.len", len(before), len(after)))
        else:
            for index, (left, right) in enumerate(zip(before, after, strict=True)):
                _collect_differences(left, right, f"{path}[{index}]", differences)
    elif before != after:
        differences.append((path, before, after))


def _compare(reference: dict[str, Any], current: dict[str, Any]) -> int:
    """Print a parity report between two saved runs.

    :param reference:
        Saved payload of the earlier run.
    :param current:
        Payload of this run.
    :return:
        Total number of differing values.
    """
    if reference["columns"] != current["columns"]:
        print("Column lists differ")
        print(f"  only before: {[c for c in reference['columns'] if c not in current['columns']]}")
        print(f"  only after: {[c for c in current['columns'] if c not in reference['columns']]}")
        print(f"  same set, different order: {set(reference['columns']) == set(current['columns'])}")
    else:
        print(f"Column lists are identical ({len(current['columns'])} columns, same order)")

    before_rows = reference["rows"]
    after_rows = current["rows"]
    only_before = sorted(before_rows.keys() - after_rows.keys())
    only_after = sorted(after_rows.keys() - before_rows.keys())
    print(f"Vaults: {len(before_rows):,} before, {len(after_rows):,} after, {len(only_before)} only before, {len(only_after)} only after")

    per_field: dict[str, list] = collections.defaultdict(list)
    for vault_id in sorted(before_rows.keys() & after_rows.keys()):
        differences: list[tuple[str, Any, Any]] = []
        _collect_differences(before_rows[vault_id], after_rows[vault_id], "", differences)
        for path, left, right in differences:
            per_field[_normalise_path(path)].append((vault_id, path, left, right))

    total = sum(len(entries) for entries in per_field.values())
    if not per_field:
        print("No differing values")
        return 0

    table = []
    for field, entries in sorted(per_field.items(), key=lambda item: -len(item[1])):
        vault_id, path, left, right = entries[0]
        vault_count = len({entry[0] for entry in entries})
        table.append([field, len(entries), vault_count, vault_id, path, repr(left)[:40], repr(right)[:40]])
    print(tabulate(table, headers=["Field", "Values", "Vaults", "Example vault", "Example path", "Before", "After"], tablefmt="fancy_grid"))
    return total + len(only_before) + len(only_after)


def main() -> None:  # noqa: PLR0914 - one operator report keeps every measured input visible
    """Run the benchmark and optional parity comparison."""
    setup_console_logging(default_log_level=os.environ.get("LOG_LEVEL", "warning"))
    data_dir = Path(os.environ.get("PIPELINE_DATA_DIR", "~/.tradingstrategy")).expanduser()
    vault_db_path = Path(os.environ.get("VAULT_DATABASE", data_dir / "vault-metadata-db.pickle")).expanduser()
    price_path = Path(os.environ.get("LIFETIME_BENCHMARK_PRICE_DATABASE", data_dir / "cleaned-vault-prices-1h.parquet")).expanduser()
    limit = int(os.environ.get("LIFETIME_BENCHMARK_N", "1000"))
    output_path = os.environ.get("LIFETIME_BENCHMARK_OUTPUT")
    compare_path = os.environ.get("LIFETIME_BENCHMARK_COMPARE_WITH")

    vault_db = VaultDatabase.read(vault_db_path)
    stablecoin_ids = {f"{row['_detection_data'].chain}-{row['_detection_data'].address}" for row in vault_db.values() if is_stablecoin_like(row["Denomination"])}
    present_ids = set(pd.read_parquet(price_path, columns=["id"])["id"].astype(str).unique())
    selected_ids = _choose_vault_ids(stablecoin_ids & present_ids, limit)

    # Same projection and filter as top_vaults_json.main().
    metric_columns = [name for name in pq.read_schema(price_path).names if name not in UNUSED_METRIC_PRICE_COLUMNS]
    started = time.perf_counter()
    prices_df = pd.read_parquet(price_path, columns=metric_columns, filters=[("id", "in", selected_ids)])
    read_seconds = time.perf_counter() - started

    started = time.perf_counter()
    returns_df = calculate_hourly_returns_for_all_vaults(prices_df)
    prep_seconds = time.perf_counter() - started
    source_rows = len(prices_df)
    del prices_df
    gc.collect()

    # Warm the process-wide YAML caches (stablecoin, curator metadata) on a
    # few vaults, so the timed run measures the steady-state loop only.
    warm_ids = sorted(selected_ids)[:20]
    calculate_lifetime_metrics(returns_df.loc[returns_df["id"].isin(warm_ids)], vault_db)
    gc.collect()

    started = time.perf_counter()
    metrics_df = calculate_lifetime_metrics(returns_df, vault_db)
    metrics_seconds = time.perf_counter() - started
    peak_rss_gib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024**2)

    print(
        tabulate(
            [
                ["Vaults selected", f"{len(selected_ids):,}"],
                ["Vaults with metrics", f"{len(metrics_df):,}"],
                ["Source rows", f"{source_rows:,}"],
                ["Daily rows", f"{len(returns_df):,}"],
                ["Parquet read", f"{read_seconds:.2f} s"],
                ["Daily preparation", f"{prep_seconds:.2f} s"],
                ["calculate_lifetime_metrics()", f"{metrics_seconds:.2f} s"],
                ["Vaults per second", f"{len(metrics_df) / metrics_seconds:.1f}"],
                ["Peak RSS", f"{peak_rss_gib:.2f} GiB"],
            ],
            tablefmt="fancy_grid",
        )
    )

    payload = {
        "columns": list(metrics_df.columns),
        "rows": {str(row["id"]): export_lifetime_row(row) for _, row in metrics_df.iterrows()},
        "timings": {"prep_seconds": prep_seconds, "metrics_seconds": metrics_seconds, "peak_rss_gib": peak_rss_gib},
    }

    if output_path:
        with open(output_path, "wb") as out:
            pickle.dump(payload, out)
        print(f"Saved exported rows to {output_path}")

    if compare_path:
        with open(compare_path, "rb") as inp:
            reference = pickle.load(inp)  # noqa: S301 - operator-supplied local result file
        timings = reference["timings"]
        print(f"Reference run: preparation {timings['prep_seconds']:.2f} s, metrics {timings['metrics_seconds']:.2f} s, peak RSS {timings['peak_rss_gib']:.2f} GiB")
        differences = _compare(reference, payload)
        print(f"Total differing values: {differences}")


if __name__ == "__main__":
    main()
