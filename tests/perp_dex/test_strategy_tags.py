"""Tests for native perpetual DEX vault strategy classifications."""

import datetime

import pytest

from eth_defi.grvt import tags as grvt_tags
from eth_defi.grvt.vault_data_export import create_grvt_vault_row
from eth_defi.hibachi import tags as hibachi_tags
from eth_defi.hibachi.vault_data_export import create_hibachi_vault_row
from eth_defi.hyperliquid import tags as hyperliquid_tags
from eth_defi.hyperliquid.vault_data_export import create_hyperliquid_vault_row
from eth_defi.lighter import tags as lighter_tags
from eth_defi.lighter.vault_data_export import create_lighter_pool_row
from eth_defi.vault.strategy_tag import StrategyTag


def test_native_perp_dex_vault_rows_have_default_strategy_tag() -> None:
    """Native perp DEX exporters persist the perpetual-futures tag."""
    timestamp = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC).replace(tzinfo=None)
    _, hyperliquid = create_hyperliquid_vault_row(
        "0x0000000000000000000000000000000000000001",
        "Hyperliquid test vault",
        None,
        1_000,
        timestamp,
    )
    _, grvt = create_grvt_vault_row("VLT:test", 1, "GRVT test vault", None, 1_000)
    _, hibachi = create_hibachi_vault_row(1, "HIB", "Hibachi test vault", None, 1_000)
    _, lighter = create_lighter_pool_row(1, "Lighter test pool", None, 1_000, timestamp)

    assert all(row["_strategy_tags"] == {StrategyTag.perpetual_futures} for row in (hyperliquid, grvt, hibachi, lighter))


def test_native_perp_dex_tag_mappings_add_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Address-specific native tags supplement the perpetual-futures default."""
    tag_mappings = (
        (hyperliquid_tags, "0x0000000000000000000000000000000000000001"),
        (grvt_tags, "vlt:test"),
        (hibachi_tags, "hibachi-vault-1"),
        (lighter_tags, "lighter-pool-1"),
    )

    for module, address in tag_mappings:
        monkeypatch.setitem(module.STRATEGY_TAGS, address, {StrategyTag.delta_neutral})
        assert module.get_strategy_tags(address.upper()) == {StrategyTag.perpetual_futures, StrategyTag.delta_neutral}
