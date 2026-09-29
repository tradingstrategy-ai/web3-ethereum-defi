"""Focused scope and preservation checks for the Kamui price backfill."""

import datetime
import importlib.util
import math
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase

REVIEWED_KAMUI_COUNT = 3
UNRELATED_VAULT_COUNT = 1


def load_backfill_module() -> ModuleType:
    """Load the one-off script for direct plan and stage validation tests.

    :return:
        Imported Kamui backfill script module.
    """

    script = Path(__file__).resolve().parents[2] / "scripts" / "erc-4626" / "backfill-kamui-vault-prices.py"
    spec = importlib.util.spec_from_file_location("backfill_kamui_vault_prices", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def create_inputs(tmp_path: Path, backfill: ModuleType, *, existing_history: bool = False) -> tuple[Path, Path]:
    """Create three reviewed metadata rows, their prices and an unrelated row.

    :param tmp_path:
        Isolated test directory.
    :param backfill:
        Script module supplying the fixed reviewed block and address scope.
    :param existing_history:
        Include one earlier Kamui row per address to model an idempotent rerun.
    :return:
        Metadata pickle and raw Parquet paths.
    """

    now = datetime.datetime(2026, 9, 29)  # noqa: DTZ001 - Repository convention is naive UTC.
    metadata_rows = {}
    price_rows = []
    for address, first_block in backfill.KAMUI_FIRST_SEEN_BLOCKS.items():
        spec = VaultSpec(backfill.KAMUI_CHAIN_ID, address)
        detection = ERC4262VaultDetection(
            chain=backfill.KAMUI_CHAIN_ID,
            address=address,
            first_seen_at_block=first_block,
            first_seen_at=now,
            features={ERC4626Feature.erc_7540_like, ERC4626Feature.lagoon_like},
            updated_at=now,
            deposit_count=0,
            redeem_count=2,
        )
        metadata_rows[spec] = {"Protocol": "Lagoon Finance", "_detection_data": detection}
        price_rows.append({"chain": 1, "address": address, "block_number": backfill.KAMUI_BACKFILL_END_BLOCK, "timestamp": now, "share_price": 1.01, "total_assets": 100_000.0, "performance_fee": math.nan})
        if existing_history:
            price_rows.append({"chain": 1, "address": address, "block_number": first_block, "timestamp": now - datetime.timedelta(days=20), "share_price": 1.0, "total_assets": 1_000.0, "performance_fee": math.nan})

    price_rows.append({"chain": 1, "address": "0x1111111111111111111111111111111111111111", "block_number": backfill.KAMUI_BACKFILL_END_BLOCK - 1, "timestamp": now, "share_price": 2.0, "total_assets": 500.0, "performance_fee": math.nan})
    vault_database = tmp_path / "vault-metadata-db.pickle"
    price_database = tmp_path / "vault-prices-1h.parquet"
    VaultDatabase(rows=metadata_rows).write(vault_database)
    pq.write_table(pa.Table.from_pylist(price_rows), price_database)
    return vault_database, price_database


def test_kamui_backfill_plan_is_fixed_scope_and_read_only(tmp_path: Path) -> None:
    """Plan only three reviewed addresses and leave both input files unchanged."""

    backfill = load_backfill_module()
    vault_database, price_database = create_inputs(tmp_path, backfill)
    original_metadata = vault_database.read_bytes()
    original_prices = price_database.read_bytes()

    plan = backfill.plan_kamui_backfill(vault_database, price_database)

    assert {detection.address for detection in plan.detections} == set(backfill.KAMUI_FIRST_SEEN_BLOCKS)
    assert plan.start_block == min(backfill.KAMUI_FIRST_SEEN_BLOCKS.values())
    assert plan.end_block == backfill.KAMUI_BACKFILL_END_BLOCK
    assert plan.rows_to_replace == 0
    assert plan.original_row_count == REVIEWED_KAMUI_COUNT + UNRELATED_VAULT_COUNT
    assert set(plan.forward_rows_by_address.values()) == {1}
    assert vault_database.read_bytes() == original_metadata
    assert price_database.read_bytes() == original_prices


def test_kamui_backfill_rerun_replaces_only_reviewed_history(tmp_path: Path) -> None:
    """Count prior historical rows inside the same half-open replacement range."""

    backfill = load_backfill_module()
    vault_database, price_database = create_inputs(tmp_path, backfill, existing_history=True)

    plan = backfill.plan_kamui_backfill(vault_database, price_database)

    assert plan.rows_to_replace == REVIEWED_KAMUI_COUNT
    assert plan.original_row_count == REVIEWED_KAMUI_COUNT * 2 + UNRELATED_VAULT_COUNT
    assert set(plan.forward_rows_by_address.values()) == {1}


def test_kamui_backfill_stage_preserves_forward_rows(tmp_path: Path) -> None:
    """Accept only staged histories with finite prices and unchanged later rows."""

    backfill = load_backfill_module()
    vault_database, price_database = create_inputs(tmp_path, backfill)
    plan = backfill.plan_kamui_backfill(vault_database, price_database)
    before = backfill.load_scoped_price_rows(price_database)
    staged_database = tmp_path / "staged.parquet"
    original_rows = pq.read_table(price_database).to_pylist()
    historical_rows = [
        {"chain": 1, "address": address, "block_number": first_block, "timestamp": datetime.datetime(2026, 9, 10), "share_price": 1.0, "total_assets": 1_000.0, "performance_fee": math.nan}  # noqa: DTZ001 - Repository convention is naive UTC.
        for address, first_block in backfill.KAMUI_FIRST_SEEN_BLOCKS.items()
    ]
    pq.write_table(pa.Table.from_pylist(original_rows + historical_rows), staged_database)
    result = {
        "chain_id": 1,
        "start_block": plan.start_block,
        "end_block": plan.end_block,
        "rows_deleted": 0,
        "existing_row_count": plan.original_row_count,
        "rows_written": 3,
        "rows_written_by_vault": dict.fromkeys(backfill.KAMUI_FIRST_SEEN_BLOCKS, 1),
        "price_rows_written_by_vault": dict.fromkeys(backfill.KAMUI_FIRST_SEEN_BLOCKS, 1),
    }

    backfill.validate_staged_prices(plan, before, staged_database, result)

    short_history_rows = [dict(row) for row in original_rows + historical_rows]
    for row in short_history_rows:
        if row["block_number"] < backfill.KAMUI_BACKFILL_END_BLOCK and row["address"] in backfill.KAMUI_FIRST_SEEN_BLOCKS:
            row["timestamp"] = datetime.datetime(2026, 9, 28)  # noqa: DTZ001 - Repository convention is naive UTC.
    pq.write_table(pa.Table.from_pylist(short_history_rows), staged_database)
    with pytest.raises(RuntimeError, match="less than 14 days"):
        backfill.validate_staged_prices(plan, before, staged_database, result)

    altered_nan_rows = [dict(row) for row in original_rows + historical_rows]
    altered_nan_rows[0]["performance_fee"] = None
    pq.write_table(pa.Table.from_pylist(altered_nan_rows), staged_database)
    with pytest.raises(RuntimeError, match="forward price row NaN"):
        backfill.validate_staged_prices(plan, before, staged_database, result)

    altered_rows = [dict(row) for row in original_rows + historical_rows]
    altered_rows[0]["share_price"] = 1.5
    pq.write_table(pa.Table.from_pylist(altered_rows), staged_database)
    with pytest.raises(RuntimeError, match="forward price row"):
        backfill.validate_staged_prices(plan, before, staged_database, result)


def test_kamui_backfill_rejects_metadata_drift(tmp_path: Path) -> None:
    """Stop if a reviewed vault's first-seen block no longer matches the plan."""

    backfill = load_backfill_module()
    vault_database, price_database = create_inputs(tmp_path, backfill)
    database = VaultDatabase.read(vault_database)
    first_row = next(iter(database.rows.values()))
    detection = first_row["_detection_data"]
    first_row["_detection_data"] = replace(detection, first_seen_at_block=detection.first_seen_at_block + 1)
    database.write(vault_database)

    with pytest.raises(RuntimeError, match="first-seen block changed"):
        backfill.plan_kamui_backfill(vault_database, price_database)


def test_kamui_backfill_rejects_ambiguous_dry_run_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """A misspelt dry-run setting must never enter the persistent write path."""

    backfill = load_backfill_module()
    monkeypatch.setenv("DRY_RUN", "ture")

    with pytest.raises(ValueError, match="DRY_RUN must be true or false"):
        backfill.main()
