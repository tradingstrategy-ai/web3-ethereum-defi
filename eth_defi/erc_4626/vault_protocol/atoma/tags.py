"""Maintained strategy classifications for Atoma vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

#: Atoma's documented strategy holds offsetting perpetual positions across
#: venues to capture funding-rate spreads. Its RWA vault applies this approach
#: to perpetual markets for traditional assets.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Extended and Nado arbitrage.
    #: Added: 2026-08-18.
    #: Decision material: Atoma describes this vault as a delta-neutral USDC
    #: strategy that holds offsetting long and short perpetual positions across
    #: Nado and Extended to capture funding-rate spreads.
    #: Sources:
    #: - https://atoma.fi/
    #: - https://arbiscan.io/address/0xCC56410e1a136aF0eCEb7241c6aE394F4d8b581c
    #: - eth_defi/erc_4626/vault_protocol/atoma/vault.py
    "0xcc56410e1a136af0eceb7241c6ae394f4d8b581c": {
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.perpetual_futures,
    },
    #: Vault: Lighter and Trade.xyz arbitrage.
    #: Added: 2026-08-18.
    #: Decision material: Atoma describes this RWA vault as a delta-neutral
    #: USDC strategy that captures funding and price spreads in gold, oil and
    #: equity-index perpetuals through offsetting Lighter and Trade.xyz
    #: positions.
    #: Sources:
    #: - https://x.com/atoma_fi/status/2079672209400832319?s=46
    #: - https://arbiscan.io/address/0x1C788E14d8e5B446e3F71B5142e2edaBcAB36da1
    #: - eth_defi/erc_4626/vault_protocol/atoma/vault.py
    "0x1c788e14d8e5b446e3f71b5142e2edabcab36da1": {
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.perpetual_futures,
        StrategyTag.rwa,
    },
}
