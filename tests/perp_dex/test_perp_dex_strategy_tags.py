"""Tests for native perpetual DEX vault strategy classifications."""

import datetime
from collections.abc import Callable

import pytest

from eth_defi.apex import tags as apex_tags
from eth_defi.apex.vault_data_export import create_apex_vault_row
from eth_defi.erc_4626.vault_protocol.morpho import tags as morpho_tags
from eth_defi.grvt import tags as grvt_tags
from eth_defi.grvt.vault_data_export import create_grvt_vault_row
from eth_defi.hibachi import tags as hibachi_tags
from eth_defi.hibachi.vault_data_export import create_hibachi_vault_row
from eth_defi.hyperliquid import tags as hyperliquid_tags
from eth_defi.hyperliquid.vault_data_export import create_hyperliquid_vault_row
from eth_defi.lighter import tags as lighter_tags
from eth_defi.lighter.vault_data_export import create_lighter_pool_row
from eth_defi.vault.strategy_tag import STRATEGY_TAG_METADATA, StrategyTag

EXPECTED_GRVT_VAULT_COUNT = 26
EXPECTED_LIGHTER_STRATEGY_TAGGED_VAULT_COUNT = 233
EXPECTED_LIGHTER_DIRECTIONAL_LEVERAGE_VAULT_COUNT = 194


def test_market_making_is_the_only_general_market_tag() -> None:
    """Canonicalise retired market-making tag values from old metadata."""
    assert StrategyTag("market_maker") is StrategyTag.market_making
    assert "market_maker" not in {tag.value for tag in StrategyTag}
    assert StrategyTag.market_making_clob.value == "market_making_clob"
    assert STRATEGY_TAG_METADATA[StrategyTag.market_making_clob]["label"] == "Orderbook market making"


def test_native_perp_dex_vault_rows_have_default_strategy_tag() -> None:
    """Native perp DEX exporters persist the perpetual-futures tag."""
    timestamp = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC).replace(tzinfo=None)
    _, apex = create_apex_vault_row(
        "test",
        name="ApeX test vault",
        description=None,
        tvl=1_000,
        share_count=1_000,
        created_at=timestamp,
        first_seen=timestamp,
        status="normal",
    )
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

    assert apex["_strategy_tags"] == {StrategyTag.discretionary_trading, StrategyTag.perpetual_futures}
    assert all(row["_strategy_tags"] == {StrategyTag.perpetual_futures} for row in (hyperliquid, grvt, hibachi, lighter))


def test_apex_official_vaults_are_market_making() -> None:
    """ApeX's official liquidation-fee vaults have their documented tags."""
    expected = {
        StrategyTag.liquidity_provider,
        StrategyTag.market_making,
        StrategyTag.market_making_clob,
        StrategyTag.perpetual_futures,
    }

    assert apex_tags.get_strategy_tags("apex-vault-10000") == expected
    assert apex_tags.get_strategy_tags("apex-vault-10001") == expected


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


