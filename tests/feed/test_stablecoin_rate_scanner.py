"""Tests for the post scanner stablecoin rate side job."""

import datetime
import json
from pathlib import Path

import pytest

from eth_defi.feed import scanner, stablecoin_rate
from eth_defi.feed.collector import CollectorRunSummary
from eth_defi.feed.scanner import PostScanConfig, run_post_scan_cycle
from eth_defi.feed.stablecoin_rate import StablecoinRateRefreshSummary


def _write_usdc_yaml(data_dir: Path) -> None:
    """Create a minimal USDC YAML file for scanner side-job tests."""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "usdc.yaml").write_text(
        """symbol: USDC
name: Circle USDC
short_description: USD Coin
long_description: ''
category: stablecoin
links:
  homepage: https://www.circle.com/usdc
  coingecko: https://www.coingecko.com/en/coins/usd-coin
  defillama: ''
  twitter: ''
slug: usdc
checks:
  twitter_last_post_at: ''
  domain_up_at: ''
  marked_dead_at: ''
  information_found_missing_at: ''
"""
    )


def test_stablecoin_rate_side_job_uses_24h_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scanner side job refreshes once, skips recent runs, then honours force."""
    calls = []

    def fake_refresh_stablecoin_rates(**kwargs) -> StablecoinRateRefreshSummary:
        calls.append(kwargs)
        return StablecoinRateRefreshSummary(files_scanned=1, entries_seen=1, rates_fetched=1)

    monkeypatch.setattr(scanner, "refresh_stablecoin_rates", fake_refresh_stablecoin_rates)

    gate_path = tmp_path / "stablecoin-rate-state.json"
    config = PostScanConfig(
        db_path=tmp_path / "posts.duckdb",
        mappings_dir=tmp_path / "feeds",
        stablecoin_data_dir=tmp_path / "stablecoins",
        stablecoin_rate_gate_path=gate_path,
        stablecoin_rate_timeout=7.5,
    )

    first_summary = CollectorRunSummary()
    scanner._run_stablecoin_rate_side_job(config, first_summary)

    assert first_summary.stablecoin_rate_status == "succeeded"
    assert first_summary.stablecoin_rate_summary.rates_fetched == 1
    assert calls[0]["data_dir"] == tmp_path / "stablecoins"
    assert calls[0]["force"] is False
    assert calls[0]["timeout"] == 7.5
    assert json.loads(gate_path.read_text())["last_succeeded_at"]

    second_summary = CollectorRunSummary()
    scanner._run_stablecoin_rate_side_job(config, second_summary)

    assert second_summary.stablecoin_rate_status == "skipped_recent"
    assert len(calls) == 1

    forced_summary = CollectorRunSummary()
    forced_config = PostScanConfig(
        db_path=tmp_path / "posts.duckdb",
        mappings_dir=tmp_path / "feeds",
        stablecoin_data_dir=tmp_path / "stablecoins",
        stablecoin_rate_gate_path=gate_path,
        force_stablecoin_rate_refresh=True,
    )
    scanner._run_stablecoin_rate_side_job(forced_config, forced_summary)

    assert forced_summary.stablecoin_rate_status == "succeeded"
    assert calls[-1]["force"] is True
    assert len(calls) == 2


def test_stablecoin_rate_side_job_can_be_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Disabled scanner config does not call the stablecoin refresh function."""
    called = False

    def fake_refresh_stablecoin_rates(**kwargs) -> StablecoinRateRefreshSummary:
        nonlocal called
        assert isinstance(kwargs, dict)
        called = True
        return StablecoinRateRefreshSummary()

    monkeypatch.setattr(scanner, "refresh_stablecoin_rates", fake_refresh_stablecoin_rates)

    summary = CollectorRunSummary()
    config = PostScanConfig(
        db_path=tmp_path / "posts.duckdb",
        mappings_dir=tmp_path / "feeds",
        stablecoin_data_dir=tmp_path / "stablecoins",
        stablecoin_rate_gate_path=tmp_path / "gate.json",
        refresh_stablecoin_rates=False,
    )

    scanner._run_stablecoin_rate_side_job(config, summary)

    assert summary.stablecoin_rate_status == "disabled"
    assert called is False


