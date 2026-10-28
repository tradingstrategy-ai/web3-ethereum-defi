"""Strategy classifications for Morpho vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

#: Morpho MetaMorpho vaults supply assets to Morpho lending markets by definition.
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset({StrategyTag.lending})

#: Address-specific classifications maintained by the vault categorisation skill.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Presto USDC Forte (chain 1).
    #: Added: 2026-10-21.
    #: Decision material: Morpho live API identifies the curator; published collateral strategy supplies the additional tags.
    #: Sources:
    #: - https://app.morpho.org/ethereum/vault/0x68a65f315bcd3c3456d1368ff88bc661856548e1
    #: - https://www.linkedin.com/posts/presto-official_were-proud-to-announce-that-presto-is-now-activity-7488515573493190656-QRRJ
    "0x68a65f315bcd3c3456d1368ff88bc661856548e1": {StrategyTag.rwa, StrategyTag.rwa_lending},
    #: Vault: Birch Hill RWA USDC (chain 8453).
    #: Added: 2026-10-21.
    #: Decision material: Morpho live API identifies the curator; published collateral strategy supplies the additional tags.
    #: Sources:
    #: - https://app.morpho.org/base/vault/0xb038cf3e233b78225f6d350ac8319bec8457db4a
    #: - https://research.birchhill.io/p/birch-hills-first-onchain-product
    "0xb038cf3e233b78225f6d350ac8319bec8457db4a": {StrategyTag.rwa, StrategyTag.rwa_lending},
    #: Vault: SharpByte USDC High Yield (chain 143).
    #: Added: 2026-10-21.
    #: Decision material: Morpho live API identifies the curator; published collateral strategy supplies the additional tags.
    #: Sources:
    #: - https://app.morpho.org/monad/vault/0xf57e9898575fab80b0f2acde5443276bec8c542d
    "0xf57e9898575fab80b0f2acde5443276bec8c542d": {StrategyTag.rwa, StrategyTag.rwa_lending},
    #: Vault: Tenbin USDC (chain 1).
    #: Added: 2026-10-21.
    #: Decision material: Morpho live API identifies the curator; published collateral strategy supplies the additional tags.
    #: Sources:
    #: - https://app.morpho.org/ethereum/vault/0x7d4741ba166b21cf3168a9a0ea71388531c52ff7
    "0x7d4741ba166b21cf3168a9a0ea71388531c52ff7": {StrategyTag.fx, StrategyTag.rwa, StrategyTag.rwa_lending},
    #: Vault: syrupUSDC/USDC Morpho Looper (chain 1).
    #: Added: 2026-10-21.
    #: Decision material: Exact Yearn strategy address from the current official yvUSD report; underlying protocol names do not establish management.
    #: Sources:
    #: - https://curation.yearn.fi/report/yearn-yvusd/
    "0xf28dc8b6ded7e45f8cf84b9972487c8e1857a442": {StrategyTag.lending_looping},
    #: Vault: syrupUSDC/USDC Morpho Looper (chain 1).
    #: Added: 2026-10-21.
    #: Decision material: Exact Yearn strategy address from the current official yvUSD report; underlying protocol names do not establish management.
    #: Sources:
    #: - https://curation.yearn.fi/report/yearn-yvusd/
    "0x59b6c75eeb7dfe417505919c3e5a919b7c1b0773": {StrategyTag.lending_looping},
    #: Vault: stcUSD/USDC Pawn Broker Looper (chain 1).
    #: Added: 2026-10-21.
    #: Decision material: Exact Yearn strategy address from the current official yvUSD report; underlying protocol names do not establish management.
    #: Sources:
    #: - https://curation.yearn.fi/report/yearn-yvusd/
    "0xd362efc75ef1879f37a900823495f402cfdb0986": {StrategyTag.lending_looping},
    #: Vault: USDC Mix Yield Token (chain 1).
    #: Added: 2026-10-21.
    #: Decision material: Official chain-specific deployment table confirms the MYT address; DAO selects and rebalances yield strategies.
    #: Sources:
    #: - https://docs.alchemix.fi/dev/indexer-api
    #: - https://docs.alchemix.fi/user/tutorials/use-passive-myt
    "0x9b44efca3e2a707b63dc00ce79d646e5e5d24ba5": {StrategyTag.multistrategy},
    #: Vault: Arbitrum USDC Mix Yield Token (chain 42161).
    #: Added: 2026-10-21.
    #: Decision material: Official chain-specific deployment table confirms the MYT address; DAO selects and rebalances yield strategies.
    #: Sources:
    #: - https://docs.alchemix.fi/dev/indexer-api
    #: - https://docs.alchemix.fi/user/tutorials/use-passive-myt
    "0xeba62b842081cef5a8184318dc5c4e4aaca9f651": {StrategyTag.multistrategy},
    #: Vault: USDC Mix Yield Token (chain 8453).
    #: Added: 2026-10-21.
    #: Decision material: Official chain-specific deployment table confirms the MYT address; DAO selects and rebalances yield strategies.
    #: Sources:
    #: - https://docs.alchemix.fi/dev/indexer-api
    #: - https://docs.alchemix.fi/user/tutorials/use-passive-myt
    "0xb8befe5a6941ca4022a52042075ff269c3c67467": {StrategyTag.multistrategy},
    #: Vault: 3F x Steakhouse USDC on Ethereum.
    #: Added: 2026-08-24.
    #: Decision material: 3F describes its Morpho-powered lending vaults as
    #: lending stablecoins against RWAs.
    #: Sources:
    #: - https://3f.xyz/
    #: - https://app.morpho.org/ethereum/vault/0xBEEf3f3A04e28895f3D5163d910474901981183D/3f-x-steakhouse-usdc
    "0xbeef3f3a04e28895f3d5163d910474901981183d": {StrategyTag.rwa, StrategyTag.rwa_lending},
    #: Vault: Morini RLUSD Emerging Yield on Ethereum.
    #: Added: 2026-09-11 from the public vault metadata database.
    "0x810b29d043eb851ba4cf80b1b194ed5177e70958": {StrategyTag.fx},
    #: Vault: Morini USDC Emerging Yield on Ethereum.
    #: Added: 2026-09-11 from the public vault metadata database.
    "0x58e0f0b81576f23c5f002d949b2bb11a5d2714d6": {StrategyTag.fx},
}


def get_strategy_tags(address: HexAddress) -> set[StrategyTag]:
    """Return automatic Morpho lending and any address-specific tags.

    :param address:
        Morpho vault address.
    :return:
        A copy of the default lending tag plus any manually maintained tags.
    """
    return set(DEFAULT_STRATEGY_TAGS) | (lookup_strategy_tags(STRATEGY_TAGS, address) or set())
