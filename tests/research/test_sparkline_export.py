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
    spec = VaultSpec(1, "0x0000000000000000000000000000000000000001")
    row = {"Denomination": "USDC", "_detection_data": DetectionStub(spec)}
    vault_db_path = tmp_path / "vaults.pickle"
    VaultDatabase(rows={spec: row}).write(vault_db_path)
    index = pd.date_range("2026-01-01", periods=15, freq="D", name="timestamp")
    prices_path = tmp_path / "prices.parquet"
    pd.DataFrame(
        {
            "id": [spec.as_string_id()] * len(index),
            "share_price": [1.0 + i / 100 for i in range(len(index))],
            "total_assets": [10_000.0] * len(index),
        },
        index=index,
    ).to_parquet(prices_path)
    state_path = tmp_path / "state.json"
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
    )
    second = sparkline_export.run_sparkline_export(
        vault_db_path=vault_db_path,
        prices_path=prices_path,
        state_path=state_path,
        max_workers=1,
    )

    assert first.success and second.success
    assert renders == [spec.as_string_id()]
    assert second.counters["unchanged"] == 1
