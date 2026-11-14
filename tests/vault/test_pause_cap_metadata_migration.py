"""Refresh reviewed catalogue fields without rewriting price or discovery evidence."""

import datetime
import importlib.util
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eth_defi.erc_4626.core import ERC4262VaultDetection, ERC4626Feature, GENERIC_ERC4626_PROTOCOL_NAME
from eth_defi.vault.fee import BROKEN_FEE_DATA
from eth_defi.vault.flag import VaultFlag
from eth_defi.vault.strategy_tag import StrategyTag
from eth_defi.vault.base import VaultSpec
from eth_defi.vault_protocols.migration import discover_migrations


@pytest.mark.parametrize("nav", [0, 999])
def test_pause_cap_refresh_preserves_prices_and_unrelated_rows(monkeypatch: pytest.MonkeyPatch, nav: int) -> None:
    """Refresh the repaired readers even below the scanner's activity threshold.

    1. Model the five existing catalogue rows and their saved enrichment.
    2. Read a specific closure reason even when general deposit availability is unknown.
    3. Preserve asset values while clearing defaults inherited from the wrong protocol.
    """
    script = next(s for s in discover_migrations() if s.name == "075-vault-pause-cap-and-classification.py")
    spec = importlib.util.spec_from_file_location("pause_cap_migration", script.path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # 1. Saved rows let the test check changes without a production database.
    rows = {}
    timestamp = datetime.datetime(2026, 1, 1)
    protocols = {}
    for address in module.TARGETS:
        protocols[address] = "Morpho" if address == module.TARGETS[-1] else "Lagoon Finance" if address == module.TARGETS[-2] else "Upshift"
        identity = VaultSpec(1, address)
        detection = ERC4262VaultDetection(1, address, 1, timestamp, {ERC4626Feature.morpho_like}, timestamp, 10, 20)
        rows[identity] = {"Protocol": protocols[address], "_detection_data": detection, "_deposit_closed_reason": None, "NAV": nav, "Shares": 123, "First seen": timestamp, "Address": address, "_notes": "Keep", "_fees": "saved fees", "_strategy_tags": {"saved tag"}}
    # Include another saved family member so the repair is not limited to the test vault.
    extra = VaultSpec(42161, "0x" + "88" * 20)
    extra_detection = ERC4262VaultDetection(42161, extra.vault_address, 1, timestamp, {ERC4626Feature.morpho_like}, timestamp, 10, 20)
    rows[extra] = {"Name": "VaultMorpho", "Protocol": "Morpho", "_detection_data": extra_detection, "NAV": nav}
    other = VaultSpec(1, "0x" + "99" * 20)
    rows[other] = {"unchanged": True}
    context = Mock()
    context.metadata.return_value = SimpleNamespace(rows=rows)
    provider = Mock()
    provider.eth.block_number = 12345
    monkeypatch.setattr(module, "read_json_rpc_url", lambda _chain: "http://unused")
    monkeypatch.setattr(module, "create_multi_provider_web3", lambda _url: provider)
    monkeypatch.setattr(module, "detect_vault_features", lambda *_a, **_k: {ERC4626Feature.morpho_like, ERC4626Feature.vault_morpho_like})
    # A specific closure reason can be known even when general availability is unknown.
    vault = SimpleNamespace(fetch_deposit_open=lambda: None, fetch_deposit_closed_reason=lambda: "Cap reached")
    factory = Mock(return_value=vault)
    monkeypatch.setattr(module, "create_vault_instance", factory)

    def scan(_web3: object, detection: ERC4262VaultDetection, _block: int, token_cache: dict) -> dict:
        """Supply scanner fields without network calls so only migration changes are tested."""
        return {
            "Protocol": GENERIC_ERC4626_PROTOCOL_NAME,
            "_detection_data": detection,
            "features": set(detection.features),
            "Features": ", ".join(sorted(f.name for f in detection.features)),
            "Link": "https://routescan.io/address/" + detection.address,
            "Mgmt fee": None,
            "Perf fee": None,
            "Deposit fee": None,
            "Withdraw fee": None,
            "Protocol fee": None,
            "_fees": BROKEN_FEE_DATA,
            "_manager_name": None,
            "_morpho_offchain_data": None,
            "_morpho_api_status": None,
            "_flags": {VaultFlag.unofficial},
            "_notes": None,
            "_strategy_tags": {StrategyTag.lending},
            "_lockup": None,
            "_withdrawal_period": None,
            "_deposit_permission": "unknown",
            "_whitelist_notes": None,
            "_deposit_manager": None,
        }

    monkeypatch.setattr(module, "create_vault_scan_record", scan)
    # 2. Read a specific closure reason even when general deposit availability is unknown.
    module.migrate(context)
    # 3. Only the misclassified family loses the unsupported Morpho defaults.
    for address in module.TARGETS:
        row = rows[VaultSpec(1, address)]
        assert (row["NAV"], row["Shares"], row["First seen"], row["Address"]) == (nav, 123, timestamp, address)
        if address == module.TARGETS[-1]:
            assert row["_fees"] == BROKEN_FEE_DATA
            assert row["_strategy_tags"] == {StrategyTag.lending}
            assert row["_deposit_permission"] == "unknown"
            assert row["_morpho_offchain_data"] is None
            assert VaultFlag.unofficial in row["_flags"]
        else:
            assert row["_notes"] == "Keep"
            assert row["_fees"] == "saved fees"
            assert row["_strategy_tags"] == {"saved tag"}
        assert row["_deposit_closed_reason"] == "Cap reached"
        assert row["_deposits_open"] is None
        assert row["Protocol"] == (GENERIC_ERC4626_PROTOCOL_NAME if address == module.TARGETS[-1] else protocols[address])
    corrected = rows[VaultSpec(1, module.TARGETS[-1])]
    assert ERC4626Feature.vault_morpho_like in corrected["_detection_data"].features
    assert corrected["features"] == corrected["_detection_data"].features
    assert "vault_morpho_like" in corrected["Features"]
    assert all(call.kwargs["default_block_identifier"] == 12345 for call in factory.call_args_list)
    assert rows[extra]["Protocol"] == GENERIC_ERC4626_PROTOCOL_NAME
    assert rows[extra]["NAV"] == nav
    assert rows[other] == {"unchanged": True}
    context.commit_metadata.assert_called_once()


@pytest.mark.parametrize("failure_mode", ["closure", "rebuild"])
def test_pause_cap_refresh_propagates_read_failure(monkeypatch: pytest.MonkeyPatch, failure_mode: str) -> None:
    """A failed contract read must not become a replacement catalogue value.

    1. Retain one existing capped-vault row in the migration context.
    2. Fail either the closure read or metadata rebuild, as a node failure would.
    3. Leave the row unchanged and prevent a metadata commit.
    """
    script = next(s for s in discover_migrations() if s.name == "075-vault-pause-cap-and-classification.py")
    spec = importlib.util.spec_from_file_location("pause_cap_failed_migration", script.path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    address = module.TARGETS[-1]
    timestamp = datetime.datetime(2026, 1, 1)
    detection = ERC4262VaultDetection(1, address, 1, timestamp, {ERC4626Feature.morpho_like}, timestamp, 10, 20)
    # 1. Keep a copy to detect any partial update after a failed read.
    row = {"Protocol": "Morpho", "_detection_data": detection, "NAV": 999}
    original = row.copy()
    context = Mock()
    context.metadata.return_value = SimpleNamespace(rows={VaultSpec(1, address): row})
    provider = Mock()
    provider.eth.block_number = 12345
    monkeypatch.setattr(module, "read_json_rpc_url", lambda _chain: "http://unused")
    monkeypatch.setattr(module, "create_multi_provider_web3", lambda _url: provider)
    monkeypatch.setattr(module, "detect_vault_features", lambda *_a, **_k: {ERC4626Feature.morpho_like, ERC4626Feature.vault_morpho_like})
    vault = SimpleNamespace(fetch_deposit_open=lambda: None, fetch_deposit_closed_reason=Mock(side_effect=ValueError("RPC failed") if failure_mode == "closure" else None, return_value="Cap reached"))
    monkeypatch.setattr(module, "create_vault_instance", lambda *_a, **_k: vault)
    monkeypatch.setattr(module, "create_vault_scan_record", lambda *_a, **_k: {"Protocol": "<unknown>", "_rpc_failure_category": "provider_error"})
    # 2. Mock node failure rather than depending on a live provider outage.
    with pytest.raises(ValueError, match="RPC failed|metadata rebuild failed"):
        module.migrate(context)
    # 3. Neither an invented value nor a partial repair may be committed.
    assert row == original
    context.commit_metadata.assert_not_called()
