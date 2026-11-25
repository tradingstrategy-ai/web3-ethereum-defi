"""Regression tests for the staged ApeX price repair."""

import json
import runpy
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eth_defi.apex.constants import APEX_CHAIN_ID, APEX_METRICS_DATABASE
from eth_defi.apex.metrics import ApexMetricsDatabase
from eth_defi.perp_dex import parquet
from eth_defi.vault.publication_state import NATIVE_REVISIONS_KEY
from eth_defi.vault_protocols.migration import MigrationContext, discover_migrations

SCRIPT_NAME = "106-apex-price-key-collisions.py"


@pytest.fixture
def migrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[MigrationContext], None]:
    """Provide a read-only source and point-in-time snapshots for the repair."""
    database = ApexMetricsDatabase(tmp_path / APEX_METRICS_DATABASE.name)
    try:
        database.con.execute("""
            INSERT INTO vault_prices VALUES (
                '100', 'apex-vault-100', TIMESTAMP '2026-11-22 00:00:00.100250',
                1.5, 20, 10, 'ranking_snapshot', NULL, TIMESTAMP '2026-11-22'
            )
        """)
    finally:
        database.close()

    snapshots = parquet.ensure_perp_metric_columns(
        pd.DataFrame(
            {
                "chain": [APEX_CHAIN_ID, APEX_CHAIN_ID],
                "address": ["apex-vault-100", "apex-vault-100"],
                "position_effective_at": pd.to_datetime(["2026-11-22", "2026-11-23 00:05"], format="mixed"),
                "perp_metrics_observed_at": pd.to_datetime(["2026-11-22 00:00:00.000250", "2026-11-23 00:05"], format="mixed"),
                "perp_quote_asset": ["USDT", "USDC"],
                "perp_position_data_status": ["authentication_required", "authentication_required"],
            }
        )
    )
    monkeypatch.setattr(parquet, "derive_perp_vault_metric_snapshots", lambda *_: snapshots)
    script = next(spec for spec in discover_migrations() if spec.name == SCRIPT_NAME)
    repair = runpy.run_path(str(script.path))["migrate"]
    # Exercise different input and output row-group boundaries on a small fixture.
    monkeypatch.setitem(repair.__globals__, "BATCH_ROWS", 2)
    return repair


@pytest.fixture
def raw_prices(tmp_path: Path) -> Path:
    """Write duplicate ApeX rows separated by a retained one in source order."""
    path = tmp_path / "vault-prices-1h.parquet"
    table = pa.table(
        {
            "chain": [APEX_CHAIN_ID, 42161, APEX_CHAIN_ID, 42161, APEX_CHAIN_ID],
            "address": ["apex-vault-100", "0xbtc", "apex-vault-100", "0xbtc", "apex-vault-100"],
            "timestamp": pa.Array.from_pandas(pd.to_datetime(["2026-11-22 00:00:00.100", "2026-11-22", "2026-11-22 00:00:00.100", "2026-11-22", "2026-11-23"], format="mixed")).cast(pa.timestamp("ms")),
            "block_number": [0, 101, 0, 102, 0],
            "share_price": [1.0, 2.0, 1.0, 2.0, 1.1],
            "independent_value": ["A", "B", "A", "C", "D"],
            "perp_quote_asset": [None, None, "USDC", None, None],
            "perp_position_data_status": [None, None, "authentication_required", None, None],
            "perp_metrics_observed_at": pa.Array.from_pandas(pd.to_datetime([None, None, "2026-11-21", None, None])).cast(pa.timestamp("ms")),
        }
    ).replace_schema_metadata({NATIVE_REVISIONS_KEY: json.dumps({"apex": [1], "grvt": [2]}).encode()})
    pq.write_table(table, path, row_group_size=2)
    return path


