"""Exact strategy expectations from the November 2026 Lagoon evidence review."""

import pytest

from eth_defi.erc_4626.vault_protocol.lagoon.tags import CHAIN_STRATEGY_TAGS, lookup_lagoon_strategy_tags
from eth_defi.vault.strategy_tag import StrategyTag


@pytest.mark.parametrize(
    ("chain_id", "address", "expected"),
    (
        (1, "0x09252d2c4afca9b1479efdd39faa53de9ff23114", "amm lending lending_looping liquidity_provider money_market_fund multistrategy rwa rwa_credit"),
        (1, "0x16b85563902556d205f5c2b4485df3d26b153f96", "lending liquidity_provider multistrategy"),
        (1, "0x23b27310451f2754de34d9c04aa24e8be367124a", "multistrategy"),
        (1, "0x28663161f9fa2963eb6102b88a741e195e974df6", "algorithmic_trading delta_neutral multistrategy"),
        (1, "0x2927c6d7f89b5ffdf5ad293b4aa6519d495cb341", "amm lending liquidity_provider multistrategy"),
        (1, "0x401550bc1ce4fb0ffdd86015f49142bbaad29ad8", "algorithmic_trading directional_trading discretionary_trading perpetual_futures"),
        (1, "0x423b469268b15821107c38d1e1f702877219bc52", "amm lending liquidity_provider multistrategy"),
        (1, "0x47b2010f3f0cb5782ad796dccc8d42b0c9062cb7", "directional_trading"),
        (1, "0x4ef75e307f7edbf0462c679192c1ff7e911c389a", "carry_trade lending lending_looping"),
        (1, "0x57e6824a8b15b709cefb4ccef644ba1349057e77", "liquidity_provider"),
        (1, "0x59b7942f7d2afd085691ce65c152e0d38d4eff22", "lending liquidity_provider multistrategy"),
        (1, "0x5a97b0b97197299456af841f8605543b13b12ee3", "algorithmic_trading"),
        (1, "0x5f9efcc03b95609346798596494cf9dbaf176312", "rwa rwa_credit rwa_lending"),
        (1, "0x6abf7b760a40c4f015803cba3cd8b430e41fba7f", "arbitrage carry_trade delta_neutral funding_rate_arbitrage perpetual_futures"),
        (1, "0x6fd4a2bd1e879803a0e083360dddf764d0bbd2df", "arbitrage carry_trade delta_neutral funding_rate_arbitrage options perpetual_futures"),
        (1, "0x7895a046b26cc07272b022a0c9bafc046e6f6396", "delta_neutral lending lending_looping multistrategy"),
        (1, "0x8092ca384d44260ea4feaf7457b629b8dc6f88f0", "arbitrage carry_trade delta_neutral"),
        (1, "0x8417430a31851ae0a36a854394227c5d86be8fc9", "amm delta_neutral lending liquidity_provider market_making market_making_amm multistrategy"),
        (1, "0x8b21fa55e3afc537de8abb74b2068746c676c828", "lending multistrategy rwa"),
        (1, "0x9b1c616f5d2d8efb451b2823a58e789dc370705f", "delta_neutral lending lending_looping liquidity_provider multistrategy"),
        (1, "0xab122081cbe59388f96e320dae77468f64512232", "amm lending liquidity_provider multistrategy"),
        (1, "0xb118de4917f4bac5c3a453ea8de915a7cd84891c", "algorithmic_trading delta_neutral multistrategy"),
        (1, "0xb34447f729abfd21b3d8997fedcb07e86268a2b3", "amm liquidity_provider"),
        (1, "0xbb211be8664128e30c6adcd5998eca9592be272f", "amm lending liquidity_provider market_making market_making_amm multistrategy"),
        (1, "0xbc6a48b30405cc1660627d8dfb3872505f14bca0", "algorithmic_trading delta_neutral multistrategy"),
        (1, "0xbca723c30d55f0915e32019a95aa29ea21fd555c", "amm lending liquidity_provider multistrategy"),
        (1, "0xc5a7884709967a51f67d2daca7451fe3b820e2e9", "lending"),
        (1, "0xd2386d8413c2ec4a761901da14c74a98ebe5054b", "carry_trade fx rwa rwa_credit rwa_lending"),
        (1, "0xd4b21ed00aee22d992ffa75f567d4419c1312fed", "arbitrage delta_neutral lending liquidity_provider multistrategy options"),
        (1, "0xd56031b6e6860bd41dce2729d1bed21c387b26ce", "amm lending liquidity_provider multistrategy"),
        (1, "0xe0dfbe4748ed96350754f1328679bd9647bf9621", "amm lending liquidity_provider multistrategy"),
        (1, "0xeff2c1cc0e3bbb6bedc9622a309ed75eab730521", "carry_trade"),
        (10, "0x6657a22a3ab82e2f46575423e74ccc1943c8005f", "algorithmic_trading amm liquidity_provider market_making market_making_amm"),
        (143, "0x0da39b740834090c146dc48357f6a435a1bb33b3", "amm lending liquidity_provider multistrategy"),
        (999, "0x2229eaf48774e6c49d8a45150b6110239f24e476", "algorithmic_trading directional_trading index lending multistrategy perpetual_futures"),
        (999, "0x3b65e02b1ff8072bdc094a899a4e41d07ce034aa", "algorithmic_trading directional_trading index lending multistrategy perpetual_futures"),
        (999, "0x8982df557d81540678285450898f77c31d69c8b2", "algorithmic_trading directional_trading lending multistrategy perpetual_futures"),
        (8453, "0x0a63471da694fc822f5a0a0b65978dda8c8edbe5", "amm liquidity_provider"),
        (8453, "0x7dd04fa502c1ae6dc81762bea2177aa38398ecee", "directional_trading lending multistrategy"),
        (8453, "0x87b433543660001cf64ad50afc51cc2a27e73e1d", "algorithmic_trading directional_trading lending multistrategy"),
        (8453, "0xb725f2277f3f62ad017a6dc418433b326a9f6334", "directional_trading discretionary_trading multistrategy"),
        (9745, "0x0c1c83c9c203f6920726a0045ff1ea95e9698d0a", "lending liquidity_provider multistrategy"),
        (9745, "0xff5ef6464896516c6ccf60da9e199680929fb01c", "carry_trade directional_trading fx multistrategy"),
        (42161, "0x647b5ad322c9173a8d3bdf8041b8bddfc456ddc4", "lending liquidity_provider multistrategy"),
        (42161, "0x7197b77e717d837c00204cb21144692b2dedaa12", "lending multistrategy rwa rwa_credit rwa_lending"),
        (42161, "0x7c43710b240d1810d9799001ec0964512ffdb077", "directional_trading discretionary_trading multistrategy options"),
        (43111, "0x1e32c96757c07775ca4fc796c4f4311722eaf35e", "amm lending liquidity_provider multistrategy"),
        (42161, "0x9e6de1f581d63129af453fe1ea7253b280b4dca8", "algorithmic_trading directional_trading discretionary_trading perpetual_futures"),
    ),
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
