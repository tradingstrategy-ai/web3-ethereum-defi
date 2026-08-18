"""Maintained strategy classifications for Gains Network gTrade vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

_GTRADE_TAGS: set[StrategyTag] = {
    StrategyTag.amm,
    StrategyTag.liquidity_provider,
    StrategyTag.market_maker,
    StrategyTag.market_making,
    StrategyTag.market_making_amm,
    StrategyTag.perpetual_futures,
}


STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: gTrade (Gains Network USDC) on Base.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vaults/gUSDC
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0xad20523a7dc37babc1cc74897e4977232b3d02e5": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (Gains Network BTCUSD) on Base.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0xb7cb7cb7c3cd96e251c9bf8800b9631134bbadc6": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (xVault) on HyperEVM.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vault
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0x31297b564fb8ec52a7d84cc2dee437e0992ef2b8": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (Gains Network DAI) on Polygon.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vaults/gDAI
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0x91993f2101cc758d0deb7279d41e880f7defe827": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (Gains Network ETH) on Polygon.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vault
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0x1544e1ff1a6f6bdbfb901622c12bb352a43464fb": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (Gains Network USDC) on Polygon.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vaults/gUSDC
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0x29019fe2e72e8d4d2118e8d0318bef389ffe2c81": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (hsHONEY) on Berachain.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vault
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0x6a6e4ad4a5ca14b940cd6949b1a90f947ae21c19": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (hsHONEY) on Berachain.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vault
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0xdd560bc2c98bc3fa39fcafe256249707f9b83b3c": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (gDAI) on Arbitrum.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vaults/gDAI
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0xfe3e29b3328026003a15bf0846846b03af86b537": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (Gains Network DAI) on Arbitrum.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vaults/gDAI
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0xd85e038593d7a098614721eae955ec2022b9b91b": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (Gains Network DAI) on Arbitrum.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vaults/gDAI
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0xf40808f50b8d858f3ac6d10c441bb61da4564d53": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (mUSDC) on Arbitrum.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vault
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0x992eb7040b66b13abea94e2621d4e61d5ce608bd": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (Gains Network ETH) on Arbitrum.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vault
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0x5977a9682d7af81d347cfc338c61692163a2784c": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (Gains Network USDC) on Arbitrum.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vaults/gUSDC
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (Vault Staked GNS) on Arbitrum.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vault
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0x4beef1113f968326905224d2ca272f3032a9a9f4": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (X-Cloud USD) on BNB Chain.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vault
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0xd796a9e7e30bfc1b1a9380f501430f681c31eb78": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (X-Solaris USD) on BNB Chain.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vault
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0xfb34af2138280e13b0759fd322fe63fccc7508a6": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (hsUSDT) on BNB Chain.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vault
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0x28e1afcd2d91a7f0ea49e81192599fbe1e700169": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (Staked ARA Vault) on Base.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vault
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0xd78bd3aef2e8aa7820fea8ffb33eddc4f13fa933": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (hsUSD1) on BNB Chain.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vault
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0x1e98b6143a4eaf78ab63de8ea8186eec3dbe5edc": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (Gains Network USDM) on MegaETH.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vaults/gUSDM
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0x46344456f130e9dcdea7f98cdb0e02fb9f4ab72d": _GTRADE_TAGS.copy(),
    #: Vault: gTrade (Gains Network USDC (staging)) on Arbitrum.
    #: Added: 2026-08-18.
    #: Decision material: The database identifies this as a Gains Network
    #: gTrade gToken vault. Gains documents that gToken vaults provide the
    #: trading collateral, serve as counterparty to every trade, and receive
    #: trading fees. The gTrade engine uses a single liquidity pool per vault
    #: instead of an order book. This supports liquidity-provider, market-maker,
    #: market-making, AMM, and perpetual-futures tags; market-making-AMM is
    #: applied to every Gains Network vault as requested.
    #: Sources:
    #: - https://gains.trade/vaults/gUSDC
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    #: - https://docs.gains.trade/gtrade-leveraged-trading/overview
    #: - eth_defi/erc_4626/vault_protocol/gains/vault.py
    "0xb7058370db10f0712eddb297bc3a58c3a2e5c3a7": _GTRADE_TAGS.copy(),
}
