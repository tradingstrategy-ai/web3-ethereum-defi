"""Maintained strategy classifications for KiloEx Hybrid Vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag

_KILOEX_TAGS: set[StrategyTag] = {
    StrategyTag.amm,
    StrategyTag.liquidity_provider,
    StrategyTag.market_maker,
    StrategyTag.market_making,
    StrategyTag.market_making_amm,
    StrategyTag.perpetual_futures,
}


STRATEGY_TAGS: dict[HexAddress, set[StrategyTag]] = {
    #: Vault: KiloEx kUSDC Hybrid Vault on Base.
    #: Added: 2026-08-18.
    #: Decision material: KiloEx identifies the Hybrid Vault as the liquidity
    #: counterparty for its perpetual DEX, with revenue shared with liquidity
    #: providers. The transferred classification therefore retains the AMM,
    #: market-making, liquidity-provider, and perpetual-futures tags.
    #: Sources:
    #: - https://docs.kiloex.io/kiloex/about-kiloex/hybrid-vault
    #: - https://app.kiloex.io/earn/chain/Base/
    #: - eth_defi/erc_4626/vault_protocol/kiloex/vault.py
    HexAddress("0x43e3e6ffb2e363e64cd480cbb7cd0cf47bc6b477"): _KILOEX_TAGS.copy(),
    #: Vault: KiloEx kUSDT Hybrid Vault on BNB Smart Chain.
    #: Added: 2026-08-18.
    #: Decision material: KiloEx identifies the Hybrid Vault as the liquidity
    #: counterparty for its perpetual DEX, with revenue shared with liquidity
    #: providers. The transferred classification therefore retains the AMM,
    #: market-making, liquidity-provider, and perpetual-futures tags.
    #: Sources:
    #: - https://docs.kiloex.io/kiloex/about-kiloex/hybrid-vault
    #: - https://app.kiloex.io/earn/chain/BNB/
    #: - eth_defi/erc_4626/vault_protocol/kiloex/vault.py
    HexAddress("0x1c3f35f7883fc4ea8c4bca1507144dc6087ad0fb"): _KILOEX_TAGS.copy(),
    #: Vault: KiloEx kREX Hybrid Vault on BNB Smart Chain.
    #: Added: 2026-08-18.
    #: Decision material: KiloEx identifies the Hybrid Vault as the liquidity
    #: counterparty for its perpetual DEX, with revenue shared with liquidity
    #: providers. The transferred classification therefore retains the AMM,
    #: market-making, liquidity-provider, and perpetual-futures tags.
    #: Sources:
    #: - https://docs.kiloex.io/kiloex/about-kiloex/hybrid-vault
    #: - https://app.kiloex.io/earn/chain/BNB/
    #: - eth_defi/erc_4626/vault_protocol/kiloex/vault.py
    HexAddress("0xa40e085d0584eed39daaa077fcc4cd153ae9a5b0"): _KILOEX_TAGS.copy(),
    #: Vault: KiloEx kBOX Hybrid Vault on BNB Smart Chain.
    #: Added: 2026-08-18.
    #: Decision material: KiloEx identifies the Hybrid Vault as the liquidity
    #: counterparty for its perpetual DEX, with revenue shared with liquidity
    #: providers. The transferred classification therefore retains the AMM,
    #: market-making, liquidity-provider, and perpetual-futures tags.
    #: Sources:
    #: - https://docs.kiloex.io/kiloex/about-kiloex/hybrid-vault
    #: - https://app.kiloex.io/earn/chain/BNB/
    #: - eth_defi/erc_4626/vault_protocol/kiloex/vault.py
    HexAddress("0x6e7a6eb5feec64bf6401a744757aba89c5c7e813"): _KILOEX_TAGS.copy(),
}
