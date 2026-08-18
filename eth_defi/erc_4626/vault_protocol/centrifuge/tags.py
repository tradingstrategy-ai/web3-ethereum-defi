"""Maintained strategy classifications for Centrifuge vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Janus Henderson Anemoy S&P500® Fund (SPXA) on Base.
    #: Added: 2026-08-18.
    #: Decision material: Trading Strategy describes this as a tokenised
    #: S&P 500 index-fund share class. Anemoy describes SPXA as a passively
    #: managed portfolio corresponding to the S&P 500 Index with rules-based
    #: onchain operations. The fund is an onchain representation of real-world
    #: institutional equities, supporting both index and RWA tags.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/janus-henderson-anemoy-s-p500-r-fund
    #: - https://www.anemoy.io/funds/spxa
    #: - https://app.centrifuge.io/pool/281474976710665
    #: - https://centrifuge.io/blog/despxa-on-base
    #: - https://docs.centrifuge.io/
    #: - https://basescan.org/address/0x99e9092bae6d4394e54034ecb1e45441678323b9
    #: - eth_defi/erc_4626/vault_protocol/centrifuge/vault.py
    "0x99e9092bae6d4394e54034ecb1e45441678323b9": {
        StrategyTag.index,
        StrategyTag.rwa,
    },
}
