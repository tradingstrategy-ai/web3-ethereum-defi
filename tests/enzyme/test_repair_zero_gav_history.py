"""Tests for the recoverable Enzyme zero-GAV history repair."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pyarrow as pa
import pyarrow.parquet as pq

from eth_defi.vault.base import VaultSpec

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "enzyme" / "repair-zero-gav-history.py"
BLUE_ADDRESS = "0x000000000000000000000000000000000000beef"
OTHER_ADDRESS = "0x000000000000000000000000000000000000cafe"
RAW_ROW_COUNT = 4


def load_repair_module() -> ModuleType:
    """Load the hyphenated Enzyme repair script as a testable module.

    :return: Loaded repair module.
    """

    spec = importlib.util.spec_from_file_location("enzyme_repair_zero_gav_history", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_raw_prices(path: Path) -> None:
    """Write representative raw rows with one invalid Blue observation.

    :param path: Destination Parquet path.
    :return: ``None``.
    """

    table = pa.table(
        {
            "chain": pa.array([1, 1, 1, 1], type=pa.uint32()),
            "address": [BLUE_ADDRESS, BLUE_ADDRESS, BLUE_ADDRESS, OTHER_ADDRESS],
            "block_number": pa.array([1, 2, 3, 4], type=pa.uint64()),
            "share_price": [1.0, 0.0, 0.0, 0.0],
            "total_assets": [100.0, 0.0, 0.0, 0.0],
            "total_supply": [100.0, 100.0, 0.0, 100.0],
        }
    )
    pq.write_table(table, path)


def test_repair_zero_gav_rows_dry_run_preserves_source(tmp_path: Path) -> None:
    """Report only the positive-supply zero price without a dry-run write."""

    module = load_repair_module()
    raw_path = tmp_path / "raw.parquet"
    create_raw_prices(raw_path)

    report, affected_ids = module.repair_zero_gav_rows(raw_path, {VaultSpec(1, BLUE_ADDRESS)}, dry_run=True)

    assert report.invalid_row_count == 1
    assert report.affected_vault_count == 1
    assert report.backup_path is None
    assert affected_ids == {"1-" + BLUE_ADDRESS}
    assert pq.read_table(raw_path).num_rows == RAW_ROW_COUNT


def test_repair_zero_gav_rows_creates_backup_and_preserves_other_rows(tmp_path: Path) -> None:
    """Atomically remove only invalid Blue rows and retain a raw backup.

    :param tmp_path: Isolated Parquet storage supplied by pytest.
    :return: ``None``.
    """

    module = load_repair_module()
    raw_path = tmp_path / "raw.parquet"
    create_raw_prices(raw_path)

    report, affected_ids = module.repair_zero_gav_rows(raw_path, {VaultSpec(1, BLUE_ADDRESS)}, dry_run=False)

    repaired = pq.read_table(raw_path).to_pydict()
    assert report.invalid_row_count == 1
    assert report.backup_path is not None
    assert report.backup_path.exists()
    assert pq.read_table(report.backup_path).num_rows == RAW_ROW_COUNT
    assert affected_ids == {"1-" + BLUE_ADDRESS}
    assert repaired["address"] == [BLUE_ADDRESS, BLUE_ADDRESS, OTHER_ADDRESS]
    assert repaired["total_supply"] == [100.0, 0.0, 100.0]
