"""Check file-backed timestamp migrations and duplicate ingestion."""

import datetime
import subprocess  # noqa: S404 - isolated import regression uses a fixed interpreter and source.
import sys
from pathlib import Path

import duckdb
import pandas as pd

from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase

ROW_COUNT_AFTER_INSERT = 1_000_001
JANUARY_2026_TIMESTAMP = 1_767_225_600


def test_core_vault_import_does_not_require_duckdb() -> None:
    """Keep the optional DuckDB extra out of core vault import requirements."""
    program = """
import sys
from pathlib import Path
sys.modules['duckdb'] = None
from eth_defi.vault.base import VaultSpec
from eth_defi.event_reader.timestamp_cache import BlockTimestampDatabase
try:
    BlockTimestampDatabase(1, Path(':memory:'))
except ImportError as error:
    assert 'optional duckdb extra' in str(error)
else:
    raise AssertionError('Opening a cache must require DuckDB')
"""
    subprocess.run([sys.executable, "-c", program], check=True, capture_output=True, timeout=30)  # noqa: S603 - arguments contain no external input.


def test_timestamp_cache_migration_preserves_rows(tmp_path: Path) -> None:
    """Preserve a legacy file and safely ingest repeated and corrected chunks.

    A million-row file exercises transactional schema replacement. The manual
    migration also checks the checksum of an existing 90-million-row cache.

    :param tmp_path: Isolated file-backed database directory.
    """
    path = tmp_path / "timestamps.duckdb"
    connection = duckdb.connect(str(path))
    try:
        connection.execute("CREATE TABLE block_timestamps (block_number UINT64 PRIMARY KEY, timestamp UINT32)")
        connection.execute("INSERT INTO block_timestamps SELECT i, 1700000000 + i FROM range(1000000) AS r(i)")
        original = connection.execute("SELECT count(*), sum(hash(block_number, timestamp)) FROM block_timestamps").fetchone()
    finally:
        connection.close()

    database = BlockTimestampDatabase(1, path)
    try:
        assert database.con.execute("SELECT count(*), sum(hash(block_number, timestamp)) FROM block_timestamps").fetchone() == original
        assert not database.con.execute("SELECT * FROM duckdb_constraints() WHERE table_name='block_timestamps'").fetchall()
        chunk = pd.Series([1700000000, 1700000001, 1701000000], index=[0, 1, 1000000])
        database.import_chain_data(1, chunk)
        database.import_chain_data(1, chunk)
        database.import_chain_data(1, pd.Series([1800000000, 1800000001], index=[1, 1]))
        assert database.get_count() == ROW_COUNT_AFTER_INSERT
        assert database.con.execute("SELECT timestamp FROM block_timestamps WHERE block_number=1").fetchall() == [(1800000001,)]
        assert database.con.execute("SELECT count(DISTINCT block_number) FROM block_timestamps").fetchone()[0] == database.get_count()
        database.import_chain_data(1, {1: datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC).replace(tzinfo=None)})
        assert database.con.execute("SELECT timestamp FROM block_timestamps WHERE block_number=1").fetchone()[0] == JANUARY_2026_TIMESTAMP
        assert database.query(1, 1).iloc[0] == pd.Timestamp("2026-01-01")
    finally:
        database.con.close()
        database.con = None

    reopened = BlockTimestampDatabase(1, path)
    try:
        assert reopened.get_count() == ROW_COUNT_AFTER_INSERT
    finally:
        reopened.con.close()
        reopened.con = None
