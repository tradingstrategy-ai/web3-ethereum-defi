"""Tests for the local Arc initial vault-discovery script."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def arc_scan_module() -> ModuleType:
    """Load the hyphenated Arc bootstrap script as a module.

    :return:
        Imported Arc bootstrap module.
    """
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "erc-4626" / "scan-arc-vaults.py"
    spec = importlib.util.spec_from_file_location("scan_arc_vaults", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_derive_arc_goldsky_rpc_url_preserves_project_path_and_query(arc_scan_module: ModuleType) -> None:
    """The derivation only replaces Goldsky's final Ethereum chain-id segment."""

    result = arc_scan_module.derive_arc_goldsky_rpc_url("https://edge.goldsky.com/project/rpc/1?secret=redacted")

    assert result == "https://edge.goldsky.com/project/rpc/5042?secret=redacted"


def test_resolve_arc_rpc_url_prefers_explicit_configuration(arc_scan_module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dedicated Arc RPC setting overrides the Ethereum-derived endpoint."""
    monkeypatch.setenv("JSON_RPC_ARC", "https://arc.example")
    monkeypatch.setenv("JSON_RPC_ETHEREUM", "https://edge.goldsky.com/project/rpc/1?secret=redacted")

    rpc_url, source = arc_scan_module.resolve_arc_rpc_url()

    assert rpc_url == "https://arc.example"
    assert source == "JSON_RPC_ARC"


def test_resolve_arc_rpc_url_derives_goldsky_endpoint(arc_scan_module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """The derivation finds Goldsky among a multi-provider Ethereum setting."""
    monkeypatch.delenv("JSON_RPC_ARC", raising=False)
    monkeypatch.setenv(
        "JSON_RPC_ETHEREUM",
        "https://ethereum.example https://edge.goldsky.com/project/rpc/1?secret=redacted",
    )

    rpc_url, source = arc_scan_module.resolve_arc_rpc_url()

    assert rpc_url == "https://edge.goldsky.com/project/rpc/5042?secret=redacted"
    assert source == "JSON_RPC_ETHEREUM Goldsky endpoint"


def test_derive_arc_goldsky_rpc_url_rejects_non_ethereum_goldsky_path(arc_scan_module: ModuleType) -> None:
    """A non-Ethereum source URL cannot silently become the Arc endpoint."""

    with pytest.raises(ValueError, match="chain id 1"):
        arc_scan_module.derive_arc_goldsky_rpc_url("https://edge.goldsky.com/project/rpc/8453")
