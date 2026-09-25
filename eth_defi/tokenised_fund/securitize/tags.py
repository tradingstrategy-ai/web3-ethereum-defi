"""Maintained strategy classifications for Securitize tokenised funds."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
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
    "0x671642ac281c760e34251d51bc9eef27026f3b7a": {
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
    "0x1f41e42d0a9e3c0dd3ba15b527342783b43200a9": {
        StrategyTag.venture_funding,
    },
    #: Vault: ARK Venture Fund (ARKVX).
    #: Added: 2026-09-25.
    #: Decision material: The prospectus states the objective "to seek
    #: long-term growth of capital" by investing 20%-90% of assets in private
    #: companies, with 74.15% in private companies on 2026-08-31 and top
    #: positions in venture-backed firms such as SpaceX, Kalshi, Ayar Labs,
    #: OpenAI, Stripe and Anthropic. ARK markets it as its venture fund, which
    #: supports the venture-funding tag, matching BCAP.
    #: Sources:
    #: - https://www.ark-funds.com/funds/arkvx
    #: - https://www.sec.gov/Archives/edgar/data/1905088/000121390025102648/ea0260971-01_486bpos.htm
    #: - https://etherscan.io/token/0xdf1c8e71cbdf48af50b36f96ad2eb6f5094ba72a
    #: - eth_defi/tokenised_fund/securitize/description.py
    #: - eth_defi/tokenised_fund/securitize/vault.py
    "0xdf1c8e71cbdf48af50b36f96ad2eb6f5094ba72a": {
        StrategyTag.venture_funding,
    },
}
