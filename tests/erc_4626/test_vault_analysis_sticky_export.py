"""Tests for sticky vault export state in vault-analysis-json.py."""

from __future__ import annotations

import datetime
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest


def load_vault_analysis_json_module():
    """Load the hyphenated vault-analysis-json.py script as a test module."""
    module_name = "vault_analysis_json_sticky_test"
    module_path = Path(__file__).parents[2] / "scripts" / "erc-4626" / "vault-analysis-json.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def make_metrics_row(
    *,
    chain_id: int = 1,
    address: str = "0xAbCd000000000000000000000000000000000001",
    name: str = "Sticky USDC",
    peak_nav: float = 10_000.0,
    last_updated_at: object | None = None,
    risk: object | None = None,
    protocol_slug: str | None = "morpho",
    curator_slug: str | None = "gauntlet",
) -> dict:
    """Build a minimal lifetime metrics row for sticky export tests."""
    if last_updated_at is None:
        last_updated_at = datetime.datetime(2026, 6, 24, 12, 0, 0)
    return {
        "id": f"{chain_id}-{address}",
        "chain_id": chain_id,
        "address": address,
        "name": name,
        "chain": "Ethereum",
        "protocol_slug": protocol_slug,
        "curator_slug": curator_slug,
        "peak_nav": peak_nav,
        "current_nav": peak_nav,
        "last_updated_at": last_updated_at,
        "risk": risk,
    }


def make_lifetime_df(*rows: dict) -> pd.DataFrame:
    """Build a lifetime metrics dataframe from row dictionaries."""
    return pd.DataFrame(list(rows))


def make_export_record(module, **kwargs) -> dict:
    """Build a JSON-safe exported row for state seeding."""
    return module.export_lifetime_row(pd.Series(make_metrics_row(**kwargs)))


def test_sticky_export_first_qualification_creates_state():
    """A first qualifying vault is exported and persisted.

    1. Build a current metrics row above the peak TVL filter
    2. Apply sticky export state to an empty state
    3. Assert the row is exported
    4. Assert state keeps a lower-case canonical vault key
    """
    # 1. Build a current metrics row above the peak TVL filter
    module = load_vault_analysis_json_module()
    now = datetime.datetime(2026, 6, 24, 12, 0, 0)
    state = module.make_empty_sticky_export_state("top_vaults_by_chain", now)
    df = make_lifetime_df(make_metrics_row())

    # 2. Apply sticky export state to an empty state
    result = module.apply_sticky_export_state(
        df,
        state,
        now=now,
        threshold_tvl=5_000.0,
        stale_warning_age_days=14,
    )

    # 3. Assert the row is exported
    assert len(result.vaults) == 1
    assert result.vaults[0]["name"] == "Sticky USDC"

    # 4. Assert state keeps a lower-case canonical vault key
    assert "1-0xabcd000000000000000000000000000000000001" in result.state["vaults"]
    entry = result.state["vaults"]["1-0xabcd000000000000000000000000000000000001"]
    assert entry["status"] == "active"
    assert entry["last_exported_record"]["address"] == "0xAbCd000000000000000000000000000000000001"


def test_sticky_export_missing_current_metrics_use_fallback():
    """A previously qualified vault remains exported when current metrics disappear.

    1. Seed state with a previous exported row
    2. Apply sticky export state with no current rows
    3. Assert the fallback row is exported as stale
    4. Assert stale state timestamps do not move backwards
    """
    # 1. Seed state with a previous exported row
    module = load_vault_analysis_json_module()
    now = datetime.datetime(2026, 6, 24, 12, 0, 0)
    state = module.make_empty_sticky_export_state("top_vaults_by_chain", now)
    key = "1-0xabcd000000000000000000000000000000000001"
    state["vaults"][key] = {
        "chain_id": 1,
        "address": "0xabcd000000000000000000000000000000000001",
        "status": "active",
        "first_qualified_at": "2026-06-20T00:00:00Z",
        "last_qualified_at": "2026-06-20T00:00:00Z",
        "last_exported_at": "2026-06-20T00:00:00Z",
        "last_fresh_row_at": "2026-06-20T00:00:00Z",
        "stale_since": None,
        "qualification": {"min_tvl": 5000.0, "peak_nav": 10_000.0},
        "last_exported_record": make_export_record(module, address="0xabcd000000000000000000000000000000000001"),
    }
    df = make_lifetime_df()

    # 2. Apply sticky export state with no current rows
    result = module.apply_sticky_export_state(
        df,
        state,
        now=now,
        threshold_tvl=5_000.0,
        stale_warning_age_days=14,
    )

    # 3. Assert the fallback row is exported as stale
    assert len(result.vaults) == 1
    assert result.vaults[0]["stale_export"] is True
    assert result.vaults[0]["risk_possibly_stale"] is True

    # 4. Assert stale state timestamps do not move backwards
    entry = result.state["vaults"][key]
    assert entry["stale_since"] == "2026-06-24T12:00:00Z"
    assert entry["last_fresh_row_at"] == "2026-06-20T00:00:00Z"


