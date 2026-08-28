"""Focused tests for cleaned currency API Parquet materialisation."""

import datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from eth_defi.currency_api.constants import SOURCE_NAME
from eth_defi.currency_api.parquet import EXCHANGE_RATE_PARQUET_SCHEMA, materialise_exchange_rate_parquet

EXPECTED_CLEANED_ROW_COUNT = 2


def _create_exchange_rate_database(path: Path) -> None:
    """Create a small raw exchange-rate database for Parquet tests.

    :param path:
        DuckDB destination.
    :return:
        ``None`` after writing raw provider rows.
    """
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            """
            CREATE TABLE exchange_rates (
                date DATE NOT NULL,
                base_currency VARCHAR NOT NULL,
                quote_currency VARCHAR NOT NULL,
                rate DOUBLE NOT NULL,
                source VARCHAR NOT NULL,
                written_at TIMESTAMP
            )
            """
        )
        connection.executemany(
            "INSERT INTO exchange_rates VALUES (?, ?, ?, ?, ?, ?)",
            [
                (datetime.date(2025, 12, 5), "usd", "eth", 0.0005, SOURCE_NAME, datetime.datetime.fromisoformat("2025-12-05T00:01:00")),
                (datetime.date(2025, 12, 6), "usd", "eth", 49_644.07797781, SOURCE_NAME, datetime.datetime.fromisoformat("2025-12-06T00:01:00")),
                (datetime.date(2025, 12, 6), "usd", "btc", 38.87461377, SOURCE_NAME, datetime.datetime.fromisoformat("2025-12-06T00:01:00")),
                (datetime.date(2025, 12, 5), "usd", "eur", 0.91, SOURCE_NAME, datetime.datetime.fromisoformat("2025-12-05T00:01:00")),
            ],
        )
    finally:
        connection.close()


def test_materialise_exchange_rate_parquet_is_cleaned_deterministic_and_read_only(tmp_path: Path) -> None:
    """Export exactly typed source-rate rows without mutating the DuckDB input.

    :param tmp_path:
        Isolated temporary directory supplied by pytest.
    """
    source_path = tmp_path / "source" / "exchange-rates.duckdb"
    source_path.parent.mkdir()
    _create_exchange_rate_database(source_path)
    source_bytes = source_path.read_bytes()
    destination_path = tmp_path / "pipeline" / "exchange-rates.parquet"

    first = materialise_exchange_rate_parquet(source_path, destination_path)
    first_bytes = destination_path.read_bytes()
    second = materialise_exchange_rate_parquet(source_path, destination_path)

    table = pq.read_table(destination_path)
    assert table.schema == EXCHANGE_RATE_PARQUET_SCHEMA
    assert table.column("date").type == pa.date32()
    assert table.column("written_at").type == pa.timestamp("us")
    assert first.row_count == EXPECTED_CLEANED_ROW_COUNT
    assert first.sha256 == second.sha256
    assert first_bytes == destination_path.read_bytes()
    assert source_path.read_bytes() == source_bytes
    assert table.to_pydict()["quote_currency"] == ["eth", "eur"]
    assert table.to_pydict()["rate"] == [0.0005, 0.91]
    assert not list(destination_path.parent.glob(".exchange-rates-*.parquet"))
