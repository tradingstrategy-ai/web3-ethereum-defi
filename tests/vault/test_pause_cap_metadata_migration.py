"""Refresh reviewed catalogue fields without rewriting price or discovery evidence."""

import datetime
import importlib.util
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature
from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import discover_migrations


@pytest.mark.parametrize("nav", [0, 999])
def test_pause_cap_refresh_preserves_prices_and_unrelated_rows(monkeypatch: pytest.MonkeyPatch, nav: int) -> None:
    """Refresh the repaired readers even below the scanner's activity threshold.

    1. Model the five existing catalogue rows and their saved enrichment.
    2. Read current closure directly, with realistic unknown tri-state openness.
    3. Keep economics and unrelated metadata while correcting the capped adapter.
    """
    script = next(s for s in discover_migrations() if s.name == "075-vault-pause-cap-and-classification.py")
    spec = importlib.util.spec_from_file_location("pause_cap_migration", script.path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = {}
    timestamp = datetime.datetime(2026, 1, 1)
    protocols = {}
    for address in module.TARGETS:
        protocols[address] = "Morpho" if address == module.TARGETS[-1] else "Lagoon Finance" if address == module.TARGETS[-2] else "Upshift"
        identity = VaultSpec(1, address)
        detection = ERC4262VaultDetection(1, address, 1, timestamp, {ERC4626Feature.morpho_like}, timestamp, 10, 20)
        rows[identity] = {"Protocol": protocols[address], "_detection_data": detection, "_deposit_closed_reason": None, "NAV": nav, "Shares": 123, "First seen": timestamp, "Address": address, "_notes": "Keep", "_fees": "saved fees", "_strategy_tags": {"saved tag"}}
    other = VaultSpec(1, "0x" + "99" * 20)
    rows[other] = {"unchanged": True}
    context = Mock()
    context.metadata.return_value = SimpleNamespace(rows=rows)
    provider = Mock()
    provider.eth.block_number = 12345
    monkeypatch.setattr(module, "read_json_rpc_url", lambda _chain: "http://unused")
    monkeypatch.setattr(module, "create_multi_provider_web3", lambda _url: provider)
    monkeypatch.setattr(module, "detect_vault_features", lambda *_a, **_k: {ERC4626Feature.morpho_like, ERC4626Feature.vault_morpho_like})
    # Unknown openness is valid: the explicit closure reason has separate semantics.
    vault = SimpleNamespace(fetch_deposit_open=lambda: None, fetch_deposit_closed_reason=lambda: "Cap reached")
    factory = Mock(return_value=vault)
    monkeypatch.setattr(module, "create_vault_instance", factory)
    module.migrate(context)
    for address in module.TARGETS:
        row = rows[VaultSpec(1, address)]
        assert (row["NAV"], row["Shares"], row["First seen"], row["Address"], row["_notes"]) == (nav, 123, timestamp, address, "Keep")
        assert row["_fees"] == "saved fees"
        assert row["_strategy_tags"] == {"saved tag"}
        assert row["_deposit_closed_reason"] == "Cap reached"
        assert row["_deposits_open"] is None
        assert row["Protocol"] == protocols[address]
    corrected = rows[VaultSpec(1, module.TARGETS[-1])]
    assert ERC4626Feature.vault_morpho_like in corrected["_detection_data"].features
    assert corrected["features"] == corrected["_detection_data"].features
    assert "vault_morpho_like" in corrected["Features"]
    assert all(call.kwargs["default_block_identifier"] == 12345 for call in factory.call_args_list)
    assert rows[other] == {"unchanged": True}
    context.commit_metadata.assert_called_once()


def test_pause_cap_refresh_propagates_read_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed contract read must not become a replacement catalogue value.

    1. Retain one existing capped-vault row in the migration context.
    2. Fail the direct closure read as a provider would.
    3. Leave the row unchanged and prevent a metadata commit.
    """
    script = next(s for s in discover_migrations() if s.name == "075-vault-pause-cap-and-classification.py")
    spec = importlib.util.spec_from_file_location("pause_cap_failed_migration", script.path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    address = module.TARGETS[-1]
    timestamp = datetime.datetime(2026, 1, 1)
    detection = ERC4262VaultDetection(1, address, 1, timestamp, {ERC4626Feature.morpho_like}, timestamp, 10, 20)
    row = {"Protocol": "Morpho", "_detection_data": detection, "NAV": 999}
    original = row.copy()
    context = Mock()
    context.metadata.return_value = SimpleNamespace(rows={VaultSpec(1, address): row})
    provider = Mock()
    provider.eth.block_number = 12345
    monkeypatch.setattr(module, "read_json_rpc_url", lambda _chain: "http://unused")
    monkeypatch.setattr(module, "create_multi_provider_web3", lambda _url: provider)
    monkeypatch.setattr(module, "detect_vault_features", lambda *_a, **_k: {ERC4626Feature.morpho_like, ERC4626Feature.vault_morpho_like})
    vault = SimpleNamespace(fetch_deposit_open=lambda: None, fetch_deposit_closed_reason=Mock(side_effect=ValueError("RPC failed")))
    monkeypatch.setattr(module, "create_vault_instance", lambda *_a, **_k: vault)
    with pytest.raises(ValueError, match="RPC failed"):
        module.migrate(context)
    assert row == original
    context.commit_metadata.assert_not_called()
