"""Tests for sparkline publication state, cadence and canonical inputs."""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from eth_defi.research import sparkline_export
from eth_defi.research.sparkline import SparklineData, prepare_sparkline_data
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.vaultdb import VaultDatabase


@dataclass
class DetectionStub:
    """Pickleable metadata detection stub for the exporter fixture."""

    spec: VaultSpec

    def get_spec(self) -> VaultSpec:
        """Return the synthetic vault identity."""
        return self.spec


def _sparkline_data(share_prices: list[float] | None = None) -> SparklineData:
    """Build a deterministic prepared chart for helper tests.

    :param share_prices:
        Optional daily source prices; omitted values use a rising series.
    :return:
        Prepared chart data for a series with sufficient history.
    """
    share_prices = share_prices or [1.0 + i / 100 for i in range(15)]
    index = pd.date_range("2026-01-01", periods=len(share_prices), freq="D", name="timestamp")
    sparkline_data = prepare_sparkline_data(pd.DataFrame({"share_price": share_prices}, index=index))
    assert sparkline_data is not None
    return sparkline_data


def _write_export_inputs(tmp_path: Path, *, total_assets: float, vault_count: int = 1) -> tuple[str, Path, Path, Path, pd.DataFrame]:
    """Write synthetic vault metadata and prices for coordinator tests.

    :param tmp_path:
        Temporary directory for the metadata, price and state files.
    :param total_assets:
        Repeated latest TVL value for each synthetic vault.
    :param vault_count:
        Number of distinct vaults to include in the price input.
    :return:
        First vault ID and paths to the metadata, prices and state, plus the
        price DataFrame.
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
    return vault_ids[0], vault_db_path, prices_path, tmp_path / "state.json", prices


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


@pytest.mark.parametrize(
    "share_prices",
    [
        pytest.param([1.0] * 15, id="constant"),
        pytest.param([1.0 + i / 100 for i in range(15)], id="rising"),
        pytest.param([1.15 - i / 100 for i in range(15)], id="falling"),
        pytest.param([1.0, float("nan"), float("nan"), 1.03, float("nan"), float("nan"), 1.06, float("nan"), float("nan"), 1.09, float("nan"), float("nan"), 1.12, float("nan"), 1.14], id="sparse"),
    ],
)
def test_render_processes_match_thread_output(share_prices: list[float]) -> None:
    """The process backend produces the same bytes as the threaded renderer.

    This confirms that crossing a Joblib process boundary does not alter the
    generated SVG or PNG payloads.

    :param share_prices:
        Daily price pattern used to prepare the test chart.
    :return:
        None.
    """
    sparkline_data = _sparkline_data(share_prices)
    vault_data = [("1-0x0000000000000000000000000000000000000001", sparkline_data)]

    threaded = sparkline_export.render_sparklines(vault_data, max_workers=1, backend="threads")
    processed = sparkline_export.render_sparklines(vault_data, max_workers=2, backend="processes")

    assert [(image["extension"], image["payload"]) for image in processed] == [(image["extension"], image["payload"]) for image in threaded]


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -1.0])
def test_invalid_latest_tvl_is_not_classified(value: float | None) -> None:
    """Missing, non-finite and negative TVL values do not select a cadence."""
    assert sparkline_export._classify_latest_tvl("USDC", value) is None


def test_worker_counts_have_separate_defaults_and_legacy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The legacy setting remains available while worker defaults split.

    :param monkeypatch:
        Isolated environment-variable overrides for this test.
    :return:
        None.
    """
    for variable in ("SPARKLINE_MAX_WORKERS", "SPARKLINE_RENDER_WORKERS", "SPARKLINE_UPLOAD_WORKERS"):
        monkeypatch.delenv(variable, raising=False)
    assert sparkline_export._resolve_worker_counts(max_workers=None, render_workers=None, upload_workers=None) == (6, 8)

    monkeypatch.setenv("SPARKLINE_MAX_WORKERS", "12")
    assert sparkline_export._resolve_worker_counts(max_workers=None, render_workers=None, upload_workers=None) == (6, 12)

    monkeypatch.setenv("SPARKLINE_RENDER_WORKERS", "2")
    monkeypatch.setenv("SPARKLINE_UPLOAD_WORKERS", "7")
    assert sparkline_export._resolve_worker_counts(max_workers=None, render_workers=None, upload_workers=None) == (2, 7)
    assert sparkline_export._resolve_worker_counts(max_workers=3, render_workers=None, upload_workers=None) == (3, 3)


