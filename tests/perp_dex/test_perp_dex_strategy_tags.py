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

EXPECTED_GRVT_VAULT_COUNT = 26


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


def test_all_grvt_vaults_have_binary_execution_tag() -> None:
    """Every maintained GRVT vault has one algorithmic/discretionary tag."""
    execution_tags = {StrategyTag.algorithmic_trading, StrategyTag.discretionary_trading}

    assert len(grvt_tags.STRATEGY_TAGS) == EXPECTED_GRVT_VAULT_COUNT
    assert grvt_tags.NON_PERPETUAL_VAULTS <= grvt_tags.STRATEGY_TAGS.keys()

    for address, tags in grvt_tags.STRATEGY_TAGS.items():
        assert address == address.lower()
        assert len(tags & execution_tags) == 1
        expected_defaults = set() if address in grvt_tags.NON_PERPETUAL_VAULTS else {StrategyTag.perpetual_futures}
        assert grvt_tags.get_strategy_tags(address) == expected_defaults | tags


def test_grvt_rwa_bundle_vaults_do_not_receive_perpetual_futures_default() -> None:
    """Documented GRVT RWA bundle products opt out of the perp default."""
    for address in grvt_tags.NON_PERPETUAL_VAULTS:
        assert StrategyTag.perpetual_futures not in grvt_tags.get_strategy_tags(address)


def test_grvt_ai_grid_vault_is_grid_trading() -> None:
    """The AI Grid Trading vault has the specific grid-trading tag."""
    assert grvt_tags.get_strategy_tags("VLT:35jPjdfRWaEuJ0W7He4wI5vbOaG") == {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.grid_trading,
        StrategyTag.multistrategy,
        StrategyTag.perpetual_futures,
    }


def test_pmalt_native_vaults_are_algorithmic_pair_trading() -> None:
    """pmalt's Hyperliquid and Lighter vaults have their maintained tags."""
    assert hyperliquid_tags.get_strategy_tags("0x4dec0a851849056e259128464ef28ce78afa27f6") == {
        StrategyTag.algorithmic_trading,
        StrategyTag.perpetual_futures,
        StrategyTag.pair_trading,
    }
    assert lighter_tags.get_strategy_tags("lighter-pool-281474976552918") == {
        StrategyTag.algorithmic_trading,
        StrategyTag.perpetual_futures,
        StrategyTag.pair_trading,
    }


def test_growi_vaults_are_mean_reversion() -> None:
    """Growi's Hyperliquid and Hibachi vaults have mean-reversion tags."""
    expected = {StrategyTag.mean_reversion, StrategyTag.perpetual_futures}

    assert hyperliquid_tags.get_strategy_tags("0x1e37a337ed460039d1b15bd3bc489de789768d5e") == expected
    assert hibachi_tags.get_strategy_tags("hibachi-vault-2") == expected


def test_hlp_and_fire_liquidity_provider_are_market_makers() -> None:
    """Protocol liquidity pools receive both requested role tags."""
    expected = {StrategyTag.liquidity_provider, StrategyTag.market_maker, StrategyTag.perpetual_futures}

    assert hyperliquid_tags.get_strategy_tags("0xdfc24b077bc1425ad1dea75bcb6f8158e10df303") == expected
    assert hibachi_tags.get_strategy_tags("hibachi-vault-3") == expected


@pytest.mark.parametrize(
    "vault_address",
    [
        "0xdda7f4805dfdf145a74cd68992d90780f73cf6c7",
        "0xfb7b73ff7c93f5552541de37454ffa0f8b76462a",
        "0x1b03878805333a0e13d7eea4abdfa2d97977c448",
        "0x6b13de56131bfa2256e2dcd64b67c38272c72318",
        "0x15a141990fc6591838646467273c41c92999772f",
        "0x5048900eb10b569e77f515efe85f8da5cfd5fb3a",
    ],
)
def test_hyperliquid_trend_following_descriptions_are_tagged(vault_address: str) -> None:
    """Explicit trend-following descriptions receive the maintained tag."""
    assert hyperliquid_tags.get_strategy_tags(vault_address) == {
        StrategyTag.perpetual_futures,
        StrategyTag.trend_following,
    }
