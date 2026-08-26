"""Test Flying Tulip genesis-to-safe-head backfill script wiring."""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def load_backfill_module() -> ModuleType:
    """Import the hyphenated script without running its entry point.

    :return:
        Imported source-history backfill module.
    """

    repository_root = Path(__file__).resolve().parents[2]
    script_path = repository_root / "scripts" / "erc-4626" / "backfill-flying-tulip-history.py"
    spec = importlib.util.spec_from_file_location("backfill_flying_tulip_history", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_full_backfill_range_starts_at_proxy_genesis_and_uses_safe_head(monkeypatch: pytest.MonkeyPatch) -> None:
    """The source replay starts at proxy deployment and ends at one safe head.

    :param monkeypatch:
        Pytest fixture replacing the provider-specific safe-head lookup.
    :return:
        None.
    """

    module = load_backfill_module()
    deployment_block = 123_456
    web3 = SimpleNamespace(eth=SimpleNamespace(get_code=lambda _address, block_identifier: b"code" if block_identifier >= deployment_block else b""))
    monkeypatch.setattr(module, "get_almost_latest_block_number", lambda _web3: 987_654)

    assert module.fetch_flying_tulip_full_backfill_range(web3, "0x0000000000000000000000000000000000000001") == (deployment_block, 987_654)


def test_backfill_uses_conservative_hypersync_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full timestamp-cache repairs do not inherit scanner concurrency defaults.

    :param monkeypatch:
        Pytest fixture isolating environment and Hypersync construction.
    :return:
        None.
    """

    module = load_backfill_module()
    # Seed values so ``monkeypatch.delenv()`` records restoration entries.
    monkeypatch.setenv("HYPERSYNC_RPM", "test-original-rpm")
    monkeypatch.setenv("HYPERSYNC_CONCURRENCY", "test-original-concurrency")
    monkeypatch.delenv("HYPERSYNC_RPM", raising=False)
    monkeypatch.delenv("HYPERSYNC_CONCURRENCY", raising=False)
    monkeypatch.setenv("FLYING_TULIP_HYPERSYNC_RPM", "20")
    monkeypatch.setenv("FLYING_TULIP_HYPERSYNC_CONCURRENCY", "1")
    expected = object()
    monkeypatch.setattr(module, "configure_hypersync_from_env", lambda _web3: expected)

    assert module.configure_flying_tulip_backfill_hypersync(object()) is expected
    assert module.os.environ["HYPERSYNC_RPM"] == "20"
    assert module.os.environ["HYPERSYNC_CONCURRENCY"] == "1"
