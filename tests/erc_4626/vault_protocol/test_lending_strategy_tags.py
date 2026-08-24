"""Tests for automatic lending strategy classifications."""

from typing import Any

import pytest
from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.aave import tags as aave_tags
from eth_defi.erc_4626.vault_protocol.aave.vault import AaveVault
from eth_defi.erc_4626.vault_protocol.euler.vault import EulerEarnVault, EulerVault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v1 import MorphoV1Vault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.vault.strategy_tag import StrategyTag

TEST_VAULT_ADDRESS = HexAddress("0x000000000000000000000000000000000000f00d")


def _make_vault(vault_type: type) -> Any:
    """Create an adapter without invoking an RPC-backed constructor."""
    vault = object.__new__(vault_type)
    vault.__dict__["vault_address"] = TEST_VAULT_ADDRESS
    return vault


def test_lending_protocols_get_automatic_lending_tag() -> None:
    """Aave, Euler and both Morpho versions always include lending."""
    for vault_type in (AaveVault, EulerVault, EulerEarnVault, MorphoV1Vault, MorphoV2Vault):
        vault = _make_vault(vault_type)
        assert vault.get_strategy_tags() == {StrategyTag.lending}


def test_manual_aave_tags_are_added_to_automatic_lending(monkeypatch: pytest.MonkeyPatch) -> None:
    """Protocol-maintained tags are preserved alongside the default tag."""
    monkeypatch.setitem(aave_tags.STRATEGY_TAGS, TEST_VAULT_ADDRESS, {StrategyTag.algorithmic_trading})
    vault = _make_vault(AaveVault)

    assert vault.get_strategy_tags() == {
        StrategyTag.algorithmic_trading,
        StrategyTag.lending,
    }


def test_3f_steakhouse_usdc_has_rwa_lending_tags() -> None:
    """3F's reviewed Morpho vault is classified as RWA-backed lending."""

    vault = _make_vault(MorphoV2Vault)
    vault.vault_address = HexAddress("0xBEEf3f3A04e28895f3D5163d910474901981183D")

    assert vault.get_strategy_tags() == {
        StrategyTag.lending,
        StrategyTag.rwa,
        StrategyTag.rwa_lending,
    }
