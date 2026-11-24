"""Tests for sparkline publication state, cadence and canonical inputs."""

from __future__ import annotations

import datetime
import gzip
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from eth_defi.cloudflare_r2 import calculate_bytes_digest
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


class _SparklineR2:
    """Record object requests while exercising the real R2 upload helper."""

    def __init__(self, client_error: type[Exception]) -> None:
        self.client_error = client_error
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []

    def head_object(self, **request: str) -> dict[str, Any]:
        """Return stored metadata or the S3 response for a missing object."""
        key = request["Key"]
        self.calls.append(("HEAD", key))
        address = (request["Bucket"], key)
        if address not in self.objects:
            raise self.client_error({"Error": {"Code": "404"}}, "HeadObject")
        return self.objects[address]

    def put_object(self, **request: Any) -> dict[str, Any]:
        """Save the uploaded bytes and metadata for subsequent remote checks."""
        key = request["Key"]
        self.calls.append(("PUT", key))
        self.objects[request["Bucket"], key] = {"ContentLength": len(request["Body"]), **request}
        return {}


@pytest.fixture
def sparkline_r2(monkeypatch: pytest.MonkeyPatch) -> _SparklineR2:
    """Exercise the R2 helper with an isolated in-memory client."""
    botocore = pytest.importorskip("botocore.exceptions")
    r2 = _SparklineR2(botocore.ClientError)
    monkeypatch.setattr(sparkline_export, "_create_s3_client_from_environment", lambda _workers: (r2, "test-bucket"))
    monkeypatch.delenv("R2_SPARKLINE_ENDPOINT_URL", raising=False)
    return r2


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
    # Serial uploads keep the request order reproducible in publication tests.
    export = partial(sparkline_export.run_sparkline_export, vault_db_path=vault_db_path, prices_path=prices_path, state_path=state_path, render_workers=1, upload_workers=1, force=False)
    return vault_ids[0], prices_path, state_path, prices, export


def test_sparkline_publication_reuses_unchanged_images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sparkline_r2: _SparklineR2) -> None:
    """Publish first-day charts and upload only changed image bytes on later runs.

    :param tmp_path:
        Export input and checkpoint location.
    :param monkeypatch:
        Isolated clock and batch checkpoint observation.
    :param sparkline_r2:
        In-memory R2 object state and recorded requests.
    """
    vault_count = 2
    first_vault, prices_path, state_path, prices, export = _write_export_inputs(tmp_path, total_assets=10_000.0, vault_count=vault_count)
    prices = prices.iloc[: 2 * vault_count].copy()
    prices.index = pd.DatetimeIndex(["2026-09-22 12:10:57", "2026-09-22 18:37:08"], name="timestamp").repeat(vault_count)
    prices.to_parquet(prices_path)
    second_vault = prices["id"].iloc[1]
    now = datetime.datetime(2026, 9, 22, 19)  # noqa: DTZ001
    monkeypatch.setattr(sparkline_export, "native_datetime_utc_now", lambda: now)
    render = sparkline_export.render_vault_sparklines

    def check_previous_checkpoint(vault_id: str, data: SparklineData) -> list[sparkline_export.RenderData]:
        if vault_id == second_vault:
            checkpoint = sparkline_export.load_sparkline_state(state_path, now)
            assert tuple(checkpoint["vaults"]) == (first_vault,)
        return render(vault_id, data)

    monkeypatch.setattr(sparkline_export, "render_vault_sparklines", check_previous_checkpoint)
    result = export(batch_size=1)
    monkeypatch.setattr(sparkline_export, "render_vault_sparklines", render)

    assert result.success
    assert result.counters["batches"] == result.counters["rendered"] == vault_count
    assert result.counters["uploaded"] == vault_count * sparkline_export.SPARKLINE_FORMAT_COUNT
    before = sparkline_export.load_sparkline_state(state_path, now)["vaults"]
    objects_by_vault = {vault: (f"sparkline-90d-{vault}.svg", f"sparkline-90d-{vault}.png", f"sparkline-table-90d-{vault}.png") for vault in (first_vault, second_vault)}
    assert sparkline_r2.calls == [(method, key) for keys in objects_by_vault.values() for key in keys for method in ("HEAD", "PUT")]
    for vault, keys in objects_by_vault.items():
        assert before[vault]["last_completed_at"] == "2026-09-22T19:00:00Z"
        payloads = [gzip.decompress(sparkline_r2.objects["test-bucket", key]["Body"]) for key in keys]
        assert payloads[0].startswith(b"<?xml")
        assert all(payload.startswith(b"\x89PNG") for payload in payloads[1:])
        assert before[vault]["published_asset_digests"] == {key: calculate_bytes_digest(payload).sha256 for key, payload in zip(keys, payloads, strict=True)}

    sparkline_r2.calls.clear()
    result = export(batch_size=1)

    assert result.success
    assert result.counters["skipped_preparation"] == result.counters["unchanged"] == vault_count
    assert result.counters["rendered"] == 0
    assert sparkline_r2.calls == []

    # Correct the first observation without changing the latest price.
    # Scaling the second chart's prices preserves its rendered geometry.
    prices.loc[(prices["id"] == first_vault) & (prices.index == prices.index.min()), "share_price"] = 2.0
    prices.loc[prices["id"] == second_vault, "share_price"] *= 2
    prices.to_parquet(prices_path)
    result = export(batch_size=vault_count, render_workers=2, render_backend="processes")

    assert result.success
    assert result.counters["rendered"] == vault_count
    assert result.counters["uploaded"] == result.counters["r2_unchanged"] == sparkline_export.SPARKLINE_FORMAT_COUNT
    assert sparkline_r2.calls == [*(("PUT", key) for key in objects_by_vault[first_vault]), *(("HEAD", key) for key in objects_by_vault[second_vault])]
    after = sparkline_export.load_sparkline_state(state_path, now)["vaults"]
    assert after[first_vault]["published_asset_digests"] != before[first_vault]["published_asset_digests"]
    assert after[second_vault]["published_asset_digests"] == before[second_vault]["published_asset_digests"]


