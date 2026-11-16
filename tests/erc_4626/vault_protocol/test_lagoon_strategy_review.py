"""Exact strategy expectations from the November 2026 Lagoon evidence review."""

import pytest

from eth_defi.erc_4626.vault_protocol.lagoon.tags import CHAIN_STRATEGY_TAGS, lookup_lagoon_strategy_tags
from eth_defi.vault.strategy_tag import StrategyTag


@pytest.mark.parametrize(
    ("chain_id", "address", "expected"),
    [
        (1, "0x09252d2c4afca9b1479efdd39faa53de9ff23114", "amm lending lending_looping liquidity_provider money_market_fund multistrategy rwa rwa_credit"),
        (1, "0x28663161f9fa2963eb6102b88a741e195e974df6", "algorithmic_trading delta_neutral multistrategy"),
        (1, "0x6abf7b760a40c4f015803cba3cd8b430e41fba7f", "arbitrage carry_trade delta_neutral funding_rate_arbitrage perpetual_futures"),
        (143, "0x0da39b740834090c146dc48357f6a435a1bb33b3", "amm lending liquidity_provider multistrategy"),
        (999, "0x2229eaf48774e6c49d8a45150b6110239f24e476", "algorithmic_trading directional_trading index lending multistrategy perpetual_futures"),
        (8453, "0x0a63471da694fc822f5a0a0b65978dda8c8edbe5", "amm liquidity_provider"),
        (8453, "0xb725f2277f3f62ad017a6dc418433b326a9f6334", "directional_trading discretionary_trading multistrategy"),
        (42161, "0x647b5ad322c9173a8d3bdf8041b8bddfc456ddc4", "lending liquidity_provider multistrategy"),
    ],
)
def test_reviewed_lagoon_strategy(chain_id: int, address: str, expected: str) -> None:
    """Match the researched tag set and preserve deployment boundaries.

    Expectations are frozen independently from the maintained resolver table.
    These tests need neither RPC access nor the live provider.

    :param chain_id: Reviewed deployment chain.
    :param address: Exact reviewed vault address.
    :param expected: Space-separated canonical strategy identifiers.
    :return: ``None`` after checking the exact set and copy isolation.
    """
    tags = lookup_lagoon_strategy_tags(chain_id, address.upper())
    assert tags == {StrategyTag(tag) for tag in expected.split()}
    if (chain_id, address) in CHAIN_STRATEGY_TAGS:
        assert lookup_lagoon_strategy_tags(31337, address) is None
    tags.clear()
    assert lookup_lagoon_strategy_tags(chain_id, address)
