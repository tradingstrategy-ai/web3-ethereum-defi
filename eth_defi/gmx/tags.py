"""Maintained strategy classifications for gmx vaults."""

from collections.abc import Collection

from eth_defi.vault.strategy_tag import StrategyTag

#: Swap-only GM pools whose two pool tokens are both stablecoins. They have no
#: index market, so liquidity providers earn swap fees without backing traders.
SWAP_STABLECOIN_TAGS = frozenset({StrategyTag.amm_stablecoin, StrategyTag.liquidity_provider, StrategyTag.market_making, StrategyTag.market_making_amm})

#: GLV products backed only by stablecoins. Their GM markets back perpetual
#: traders, so they also carry the perpetual-futures tag.
GLV_STABLECOIN_TAGS = SWAP_STABLECOIN_TAGS | {StrategyTag.perpetual_futures}

#: Address-specific strategy classifications supported by official sources.
#:
#: Single-market GM pools backed only by USDG, such as GM HYPE [USDG-USDG], are
#: deliberately not tagged ``amm_stablecoin``: they have concentrated exposure
#: to one market's trader profit and loss. The ``three_months_volatility``
#: field of https://top-defi-vaults.tradingstrategy.ai/top_vaults_by_chain.json,
#: downloaded on 2026-11-12, gave annualised volatility of 14.5%, 15.0% and
#: 18.1% for the HYPE, NEAR and ZEC pools over about one month of history,
#: against 0.9% for GLV [USDG-USDG].
STRATEGY_TAGS: dict[str, Collection[StrategyTag]] = {
    #: Vault: GM SKY [WETH-USDC] (chain 42161).
    #: Added: 2026-10-21.
    #: Decision material: Native GM pool; SKY is the index market, not the curator.
    #: Sources:
    #: - https://docs.gmx.io/docs/providing-liquidity/
    "0x00310c6d8a9f821e3fe991f4835f2ca4d87034cf": {StrategyTag.liquidity_provider, StrategyTag.market_making, StrategyTag.market_making_amm, StrategyTag.perpetual_futures},
    #: Vault: GLV [USDG-USDG] (chain 42161).
    #: Added: 2026-11-12.
    #: Decision material: GMX Liquidity Vault whose only long and short
    #: backing token is USDG. It allocates liquidity across USDG-backed GM
    #: perpetual markets (BTC, ETH, SOL, NEAR, XRP, ZEC and HYPE in November
    #: 2026) and earns trading, borrowing, liquidation and swap fees. No pool
    #: asset is volatile, so it is the reference example for amm_stablecoin;
    #: it still bears net trader profit and loss across those markets.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/glv-usdg-usdg
    #: - https://app.gmx.io/#/pools/details?market=0x4cd5a94a30876320ac65f2e192493ee476f13866&operation=Deposit&chainId=42161
    #: - https://docs.gmx.io/docs/providing-liquidity/
    "0x4cd5a94a30876320ac65f2e192493ee476f13866": GLV_STABLECOIN_TAGS,
    #: Vault: GM swap [USDC-USDG] (chain 42161).
    #: Added: 2026-11-12.
    #: Decision material: Swap-only GM pool with no index market, whose two
    #: pool tokens are both stablecoins. It earns swap fees between them and
    #: does not back perpetual traders.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/gm-swap-usdc-usdg
    #: - https://docs.gmx.io/docs/providing-liquidity/
    "0x408e8e83d4b8ac4bdeffdf896b49268a5fe5ef44": SWAP_STABLECOIN_TAGS,
    #: Vault: GM swap [USDC-USDC.e] (chain 42161).
    #: Added: 2026-11-12.
    #: Decision material: Swap-only GM pool with no index market, whose two
    #: pool tokens are both stablecoins. It earns swap fees between them and
    #: does not back perpetual traders.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/gm-swap-usdc-usdc-e
    #: - https://docs.gmx.io/docs/providing-liquidity/
    "0x9c2433dfd71096c435be9465220bb2b189375ea7": SWAP_STABLECOIN_TAGS,
    #: Vault: GM swap [USDC-USD₮0] (chain 42161).
    #: Added: 2026-11-12.
    #: Decision material: Swap-only GM pool with no index market, whose two
    #: pool tokens are both stablecoins. It earns swap fees between them and
    #: does not back perpetual traders.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/gm-swap-usdc-usdt0
    #: - https://docs.gmx.io/docs/providing-liquidity/
    "0xb686bcb112660343e6d15bdb65297e110c8311c4": SWAP_STABLECOIN_TAGS,
    #: Vault: GM swap [USDC-DAI] (chain 42161).
    #: Added: 2026-11-12.
    #: Decision material: Swap-only GM pool with no index market, whose two
    #: pool tokens are both stablecoins. It earns swap fees between them and
    #: does not back perpetual traders. The pool is
    #: disabled for new deposits; the classification describes its strategy.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/gm-swap-usdc-dai
    #: - https://docs.gmx.io/docs/providing-liquidity/
    "0xe2fedb9e6139a182b98e7c2688ccfa3e9a53c665": SWAP_STABLECOIN_TAGS,
    #: Vault: GM swap [USDe-USDC] (chain 42161).
    #: Added: 2026-11-12.
    #: Decision material: Swap-only GM pool with no index market, whose two
    #: pool tokens are both stablecoins. It earns swap fees between them and
    #: does not back perpetual traders. USDe is a
    #: synthetic dollar with its own depeg risk. The pool is disabled for new
    #: deposits; the classification describes its strategy.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/gm-swap-usde-usdc
    #: - https://docs.gmx.io/docs/providing-liquidity/
    "0x45ad16aaa28fb66ef74d5ca0ab9751f2817c81a4": SWAP_STABLECOIN_TAGS,
    #: Vault: GM swap [USDC-USDC.e] (chain 43114).
    #: Added: 2026-11-12.
    #: Decision material: Swap-only GM pool with no index market, whose two
    #: pool tokens are both stablecoins. It earns swap fees between them and
    #: does not back perpetual traders.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/gm-swap-usdc-usdc-e-2
    #: - https://docs.gmx.io/docs/providing-liquidity/
    "0x297e71a931c5825867e8fb937ae5cda9891c2e99": SWAP_STABLECOIN_TAGS,
    #: Vault: GM swap [USDt-USDT.e] (chain 43114).
    #: Added: 2026-11-12.
    #: Decision material: Swap-only GM pool with no index market, whose two
    #: pool tokens are both stablecoins. It earns swap fees between them and
    #: does not back perpetual traders.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/gm-swap-usdt-usdt-e
    #: - https://docs.gmx.io/docs/providing-liquidity/
    "0xa7b768d6a1f746fd5a513d440df2970ff099b0fc": SWAP_STABLECOIN_TAGS,
    #: Vault: GM swap [USDC-USDT.e] (chain 43114).
    #: Added: 2026-11-12.
    #: Decision material: Swap-only GM pool with no index market, whose two
    #: pool tokens are both stablecoins. It earns swap fees between them and
    #: does not back perpetual traders.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/gm-swap-usdc-usdt-e
    #: - https://docs.gmx.io/docs/providing-liquidity/
    "0xf3652eba45dc761e7add4091627d5cda21f61613": SWAP_STABLECOIN_TAGS,
}
