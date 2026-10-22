"""Maintained strategy classifications for Accountable vaults on Monad."""

from eth_defi.vault.strategy_tag import StrategyTag

#: Chain containing the reviewed Accountable deployments below.
MONAD_CHAIN_ID = 143

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Glasgow Cosmic by Ryllo, Monad (143).
    #: Added: 2026-10-22.
    #: Decision material: The vault finances the cricket team's working capital
    #: through credit secured by its real-world receivables. Accountable's
    #: cached metadata identifies Ryllo as the manager and describes this
    #: strategy; the current public vault listing corroborates the description.
    #: Sources:
    #: - https://yield.accountable.capital/vaults/0x44DF31Ec2F1dD517268929903061a47edb7fbD1f
    #: - https://tradingstrategy.ai/vaults/glasgow-cosmic-by-ryllo
    #: - https://ryllo.co/
    "0x899e270df335287f687fdac8a9089059b042044f": {
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
        StrategyTag.rwa_lending,
    },
}