def test_post_scan_cycle_runs_stablecoin_rate_side_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The full post scan cycle invokes the stablecoin rate side job."""
    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()
    stablecoins_dir = tmp_path / "stablecoins"
    stablecoins_dir.mkdir()

    def fake_refresh_stablecoin_rates(**kwargs) -> StablecoinRateRefreshSummary:
        assert kwargs["data_dir"] == stablecoins_dir
        return StablecoinRateRefreshSummary(files_scanned=1, entries_seen=1, rates_fetched=1)

    monkeypatch.setattr(scanner, "refresh_stablecoin_rates", fake_refresh_stablecoin_rates)

    summary = run_post_scan_cycle(
        PostScanConfig(
            db_path=tmp_path / "posts.duckdb",
            mappings_dir=feeds_dir,
            stablecoin_data_dir=stablecoins_dir,
            stablecoin_rate_gate_path=tmp_path / "stablecoin-rate-state.json",
            request_delay_seconds=0,
            twitter_rss_base_urls=[],
        )
    )

    assert summary.stablecoin_rate_status == "succeeded"
    assert summary.stablecoin_rate_summary.rates_fetched == 1
    assert summary.sources_loaded == 0


def test_post_scan_cycle_continues_after_unexpected_stablecoin_rate_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unexpected stablecoin side-job errors do not abort the post scan."""
    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()
    stablecoins_dir = tmp_path / "stablecoins"
    stablecoins_dir.mkdir()

    def fake_refresh_stablecoin_rates(**kwargs) -> StablecoinRateRefreshSummary:
        assert kwargs["data_dir"] == stablecoins_dir
        raise TypeError("unexpected stablecoin YAML shape")

    monkeypatch.setattr(scanner, "refresh_stablecoin_rates", fake_refresh_stablecoin_rates)

    summary = run_post_scan_cycle(
        PostScanConfig(
            db_path=tmp_path / "posts.duckdb",
            mappings_dir=feeds_dir,
            stablecoin_data_dir=stablecoins_dir,
            stablecoin_rate_gate_path=tmp_path / "stablecoin-rate-state.json",
            request_delay_seconds=0,
            twitter_rss_base_urls=[],
        )
    )

    assert summary.stablecoin_rate_status == "failed"
    assert summary.stablecoin_rate_error == "unexpected stablecoin YAML shape"
    assert summary.sources_loaded == 0


def test_post_scan_cycle_continues_after_malformed_stablecoin_yaml(tmp_path: Path) -> None:
    """Malformed stablecoin YAML is reported as a side-job failure only."""
    feeds_dir = tmp_path / "feeds"
    feeds_dir.mkdir()
    stablecoins_dir = tmp_path / "stablecoins"
    stablecoins_dir.mkdir()
    (stablecoins_dir / "broken.yaml").write_text("symbol: [broken\n")

    summary = run_post_scan_cycle(
        PostScanConfig(
            db_path=tmp_path / "posts.duckdb",
            mappings_dir=feeds_dir,
            stablecoin_data_dir=stablecoins_dir,
            stablecoin_rate_gate_path=tmp_path / "stablecoin-rate-state.json",
            request_delay_seconds=0,
            twitter_rss_base_urls=[],
        )
    )

    assert summary.stablecoin_rate_status == "failed"
    assert "broken.yaml" in summary.stablecoin_rate_error
    assert summary.sources_loaded == 0


