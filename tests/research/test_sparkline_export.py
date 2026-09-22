"""Tests for sparkline publication state, cadence and canonical inputs."""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.research import sparkline_export
from eth_defi.research.sparkline import prepare_sparkline_data
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


@dataclass
class DetectionStub:
    """Pickleable metadata detection stub for the exporter fixture."""

    spec: VaultSpec

    def get_spec(self) -> VaultSpec:
        """Return the synthetic vault identity."""
        return self.spec


def _sparkline_data() -> object:
    """Build a deterministic prepared chart for helper tests."""
    index = pd.date_range("2026-01-01", periods=15, freq="D", name="timestamp")
    return prepare_sparkline_data(pd.DataFrame({"share_price": [1.0 + i / 100 for i in range(15)]}, index=index))


def _write_export_inputs(tmp_path: Path, *, total_assets: float) -> tuple[str, Path, Path, Path, pd.DataFrame]:
    """Write one-vault metadata and price inputs for coordinator tests."""
    spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    vault_id = spec.as_string_id()
    vault_db_path = tmp_path / "vaults.pickle"
    VaultDatabase(rows={spec: {"Denomination": "USDC", "_detection_data": DetectionStub(spec)}}).write(vault_db_path)
    index = pd.date_range("2026-01-01", periods=15, freq="D", name="timestamp")
    prices = pd.DataFrame(
        {
            "id": [vault_id] * len(index),
            "share_price": [1.0 + i / 100 for i in range(len(index))],
            "total_assets": [total_assets] * len(index),
        },
        index=index,
    )
    prices_path = tmp_path / "prices.parquet"
    prices.to_parquet(prices_path)
    return vault_id, vault_db_path, prices_path, tmp_path / "state.json", prices


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


