"""Tests for sparkline publication state, cadence and canonical inputs."""

from __future__ import annotations

import datetime
import gzip
import json
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from eth_defi.research import sparkline_export
from eth_defi.research.sparkline import SparklineData
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


@dataclass(slots=True)
class DetectionStub:
    """Pickleable metadata detection stub for the exporter fixture."""

    spec: VaultSpec

    def get_spec(self) -> VaultSpec:
        """Return the synthetic vault identity."""
        return self.spec


@pytest.fixture
def uploaded(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture the real exporter's R2 requests in memory."""
    requests: list[dict[str, Any]] = []

    def upload(**kwargs: Any) -> bool:
        requests.append(kwargs)
        return True

    monkeypatch.setattr(sparkline_export, "_create_s3_client_from_environment", lambda _workers: (object(), "test-bucket"))
    monkeypatch.setattr(sparkline_export, "upload_bytes_to_r2", upload)
    return requests


def _write_export_inputs(tmp_path: Path, *, total_assets: float, vault_count: int = 1) -> tuple[str, Path, Path, pd.DataFrame, partial[sparkline_export.SparklineExportResult]]:
    """Write synthetic vault metadata and prices for coordinator tests.

    :param tmp_path:
        Temporary directory for the metadata, price and state files.
    :param total_assets:
        Repeated latest TVL value for each synthetic vault.
    :param vault_count:
        Number of distinct vaults to include in the price input.
    :return:
        First vault ID, price and state paths, source prices, and the exporter
        bound to those files.
    """
    specs = [VaultSpec(1, f"0x{index:040x}") for index in range(1, vault_count + 1)]
    vault_ids = [spec.as_string_id() for spec in specs]
    vault_db_path = tmp_path / "vaults.pickle"
    vault_rows = {spec: {"Denomination": "USDC", "_detection_data": DetectionStub(spec)} for spec in specs}
    VaultDatabase(rows=vault_rows).write(vault_db_path)
    index = pd.date_range("2026-01-01", periods=15, freq="D", name="timestamp")
    prices = pd.DataFrame(
        {
            "id": [vault_id for _ in index for vault_id in vault_ids],
            "share_price": [1.0 + i / 100 for i in range(len(index)) for _vault_id in vault_ids],
            "total_assets": [total_assets] * (len(vault_ids) * len(index)),
        },
        index=index.repeat(len(vault_ids)),
    )
    prices_path = tmp_path / "prices.parquet"
    prices.to_parquet(prices_path)
    state_path = tmp_path / "state.json"
    export = partial(sparkline_export.run_sparkline_export, vault_db_path=vault_db_path, prices_path=prices_path, state_path=state_path, render_workers=1, upload_workers=1, force=False)
    return vault_ids[0], prices_path, state_path, prices, export


def test_state_round_trip_and_retention(tmp_path: Path) -> None:
    """State saves atomically and prunes entries only after 90 days."""
    now = datetime.datetime(2026, 9, 22)  # noqa: DTZ001
    state = sparkline_export.make_empty_sparkline_state(now)
    state["vaults"] = {
        "recent": {"last_seen_at": "2026-06-24T00:00:00Z", "consecutive_failures": 0},
        "old": {"last_seen_at": "2026-06-23T23:59:59Z", "consecutive_failures": 0},
    }
    path = tmp_path / "sparkline-export-state.json"

    sparkline_export.save_sparkline_state(state, path, now)
    loaded = sparkline_export.load_sparkline_state(path, now)

    assert set(loaded["vaults"]) == {"recent"}
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_two_render_batches_complete_with_correct_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Separate bounded batches update counters and state for each vault.

    :param tmp_path:
        Temporary metadata, price and export-state location.
    :param monkeypatch:
        Isolated clock for this test.
    :return:
        None.
    """
    expected_vault_count = 2
    first_vault_id, _, state_path, prices, export = _write_export_inputs(tmp_path, total_assets=10_000.0, vault_count=expected_vault_count)
    fixed_now = datetime.datetime(2026, 9, 22)  # noqa: DTZ001
    monkeypatch.setattr(sparkline_export, "native_datetime_utc_now", lambda: fixed_now)
    render = sparkline_export.render_vault_sparklines

    def check_previous_batch(vault_id: str, data: SparklineData) -> list[sparkline_export.RenderData]:
        if vault_id != first_vault_id:
            state = sparkline_export.load_sparkline_state(state_path, fixed_now)
            assert state["vaults"][first_vault_id]["last_completed_at"] == "2026-09-22T00:00:00Z"
        return render(vault_id, data)

    monkeypatch.setattr(sparkline_export, "render_vault_sparklines", check_previous_batch)

    result = export(batch_size=1, force=True, dry_run=True)

    state = sparkline_export.load_sparkline_state(state_path, fixed_now)
    assert result.success
    assert result.counters["batches"] == expected_vault_count
    assert result.counters["rendered"] == expected_vault_count
    assert all(state["vaults"][vault_id]["last_completed_at"] == "2026-09-22T00:00:00Z" for vault_id in prices["id"].unique())


def test_young_vault_publication_uses_launch_observations(tmp_path: Path, uploaded: list[dict[str, Any]]) -> None:
    """Publish all three images on the first day from the canonical daily bundle.

    The bundle keeps the initial price alongside the daily close. The exporter
    must use these two observations without a separate hourly price input.
    Render in a child process to exercise the production worker boundary.

    :param tmp_path:
        Isolated metadata, price and publication-state paths.
    :param uploaded:
        Capture R2 uploads without network writes.
    :return:
        None.
    """
    vault_id, prices_path, _, prices, export = _write_export_inputs(tmp_path, total_assets=10_000.0)
    prices = prices.iloc[:2].copy()
    prices.index = pd.to_datetime(["2026-09-24 12:10:57", "2026-09-24 18:37:08"])
    prices.index.name = "timestamp"
    prices["share_price"] = [96.606690, 76.246850]
    prices.to_parquet(prices_path)
    result = export(render_workers=2, render_backend="processes")
    assert result.success
    assert result.counters["eligible"] == 1
    assert result.counters["uploaded"] == sparkline_export.SPARKLINE_FORMAT_COUNT
    payloads = {entry["object_name"]: gzip.decompress(entry["payload"]) for entry in uploaded}
    assert payloads.keys() == {f"sparkline-90d-{vault_id}.svg", f"sparkline-90d-{vault_id}.png", f"sparkline-table-90d-{vault_id}.png"}
    assert payloads[f"sparkline-90d-{vault_id}.svg"].startswith(b"<?xml")
    assert payloads[f"sparkline-90d-{vault_id}.png"].startswith(b"\x89PNG")
    assert payloads[f"sparkline-table-90d-{vault_id}.png"].startswith(b"\x89PNG")


def test_export_state_skips_unchanged_high_tvl_without_rendering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uploaded: list[dict[str, Any]]) -> None:
    """Unchanged inputs skip both preparation and rendering across exports."""
    _, prices_path, _, prices, export = _write_export_inputs(tmp_path, total_assets=10_000.0)
    fixed_now = datetime.datetime(2026, 9, 22)  # noqa: DTZ001
    monkeypatch.setattr(sparkline_export, "native_datetime_utc_now", lambda: fixed_now)
    first = export()
    assert len(uploaded) == 3
    original_prepare = sparkline_export.prepare_sparkline_data
    prepared = []

    def track_preparation(frame: pd.DataFrame) -> SparklineData | None:
        prepared.append(frame.index[-1])
        return original_prepare(frame)

    monkeypatch.setattr(sparkline_export, "prepare_sparkline_data", track_preparation)
    second = export()

    assert first.success and second.success
    assert second.counters["rendered"] == 0
    assert len(uploaded) == 3
    assert second.counters["skipped_preparation"] == second.counters["unchanged"] == 1
    assert prepared == []

    prices.iloc[4, prices.columns.get_loc("share_price")] = 1.6
    prices.to_parquet(prices_path)
    corrected = export()
    assert corrected.success
    assert corrected.counters["skipped_preparation"] == 0
    assert corrected.counters["rendered"] == 1
    assert len(uploaded) == 6
    assert len(prepared) == 1


def test_low_tvl_export_is_due_at_exactly_72_hours(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uploaded: list[dict[str, Any]]) -> None:
    """Changed low-TVL input is throttled before, but not at, 72 hours."""
    _, prices_path, _, prices, export = _write_export_inputs(tmp_path, total_assets=1_000.0)
    first_run_at = datetime.datetime(2026, 9, 22)  # noqa: DTZ001
    current_now = [first_run_at]
    monkeypatch.setattr(sparkline_export, "native_datetime_utc_now", lambda: current_now[0])
    first = export()
    assert len(uploaded) == 3
    prices.loc[prices.index[-1], "share_price"] = 2.0
    prices.to_parquet(prices_path)
    boundary = first_run_at + datetime.timedelta(hours=72)
    current_now[0] = boundary - datetime.timedelta(seconds=1)
    before_boundary = export()
    assert before_boundary.counters["rendered"] == 0
    assert len(uploaded) == 3
    current_now[0] = boundary
    at_boundary = export()

    assert first.success and before_boundary.success and at_boundary.success
    assert before_boundary.counters["low_tvl_throttled"] == 1
    assert at_boundary.counters["rendered"] == 1
    assert len(uploaded) == 6
