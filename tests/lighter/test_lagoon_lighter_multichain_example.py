"""The manual script must use defaults and the shared library coordinator."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.fixture
def tutorial() -> ModuleType:
    """Import the hyphenated script without executing its main function."""
    path = Path(__file__).parents[2] / "scripts/lagoon/deploy-lagoon-lighter-multichain.py"
    spec = importlib.util.spec_from_file_location("lighter_multichain_example", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_routine_settings_have_defaults(tutorial: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Credentials and RPCs are the only necessary deployment inputs."""
    for name in ("CHAINS", "SALT_NONCE", "LIGHTER_API_KEY_INDEX", "LIGHTER_DEPLOYMENT_REPORT"):
        monkeypatch.delenv(name, raising=False)
    settings = tutorial.Settings.from_env()
    assert settings.chains == ("hyperliquid", "ethereum")
    assert settings.api_key_index == tutorial.MIN_API_KEY_INDEX
    assert settings.report.suffix == ".jsonl"


@pytest.mark.parametrize("chains", ["ethereum,hyperliquid", "base,ethereum", "hyperliquid,ethereum,ethereum", "hyperliquid,ethereum,unknown"])
def test_reject_invalid_topology(tutorial: ModuleType, monkeypatch: pytest.MonkeyPatch, chains: str) -> None:
    """Unsupported routes fail before a deployment can start."""
    monkeypatch.setenv("CHAINS", chains)
    with pytest.raises(AssertionError):
        tutorial.Settings.from_env()


def test_read_only_mode_calls_shared_preflight(tutorial: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Preflight sends no transactions and needs no custom script orchestration."""
    settings = tutorial.Settings(("hyperliquid", "ethereum"), 123, 4, tmp_path / "report.jsonl")
    monkeypatch.setattr(tutorial, "build_configs", lambda *_: {})
    preflight = Mock()
    deploy = Mock()
    monkeypatch.setattr(tutorial, "preflight_lighter_multichain", preflight)
    monkeypatch.setattr(tutorial, "deploy_multichain_lagoon_vault", deploy)
    tutorial.run_deployment(settings, {}, Mock(), preflight_only=True)
    preflight.assert_called_once()
    deploy.assert_not_called()
    assert not settings.report.exists()