def test_low_tvl_export_is_due_at_exactly_72_hours(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uploaded: list[dict[str, Any]]) -> None:
    """Publish changed low-TVL input once it reaches the 72-hour boundary.

    :param tmp_path:
        Export input and checkpoint location.
    :param monkeypatch:
        Isolated clock.
    :param uploaded:
        Captured uploads.
    """
    _, prices_path, _, prices, export = _write_export_inputs(tmp_path, total_assets=1_000.0)
    first_run_at = datetime.datetime(2026, 9, 22)  # noqa: DTZ001
    current_now = [first_run_at]
    monkeypatch.setattr(sparkline_export, "native_datetime_utc_now", lambda: current_now[0])
    first = export()
    assert len(uploaded) == sparkline_export.SPARKLINE_FORMAT_COUNT
    prices.loc[prices.index[-1], "share_price"] = 2.0
    prices.to_parquet(prices_path)
    boundary = first_run_at + datetime.timedelta(hours=72)
    current_now[0] = boundary - datetime.timedelta(seconds=1)
    before_boundary = export()
    assert before_boundary.counters["rendered"] == 0
    assert len(uploaded) == sparkline_export.SPARKLINE_FORMAT_COUNT
    current_now[0] = boundary
    at_boundary = export()

    assert first.success and before_boundary.success and at_boundary.success
    assert before_boundary.counters["low_tvl_throttled"] == 1
    assert at_boundary.counters["rendered"] == 1
    assert len(uploaded) == 2 * sparkline_export.SPARKLINE_FORMAT_COUNT


def test_export_uses_default_upload_workers_and_environment_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uploaded: list[dict[str, Any]]) -> None:
    """Use the default worker count and honour the explicit override.

    :param tmp_path:
        Export input and checkpoint location.
    :param monkeypatch:
        Isolated environment and R2 client.
    :param uploaded:
        Captured uploads.
    """
    _, _, _, _, export = _write_export_inputs(tmp_path, total_assets=10_000.0)
    monkeypatch.delenv("SPARKLINE_UPLOAD_WORKERS", raising=False)
    monkeypatch.delenv("SPARKLINE_MAX_WORKERS", raising=False)
    worker_counts: list[int] = []

    def create_client(workers: int) -> tuple[object, str]:
        worker_counts.append(workers)
        return object(), "test-bucket"

    monkeypatch.setattr(sparkline_export, "_create_s3_client_from_environment", create_client)
    assert export(upload_workers=None).success
    monkeypatch.setenv("SPARKLINE_MAX_WORKERS", "7")
    monkeypatch.setenv("SPARKLINE_UPLOAD_WORKERS", "12")
    assert export(upload_workers=None).success

    assert worker_counts == [16, 12]
    assert len(uploaded) == sparkline_export.SPARKLINE_FORMAT_COUNT
