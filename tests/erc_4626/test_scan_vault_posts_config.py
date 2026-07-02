"""Tests for the vault post scanner script configuration."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

DEFAULT_STABLECOIN_RATE_TIMEOUT = 20.0


def _load_scan_vault_posts_module():
    """Load the post scanner script as a Python module."""
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "scan-vault-posts.py"
    spec = importlib.util.spec_from_file_location("scan_vault_posts", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stablecoin_rate_timeout_config_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed STABLECOIN_RATE_TIMEOUT does not abort post scanner config loading."""
    module = _load_scan_vault_posts_module()
    monkeypatch.setenv("STABLECOIN_RATE_TIMEOUT", "not-a-number")

    config = module._build_config()

    assert config.stablecoin_rate_timeout == DEFAULT_STABLECOIN_RATE_TIMEOUT