def test_sticky_export_structurally_unsafe_current_row_falls_back():
    """A sticky vault with incomplete current metadata replays its stored row.

    1. Seed state with a valid previous export
    2. Run with the same vault missing a non-key current metadata field
    3. Assert the stored row is exported instead of dropping the vault
    4. Assert the fallback reason is annotated
    """
    # 1. Seed state with a valid previous export
    module = load_vault_analysis_json_module()
    now = datetime.datetime(2026, 6, 24, 12, 0, 0)
    state = module.make_empty_sticky_export_state("top_vaults_by_chain", now)
    key = "1-0xabcd000000000000000000000000000000000001"
    state["vaults"][key] = {
        "chain_id": 1,
        "address": "0xabcd000000000000000000000000000000000001",
        "status": "active",
        "first_qualified_at": "2026-06-20T00:00:00Z",
        "last_qualified_at": "2026-06-20T00:00:00Z",
        "last_exported_at": "2026-06-20T00:00:00Z",
        "last_fresh_row_at": "2026-06-20T00:00:00Z",
        "stale_since": None,
        "qualification": {"min_tvl": 5000.0, "peak_nav": 10_000.0},
        "last_exported_record": make_export_record(module, address="0xabcd000000000000000000000000000000000001", name="Stored name"),
    }
    current = make_metrics_row(address="0xabcd000000000000000000000000000000000001", name="Broken name")
    del current["name"]
    df = make_lifetime_df(current)

    # 2. Run with the same vault missing a non-key current metadata field
    result = module.apply_sticky_export_state(
        df,
        state,
        now=now,
        threshold_tvl=5_000.0,
        stale_warning_age_days=14,
    )

    # 3. Assert the stored row is exported instead of dropping the vault
    assert len(result.vaults) == 1
    assert result.vaults[0]["name"] == "Stored name"

    # 4. Assert the fallback reason is annotated
    assert result.vaults[0]["fallback_reason"] == "current_row_structurally_unsafe"
    assert result.stats.current_row_structural_fallbacks == 1


def test_sticky_export_null_current_metadata_falls_back():
    """A sticky vault with null current metadata replays its stored row.

    1. Seed state with a valid previous export
    2. Run with the same vault carrying a null required metadata value
    3. Assert the stored row is exported instead of replacing it with null data
    4. Assert nullable curator_slug alone does not make the row unsafe
    """
    # 1. Seed state with a valid previous export
    module = load_vault_analysis_json_module()
    now = datetime.datetime(2026, 6, 24, 12, 0, 0)
    state = module.make_empty_sticky_export_state("top_vaults_by_chain", now)
    key = "1-0xabcd000000000000000000000000000000000001"
    state["vaults"][key] = {
        "chain_id": 1,
        "address": "0xabcd000000000000000000000000000000000001",
        "status": "active",
        "first_qualified_at": "2026-06-20T00:00:00Z",
        "last_qualified_at": "2026-06-20T00:00:00Z",
        "last_exported_at": "2026-06-20T00:00:00Z",
        "last_fresh_row_at": "2026-06-20T00:00:00Z",
        "stale_since": None,
        "qualification": {"min_tvl": 5000.0, "peak_nav": 10_000.0},
        "last_exported_record": make_export_record(module, address="0xabcd000000000000000000000000000000000001", name="Stored name"),
    }
    current = make_metrics_row(address="0xabcd000000000000000000000000000000000001", name=None)
    df = make_lifetime_df(current)

    # 2. Run with the same vault carrying a null required metadata value
    result = module.apply_sticky_export_state(
        df,
        state,
        now=now,
        threshold_tvl=5_000.0,
        stale_warning_age_days=14,
    )

    # 3. Assert the stored row is exported instead of replacing it with null data
    assert len(result.vaults) == 1
    assert result.vaults[0]["name"] == "Stored name"
    assert result.vaults[0]["fallback_reason"] == "current_row_structurally_unsafe"

    # 4. Assert nullable curator_slug alone does not make the row unsafe
    safe, reason = module.is_current_record_export_safe(make_export_record(module, curator_slug=None))
    assert safe is True
    assert reason is None


