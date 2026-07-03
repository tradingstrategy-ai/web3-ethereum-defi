"""Tests for protocol and stablecoin metadata export helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from eth_defi.feed.stablecoin_rate import StablecoinRateRefreshSummary

STABLECOIN_RATE_TIMEOUT = 7.5
DEFAULT_STABLECOIN_RATE_TIMEOUT = 20.0


def _load_export_protocol_metadata_module():
    """Load the metadata export script as a Python module."""
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "export-protocol-metadata.py"
    spec = importlib.util.spec_from_file_location("export_protocol_metadata", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stablecoin_rate_refresh_runs_before_metadata_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Metadata exporter refreshes stablecoin rates with configured CoinGecko options."""
    module = _load_export_protocol_metadata_module()
    calls: list[dict] = []

    def fake_refresh_stablecoin_rates(**kwargs: object) -> StablecoinRateRefreshSummary:
        calls.append(kwargs)
        return StablecoinRateRefreshSummary(files_scanned=1, entries_seen=1, due_count=1, rates_fetched=1)

    monkeypatch.setattr(module, "refresh_stablecoin_rates", fake_refresh_stablecoin_rates)
    monkeypatch.setenv("REFRESH_STABLECOIN_RATES", "true")
    monkeypatch.setenv("FORCE_STABLECOIN_RATE_REFRESH", "true")
    monkeypatch.setenv("STABLECOIN_RATE_TIMEOUT", str(STABLECOIN_RATE_TIMEOUT))
    monkeypatch.chdir(tmp_path)

    summary = module.refresh_stablecoin_rates_for_metadata_export()

    assert summary is not None
    assert summary.rates_fetched == 1
    assert len(calls) == 1
    assert calls[0]["data_dir"] == module.STABLECOINS_DATA_DIR
    assert calls[0]["force"] is True
    assert calls[0]["timeout"] == STABLECOIN_RATE_TIMEOUT
    assert calls[0]["progress_bar"] is True


def test_stablecoin_rate_refresh_timeout_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed STABLECOIN_RATE_TIMEOUT does not abort metadata export."""
    module = _load_export_protocol_metadata_module()
    calls: list[dict] = []

    def fake_refresh_stablecoin_rates(**kwargs: object) -> StablecoinRateRefreshSummary:
        calls.append(kwargs)
        return StablecoinRateRefreshSummary(files_scanned=1)

    monkeypatch.setattr(module, "refresh_stablecoin_rates", fake_refresh_stablecoin_rates)
    monkeypatch.setenv("REFRESH_STABLECOIN_RATES", "true")
    monkeypatch.setenv("STABLECOIN_RATE_TIMEOUT", "not-a-number")

    summary = module.refresh_stablecoin_rates_for_metadata_export()

    assert summary is not None
    assert calls[0]["timeout"] == DEFAULT_STABLECOIN_RATE_TIMEOUT


def test_stablecoin_rate_refresh_failure_does_not_abort_metadata_export(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stablecoin refresh failures are logged and existing metadata can still upload."""
    module = _load_export_protocol_metadata_module()

    def fake_refresh_stablecoin_rates(**kwargs: object) -> StablecoinRateRefreshSummary:
        assert isinstance(kwargs, dict)
        message = "cannot write stablecoin metadata"
        raise OSError(message)

    monkeypatch.setattr(module, "refresh_stablecoin_rates", fake_refresh_stablecoin_rates)
    monkeypatch.setenv("REFRESH_STABLECOIN_RATES", "true")

    summary = module.refresh_stablecoin_rates_for_metadata_export()

    assert summary is None


def test_stablecoin_rate_refresh_can_be_disabled_for_metadata_export(monkeypatch: pytest.MonkeyPatch) -> None:
    """Metadata exporter honours REFRESH_STABLECOIN_RATES=false."""
    module = _load_export_protocol_metadata_module()
    called = False

    def fake_refresh_stablecoin_rates(**kwargs: object) -> StablecoinRateRefreshSummary:
        nonlocal called
        assert isinstance(kwargs, dict)
        called = True
        return StablecoinRateRefreshSummary()

    monkeypatch.setattr(module, "refresh_stablecoin_rates", fake_refresh_stablecoin_rates)
    monkeypatch.setenv("REFRESH_STABLECOIN_RATES", "false")

    summary = module.refresh_stablecoin_rates_for_metadata_export()

    assert summary is None
    assert called is False
