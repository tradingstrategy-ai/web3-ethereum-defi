#!/usr/bin/env python3
"""Benchmark the vault-price cleaning pipeline on an immutable input copy.

The benchmark deliberately writes to a temporary output by default and never
replaces the configured production Parquet.  It records the compact input
fingerprint, wall/CPU time, peak resident set size and output row count so a
future scanner run can distinguish an algorithmic regression from a changed
dataset or host.

Environment variables:

``VAULT_BENCHMARK_VAULT_DB_PATH``
    Metadata pickle. Defaults to ``$PIPELINE_DATA_DIR/vault-metadata-db.pickle``.
``VAULT_BENCHMARK_PRICE_PATH``
    Raw price Parquet. Defaults to ``$PIPELINE_DATA_DIR/vault-prices-1h.parquet``.
``VAULT_BENCHMARK_SETTLEMENT_DB_PATH``
    Optional settlement DuckDB path. When omitted, settlement annotation uses
    an empty temporary source instead of the operator's default production DB.
``VAULT_BENCHMARK_OUTPUT_PATH``
    Optional output path. It must not already exist and must differ from the
    input path; otherwise a temporary directory is used.
``VAULT_BENCHMARK_DAILY_OUTPUT_PATH``
    Optional daily sidecar path. The same non-overwrite rule applies.
``VAULT_BENCHMARK_DENOMINATION_FAMILY``
    ``stablecoin`` (default), ``eth``, ``btc`` or a comma-separated selection.
``VAULT_BENCHMARK_RESULT_PATH``
    Optional JSON result path. The result is always also printed to stdout.

Example:

.. code-block:: shell

    source .local-test.env && poetry run python scripts/erc-4626/benchmark-vault-price-cleaning.py
"""

import json
import logging
import os
import resource
import tempfile
import time
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq

from eth_defi.research.wrangle_vault_prices import generate_cleaned_vault_datasets
from eth_defi.vault.denomination import DenominationFamily

logger = logging.getLogger(__name__)


def _configured_path(environment_name: str, default: Path) -> Path:
    """Resolve one configured filesystem path.

    Environment-provided paths support ``~`` expansion. The default is
    returned unchanged so repository constants retain their canonical form.

    :param environment_name:
        Environment variable containing the optional override.
    :param default:
        Path used when the variable is unset or empty.
    :return:
        The configured, user-expanded path or ``default``.
    """
    configured = os.environ.get(environment_name)
    return Path(configured).expanduser() if configured else default


def _input_fingerprint(path: Path) -> dict[str, Any]:
    """Describe an input Parquet without hashing its complete contents.

    File metadata plus the Arrow schema is sufficient to distinguish ordinary
    benchmark runs while avoiding a second read of a production-sized file.
    This is an audit fingerprint, not a cryptographic content identity.

    :param path:
        Parquet file to inspect.
    :return:
        JSON-serialisable file, row-count and schema metadata.
    """
    parquet_file = pq.ParquetFile(path)
    metadata = parquet_file.metadata
    schema = parquet_file.schema_arrow
    stat = path.stat()
    return {
        "path": str(path),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "row_count": metadata.num_rows,
        "column_count": metadata.num_columns,
        "schema": [{"name": field.name, "type": str(field.type)} for field in schema],
    }