def test_sticky_export_below_threshold_current_row_stays_exported():
    """A sticky vault remains exported when current peak TVL is below threshold.

    1. Seed state with a previously qualified vault
    2. Run with a fresh current row below the current threshold
    3. Assert the vault remains exported as sticky
    4. Assert it does not update last_qualified_at
    """
    # 1. Seed state with a previously qualified vault
    module = load_vault_analysis_json_module()
    now = datetime.datetime(2026, 6, 24, 12, 0, 0)
    state = module.make_empty_sticky_export_state("top_vaults_by_chain", now)
    key = "1-0xabcd000000000000000000000000000000000001"
    state["vaults"][key] = {
        "chain_id": 1,
        "address": "0xabcd000000000000000000000000000000000001",
        "status": "active",
        "first_qualified_at": "2026-06-20T00:00:00Z",
        "last_qualified_at": "2026-06-20T00:00:00Z",
        "last_exported_at": "2026-06-20T00:00:00Z",
        "last_fresh_row_at": "2026-06-20T00:00:00Z",
        "stale_since": None,
        "qualification": {"min_tvl": 5000.0, "peak_nav": 10_000.0},
        "last_exported_record": make_export_record(module, address="0xabcd000000000000000000000000000000000001"),
    }
    current = make_metrics_row(address="0xabcd000000000000000000000000000000000001", peak_nav=1_000.0)
    df = make_lifetime_df(current)

    # 2. Run with a fresh current row below the current threshold
    result = module.apply_sticky_export_state(
        df,
        state,
        now=now,
        threshold_tvl=5_000.0,
        stale_warning_age_days=14,
    )

    # 3. Assert the vault remains exported as sticky
    assert len(result.vaults) == 1
    assert result.vaults[0]["sticky_export"] is True
    assert result.vaults[0]["stale_export"] is False
    assert result.vaults[0]["peak_nav"] == 1_000.0

    # 4. Assert it does not update last_qualified_at
    assert result.state["vaults"][key]["last_qualified_at"] == "2026-06-20T00:00:00Z"


def test_sticky_export_structural_suppression_recovers_on_clean_current_row():
    """A structurally suppressed vault recovers when clean current data qualifies.

    1. Seed state with a structurally suppressed vault
    2. Run with a clean current row above the threshold
    3. Assert the row is exported
    4. Assert suppression fields are cleared
    """
    # 1. Seed state with a structurally suppressed vault
    module = load_vault_analysis_json_module()
    now = datetime.datetime(2026, 6, 24, 12, 0, 0)
    state = module.make_empty_sticky_export_state("top_vaults_by_chain", now)
    key = "1-0xabcd000000000000000000000000000000000001"
    state["vaults"][key] = {
        "chain_id": 1,
        "address": "0xabcd000000000000000000000000000000000001",
        "status": "suppressed",
        "suppression_reason": "invalid_last_exported_record",
        "suppressed_at": "2026-06-20T00:00:00Z",
        "first_qualified_at": "2026-06-20T00:00:00Z",
        "last_qualified_at": "2026-06-20T00:00:00Z",
        "last_exported_record": {},
    }
    df = make_lifetime_df(make_metrics_row(address="0xabcd000000000000000000000000000000000001"))

    # 2. Run with a clean current row above the threshold
    result = module.apply_sticky_export_state(
        df,
        state,
        now=now,
        threshold_tvl=5_000.0,
        stale_warning_age_days=14,
    )

    # 3. Assert the row is exported
    assert len(result.vaults) == 1
    assert result.vaults[0]["name"] == "Sticky USDC"

    # 4. Assert suppression fields are cleared
    entry = result.state["vaults"][key]
    assert entry["status"] == "active"
    assert "suppression_reason" not in entry
    assert "suppressed_at" not in entry


def test_sticky_export_blacklisted_rows_are_suppressed():
    """Blacklisted rows are suppressed using the real exported risk label.

    1. Run a current qualifying row with the blacklist enum
    2. Assert it is suppressed immediately
    3. Seed a stale fallback row with the serialised Blacklisted label
    4. Assert stale fallback is also suppressed
    """
    # 1. Run a current qualifying row with the blacklist enum
    module = load_vault_analysis_json_module()
    now = datetime.datetime(2026, 6, 24, 12, 0, 0)
    state = module.make_empty_sticky_export_state("top_vaults_by_chain", now)
    key = "1-0xabcd000000000000000000000000000000000001"
    df = make_lifetime_df(make_metrics_row(risk=module.VaultTechnicalRisk.blacklisted))

    first = module.apply_sticky_export_state(
        df,
        state,
        now=now,
        threshold_tvl=5_000.0,
        stale_warning_age_days=14,
    )

    # 2. Assert it is suppressed immediately
    assert first.vaults == []
    assert first.state["vaults"][key]["suppression_reason"] == "current_blacklisted_record"

    # 3. Seed a stale fallback row with the serialised Blacklisted label
    fallback_state = module.make_empty_sticky_export_state("top_vaults_by_chain", now)
    fallback_state["vaults"][key] = {
        "chain_id": 1,
        "address": "0xabcd000000000000000000000000000000000001",
        "status": "active",
        "first_qualified_at": "2026-06-20T00:00:00Z",
        "last_qualified_at": "2026-06-20T00:00:00Z",
        "last_exported_record": make_export_record(module, address="0xabcd000000000000000000000000000000000001", risk="Blacklisted"),
    }

    # 4. Assert stale fallback is also suppressed
    second = module.apply_sticky_export_state(
        make_lifetime_df(),
        fallback_state,
        now=now,
        threshold_tvl=5_000.0,
        stale_warning_age_days=14,
    )
    assert second.vaults == []
    assert second.state["vaults"][key]["suppression_reason"] == "stale_blacklisted_record"


