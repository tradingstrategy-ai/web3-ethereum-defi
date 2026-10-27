"""Maintained strategy tags for Asseto's RoyaltyVault variant.

Registry fund products retain their existing classification. This mapping
covers only the separately reviewed IP Vault strategy.
"""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: IP Vault on Soneium. Added 2026-10-27.
    #: Tokenised interest in a diversified royalty fund; no evidence of lending.
    #: https://ipusd.gitbook.io/ipusd-docs/general/the-underlying-fund-and-strategy
    #: https://x.com/soneium/status/2114999606400692706
    "0x2ba3fc5bdbbe7be2630fb71e759739625246bcd9": {StrategyTag.rwa, StrategyTag.rwa_royalties},
}
