"""Backfill CASHx identity and supply metadata without fabricating NAV history."""

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.tokenised_fund.kaio.constants import CASHX_ETHEREUM
from eth_defi.tokenised_fund.supply_only_backfill import backfill_supply_only_product


def main() -> None:
    """Run the address-scoped CASHx metadata backfill.

    :return: ``None``.
    """

    backfill_supply_only_product(CASHX_ETHEREUM, ERC4626Feature.kaio_like, "KAIO CASHx")