def test_sticky_export_namespaces_state_by_output_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Default sticky state paths are namespaced by output filename.

    1. Resolve state path for production output
    2. Resolve state path for standalone output
    3. Assert default paths differ
    4. Assert explicit override is honoured
    """
    # 1. Resolve state path for production output
    module = load_vault_analysis_json_module()
    production_path = module.resolve_sticky_export_state_path(tmp_path, tmp_path / "top_vaults_by_chain.json")

    # 2. Resolve state path for standalone output
    standalone_path = module.resolve_sticky_export_state_path(tmp_path, tmp_path / "stablecoin-vault-metrics.json")

    # 3. Assert default paths differ
    assert production_path.name == "vault-export-state-top_vaults_by_chain.json"
    assert standalone_path.name == "vault-export-state-stablecoin-vault-metrics.json"
    assert production_path != standalone_path

    # 4. Assert explicit override is honoured
    override = tmp_path / "shared-state.json"
    monkeypatch.setenv("VAULT_EXPORT_STATE_PATH", str(override))
    assert module.resolve_sticky_export_state_path(tmp_path, tmp_path / "top_vaults_by_chain.json") == override


def test_sticky_export_timestamp_normalisation_uses_utc_before_dropping_timezone():
    """Tz-aware timestamps are converted to UTC before naive comparison.

    1. Create a non-UTC timestamp
    2. Normalise it through the exporter helper
    3. Assert the result is naive UTC
    4. Assert stale current rows remain exported with warning annotations
    """
    # 1. Create a non-UTC timestamp
    module = load_vault_analysis_json_module()
    aware_timestamp = pd.Timestamp("2026-06-24T15:00:00+03:00")

    # 2. Normalise it through the exporter helper
    normalised = module.normalise_datetime_to_naive_utc(aware_timestamp)

    # 3. Assert the result is naive UTC
    assert normalised == datetime.datetime(2026, 6, 24, 12, 0, 0)
    assert normalised.tzinfo is None

    # 4. Assert stale current rows remain exported with warning annotations
    now = datetime.datetime(2026, 6, 24, 12, 0, 0)
    state = module.make_empty_sticky_export_state("top_vaults_by_chain", now)
    stale_timestamp = pd.Timestamp("2026-05-01T15:00:00+03:00")
    df = make_lifetime_df(make_metrics_row(last_updated_at=stale_timestamp))
    result = module.apply_sticky_export_state(
        df,
        state,
        now=now,
        threshold_tvl=5_000.0,
        stale_warning_age_days=14,
    )
    assert len(result.vaults) == 1
    assert result.vaults[0]["stale_current_row"] is True
    assert result.vaults[0]["risk_possibly_stale"] is True


def test_sticky_export_invalid_fallback_record_is_suppressed():
    """A sticky vault with no safe current row or fallback record is suppressed.

    1. Seed state with an active vault carrying an empty fallback record
    2. Run with no current metrics row
    3. Assert no vault is exported
    4. Assert structural suppression is persisted
    """
    # 1. Seed state with an active vault carrying an empty fallback record
    module = load_vault_analysis_json_module()
    now = datetime.datetime(2026, 6, 24, 12, 0, 0)
    key = "1-0xabcd000000000000000000000000000000000001"
    state = module.make_empty_sticky_export_state("top_vaults_by_chain", now)
    state["vaults"][key] = {
        "chain_id": 1,
        "address": "0xabcd000000000000000000000000000000000001",
        "status": "active",
        "first_qualified_at": "2026-06-20T00:00:00Z",
        "last_qualified_at": "2026-06-20T00:00:00Z",
        "last_exported_record": {},
    }

    # 2. Run with no current metrics row
    result = module.apply_sticky_export_state(
        make_lifetime_df(),
        state,
        now=now,
        threshold_tvl=5_000.0,
        stale_warning_age_days=14,
    )

    # 3. Assert no vault is exported
    assert result.vaults == []

    # 4. Assert structural suppression is persisted
    assert result.state["vaults"][key]["status"] == "suppressed"
    assert result.state["vaults"][key]["suppression_reason"] == "invalid_last_exported_record"
