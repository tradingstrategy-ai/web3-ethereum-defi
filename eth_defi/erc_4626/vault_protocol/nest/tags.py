"""Maintained strategy classifications for reviewed Nest vault products."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: nACRDX, USDC route. Added: 2026-10-06.
    #: Decision: Apollo's diversified global credit includes real-world loans.
    #: Source: https://app.nest.credit/vaults/nest-acrdx-vault
    "0xf991a58e1cfd4ac26843c33e0eb2adf47d341f2b": {StrategyTag.rwa, StrategyTag.rwa_credit},
    #: Vault: nACRDX, USDT route. Added: 2026-10-06.
    #: Decision: Same Apollo global-credit exposure on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nest-acrdx-vault
    "0xba1dd0dc2510a5ccfb1abb97725f0c50df95faae": {StrategyTag.rwa, StrategyTag.rwa_credit},
    #: Vault: nACRDX, Plume pUSD route. Added: 2026-10-06.
    #: Decision: Same Apollo global-credit exposure on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nest-acrdx-vault
    "0xa7d4dab9dffe2e917e49165ba4ae36969c1166b3": {StrategyTag.rwa, StrategyTag.rwa_credit},
    #: Vault: nBASIS, USDC route. Added: 2026-10-06.
    #: Decision: Bitwise's fund uses crypto basis and cash-and-carry trades.
    #: Sources: https://app.nest.credit/vaults/nest-basis-vault
    #: - https://bitwiseinvestments.com/crypto-funds/uscc
    "0x5f35d1cef957467f4c7b35b36371355170a0dbb1": {StrategyTag.arbitrage, StrategyTag.delta_neutral, StrategyTag.carry_trade},
    #: Vault: nBASIS, Plume pUSD route. Added: 2026-10-06.
    #: Decision: Same Bitwise crypto-basis strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nest-basis-vault
    "0xe74f47c1fbb7d9fbe600374cef2477166f917d0b": {StrategyTag.arbitrage, StrategyTag.delta_neutral, StrategyTag.carry_trade},
    #: Vault: nBASIS, USDT route. Added: 2026-10-06.
    #: Decision: Same Bitwise crypto-basis strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nest-basis-vault
    "0xf388e7dc2b8e264b9b4149f3981aa5208bd5f3c3": {StrategyTag.arbitrage, StrategyTag.delta_neutral, StrategyTag.carry_trade},
    #: Vault: nONYC, USDC route. Added: 2026-10-06.
    #: Decision: Underlying OnRe ONyc earns reinsurance premiums and collateral income.
    #: Sources: https://app.nest.credit/vaults/nest-onre-vault
    #: - https://docs.onre.finance/introduction/onre-tokenized-reinsurance-onyc
    "0xb483252de6c4cd540bb964b6d5ec9ae61f033a8d": {StrategyTag.rwa},
    #: Vault: nONYC, Plume pUSD route. Added: 2026-10-06.
    #: Decision: Same reinsurance strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nest-onre-vault
    "0xc48fed6a515f73715b3c05fee3867892d26f29a8": {StrategyTag.rwa},
    #: Vault: nONYC, USDT route. Added: 2026-10-06.
    #: Decision: Same reinsurance strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nest-onre-vault
    "0xa1fb1512bbf0ed85fac0f6c41455cd1f964c69c7": {StrategyTag.rwa},
    #: Vault: nWISDOM, USDC route. Added: 2026-10-06.
    #: Decision: WisdomTree private credit and alternative-income fund exposure.
    #: Source: https://app.nest.credit/vaults/nest-wisdom-vault
    "0x6330a14fc1520cfdf0834ccf23b15fd47a89a651": {StrategyTag.rwa, StrategyTag.rwa_credit},
    #: Vault: nWISDOM, Plume pUSD route. Added: 2026-10-06.
    #: Decision: Same WisdomTree credit strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nest-wisdom-vault
    "0x4ecd623f7a3fb00a81a6b224d582b3a9cc71e25b": {StrategyTag.rwa, StrategyTag.rwa_credit},
    #: Vault: nWISDOM, USDT route. Added: 2026-10-06.
    #: Decision: Same WisdomTree credit strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nest-wisdom-vault
    "0xf6d67d698c139c217cf645c285dfac183f3ca45b": {StrategyTag.rwa, StrategyTag.rwa_credit},
    #: Vault: nPRIME, USDC route. Added: 2026-10-06.
    #: Decision: Hastra allocates capital to Figure-originated home-equity lending.
    #: Source: https://www.plume.org/blog/plume-vaults-expands-access-to-figures-30b-home-equity-ecosystem-through-nprime
    "0x461f737fcc93902c380602fff20905b20da509fd": {StrategyTag.rwa, StrategyTag.rwa_lending},
    #: Vault: nPRIME, Plume pUSD route. Added: 2026-10-06.
    #: Decision: Same Hastra home-equity lending strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nprime
    "0x5d51dab15ca7879d468f156b5ce7f62be69a4235": {StrategyTag.rwa, StrategyTag.rwa_lending},
    #: Vault: nPRIME, BNB Chain USDT route. Added: 2026-10-06.
    #: Decision: Same Hastra home-equity lending strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nprime
    "0x6b511bf36d8a694916c614bec02a23117557c573": {StrategyTag.rwa, StrategyTag.rwa_lending},
    #: Vault: nPRIME, Ethereum PRIME route. Added: 2026-10-06.
    #: Decision: Same Hastra home-equity lending strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nprime
    "0xa28b27c893d01f5456ae0836a3c4c65e47877a74": {StrategyTag.rwa, StrategyTag.rwa_lending},
    #: Vault: nOPAL, USDC route. Added: 2026-10-06.
    #: Decision: Brazilian credit-card receivables financing is RWA credit.
    #: Source: https://app.nest.credit/vaults/nest-opal-vault
    "0xd258029cf5a177e3306e09fbea63424543a505c0": {StrategyTag.rwa, StrategyTag.rwa_credit},
    #: Vault: nOPAL, Plume pUSD route. Added: 2026-10-06.
    #: Decision: Same nOPAL receivables strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nest-opal-vault
    "0xfbfed42742ad40488b4888672bf077982f549a48": {StrategyTag.rwa, StrategyTag.rwa_credit},
    #: Vault: nOPAL, USDT route. Added: 2026-10-06.
    #: Decision: Same nOPAL receivables strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nest-opal-vault
    "0x5e949fa6401d7c49cdcb48e3a8bdc28f60657a2c": {StrategyTag.rwa, StrategyTag.rwa_credit},
    #: Vault: nOPAL, Robinhood USDG route. Added: 2026-10-06.
    #: Decision: Same nOPAL receivables strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nest-opal-vault
    "0x437157515e274e8b150a52d9d1fabcf701529b8f": {StrategyTag.rwa, StrategyTag.rwa_credit},
    #: Vault: FALX, USDC route. Added: 2026-10-06.
    #: Decision: The strategy lends against pledged digital-asset collateral.
    #: Source: https://app.nest.credit/vaults/nest-falconx-clo
    "0x4738386d69cf5a7ac088da2887fc0df02795c5e7": {StrategyTag.lending},
    #: Vault: FALX, Plume pUSD route. Added: 2026-10-06.
    #: Decision: Same collateralised-lending strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nest-falconx-clo
    "0x74c9db018d3767f8a4d67ec81bb805950bf54c05": {StrategyTag.lending},
    #: Vault: FALX, BNB Chain USDT route. Added: 2026-10-06.
    #: Decision: Same collateralised-lending strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nest-falconx-clo
    "0x9c9510f0f115e777cd829d4f4a4f0e3e6eb08f32": {StrategyTag.lending},
    #: Vault: FALX, Robinhood USDG route. Added: 2026-10-06.
    #: Decision: Same collateralised-lending strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/nest-falconx-clo
    "0x72097ebb2081e7ca37dec2e7c272f46b339d0109": {StrategyTag.lending},
    #: Vault: FACTOR, USDC route. Added: 2026-10-06.
    #: Decision: Asset-backed invoice financing is RWA credit.
    #: Source: https://app.nest.credit/vaults/plume-factor-vault
    "0xb195aebf42c93b50e768a97fd3087bb004b4187f": {StrategyTag.rwa, StrategyTag.rwa_credit},
    #: Vault: FACTOR, Plume pUSD route. Added: 2026-10-06.
    #: Decision: Same invoice-financing strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/plume-factor-vault
    "0xb91a67f3e962d04a01ec15285eb790368a52e656": {StrategyTag.rwa, StrategyTag.rwa_credit},
    #: Vault: FACTOR, Robinhood USDG route. Added: 2026-10-06.
    #: Decision: Same invoice-financing strategy on a separate entrypoint.
    #: Source: https://app.nest.credit/vaults/plume-factor-vault
    "0x13ad100ebb7a2d1e7c3b4797a408da25c6561895": {StrategyTag.rwa, StrategyTag.rwa_credit},
}
