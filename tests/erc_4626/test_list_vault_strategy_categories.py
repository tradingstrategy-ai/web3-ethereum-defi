"""Tests for the local strategy-category breakdown script."""

import importlib.util
import json
from pathlib import Path

import pytest


def load_category_script():
    """Load the local category-breakdown script as an importable module.

    :return:
        Loaded script module.
    """
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "list-vault-strategy-categories.py"
    spec = importlib.util.spec_from_file_location("list_vault_strategy_categories", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_category_breakdown_requires_exported_categories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not present an old export without tags as a zero-valued table."""
    input_path = tmp_path / "vault-metrics.json"
    input_path.write_text(json.dumps({"vaults": []}), encoding="utf-8")
    monkeypatch.setenv("INPUT_JSON", str(input_path))

    module = load_category_script()
    monkeypatch.setattr(module, "setup_console_logging", lambda **_: None)

    with pytest.raises(AssertionError, match="no categories mapping"):
        module.main()


def test_category_breakdown_rows_sort_by_tvl() -> None:
    """Order category records in descending USD TVL without changing logging."""
    module = load_category_script()
    rows = module.create_table_rows(
        {
            "small": {
                "label": "Small",
                "description": "Rule one. Rule two.",
                "vault_count": 1,
                "tvl_usd": 100.0,
                "one_month_apy": 0.10,
            },
            "large": {
                "label": "Large",
                "description": "Rule one. Rule two.",
                "vault_count": 2,
                "tvl_usd": 200.0,
                "one_month_apy": None,
            },
        }
    )

    assert [row["Tag"] for row in rows] == ["large", "small"]
    assert rows[1]["1M annualised return"] == "10.00%"
