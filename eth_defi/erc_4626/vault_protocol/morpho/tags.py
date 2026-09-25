"""Strategy classifications for Morpho vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

#: Morpho MetaMorpho vaults supply assets to Morpho lending markets by definition.
DEFAULT_STRATEGY_TAGS: frozenset[StrategyTag] = frozenset({StrategyTag.lending})

#: Address-specific classifications maintained by the vault categorisation skill.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
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
    #: Vault: Bitwise Premium RWA USDC on Arc.
    #: Added: 2026-09-25.
    #: Decision material: Bitwise describes PAPY-USDC as lending against a
    #: diversified set of overcollateralised real-world assets.
    #: Sources:
    #: - https://bitwiseinvestments.com/newsroom/the-bitwise-premium-rwa-vault-papy-expands-to-arc-with-usdc-deposits
    #: - https://app.morpho.org/arc/vault/0x7610094B846657dCF166D59e42973db52c7015F9/bitwise-premium-rwa-usdc
    "0x7610094b846657dcf166d59e42973db52c7015f9": {StrategyTag.rwa, StrategyTag.rwa_lending},
    #: Vault: Dialectic RWA USDC on Arc.
    #: Added: 2026-09-25.
    #: Decision material: Dialectic's Morpho announcement describes this
    #: vault as lending against a curated set of tokenised-credit and RWA
    #: collateral.
    #: Sources:
    #: - https://forum.morpho.org/t/introducing-dialectic-meccanico-by-dialectic-vaults-on-morpho/2334
    #: - https://app.morpho.org/arc/vault/0x6bdfe1165d5165808d02de05969c9a19e9b7cf30/dialectic-rwa-usdc
    "0x6bdfe1165d5165808d02de05969c9a19e9b7cf30": {StrategyTag.rwa, StrategyTag.rwa_lending},
}


def get_strategy_tags(address: HexAddress) -> set[StrategyTag]:
    """Return automatic Morpho lending and any address-specific tags.

    :param address:
        Morpho vault address.
    :return:
        A copy of the default lending tag plus any manually maintained tags.
    """
    return set(DEFAULT_STRATEGY_TAGS) | (lookup_strategy_tags(STRATEGY_TAGS, address) or set())