def test_corrupt_state_is_a_hard_error(tmp_path: Path) -> None:
    """Malformed state must not silently reset publication cadence."""
    path = tmp_path / "state.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="Could not read sparkline state"):
        sparkline_export.load_sparkline_state(path)

    path.write_text(json.dumps({"schema_version": 1, "renderer_version": 1, "generated_at": "2026-09-22T00:00:00Z", "vaults": {"x": {"input_sha256": "z"}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid sparkline input digest"):
        sparkline_export.load_sparkline_state(path)


@pytest.mark.parametrize(
    ("symbol", "value", "is_low"),
    [
        ("USDC", 0.0, True),
        ("USDC", 4_999.99, True),
        ("USDC", 5_000.0, False),
        ("WETH", 2.49, True),
        ("WETH", 2.5, False),
        ("WBTC", 0.099, True),
        ("WBTC", 0.1, False),
    ],
)
def test_latest_tvl_classification_boundaries(symbol: str, value: float, is_low: bool) -> None:  # noqa: FBT001
    """Zero and strict equality boundaries follow the native-unit policy."""
    classification = sparkline_export._classify_latest_tvl(symbol, value)
    assert classification is not None
    assert classification[2] is is_low


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -1.0])
def test_invalid_latest_tvl_is_not_classified(value: float | None) -> None:
    """Missing, non-finite and negative TVL values do not select a cadence."""
    assert sparkline_export._classify_latest_tvl("USDC", value) is None


def test_due_precedence_and_failure_backoff() -> None:
    """Future retry timestamps defer both families, while force bypasses all gates."""
    now = datetime.datetime(2026, 9, 22)  # noqa: DTZ001
    entry = {
        "last_completed_at": "2026-09-20T00:00:00Z",
        "next_retry_at": "2026-09-23T00:00:00Z",
        "consecutive_failures": 2,
    }
    assert not sparkline_export._is_due(entry, low_tvl=False, now=now, force=False)
    assert not sparkline_export._is_due(entry, low_tvl=True, now=now, force=False)
    assert sparkline_export._is_due(entry, low_tvl=True, now=now, force=True)
    assert sparkline_export._failure_backoff(now, 1) == "2026-09-22T06:00:00Z"
    assert sparkline_export._failure_backoff(now, 2) == "2026-09-22T12:00:00Z"
    assert sparkline_export._failure_backoff(now, 3) == "2026-09-23T00:00:00Z"
    assert sparkline_export._failure_backoff(now, 8) == "2026-09-23T00:00:00Z"


def test_latest_assets_are_limited_to_chart_end() -> None:
    """A later TVL-only row cannot change the chart's current classification."""
    index = pd.date_range("2026-01-01", periods=15, freq="D", name="timestamp")
    prices = pd.DataFrame(
        {
            "id": ["vault"] * 16,
            "share_price": [1.0] * 15 + [float("nan")],
            "total_assets": [1_000.0] * 15 + [9_000.0],
        },
        index=index.append(pd.DatetimeIndex([pd.Timestamp("2026-01-20")], name="timestamp")),
    )
    prepared = prepare_sparkline_data(prices.iloc[:15][["share_price", "total_assets"]])
    assert prepared is not None
    result = sparkline_export.latest_total_assets_by_id(prices, [("vault", prepared)])
    assert result == {"vault": 1_000.0}


def test_export_state_skips_unchanged_high_tvl_without_rendering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The local input digest avoids a second render for unchanged high TVL."""
    vault_id, vault_db_path, prices_path, state_path, _prices = _write_export_inputs(tmp_path, total_assets=10_000.0)
    fixed_now = datetime.datetime(2026, 9, 22)  # noqa: DTZ001
    monkeypatch.setattr(sparkline_export, "native_datetime_utc_now", lambda: fixed_now)
    renders: list[str] = []
    original = sparkline_export.render_vault_sparklines

    def spy(vault_id: str, data: object) -> list[dict[str, object]]:
        renders.append(vault_id)
        return original(vault_id, data)

    monkeypatch.setattr(sparkline_export, "render_vault_sparklines", spy)
    monkeypatch.setattr(sparkline_export, "_create_s3_client_from_environment", lambda _max_workers: (object(), "test-bucket"))
    monkeypatch.setattr(sparkline_export, "_publish_rendered_vault", lambda _client, _bucket, _images: (2, 0, None))
    first = sparkline_export.run_sparkline_export(
        vault_db_path=vault_db_path,
        prices_path=prices_path,
        state_path=state_path,
        max_workers=1,
        force=False,
    )
    second = sparkline_export.run_sparkline_export(
        vault_db_path=vault_db_path,
        prices_path=prices_path,
        state_path=state_path,
        max_workers=1,
        force=False,
    )

    assert first.success and second.success
    assert renders == [vault_id]
    assert second.counters["unchanged"] == 1


def test_invalidated_vault_honours_retry_backoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale publication target must not bypass backoff after a partial upload."""
    vault_id, vault_db_path, prices_path, state_path, _prices = _write_export_inputs(tmp_path, total_assets=10_000.0)
    fixed_now = datetime.datetime(2026, 9, 22)  # noqa: DTZ001
    state = sparkline_export.make_empty_sparkline_state(fixed_now)
    state["vaults"][vault_id] = {
        "input_sha256": "0" * sparkline_export.SHA256_HEX_LENGTH,
        "last_completed_at": "2026-09-21T00:00:00Z",
        "renderer_version": sparkline_export.SPARKLINE_RENDERER_VERSION,
        "publication_target": "old-bucket",
        "consecutive_failures": 0,
        "next_retry_at": None,
    }
    sparkline_export.save_sparkline_state(state, state_path, fixed_now)
    monkeypatch.setattr(sparkline_export, "native_datetime_utc_now", lambda: fixed_now)
    renders: list[str] = []
    original = sparkline_export.render_vault_sparklines

    def spy(vault_id: str, data: object) -> list[dict[str, object]]:
        renders.append(vault_id)
        return original(vault_id, data)

    monkeypatch.setattr(sparkline_export, "render_vault_sparklines", spy)
    monkeypatch.setattr(sparkline_export, "_create_s3_client_from_environment", lambda _max_workers: (object(), "test-bucket"))
    monkeypatch.setattr(sparkline_export, "_publish_rendered_vault", lambda _client, _bucket, _images: (1, 0, "PNG upload failed"))

    first = sparkline_export.run_sparkline_export(
        vault_db_path=vault_db_path,
        prices_path=prices_path,
        state_path=state_path,
        max_workers=1,
        force=False,
    )
    state_after_failure = sparkline_export.load_sparkline_state(state_path, fixed_now)
    failed_entry = state_after_failure["vaults"][vault_id]
    second = sparkline_export.run_sparkline_export(
        vault_db_path=vault_db_path,
        prices_path=prices_path,
        state_path=state_path,
        max_workers=1,
        force=False,
    )

    assert not first.success
    assert first.counters["uploaded"] == 1
    assert failed_entry["input_sha256"] == "0" * sparkline_export.SHA256_HEX_LENGTH
    assert failed_entry["last_completed_at"] == "2026-09-21T00:00:00Z"
    assert failed_entry["publication_target"] == "old-bucket"
    assert failed_entry["next_retry_at"] == "2026-09-22T06:00:00Z"
    assert second.success
    assert second.counters["retry_deferred"] == 1
    assert renders == [vault_id]


def test_low_tvl_target_change_bypasses_cadence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A target change republishes a low-TVL vault inside its 72-hour cadence."""
    vault_id, vault_db_path, prices_path, state_path, _prices = _write_export_inputs(tmp_path, total_assets=1_000.0)
    first_run_at = datetime.datetime(2026, 9, 22)  # noqa: DTZ001
    current_now = [first_run_at]
    monkeypatch.setattr(sparkline_export, "native_datetime_utc_now", lambda: current_now[0])
    renders: list[str] = []
    original = sparkline_export.render_vault_sparklines

    def spy(vault_id: str, data: object) -> list[dict[str, object]]:
        renders.append(vault_id)
        return original(vault_id, data)

    monkeypatch.setattr(sparkline_export, "render_vault_sparklines", spy)
    monkeypatch.setattr(sparkline_export, "_create_s3_client_from_environment", lambda _max_workers: (object(), "test-bucket"))
    monkeypatch.setattr(sparkline_export, "_publish_rendered_vault", lambda _client, _bucket, _images: (2, 0, None))

    first = sparkline_export.run_sparkline_export(
        vault_db_path=vault_db_path,
        prices_path=prices_path,
        state_path=state_path,
        max_workers=1,
        force=False,
    )
    state = sparkline_export.load_sparkline_state(state_path, first_run_at)
    state["vaults"][vault_id]["publication_target"] = "old-bucket"
    sparkline_export.save_sparkline_state(state, state_path, first_run_at)
    current_now[0] = first_run_at + datetime.timedelta(hours=1)
    after_target_change = sparkline_export.run_sparkline_export(
        vault_db_path=vault_db_path,
        prices_path=prices_path,
        state_path=state_path,
        max_workers=1,
        force=False,
    )

    assert first.success and after_target_change.success
    assert after_target_change.counters["low_tvl_throttled"] == 0
    assert after_target_change.counters["rendered"] == 1
    assert renders == [vault_id, vault_id]


def test_low_tvl_export_is_due_at_exactly_72_hours(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Changed low-TVL input is throttled before, but not at, 72 hours."""
    vault_id, vault_db_path, prices_path, state_path, prices = _write_export_inputs(tmp_path, total_assets=1_000.0)
    first_run_at = datetime.datetime(2026, 9, 22)  # noqa: DTZ001
    current_now = [first_run_at]
    monkeypatch.setattr(sparkline_export, "native_datetime_utc_now", lambda: current_now[0])
    renders: list[str] = []
    original = sparkline_export.render_vault_sparklines

    def spy(vault_id: str, data: object) -> list[dict[str, object]]:
        renders.append(vault_id)
        return original(vault_id, data)

    monkeypatch.setattr(sparkline_export, "render_vault_sparklines", spy)
    monkeypatch.setattr(sparkline_export, "_create_s3_client_from_environment", lambda _max_workers: (object(), "test-bucket"))
    monkeypatch.setattr(sparkline_export, "_publish_rendered_vault", lambda _client, _bucket, _images: (2, 0, None))

    first = sparkline_export.run_sparkline_export(
        vault_db_path=vault_db_path,
        prices_path=prices_path,
        state_path=state_path,
        max_workers=1,
        force=False,
    )
    prices.loc[prices.index[-1], "share_price"] = 2.0
    prices.to_parquet(prices_path)
    current_now[0] = first_run_at + sparkline_export.SPARKLINE_LOW_TVL_INTERVAL - datetime.timedelta(seconds=1)
    before_boundary = sparkline_export.run_sparkline_export(
        vault_db_path=vault_db_path,
        prices_path=prices_path,
        state_path=state_path,
        max_workers=1,
        force=False,
    )
    current_now[0] = first_run_at + sparkline_export.SPARKLINE_LOW_TVL_INTERVAL
    at_boundary = sparkline_export.run_sparkline_export(
        vault_db_path=vault_db_path,
        prices_path=prices_path,
        state_path=state_path,
        max_workers=1,
        force=False,
    )

    assert first.success and before_boundary.success and at_boundary.success
    assert before_boundary.counters["low_tvl_throttled"] == 1
    assert at_boundary.counters["rendered"] == 1
    assert renders == [vault_id, vault_id]
