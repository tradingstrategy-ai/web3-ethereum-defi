"""Tests for the zero-configuration GMX historical backfill script."""

import importlib.util
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

DEFAULT_MAX_WORKERS = 4


def load_backfill_module() -> ModuleType:
    """Load the hyphenated GMX backfill script as a Python module.

    :return:
        Imported script module without invoking its command-line entry point.
    """

    repository_root = Path(__file__).resolve().parents[2]
    script_path = repository_root / "scripts" / "erc-4626" / "backfill-gmx-vault-prices.py"
    spec = importlib.util.spec_from_file_location("backfill_gmx_vault_prices", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fetch_gmx_full_backfill_range_uses_safe_head(monkeypatch: pytest.MonkeyPatch) -> None:
    """The automatic range starts at block one and stops at one resolved safe head."""

    module = load_backfill_module()
    web3 = SimpleNamespace()
    monkeypatch.setattr(module, "get_almost_latest_block_number", lambda _value: 456_789)

    assert module.fetch_gmx_full_backfill_range(web3) == (1, 456_789)


def test_main_backfills_both_chains_without_range_parameters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Default invocation runs full hourly Arbitrum and Avalanche backfills."""

    module = load_backfill_module()
    calls: list[dict[str, object]] = []

    for name in ("CHAIN", "START_BLOCK", "END_BLOCK", "FREQUENCY", "VAULT_ADDRESSES", "DRY_RUN", "MAX_WORKERS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(module, "setup_console_logging", lambda **_kwargs: None)
    monkeypatch.setattr(module, "get_pipeline_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "wait_other_writers", lambda *_args, **_kwargs: nullcontext())

    def record_backfill(**kwargs: object) -> None:
        """Capture one chain invocation without network or filesystem writes."""

        calls.append(kwargs)

    monkeypatch.setattr(module, "_run_backfill", record_backfill)

    module.main()

    assert module.GMX_BACKFILL_FREQUENCY == "1h"
    assert [call["chain_name"] for call in calls] == ["arbitrum", "avalanche"]
    assert all(call["max_workers"] == DEFAULT_MAX_WORKERS for call in calls)
    assert all(call["vault_database"] == tmp_path / "vault-metadata-db.pickle" for call in calls)
    assert all(call["price_database"] == tmp_path / "vault-prices-1h.parquet" for call in calls)
    assert all(call["context_database"] == tmp_path / "vault-historical-context.duckdb" for call in calls)