def test_grvt_grid_vaults_are_grid_trading() -> None:
    """GRVT vaults with explicit grid descriptions receive the tag."""
    assert grvt_tags.get_strategy_tags("VLT:35jPjdfRWaEuJ0W7He4wI5vbOaG") == {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.grid_trading,
        StrategyTag.multistrategy,
        StrategyTag.perpetual_futures,
    }
    assert grvt_tags.get_strategy_tags("VLT:32Aef5QKjhKibVf8inQq9OQsYEP") == {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.grid_trading,
        StrategyTag.mean_reversion,
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
    expected = {
        StrategyTag.algorithmic_trading,
        StrategyTag.high_frequency_high_win_rate,
        StrategyTag.mean_reversion,
        StrategyTag.perpetual_futures,
    }

    assert hyperliquid_tags.get_strategy_tags("0x1e37a337ed460039d1b15bd3bc489de789768d5e") == expected
    assert hibachi_tags.get_strategy_tags("hibachi-vault-2") == expected


def test_hyperliquid_statistical_arbitrage_vaults_are_tagged() -> None:
    """Hyperliquid statistical strategies receive the maintained tag."""
    expected = {StrategyTag.perpetual_futures, StrategyTag.statistical_arbitrage}

    assert hyperliquid_tags.get_strategy_tags("0xf085dbd3f4cda645be4884c9d4c1af9cd1303591") == expected
    assert hyperliquid_tags.get_strategy_tags("0xdb25411e42659d910136dbe9c0f8330d952b5df8") == expected


def test_systemic_strategies_ls_grids_is_directional_grid_trading() -> None:
    """Systemic Strategies L/S Grids receives its description-backed tags."""
    assert hyperliquid_tags.get_strategy_tags("0x07fd993f0fa3a185f7207adccd29f7a87404689d") == {
        StrategyTag.directional_trading,
        StrategyTag.grid_trading,
        StrategyTag.perpetual_futures,
    }


@pytest.mark.parametrize(
    ("vault_address", "specific_tags"),
    [
        (
            "0x30f14b7169c657a03d0b6c722b969bee04b8f642",
            {StrategyTag.algorithmic_trading, StrategyTag.grid_trading},
        ),
        (
            "0xbe9ee55bc95b43b6a31bad63d5934492b99c6a87",
            {StrategyTag.directional_trading, StrategyTag.grid_trading},
        ),
        ("0xd2f03635901956b950737bbf02463dfad9f2e9e1", {StrategyTag.algorithmic_trading, StrategyTag.grid_trading, StrategyTag.high_frequency_high_win_rate}),
        ("0x3a6747c8e913085e243a2c22d188dafa8c6a612a", {StrategyTag.algorithmic_trading, StrategyTag.grid_trading, StrategyTag.high_frequency_high_win_rate}),
        ("0x73f6553d3a6b570ab37957b32a75c7fc0ebff6e9", {StrategyTag.algorithmic_trading, StrategyTag.grid_trading, StrategyTag.high_frequency_high_win_rate}),
        (
            "0x7833b1d61c016fefaa52a1da509b6daa2fbfd71b",
            {StrategyTag.algorithmic_trading, StrategyTag.grid_trading},
        ),
        ("0xa91dc75e17795cf4c0e4e5b4fc29d3f07432b895", {StrategyTag.grid_trading}),
        (
            "0x3dc8751d34ac4e5786e9cd1c52a001d2fe58dc37",
            {
                StrategyTag.algorithmic_trading,
                StrategyTag.directional_trading,
                StrategyTag.grid_trading,
            },
        ),
    ],
)
def test_hyperliquid_grid_descriptions_are_tagged(vault_address: str, specific_tags: set[StrategyTag]) -> None:
    """Explicit Hyperliquid grid descriptions receive all supported tags."""
    assert hyperliquid_tags.get_strategy_tags(vault_address) == specific_tags | {StrategyTag.perpetual_futures}


def test_stratwise_spot_vault_strategy_tags() -> None:
    """Stratwise's spot-only vault receives its address-scoped tags."""
    assert hyperliquid_tags.get_strategy_tags("0x0ff219ac20596b457558341bc410bc7a08a1394c") == {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.grid_trading,
        StrategyTag.high_frequency_high_win_rate,
        StrategyTag.mean_reversion,
    }


def test_lighter_grid_description_is_tagged() -> None:
    """The long-only Lighter grid pool receives its supported tags."""
    assert lighter_tags.get_strategy_tags("lighter-pool-281474976552443") == {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
        StrategyTag.grid_trading,
        StrategyTag.high_frequency_high_win_rate,
        StrategyTag.perpetual_futures,
    }


@pytest.mark.parametrize(
    ("resolve_tags", "address"),
    [
        pytest.param(hyperliquid_tags.get_strategy_tags, "0x1e37a337ed460039d1b15bd3bc489de789768d5e", id="growi-hf"),
        pytest.param(hyperliquid_tags.get_strategy_tags, "0x9b5b1931fa5838a891973f9877c7648c93ee80cc", id="growi-hf-2"),
        pytest.param(hibachi_tags.get_strategy_tags, "hibachi-vault-2", id="growi-alpha"),
        pytest.param(hyperliquid_tags.get_strategy_tags, "0xc3b1bf1f1e6fb8161ef4dc2f34e2c56f70b94b11", id="cold-process"),
        pytest.param(hyperliquid_tags.get_strategy_tags, "0xd2f03635901956b950737bbf02463dfad9f2e9e1", id="gucky-1coin"),
        pytest.param(hyperliquid_tags.get_strategy_tags, "0x3a6747c8e913085e243a2c22d188dafa8c6a612a", id="gucky-2coin"),
        pytest.param(hyperliquid_tags.get_strategy_tags, "0x73f6553d3a6b570ab37957b32a75c7fc0ebff6e9", id="gucky-4coin"),
        pytest.param(lighter_tags.get_strategy_tags, "lighter-pool-281474976552443", id="steady-wealth-builder"),
        pytest.param(lighter_tags.get_strategy_tags, "lighter-pool-281474976620676", id="candle-effect-v1"),
        pytest.param(lighter_tags.get_strategy_tags, "lighter-pool-281474976543116", id="candle-effect-v2"),
        pytest.param(hyperliquid_tags.get_strategy_tags, "0xa415c4ded17cac76a1d81a54d9f4aeb48ffd4dd0", id="bitrocket"),
        pytest.param(hyperliquid_tags.get_strategy_tags, "0x0ff219ac20596b457558341bc410bc7a08a1394c", id="stratwise"),
    ],
)
def test_high_frequency_high_win_rate_vaults(resolve_tags: Callable[[str], set[StrategyTag]], address: str) -> None:
    """Tag every confirmed vault without changing its earlier classifications.

    The new category supplements the maintained mapping and native defaults,
    including Stratwise's existing spot-only exception.

    :param resolve_tags: Protocol resolver used by the native exporter.
    :param address: Confirmed vault address or synthetic pool identifier.
    :return: ``None`` after checking the complete classification.
    """
    new_tag = StrategyTag.high_frequency_high_win_rate
    previous_tags = {
        "0x1e37a337ed460039d1b15bd3bc489de789768d5e": {StrategyTag.algorithmic_trading, StrategyTag.mean_reversion},
        "hibachi-vault-2": {StrategyTag.algorithmic_trading, StrategyTag.mean_reversion},
        "0xd2f03635901956b950737bbf02463dfad9f2e9e1": {StrategyTag.grid_trading},
        "0x3a6747c8e913085e243a2c22d188dafa8c6a612a": {StrategyTag.grid_trading},
        "0x73f6553d3a6b570ab37957b32a75c7fc0ebff6e9": {StrategyTag.grid_trading},
        "lighter-pool-281474976552443": {StrategyTag.directional_trading, StrategyTag.grid_trading},
        "0x0ff219ac20596b457558341bc410bc7a08a1394c": {
            StrategyTag.algorithmic_trading,
            StrategyTag.directional_trading,
            StrategyTag.grid_trading,
            StrategyTag.mean_reversion,
        },
    }.get(address, set())
    defaults = set() if address in hyperliquid_tags.NON_PERPETUAL_VAULTS else {StrategyTag.perpetual_futures}

    assert resolve_tags(address) == previous_tags | defaults | {StrategyTag.algorithmic_trading, new_tag}


def test_high_frequency_high_win_rate_requires_algorithmic_trading() -> None:
    """Require the algorithmic parent tag for every high-frequency entry.

    The category always describes programmed trading. Check all maintained
    native mappings so future entries cannot silently omit its parent tag.

    :return: ``None`` after checking the classification invariant.
    """
    for module in (apex_tags, grvt_tags, hibachi_tags, hyperliquid_tags, lighter_tags):
        for address, tags in module.STRATEGY_TAGS.items():
            if StrategyTag.high_frequency_high_win_rate in tags:
                assert StrategyTag.algorithmic_trading in tags, f"{module.__name__}: {address} must include algorithmic_trading"


@pytest.mark.parametrize(
    ("resolve_tags", "address"),
    [
        pytest.param(hyperliquid_tags.get_strategy_tags, "0x0512943ce80e9ff832c3b4a3b25df9e9ed64d911", id="growi-test"),
        pytest.param(hyperliquid_tags.get_strategy_tags, "0x15be61aef0ea4e4dc93c79b668f26b3f1be75a66", id="hypertwin"),
        pytest.param(morpho_tags.get_strategy_tags, "0x54e24c904cfc563af7a1ee9dfaf1354d034e44e4", id="growi-lending"),
        pytest.param(morpho_tags.get_strategy_tags, "0x2c8cb6b6aa7a4c8d68863d4cab8d5967d73d3c9e", id="growi-usdt-lending"),
        pytest.param(morpho_tags.get_strategy_tags, "0x2b9bd297c98f7c199fb5b4a2ee5a851434cb5aea", id="growi-metavault"),
        pytest.param(apex_tags.get_strategy_tags, "apex-vault-1905364278131687424", id="apex-candle-effect"),
        pytest.param(apex_tags.get_strategy_tags, "apex-vault-1909694993409376256", id="apex-candle-effect-1-0"),
        pytest.param(apex_tags.get_strategy_tags, "apex-vault-1945398400010813440", id="apex-candle-effect-lightning"),
        pytest.param(apex_tags.get_strategy_tags, "apex-vault-1947683078407323648", id="apex-candle-effect-fire"),
    ],
)
def test_high_frequency_high_win_rate_excludes_other_variants(resolve_tags: Callable[[str], set[StrategyTag]], address: str) -> None:
    """Keep related names outside the explicitly selected vault scope.

    Growi test, copycat and lending products and ApeX Candle Effect vaults
    retain their existing classifications without acquiring the new category.

    :param resolve_tags: Protocol strategy resolver.
    :param address: Excluded vault address or synthetic identifier.
    :return: ``None`` after checking the category is absent.
    """
    assert StrategyTag.high_frequency_high_win_rate not in resolve_tags(address)


def test_lighter_auto_rebalanced_pools_are_directional_leverage() -> None:
    """Lighter's fixed 2x pools are direct leveraged exposure, not trade intelligence."""
    expected = {
        StrategyTag.directional_trading,
        StrategyTag.directional_leverage,
        StrategyTag.perpetual_futures,
    }

    #: ADA 2x long is the documented representative direct-leverage pool.
    assert lighter_tags.get_strategy_tags("lighter-pool-281474976708413") == expected
    assert len(lighter_tags.DIRECTIONAL_LEVERAGE_VAULTS) == EXPECTED_LIGHTER_DIRECTIONAL_LEVERAGE_VAULT_COUNT
    assert all(lighter_tags.get_strategy_tags(address) == expected for address in lighter_tags.DIRECTIONAL_LEVERAGE_VAULTS)


def test_lighter_documented_delta_neutral_grid_pool_is_tagged() -> None:
    """Shrimp Liquidity Provider receives every tag stated in its description."""
    assert lighter_tags.get_strategy_tags("lighter-pool-281474976666704") == {
        StrategyTag.algorithmic_trading,
        StrategyTag.delta_neutral,
        StrategyTag.grid_trading,
        StrategyTag.perpetual_futures,
    }


def test_all_lighter_strategy_mapping_entries_use_supported_resolvers() -> None:
    """Every maintained Lighter classification augments the native default."""
    assert len(lighter_tags.STRATEGY_TAGS) == EXPECTED_LIGHTER_STRATEGY_TAGGED_VAULT_COUNT

    for address, specific_tags in lighter_tags.STRATEGY_TAGS.items():
        assert address == address.lower()
        assert lighter_tags.get_strategy_tags(address) == specific_tags | {StrategyTag.perpetual_futures}


def test_hlp_and_fire_liquidity_provider_are_market_making() -> None:
    """Protocol liquidity pools receive consistent market-making tags."""
    expected = {
        StrategyTag.liquidity_provider,
        StrategyTag.market_making,
        StrategyTag.market_making_clob,
        StrategyTag.perpetual_futures,
    }

    assert hyperliquid_tags.get_strategy_tags("0xdfc24b077bc1425ad1dea75bcb6f8158e10df303") == expected
    assert hibachi_tags.get_strategy_tags("hibachi-vault-3") == expected


def test_native_perp_liquidity_providers_are_market_making_clob() -> None:
    """Native perpetual-DEX liquidity-provider mappings operate on CLOBs."""
    for module in (apex_tags, grvt_tags, hibachi_tags, hyperliquid_tags, lighter_tags):
        for address, tags in module.STRATEGY_TAGS.items():
            if StrategyTag.liquidity_provider in tags:
                assert StrategyTag.market_making_clob in module.get_strategy_tags(address)


@pytest.mark.parametrize(
    ("vault_address", "execution_tag"),
    [
        ("0xdda7f4805dfdf145a74cd68992d90780f73cf6c7", StrategyTag.algorithmic_trading),
        ("0xfb7b73ff7c93f5552541de37454ffa0f8b76462a", StrategyTag.discretionary_trading),
        ("0x1b03878805333a0e13d7eea4abdfa2d97977c448", StrategyTag.algorithmic_trading),
        ("0x6b13de56131bfa2256e2dcd64b67c38272c72318", None),
        ("0x15a141990fc6591838646467273c41c92999772f", StrategyTag.algorithmic_trading),
        ("0x5048900eb10b569e77f515efe85f8da5cfd5fb3a", StrategyTag.algorithmic_trading),
    ],
)
def test_hyperliquid_trend_following_descriptions_are_tagged(vault_address: str, execution_tag: StrategyTag | None) -> None:
    """Explicit trend-following descriptions receive the maintained tag."""
    expected = {
        StrategyTag.perpetual_futures,
        StrategyTag.trend_following,
    }
    if execution_tag is not None:
        expected.add(execution_tag)

    assert hyperliquid_tags.get_strategy_tags(vault_address) == expected
