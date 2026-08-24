"""Test maintained Enzyme vault strategy classifications without RPC access."""

from eth_typing import HexAddress

from eth_defi.enzyme.tags import get_strategy_tags
from eth_defi.vault.strategy_tag import StrategyTag


def test_enzyme_blue_documented_strategies_have_exact_tags() -> None:
    """Resolve exact tags for representative Blue manager descriptions.

    These addresses cover quantitative directional trading, automated
    market-maker liquidity provision, and the combined GMX/Uniswap strategy.
    The resolver is intentionally local and must not need an RPC provider.

    :return: None after exact maintained classifications are resolved.
    """

    assert get_strategy_tags(HexAddress("0xd89551d350532d001ad3105968fecb24b1c3cec8")) == {
        StrategyTag.algorithmic_trading,
        StrategyTag.directional_trading,
    }
    assert get_strategy_tags(HexAddress("0x4134d87c87df974577c580561b4776c2ab70cb43")) == {
        StrategyTag.amm,
        StrategyTag.delta_neutral,
        StrategyTag.liquidity_provider,
        StrategyTag.mean_reversion,
    }
    assert get_strategy_tags(HexAddress("0x6b303b674b65ca0e24fed4c47dc2474f238eb2cb")) == {
        StrategyTag.amm,
        StrategyTag.directional_trading,
        StrategyTag.discretionary_trading,
        StrategyTag.liquidity_provider,
        StrategyTag.multistrategy,
        StrategyTag.perpetual_futures,
    }


def test_enzyme_blue_undocumented_strategy_remains_unmapped() -> None:
    """Keep an eligible Blue listing without strategy detail unclassified.

    A meaningful description alone is not a classification: no manager
    statement supports any available strategy tag for this vault.

    :return: None after confirming no tag is inferred.
    """

    assert get_strategy_tags(HexAddress("0xb8f69b26316818db0ea3b6d1639fedf744a2df41")) is None