def _file_fingerprint(path: Path) -> dict[str, int | str]:
    """Describe a non-Parquet benchmark input without reading its contents.

    Pickled metadata may be large and is already read by the benchmarked
    pipeline. File size and modification time identify the tested copy without
    adding another full-file hash pass.

    :param path:
        Existing input file to describe.
    :return:
        JSON-serialisable path, size and modification time.
    """
    stat = path.stat()
    return {"path": str(path), "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _settlement_fingerprint(path: Path) -> dict[str, Any]:
    """Describe the explicitly selected settlement database.

    The connection is read-only so benchmarking cannot initialise or mutate a
    production-format copy. The row count makes annotated and unannotated runs
    distinguishable in stored benchmark results.

    :param path:
        Existing settlement DuckDB file.
    :return:
        File metadata plus the settlement table row count.
    """
    with duckdb.connect(str(path), read_only=True) as connection:
        row_count = connection.execute("SELECT count(*) FROM vault_settlements").fetchone()[0]
    return {"enabled": True, **_file_fingerprint(path), "row_count": int(row_count)}


def _parse_families(value: str) -> frozenset[DenominationFamily]:
    """Parse the benchmark's comma-separated denomination selection.

    Every non-empty token must be a declared :class:`DenominationFamily` value;
    an empty selection is rejected before a potentially expensive scan starts.

    :param value:
        Comma-separated denomination family values.
    :return:
        Non-empty immutable family selection.
    """
    names = [name.strip().lower() for name in value.split(",") if name.strip()]
    families = frozenset(DenominationFamily(name) for name in names)
    if not families:
        message = "VAULT_BENCHMARK_DENOMINATION_FAMILY must not be empty"
        raise ValueError(message)
    return families


def _assert_safe_output(path: Path, input_path: Path) -> None:
    """Reject a benchmark destination that could destroy existing data.

    Benchmarks must never replace the raw input or any prior output. Parent
    directories may be created later, but the destination itself must not exist.

    :param path:
        Proposed benchmark output.
    :param input_path:
        Immutable raw input that must not be overwritten.
    :return:
        ``None`` if the destination is safe.
    """
    if path.resolve() == input_path.resolve():
        raise ValueError(f"Benchmark output must differ from input: {path}")
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing benchmark output: {path}")


def main() -> None:  # noqa: PLR0914 - benchmark wiring keeps all env inputs explicit
    """Run one cleaning benchmark and emit a machine-readable result.

    Configuration comes exclusively from the documented environment variables.
    Unless an output is explicitly configured, it lives only for the duration
    of the temporary benchmark directory; the JSON records that distinction.

    :return:
        ``None``. The result is printed as JSON and optionally written to the
        configured result path.
    """
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(message)s")
    data_dir = Path(os.environ.get("PIPELINE_DATA_DIR", "~/.tradingstrategy/vaults")).expanduser()
    vault_db_path = _configured_path("VAULT_BENCHMARK_VAULT_DB_PATH", data_dir / "vault-metadata-db.pickle")
    price_path = _configured_path("VAULT_BENCHMARK_PRICE_PATH", data_dir / "vault-prices-1h.parquet")
    settlement_path_text = os.environ.get("VAULT_BENCHMARK_SETTLEMENT_DB_PATH")
    settlement_path = Path(settlement_path_text).expanduser() if settlement_path_text else None
    families = _parse_families(os.environ.get("VAULT_BENCHMARK_DENOMINATION_FAMILY", "stablecoin"))

    if not vault_db_path.is_file():
        raise FileNotFoundError(vault_db_path)
    if not price_path.is_file():
        raise FileNotFoundError(price_path)
    if settlement_path is not None and not settlement_path.is_file():
        raise FileNotFoundError(settlement_path)

    configured_output = os.environ.get("VAULT_BENCHMARK_OUTPUT_PATH")
    configured_daily_output = os.environ.get("VAULT_BENCHMARK_DAILY_OUTPUT_PATH")
    configured_result = os.environ.get("VAULT_BENCHMARK_RESULT_PATH")
    with tempfile.TemporaryDirectory(prefix="vault-price-benchmark-") as temporary_directory:
        temporary_dir = Path(temporary_directory)
        output_path = Path(configured_output).expanduser() if configured_output else temporary_dir / "cleaned-vault-prices-1h.parquet"
        daily_output_path = Path(configured_daily_output).expanduser() if configured_daily_output else None
        # ``None`` makes the library resolve the operator's default production
        # settlement DB. Point at a deliberately absent temporary path when no
        # benchmark input was configured, keeping the run self-contained.
        effective_settlement_path = settlement_path if settlement_path is not None else temporary_dir / "settlements-disabled.duckdb"
        _assert_safe_output(output_path, price_path)
        if daily_output_path is not None:
            _assert_safe_output(daily_output_path, price_path)
            if daily_output_path.resolve() == output_path.resolve():
                message = "Hourly and daily benchmark outputs must differ"
                raise ValueError(message)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if daily_output_path is not None:
            daily_output_path.parent.mkdir(parents=True, exist_ok=True)

        input_fingerprint = _input_fingerprint(price_path)
        wall_started_at = time.perf_counter()
        cpu_started_at = time.process_time()
        generate_cleaned_vault_datasets(
            vault_db_path=vault_db_path,
            price_df_path=price_path,
            cleaned_price_df_path=output_path,
            settlement_db_path=effective_settlement_path,
            denomination_families=families,
            daily_price_df_path=daily_output_path,
            logger=logger.info,
            warning_logger=logger.warning,
        )
        output_metadata = pq.ParquetFile(output_path).metadata
        result: dict[str, Any] = {
            "input": input_fingerprint,
            "vault_metadata": _file_fingerprint(vault_db_path),
            "settlements": _settlement_fingerprint(settlement_path) if settlement_path is not None else {"enabled": False},
            "families": sorted(family.value for family in families),
            "output_path": str(output_path),
            "output_is_temporary": configured_output is None,
            "output_rows": output_metadata.num_rows,
            "output_columns": output_metadata.num_columns,
            "wall_seconds": round(time.perf_counter() - wall_started_at, 3),
            "cpu_seconds": round(time.process_time() - cpu_started_at, 3),
            # Linux reports ru_maxrss in KiB.  Keep the unit in the key so the
            # number is not mistaken for bytes when copied into a dashboard.
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }
        encoded_result = json.dumps(result, sort_keys=True)
        print(encoded_result)
        if configured_result:
            result_path = Path(configured_result).expanduser()
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(encoded_result + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
