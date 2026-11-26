"""Maintained strategy classifications for Upshift vaults."""

from eth_defi.vault.strategy_tag import StrategyTag

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Axis Origin USDx.
    #: Added: 2026-08-17.
    #: Decision material: Axis describes its engine as a delta-neutral,
    #: cross-venue arbitrage strategy with multiple uncorrelated return sources;
    #: maintainer classification also marks it as algorithmic trading.
    #: Sources:
    #: - https://app.axis.to/origin
    #: - docs/protocol-research/axis-origin-0xad958c4c0c90bf0216e0f5472f074a9ab30f595f.md
    "0xad958c4c0c90bf0216e0f5472f074a9ab30f595f": {
        StrategyTag.algorithmic_trading,
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.multistrategy,
    },
    #: Vault: earnAUSD Loop (Monad).
    #: Added: 2026-11-26.
    #: Decision material: The vault posts earnAUSD as collateral on Morpho
    #: and Curvance, borrows stablecoins and redeposits them into earnAUSD.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/earnausd-loop
    #: - https://www.upshift.finance/blog/earnausd-looping-vault-jumper-earn
    "0x42b3b62a6685e3a3f93483715ef4300f5e7336cc": {StrategyTag.lending_looping},
    #: Vault: Sentora PRIME Looping (Ethereum).
    #: Added: 2026-11-26.
    #: Decision material: Upshift names this deployment Sentora PRIME Looping,
    #: and Flare identifies leveraged looping in the Protected RWA strategy.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/sentora-prime-looping
    #: - https://docs.upshift.finance/contracts/addresses
    #: - https://flare.network/news/firelight-is-live-backed-by-staked-fxrp-on-flare
    "0xd000e6bcad5457e8f4de67eddefe50bcc4b3d743": {StrategyTag.lending_looping},
    #: Vault: Resolv USR Yield Maxi (Ethereum).
    #: Added: 2026-11-26.
    #: Decision material: Resolv describes looping its senior tranche on
    #: lending markets. Upshift lists the deployment as closed.
    #: Sources:
    #: - https://tradingstrategy.ai/vaults/resolv-usr-yield-maxi
    #: - https://resolvlabs.substack.com/p/resolv-community-update
    #: - https://docs.upshift.finance/contracts/addresses
    "0xda89af5bf2eb0b225d787abfa9095610f2e79e7d": {StrategyTag.lending_looping},
}