def test_sparkline_r2_connection_pool_uses_upload_worker_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """The R2 connection pool follows upload concurrency, not render workers.

    :param monkeypatch:
        Isolated environment and client-factory overrides for this test.
    :return:
        None.
    """
    monkeypatch.setenv("R2_SPARKLINE_BUCKET_NAME", "sparkline-test")
    monkeypatch.setenv("R2_SPARKLINE_ENDPOINT_URL", "https://r2.example.invalid")
    monkeypatch.setenv("R2_SPARKLINE_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("R2_SPARKLINE_SECRET_ACCESS_KEY", "test-secret")
    client_options: dict[str, object] = {}
    expected_upload_workers = 7

    def create_client(**kwargs: object) -> object:
        client_options.update(kwargs)
        return object()

    monkeypatch.setattr(sparkline_export, "create_r2_client", create_client)
    client, bucket_name = sparkline_export._create_s3_client_from_environment(upload_workers=expected_upload_workers)

    assert client is not None
    assert bucket_name == "sparkline-test"
    assert client_options["max_pool_connections"] == expected_upload_workers


def test_dry_run_renders_and_saves_state_without_r2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dry run renders and updates scratch state without creating an R2 client.

    :param tmp_path:
        Temporary inputs and export-state location.
    :param monkeypatch:
        Isolated clock and R2 factory overrides for this test.
    :return:
        None.
    """
    with pytest.raises(ValueError, match="explicit scratch state_path"):
        sparkline_export.run_sparkline_export(dry_run=True)

    vault_id, vault_db_path, prices_path, state_path, _prices = _write_export_inputs(tmp_path, total_assets=10_000.0)
    fixed_now = datetime.datetime(2026, 9, 22)  # noqa: DTZ001
    monkeypatch.setattr(sparkline_export, "native_datetime_utc_now", lambda: fixed_now)

    def fail_if_r2_created(_upload_workers: int) -> tuple[object, str]:
        message = "dry run must not create an R2 client"
        raise AssertionError(message)

    monkeypatch.setattr(sparkline_export, "_create_s3_client_from_environment", fail_if_r2_created)
    result = sparkline_export.run_sparkline_export(
        vault_db_path=vault_db_path,
        prices_path=prices_path,
        state_path=state_path,
        render_workers=2,
        upload_workers=5,
        force=True,
        dry_run=True,
    )

    state = sparkline_export.load_sparkline_state(state_path, fixed_now)
    assert result.success
    assert result.counters["rendered"] == 1
    assert result.counters["uploaded"] == 0
    assert state["vaults"][vault_id]["publication_target"] == "dry-run"
    assert state["vaults"][vault_id]["last_completed_at"] == "2026-09-22T00:00:00Z"

    with pytest.raises(ValueError, match="new scratch file"):
        sparkline_export.run_sparkline_export(vault_db_path=vault_db_path, prices_path=prices_path, state_path=state_path, force=True, dry_run=True)


def test_render_failure_does_not_block_other_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A render error is isolated to its vault while its batch continues.

    :param tmp_path:
        Temporary metadata, price and export-state location.
    :param monkeypatch:
        Isolated clock and renderer overrides for this test.
    :return:
        None.
    """
    vault_id, vault_db_path, prices_path, state_path, prices = _write_export_inputs(tmp_path, total_assets=10_000.0, vault_count=2)
    failed_vault_id = prices["id"].unique()[1]
    fixed_now = datetime.datetime(2026, 9, 22)  # noqa: DTZ001
    original_render = sparkline_export.render_vault_sparklines
    render_failure = "synthetic render failure"
    monkeypatch.setattr(sparkline_export, "native_datetime_utc_now", lambda: fixed_now)

    def render_with_one_failure(current_vault_id: str, sparkline_data: SparklineData) -> list[sparkline_export.RenderData]:
        """Raise for one selected vault and render the other normally.

        :param current_vault_id:
            Vault currently being rendered.
        :param sparkline_data:
            Prepared chart for the current vault.
        :return:
            Both generated image formats for a non-failing vault.
        """
        if current_vault_id == failed_vault_id:
            raise ValueError(render_failure)
        return original_render(current_vault_id, sparkline_data)

    monkeypatch.setattr(sparkline_export, "render_vault_sparklines", render_with_one_failure)
    result = sparkline_export.run_sparkline_export(
        vault_db_path=vault_db_path,
        prices_path=prices_path,
        state_path=state_path,
        render_workers=2,
        render_backend="threads",
        force=True,
        dry_run=True,
    )

    state = sparkline_export.load_sparkline_state(state_path, fixed_now)
    assert not result.success
    assert result.counters["rendered"] == 1
    assert result.counters["failed"] == 1
    assert state["vaults"][vault_id]["last_completed_at"] == "2026-09-22T00:00:00Z"
    assert state["vaults"][failed_vault_id]["consecutive_failures"] == 1


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
    _vault_id, vault_db_path, prices_path, state_path, prices = _write_export_inputs(tmp_path, total_assets=10_000.0, vault_count=expected_vault_count)
    fixed_now = datetime.datetime(2026, 9, 22)  # noqa: DTZ001
    monkeypatch.setattr(sparkline_export, "native_datetime_utc_now", lambda: fixed_now)

    result = sparkline_export.run_sparkline_export(
        vault_db_path=vault_db_path,
        prices_path=prices_path,
        state_path=state_path,
        render_workers=1,
        batch_size=1,
        force=True,
        dry_run=True,
    )

    state = sparkline_export.load_sparkline_state(state_path, fixed_now)
    assert result.success
    assert result.counters["batches"] == expected_vault_count
    assert result.counters["rendered"] == expected_vault_count
    assert all(state["vaults"][vault_id]["last_completed_at"] == "2026-09-22T00:00:00Z" for vault_id in prices["id"].unique())


def test_process_worker_honours_existing_low_tvl_cadence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real child process reads the persisted state and skips a recent vault.

    :param tmp_path:
        Temporary metadata, prices and state path.
    :param monkeypatch:
        Isolated clock and R2 client factory.
    :return:
        None.
    """
    vault_id, vault_db_path, prices_path, state_path, _prices = _write_export_inputs(tmp_path, total_assets=1_000.0)
    fixed_now = datetime.datetime(2026, 9, 22)  # noqa: DTZ001
    monkeypatch.setenv("R2_SPARKLINE_ENDPOINT_URL", "https://r2.example.invalid")
    state = sparkline_export.make_empty_sparkline_state(fixed_now)
    state["vaults"][vault_id] = {
        "last_completed_at": "2026-09-22T00:00:00Z",
        "renderer_version": sparkline_export.SPARKLINE_RENDERER_VERSION,
        "publication_target": sparkline_export._publication_target("test-bucket"),
        "consecutive_failures": 0,
        "next_retry_at": None,
    }
    sparkline_export.save_sparkline_state(state, state_path, fixed_now)
    monkeypatch.setattr(sparkline_export, "native_datetime_utc_now", lambda: fixed_now)
    monkeypatch.setattr(sparkline_export, "_create_s3_client_from_environment", lambda _upload_workers: (object(), "test-bucket"))

    result = sparkline_export.run_sparkline_export(
        vault_db_path=vault_db_path,
        prices_path=prices_path,
        state_path=state_path,
        render_workers=2,
        render_backend="processes",
    )

    assert result.success
    assert result.counters["low_tvl_throttled"] == 1
    assert result.counters["rendered"] == 0
    assert sparkline_export.load_sparkline_state(state_path, fixed_now)["vaults"][vault_id]["last_completed_at"] == "2026-09-22T00:00:00Z"


def test_insufficient_history_is_not_counted_as_eligible(tmp_path: Path) -> None:
    """A source-only batch with no qualifying vault still saves empty state.

    :param tmp_path:
        Temporary input and scratch state paths.
    :return:
        None.
    """
    _vault_id, vault_db_path, prices_path, state_path, prices = _write_export_inputs(tmp_path, total_assets=10_000.0)
    prices.iloc[:5].to_parquet(prices_path)

    result = sparkline_export.run_sparkline_export(vault_db_path=vault_db_path, prices_path=prices_path, state_path=state_path, force=True, dry_run=True)

    assert result.success
    assert result.counters["eligible"] == 0
    assert result.counters["insufficient_history"] == 1
    assert result.counters["batches"] == 0
    assert json.loads(state_path.read_text(encoding="utf-8"))["vaults"] == {}


def test_invalid_tvl_is_counted_without_rendering(tmp_path: Path) -> None:
    """A finite but negative latest TVL cannot select a publication cadence.

    :param tmp_path:
        Temporary input and scratch state paths.
    :return:
        None.
    """
    _vault_id, vault_db_path, prices_path, state_path, _prices = _write_export_inputs(tmp_path, total_assets=-1.0)

    result = sparkline_export.run_sparkline_export(vault_db_path=vault_db_path, prices_path=prices_path, state_path=state_path, force=True, dry_run=True)

    assert result.success
    assert result.counters["eligible"] == 1
    assert result.counters["invalid_tvl"] == 1
    assert result.counters["rendered"] == 0


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


def test_process_worker_matches_previous_preparation(tmp_path: Path) -> None:
    """The bounded worker path preserves prepared inputs and image bytes.

    The extra TVL-only row checks that classification stops at the final chart
    day even when a later source row has a different TVL.

    :param tmp_path:
        Temporary metadata and price files for two interleaved vaults.
    :return:
        None.
    """
    expected_latest_assets = 1_000.0
    first_vault_id, vault_db_path, prices_path, _state_path, prices = _write_export_inputs(tmp_path, total_assets=expected_latest_assets, vault_count=2)
    pd.concat(
        [
            prices,
            pd.DataFrame(
                {"id": [first_vault_id], "share_price": [float("nan")], "total_assets": [9_000.0]},
                index=pd.DatetimeIndex([pd.Timestamp("2026-01-20")], name="timestamp"),
            ),
        ]
    ).to_parquet(prices_path)
    loaded_prices = sparkline_export.load_sparkline_price_data(prices_path)
    vault_db = VaultDatabase.read(vault_db_path)
    included_ids = sparkline_export.get_included_vault_ids(vault_db, loaded_prices)
    prepared, skipped = sparkline_export.prepare_vault_sparklines(loaded_prices, included_ids)
    previous_data = dict(prepared)
    previous_assets = sparkline_export.latest_total_assets_by_id(loaded_prices, prepared)

    results = [
        sparkline_export._process_vault_for_export(
            vault_id,
            vault_prices,
            symbol,
            None,
            renderer_state_is_current=False,
            publication_target="dry-run",
            now=datetime.datetime(2026, 1, 21),  # noqa: DTZ001
            force=True,
        )
        for vault_id, vault_prices, symbol in sparkline_export._iter_supported_vault_groups(loaded_prices, sparkline_export._vault_symbol_map(vault_db))
    ]

    assert skipped == 0
    assert {result.vault_id for result in results} == included_ids
    for result in results:
        expected_images = sparkline_export.render_vault_sparklines(result.vault_id, previous_data[result.vault_id])
        assert result.status == "rendered"
        assert result.latest_total_assets == previous_assets[result.vault_id]
        assert result.digest == sparkline_export.calculate_sparkline_input_digest(previous_data[result.vault_id])
        assert result.images == expected_images
    assert next(result for result in results if result.vault_id == first_vault_id).latest_total_assets == expected_latest_assets


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
