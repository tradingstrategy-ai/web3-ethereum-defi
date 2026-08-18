"""Panoptic perpetual options vault support."""

from eth_typing import HexAddress

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.panoptic.tags import STRATEGY_TAGS
from eth_defi.vault.strategy_tag import StrategyTag


class PanopticVault(ERC4626Vault):
    """Panoptic perpetual option and Uniswap LP vault."""

    def get_strategy_tags(self) -> set[StrategyTag] | None:
        """Return the maintained strategy tags for this Panoptic vault.

        :return:
            Copy of the tag set, or ``None`` when this vault has not yet been
            classified.
        """
        tags = STRATEGY_TAGS.get(HexAddress(str(self.vault_address).lower()))
        return tags.copy() if tags is not None else None