def test_stablecoin_rate_side_job_gate_write_failure_is_non_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate write failures are reported without escaping the side job."""
    called = False

    def fake_refresh_stablecoin_rates(**kwargs) -> StablecoinRateRefreshSummary:
        nonlocal called
        assert isinstance(kwargs, dict)
        called = True
        return StablecoinRateRefreshSummary(files_scanned=1)

    def fake_write_stablecoin_gate(path: Path, state: dict) -> None:
        assert isinstance(state, dict)
        raise PermissionError(f"cannot write {path}")

    monkeypatch.setattr(scanner, "refresh_stablecoin_rates", fake_refresh_stablecoin_rates)
    monkeypatch.setattr(scanner, "_write_stablecoin_gate", fake_write_stablecoin_gate)

    summary = CollectorRunSummary()
    scanner._run_stablecoin_rate_side_job(
        PostScanConfig(
            db_path=tmp_path / "posts.duckdb",
            mappings_dir=tmp_path / "feeds",
            stablecoin_data_dir=tmp_path / "stablecoins",
            stablecoin_rate_gate_path=tmp_path / "gate.json",
        ),
        summary,
    )

    assert summary.stablecoin_rate_status == "failed"
    assert "cannot write" in summary.stablecoin_rate_error
    assert called is True


def test_stablecoin_rate_side_job_all_failed_summary_keeps_success_gate_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A refresh with no fetched rates is recorded as failed for scanner gating."""

    def fake_refresh_stablecoin_rates(**kwargs) -> StablecoinRateRefreshSummary:
        assert isinstance(kwargs, dict)
        return StablecoinRateRefreshSummary(files_scanned=1, entries_seen=2, failed_count=2, rates_fetched=0)

    monkeypatch.setattr(scanner, "refresh_stablecoin_rates", fake_refresh_stablecoin_rates)

    gate_path = tmp_path / "gate.json"
    summary = CollectorRunSummary()
    scanner._run_stablecoin_rate_side_job(
        PostScanConfig(
            db_path=tmp_path / "posts.duckdb",
            mappings_dir=tmp_path / "feeds",
            stablecoin_data_dir=tmp_path / "stablecoins",
            stablecoin_rate_gate_path=gate_path,
        ),
        summary,
    )

    gate_state = json.loads(gate_path.read_text())
    assert summary.stablecoin_rate_status == "failed"
    assert "failed for all due entries" in summary.stablecoin_rate_error
    assert "last_failed_at" in gate_state
    assert "last_succeeded_at" not in gate_state


def test_stablecoin_rate_side_job_same_day_failed_attempts_do_not_become_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Entries skipped after same-day failures do not close the scanner success gate."""
    stablecoins_dir = tmp_path / "stablecoins"
    _write_usdc_yaml(stablecoins_dir)

    class MissingPriceResponse:
        """Small successful HTTP response with no CoinGecko price payload."""

        def raise_for_status(self) -> None:
            """Mock a successful HTTP status."""

        def json(self) -> dict[str, object]:
            """Return an empty CoinGecko payload."""
            return {}

    def fake_missing_price_get(url: str, params: dict[str, str], headers: dict[str, str], timeout: float) -> MissingPriceResponse:
        assert url == stablecoin_rate.COINGECKO_SIMPLE_PRICE_URL
        assert params["ids"] == "usd-coin"
        assert isinstance(headers, dict)
        assert timeout > 0
        return MissingPriceResponse()

    monkeypatch.setattr(stablecoin_rate.requests, "get", fake_missing_price_get)
    monkeypatch.setattr(scanner, "native_datetime_utc_now", lambda: datetime.datetime(2026, 6, 26, 12, 0, 0))

    gate_path = tmp_path / "gate.json"
    config = PostScanConfig(
        db_path=tmp_path / "posts.duckdb",
        mappings_dir=tmp_path / "feeds",
        stablecoin_data_dir=stablecoins_dir,
        stablecoin_rate_gate_path=gate_path,
    )

    first_summary = CollectorRunSummary()
    scanner._run_stablecoin_rate_side_job(config, first_summary)
    assert first_summary.stablecoin_rate_status == "failed"
    assert first_summary.stablecoin_rate_summary.failed_count == 1

    second_summary = CollectorRunSummary()
    scanner._run_stablecoin_rate_side_job(config, second_summary)
    gate_state = json.loads(gate_path.read_text())

    assert second_summary.stablecoin_rate_status == "failed"
    assert second_summary.stablecoin_rate_summary.due_count == 0
    assert second_summary.stablecoin_rate_summary.skipped_failed_today_count == 1
    assert "last_failed_at" in gate_state
    assert "last_succeeded_at" not in gate_state


def test_stablecoin_gate_atomic_write_preserves_existing_file_mode(tmp_path: Path) -> None:
    """Atomic gate writes keep the operator-selected file permissions."""
    gate_path = tmp_path / "gate.json"
    gate_path.write_text("{}")
    gate_path.chmod(0o644)

    scanner._write_stablecoin_gate(gate_path, {"last_succeeded_at": "2026-06-26T12:00:00"})

    assert gate_path.stat().st_mode & 0o777 == 0o644
    assert json.loads(gate_path.read_text())["last_succeeded_at"] == "2026-06-26T12:00:00"
