"""Maintained strategy classifications for Securitize tokenised funds."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[HexAddress, set[StrategyTag]] = {
    #: Vault: Mantle Index Four.
    #: Added: 2026-08-18.
    #: Decision material: The vault is described as a tokenised fund providing
    #: managed exposure to a diversified basket of BTC, ETH, SOL and US dollar
    #: assets, with selected staking strategies and periodic rebalancing. This
    #: is an index-fund-style diversified basket, supporting the index tag.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/mantle-index-four
    #: - https://securitize.io/primary-market/mantle-index-four-fund
    #: - eth_defi/tokenised_fund/securitize/description.py
    #: - eth_defi/tokenised_fund/securitize/vault.py
    HexAddress("0x671642ac281c760e34251d51bc9eef27026f3b7a"): {
        StrategyTag.index,
    },
    #: Vault: BCAP.
    #: Added: 2026-08-18.
    #: Decision material: Trading Strategy describes BCAP as tokenised fund
    #: interests in Blockchain Capital's digital liquid venture fund, investing
    #: in companies building blockchain and cryptocurrency products. Blockchain
    #: Capital also identifies BCAP as its first security token and operates
    #: venture-capital funds, supporting the venture-funding tag.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/bcap
    #: - https://www.blockchaincapital.com/about-us
    #: - https://etherscan.io/address/0x1f41e42d0a9e3c0dd3ba15b527342783b43200a9
    #: - eth_defi/tokenised_fund/securitize/description.py
    #: - eth_defi/tokenised_fund/securitize/vault.py
    HexAddress("0x1f41e42d0a9e3c0dd3ba15b527342783b43200a9"): {
        StrategyTag.venture_funding,
    },
}
