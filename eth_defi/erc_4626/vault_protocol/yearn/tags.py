"""Reviewed strategy classifications for Yearn vault deployments."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

#: Base chain for the reviewed Birch Hill deployment.
BIRCH_HILL_CHAIN_ID = 8453

STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Birch Hill RWA USDC Flagship on Base.
    #: Added: 2026-11-06.
    #: Decision material: Allocates USDC to Ecosystem vaults lending against
    #: tokenised real-world assets; borrower interest accrues through their shares.
    #: Sources:
    #: - https://docs.birchhill.io/vault-books/birch-hill-rwa-usdc/1.-strategy-mandate
    #: - https://docs.birchhill.io/vault-books/birch-hill-rwa-usdc/6.-technical-appendix
    "0xa5afd3a598ef000a5edd96ed65929ac7a6feea39": {StrategyTag.lending, StrategyTag.rwa, StrategyTag.rwa_lending},
}


def get_strategy_tags(chain_id: int, address: HexAddress) -> set[StrategyTag] | None:
    """Resolve the reviewed Base deployment without classifying other chains.

    Return a fresh set so callers cannot modify the maintained mapping.
    The official mandate is linked alongside the address entry.

    :param chain_id: Deployment chain ID.
    :param address: Vault contract address, accepting mixed case.
    :return: Reviewed tags or ``None`` for deployments without a mapping.
    """
    return lookup_strategy_tags(STRATEGY_TAGS, address) if chain_id == BIRCH_HILL_CHAIN_ID else None
