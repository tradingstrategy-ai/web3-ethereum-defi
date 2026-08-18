"""Maintained strategy classifications for Spiko tokenised funds."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[HexAddress, set[StrategyTag]] = {
    #: Vault: Spiko EU T-Bills Money Market Fund (EUTBL) on Arbitrum One.
    #: Added: 2026-08-18.
    #: Decision material: The vault is a tokenised share in Spiko's regulated
    #: Eurozone Treasury-bill money-market fund. The underlying Treasury bills
    #: are offchain real-world assets, so both money-market-fund and RWA tags
    #: apply.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/spiko-eu-t-bills-money-market-fund
    #: - https://www.spiko.io/spiko-treasury-bills-euro
    #: - https://tech.spiko.io/posts/spiko-smart-contracts/
    #: - https://github.com/spiko-tech/contracts/blob/main/subgraph/config/arbitrum-one.json
    #: - eth_defi/tokenised_fund/spiko/constants.py
    #: - eth_defi/tokenised_fund/spiko/vault.py
    HexAddress("0xcbeb19549054cc0a6257a77736fc78c367216ce7"): {
        StrategyTag.money_market_fund,
        StrategyTag.rwa,
    },
}