def test_apex_price_key_migration_preserves_history(tmp_path: Path, raw_prices: Path, migrate: Callable[[MigrationContext], None]) -> None:
    """Preview, repair and rerun without selecting a later snapshot for old prices."""
    original = raw_prices.read_bytes()
    preview = MigrationContext(tmp_path, True, SCRIPT_NAME)
    migrate(preview)
    assert preview.other_data["changed_rows"] == 1
    assert raw_prices.read_bytes() == original

    apply = MigrationContext(tmp_path, False, SCRIPT_NAME)
    migrate(apply)
    result = pq.read_table(raw_prices)
    assert result["block_number"].to_pylist() == [0, 101, 102, 0]
    assert result["independent_value"].to_pylist() == ["A", "B", "C", "D"]
    assert result["share_price"].to_pylist() == pytest.approx([1.0, 2.0, 2.0, 1.1])
    assert result["perp_metrics_observed_at"][0].as_py() == pd.Timestamp("2026-11-22")
    assert result["perp_quote_asset"].to_pylist() == ["USDT", None, None, None]
    assert json.loads(result.schema.metadata[NATIVE_REVISIONS_KEY]) == {"grvt": [2]}
    assert Path(apply.other_data["backup_paths"][0]).read_bytes() == original

    repaired = raw_prices.read_bytes()
    repeat = MigrationContext(tmp_path, False, SCRIPT_NAME)
    migrate(repeat)
    assert repeat.other_data["changed_rows"] == 0
    assert repeat.modified_files == []
    assert raw_prices.read_bytes() == repaired


def test_apex_price_key_migration_rejects_colliding_source(tmp_path: Path, raw_prices: Path, migrate: Callable[[MigrationContext], None]) -> None:
    """An ambiguous source must leave the original and journal paths intact."""
    database = ApexMetricsDatabase(tmp_path / APEX_METRICS_DATABASE.name)
    try:
        database.con.execute("INSERT INTO vault_prices SELECT * REPLACE (TIMESTAMP '2026-11-22 00:00:00.100500' AS timestamp) FROM vault_prices")
    finally:
        database.close()
    original = raw_prices.read_bytes()
    context = MigrationContext(tmp_path, False, SCRIPT_NAME)
    with pytest.raises(ValueError, match="source has colliding observations"):
        migrate(context)
    assert raw_prices.read_bytes() == original
    assert context.modified_files == []


@pytest.mark.parametrize(
    ("conflict", "message"),
    [
        ("third-row", "larger than two"),
        ("different-price", "disagree in share_price"),
        ("different-snapshot", "snapshot disagrees with stored perp_open_position_count"),
        ("missing-source", "no observations"),
    ],
)
def test_apex_price_key_migration_preserves_file_on_conflict(tmp_path: Path, raw_prices: Path, migrate: Callable[[MigrationContext], None], conflict: str, message: str) -> None:
    """Ambiguous stored or source evidence leaves the original file intact."""
    rows = pq.read_table(raw_prices)
    if conflict == "third-row":
        rows = pa.concat_tables([rows, rows.slice(0, 1)])
    elif conflict == "different-price":
        rows = rows.set_column(rows.schema.get_field_index("share_price"), "share_price", pa.array([3.0, 2.0, 1.0, 2.0, 1.1]))
    elif conflict == "different-snapshot":
        rows = rows.append_column("perp_open_position_count", pa.array([2, None, 2, None, None]))
    else:
        database = ApexMetricsDatabase(tmp_path / APEX_METRICS_DATABASE.name)
        try:
            database.con.execute("DELETE FROM vault_prices")
        finally:
            database.close()
    pq.write_table(rows, raw_prices)
    original = raw_prices.read_bytes()
    with pytest.raises(ValueError, match=message):
        migrate(MigrationContext(tmp_path, False, SCRIPT_NAME))
    assert raw_prices.read_bytes() == original


def test_apex_price_key_migration_collapses_identical_raw_only_pair(tmp_path: Path, raw_prices: Path, migrate: Callable[[MigrationContext], None]) -> None:
    """An identical pair can be retained once even after it leaves the source archive."""
    rows = pq.read_table(raw_prices)
    unique = rows.take([1, 3, 4])
    pq.write_table(pa.concat_tables([unique, unique.slice(2, 1)]), raw_prices)
    migrate(MigrationContext(tmp_path, False, SCRIPT_NAME))
    assert pq.read_table(raw_prices).equals(unique)
