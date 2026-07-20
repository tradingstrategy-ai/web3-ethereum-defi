"""Backfill FDIT identity and supply metadata without fabricating NAV history."""

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.tokenised_fund.fdit.constants import FDIT_ETHEREUM
from eth_defi.tokenised_fund.supply_only_backfill import backfill_supply_only_product


def main() -> None:
    """Run the address-scoped FDIT metadata backfill.

    :return: ``None``.
    """

    backfill_supply_only_product(FDIT_ETHEREUM, ERC4626Feature.fdit_like, "FDIT")
