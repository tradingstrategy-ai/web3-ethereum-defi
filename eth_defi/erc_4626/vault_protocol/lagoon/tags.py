"""Maintained strategy classifications for Lagoon vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Kamui Stable Vault.
    #: Added: 2026-09-28.
    #: Decision material: Kamui describes Stable as holding tokenised US
    #: Treasury securities and money-market instruments.
    #: Sources:
    #: - https://www.kamui.finance/
    #: - https://www.linkedin.com/posts/kamui-finance_kamui-finance-brings-three-institutional-activity-7508163602609209344-rxYM
    "0xcda323c2df692d989b24ba51d0acca924cf9a344": {
        StrategyTag.money_market_fund,
        StrategyTag.rwa,
    },
    #: Vault: Kamui Balanced Vault.
    #: Added: 2026-09-28.
    #: Decision material: Kamui describes Balanced as diversified across
    #: asset classes. Contemporaneous launch coverage identifies its mandate
    #: as investment-grade credit, including CLOs, receivables finance,
    #: corporate bonds and commercial paper.
    #: Sources:
    #: - https://www.kamui.finance/
    #: - https://www.linkedin.com/posts/kamui-finance_kamui-finance-brings-three-institutional-activity-7508163602609209344-rxYM
    #: - https://newsletter.cryptofunds.watch/p/1b-multi-family-office-launches-systematic-xrp-strategy
    "0xa5ae405242f42c47996a0c6857ff10a77f9bdee6": {
        StrategyTag.multistrategy,
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
    },
    #: Vault: Kamui Boosted Vault.
    #: Added: 2026-09-28.
    #: Decision material: Kamui describes Boosted as combining private-credit
    #: yield with DeFi liquidity. Contemporaneous launch coverage also
    #: identifies fixed-income and reinsurance strategies in its mandate.
    #: Sources:
    #: - https://www.kamui.finance/
    #: - https://www.linkedin.com/posts/kamui-finance_kamui-finance-brings-three-institutional-activity-7508163602609209344-rxYM
    #: - https://newsletter.cryptofunds.watch/p/1b-multi-family-office-launches-systematic-xrp-strategy
    "0x9e0db8f43bb91e2148b0db920e21370525cf3aab": {
        StrategyTag.multistrategy,
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
    },
}
