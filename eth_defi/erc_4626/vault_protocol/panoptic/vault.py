"""Panoptic perpetual options vault support."""

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.panoptic.tags import STRATEGY_TAGS
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags


class PanopticVault(ERC4626Vault):
    """Panoptic-compatible perpetual-option and Uniswap LP vault.

    The detector also uses this adapter for Return Finance products that share
    Panoptic-compatible vault interfaces.
    """

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return the maintained strategy tags for this Panoptic vault.

        :return:
            Copy of the tag set, or ``None`` when this vault has not yet been
            classified.
        """
        return lookup_strategy_tags(STRATEGY_TAGS, self.vault_address)
