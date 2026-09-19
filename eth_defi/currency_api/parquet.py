"""Cleaned Parquet materialisation for currency API exchange rates.

The raw DuckDB database remains the auditable source of currency API records.
This module creates a deterministic, sanitised Parquet snapshot for consumers
that must not apply rate-cleaning policy themselves.
"""

import datetime
import hashlib
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from eth_defi.currency_api.cleaning import filter_known_bad_rates

#: Stable schema for the cleaned exchange-rate consumer export.
EXCHANGE_RATE_PARQUET_SCHEMA = pa.schema(
    [
        pa.field("date", pa.date32(), nullable=False),
        pa.field("base_currency", pa.string(), nullable=False),
        pa.field("quote_currency", pa.string(), nullable=False),
        pa.field("rate", pa.float64(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("written_at", pa.timestamp("us"), nullable=True),
    ]
)


@dataclass(frozen=True, slots=True)
class ExchangeRateParquetSnapshot:
    """Verified cleaned exchange-rate Parquet snapshot."""

    #: Local destination path of the materialised Parquet file.
    path: Path

    #: Number of cleaned exchange-rate rows in the snapshot.
    row_count: int

    #: SHA-256 digest of the byte-identical local Parquet file.
    sha256: str


def load_cleaned_exchange_rates(source_path: Path) -> pd.DataFrame:
    """Read and clean all exchange-rate rows from an existing DuckDB database.

    Uses DuckDB read-only mode and intentionally bypasses
    :class:`~eth_defi.currency_api.database.CurrencyRateDatabase`: that mutable
    storage class creates directories and may initialise schema. The returned
    frame is sorted by its logical key and has stable column order.

    :param source_path:
        Existing raw currency API DuckDB database.
    :return:
        Sanitised DataFrame with ``date``, currency, ``rate``, ``source`` and
        naive-UTC ``written_at`` columns.
    :raises FileNotFoundError:
        If the source database does not exist.
    :raises ValueError:
        If source rows do not meet the consumer export contract.
    """
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    connection = duckdb.connect(str(source_path), read_only=True)
    try:
        rates = connection.execute(
            """
            SELECT date, base_currency, quote_currency, rate, source, written_at
            FROM exchange_rates
            """
        ).fetchdf()
    finally:
        connection.close()

    cleaned = filter_known_bad_rates(rates)
    return _validate_and_normalise_rates(cleaned)


def materialise_exchange_rate_parquet(
    source_path: Path,
    destination_path: Path,
) -> ExchangeRateParquetSnapshot:
    """Atomically materialise one cleaned Parquet snapshot from DuckDB.

    The write is deterministic for unchanged source rows: row order, column
    order and row values are all fixed, and no materialisation timestamp is
    inserted. A failed conversion leaves the prior destination untouched.

    :param source_path:
        Existing raw currency API DuckDB database opened read-only.
    :param destination_path:
        Cleaned Parquet destination, normally below the pipeline data directory.
    :return:
        Verified Parquet snapshot metadata.
    """
    rates = load_cleaned_exchange_rates(source_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    table = _rates_to_arrow_table(rates)

    file_descriptor, temporary_name = tempfile.mkstemp(
        suffix=".parquet",
        dir=destination_path.parent,
        prefix=f".{destination_path.stem}-",
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    try:
        pq.write_table(table, temporary_path, compression="zstd")
        verified = pq.read_table(temporary_path)
        if verified.schema != EXCHANGE_RATE_PARQUET_SCHEMA:
            raise ValueError(f"Unexpected exchange-rate Parquet schema: {verified.schema}")
        if verified.num_rows != len(rates):
            raise ValueError(f"Exchange-rate Parquet row count mismatch: {verified.num_rows} != {len(rates)}")
        payload = temporary_path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        os.replace(temporary_path, destination_path)
    finally:
        temporary_path.unlink(missing_ok=True)

    return ExchangeRateParquetSnapshot(
        path=destination_path,
        row_count=len(rates),
        sha256=digest,
    )


def _validate_and_normalise_rates(rates: pd.DataFrame) -> pd.DataFrame:
    """Validate source rows and normalise them for deterministic Parquet output.

    :param rates:
        Cleaned currency API rows in storage schema.
    :return:
        Sorted and type-normalised DataFrame suitable for Arrow conversion.
    :raises ValueError:
        If required columns, finite positive rates or logical-key uniqueness are
        violated.
    """
    expected_columns = list(EXCHANGE_RATE_PARQUET_SCHEMA.names)
    missing_columns = set(expected_columns) - set(rates.columns)
    if missing_columns:
        raise ValueError(f"Exchange-rate source is missing columns: {sorted(missing_columns)!r}")

    normalised = rates.loc[:, expected_columns].copy()
    normalised["date"] = pd.to_datetime(normalised["date"], errors="raise").dt.date
    for column in ("base_currency", "quote_currency", "source"):
        normalised[column] = normalised[column].astype("string")
    normalised["rate"] = pd.to_numeric(normalised["rate"], errors="raise")
    normalised["written_at"] = pd.to_datetime(normalised["written_at"], errors="coerce").dt.tz_localize(None)

    if normalised[["date", "base_currency", "quote_currency", "source"]].isna().any().any():
        message = "Exchange-rate source contains null logical-key values"
        raise ValueError(message)
    if normalised["rate"].isna().any() or not normalised["rate"].map(math.isfinite).all() or (normalised["rate"] <= 0).any():
        message = "Exchange-rate source contains non-finite or non-positive rates"
        raise ValueError(message)
    logical_key = ["date", "base_currency", "quote_currency", "source"]
    if normalised.duplicated(logical_key).any():
        message = "Exchange-rate source contains duplicate logical keys"
        raise ValueError(message)

    return normalised.sort_values(logical_key, kind="stable").reset_index(drop=True)


def _rates_to_arrow_table(rates: pd.DataFrame) -> pa.Table:
    """Convert validated rates to the exact stable Arrow schema.

    :param rates:
        Validated and sorted cleaned rates.
    :return:
        Arrow table using :data:`EXCHANGE_RATE_PARQUET_SCHEMA`.
    """
    dates = [value if isinstance(value, datetime.date) else value.date() for value in rates["date"]]
    arrays = [
        pa.array(dates, type=pa.date32()),
        pa.array(rates["base_currency"].tolist(), type=pa.string()),
        pa.array(rates["quote_currency"].tolist(), type=pa.string()),
        pa.array(rates["rate"].tolist(), type=pa.float64()),
        pa.array(rates["source"].tolist(), type=pa.string()),
        pa.array(rates["written_at"].tolist(), type=pa.timestamp("us")),
    ]
    return pa.Table.from_arrays(arrays, schema=EXCHANGE_RATE_PARQUET_SCHEMA)
