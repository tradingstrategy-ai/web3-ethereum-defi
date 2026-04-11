"""Unit tests for top-vaults post-processing integration."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from eth_defi.vault import post_processing


def _load_script_module(script_relative_path: str, module_name: str):
    """Load a script file as a Python module for unit testing."""
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / script_relative_path
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_top_vaults_config_requires_exact_missing_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-fast validation should name the first missing environment variable."""
    monkeypatch.delenv("R2_TOP_VAULTS_BUCKET_NAME", raising=False)
    monkeypatch.setenv("R2_TOP_VAULTS_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_TOP_VAULTS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_TOP_VAULTS_ENDPOINT_URL", "https://example.invalid")

    with pytest.raises(RuntimeError, match="R2_TOP_VAULTS_BUCKET_NAME"):
        post_processing.validate_top_vaults_config()

    post_processing.validate_top_vaults_config(skip_top_vaults=True)


def test_export_top_vaults_json_uses_pipeline_paths_and_dual_upload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Top-vault export should use pipeline paths and upload to both buckets."""
    captured_main_kwargs = {}
    upload_calls = []

    class FakeLoader:
        """Loader that installs a fake script main() implementation."""

        @staticmethod
        def exec_module(module) -> None:
            def fake_main(**kwargs) -> None:
                captured_main_kwargs.update(kwargs)
                kwargs["output_path"].write_text("{}", encoding="utf-8")

            module.main = fake_main

    fake_spec = SimpleNamespace(loader=FakeLoader())

    monkeypatch.setattr(post_processing, "get_pipeline_data_dir", lambda: tmp_path)
    monkeypatch.setenv("R2_TOP_VAULTS_BUCKET_NAME", "public-bucket")
    monkeypatch.setenv("R2_TOP_VAULTS_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_TOP_VAULTS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_TOP_VAULTS_ENDPOINT_URL", "https://example.invalid")
    monkeypatch.setenv("R2_TOP_VAULTS_ALTERNATIVE_BUCKET_NAME", "private-bucket")
    monkeypatch.setenv("UPLOAD_PREFIX", "test-")

    def fake_spec_from_file_location(*_args, **_kwargs):
        return fake_spec

    def fake_module_from_spec(_spec):
        return SimpleNamespace()

    monkeypatch.setattr(post_processing.importlib.util, "spec_from_file_location", fake_spec_from_file_location)
    monkeypatch.setattr(post_processing.importlib.util, "module_from_spec", fake_module_from_spec)
    monkeypatch.setattr(post_processing, "create_r2_client", lambda **kwargs: {"client": kwargs})
    monkeypatch.setattr(
        post_processing,
        "upload_file_to_r2",
        lambda **kwargs: upload_calls.append(kwargs) or True,
    )

    success = post_processing.export_top_vaults_json()

    assert success is True
    assert captured_main_kwargs["data_dir"] == tmp_path
    assert captured_main_kwargs["vault_db_path"] == tmp_path / "vault-metadata-db.pickle"
    assert captured_main_kwargs["parquet_path"] == tmp_path / "cleaned-vault-prices-1h.parquet"
    assert captured_main_kwargs["output_path"] == tmp_path / "top_vaults_by_chain.json"
    expected_upload_count = 2
    assert len(upload_calls) == expected_upload_count
    assert upload_calls[0]["bucket_name"] == "public-bucket"
    assert upload_calls[1]["bucket_name"] == "private-bucket"
    assert upload_calls[0]["object_name"] == "test-top_vaults_by_chain.json"
    assert upload_calls[1]["object_name"] == "test-top_vaults_by_chain.json"


def test_post_process_prices_main_forwards_pipeline_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Debug wrapper should delegate all path and skip settings to run_post_processing."""
    module = _load_script_module("scripts/erc-4626/post-process-prices.py", "test_post_process_prices")
    captured = {}

    monkeypatch.setenv("MERGE_HYPERCORE", "true")
    monkeypatch.setenv("MERGE_GRVT", "false")
    monkeypatch.setenv("MERGE_LIGHTER", "true")
    monkeypatch.setenv("SKIP_CLEANING", "true")
    monkeypatch.setenv("SKIP_TOP_VAULTS", "true")
    monkeypatch.setenv("SKIP_SPARKLINES", "true")
    monkeypatch.setenv("SKIP_METADATA", "false")
    monkeypatch.setenv("SKIP_DATA", "true")
    monkeypatch.setattr(module, "setup_console_logging", lambda **_kwargs: None)
    monkeypatch.setattr(module, "get_pipeline_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "validate_top_vaults_config", lambda skip_top_vaults: captured.setdefault("validated", skip_top_vaults))

    def fake_run_post_processing(**kwargs):
        captured["kwargs"] = kwargs
        return {"clean-prices": True, "export-top-vaults-json": True}

    monkeypatch.setattr(module, "run_post_processing", fake_run_post_processing)

    module.main()

    assert captured["validated"] is True
    assert captured["kwargs"]["scan_hypercore"] is True
    assert captured["kwargs"]["scan_grvt"] is False
    assert captured["kwargs"]["scan_lighter"] is True
    assert captured["kwargs"]["skip_cleaning"] is True
    assert captured["kwargs"]["skip_top_vaults"] is True
    assert captured["kwargs"]["skip_sparklines"] is True
    assert captured["kwargs"]["skip_metadata"] is False
    assert captured["kwargs"]["skip_data"] is True
    assert captured["kwargs"]["vault_db_path"] == tmp_path / "vault-metadata-db.pickle"
    assert captured["kwargs"]["uncleaned_parquet_path"] == tmp_path / "vault-prices-1h.parquet"
    assert captured["kwargs"]["cleaned_path"] == tmp_path / "cleaned-vault-prices-1h.parquet"
    assert captured["kwargs"]["hyperliquid_db_path"] == tmp_path / "hyperliquid-vaults.duckdb"
    assert captured["kwargs"]["hyperliquid_hf_db_path"] == tmp_path / "hyperliquid-vaults-hf.duckdb"
    assert captured["kwargs"]["grvt_db_path"] == tmp_path / "grvt-vaults.duckdb"
    assert captured["kwargs"]["lighter_db_path"] == tmp_path / "lighter-pools.duckdb"


def test_vault_analysis_json_rereads_env_and_accepts_explicit_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Vault analysis JSON should honour current env defaults and explicit overrides."""
    module = _load_script_module("scripts/erc-4626/vault-analysis-json.py", "test_vault_analysis_json")

    class FakeVaultDb:
        """Minimal vault DB stub for the JSON export script."""

        def __init__(self) -> None:
            detection_data = SimpleNamespace(chain=42161, address="0x1111111111111111111111111111111111111111")
            self.rows = {"vault": {"Denomination": "USDC", "_detection_data": detection_data}}

        def __len__(self) -> int:
            return len(self.rows)

        def values(self):
            return self.rows.values()

    prices_df = pd.DataFrame(
        {
            "chain": [42161],
            "id": ["42161-0x1111111111111111111111111111111111111111"],
        },
        index=pd.to_datetime(["2026-01-01T00:00:00"]),
    )
    lifetime_df = pd.DataFrame({"peak_nav": [6_000]})
    calls: dict[str, Path] = {}

    monkeypatch.setattr(module, "display", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "cross_check_data", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "calculate_hourly_returns_for_all_vaults", lambda df: df)
    monkeypatch.setattr(module, "calculate_lifetime_metrics", lambda *_args, **_kwargs: lifetime_df)
    monkeypatch.setattr(module, "export_lifetime_row", lambda _row: {"slug": "vault"})
    monkeypatch.setattr(module, "is_stablecoin_like", lambda _symbol: True)

    def fake_read_parquet(path):
        calls["parquet_path"] = Path(path)
        return prices_df

    def fake_read_vault_db(path):
        calls["vault_db_path"] = Path(path)
        return FakeVaultDb()

    monkeypatch.setattr(module.pd, "read_parquet", fake_read_parquet)
    monkeypatch.setattr(module.VaultDatabase, "read", fake_read_vault_db)

    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(first_dir))
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("OUTPUT_JSON", raising=False)
    module.main()

    assert calls["vault_db_path"] == first_dir / "vault-metadata-db.pickle"
    assert calls["parquet_path"] == first_dir / "cleaned-vault-prices-1h.parquet"
    assert (first_dir / module.DEFAULT_OUTPUT_FILENAME).exists()

    defaults_one = module._resolve_defaults_from_env()
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(second_dir))
    defaults_two = module._resolve_defaults_from_env()
    assert defaults_one["data_dir"] == first_dir
    assert defaults_two["data_dir"] == second_dir

    explicit_vault_db_path = tmp_path / "custom" / "vaults.pickle"
    explicit_parquet_path = tmp_path / "custom" / "prices.parquet"
    explicit_output_path = tmp_path / "custom" / "out.json"
    calls.clear()
    module.main(
        data_dir=tmp_path / "ignored",
        vault_db_path=explicit_vault_db_path,
        parquet_path=explicit_parquet_path,
        output_path=explicit_output_path,
    )

    assert calls["vault_db_path"] == explicit_vault_db_path
    assert calls["parquet_path"] == explicit_parquet_path
    assert explicit_output_path.exists()
    output_data = json.loads(explicit_output_path.read_text(encoding="utf-8"))
    assert output_data["vaults"] == [{"slug": "vault"}]
