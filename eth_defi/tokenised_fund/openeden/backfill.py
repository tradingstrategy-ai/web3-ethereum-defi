"""Register the reviewed OpenEden TBILL lead and current metadata."""

from dataclasses import dataclass

from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.tokenised_fund.openeden.constants import OPENEDEN_CHAIN_ID, OPENEDEN_TBILL_ADDRESS, OPENEDEN_TBILL_FIRST_SEEN_AT, OPENEDEN_TBILL_FIRST_SEEN_AT_BLOCK
from eth_defi.tokenised_fund.supply_only_backfill import backfill_supply_only_product


@dataclass(slots=True, frozen=True)
class OpenEdenBackfillProduct:
    """Static record accepted by the conservative metadata migration."""

    chain_id: int = OPENEDEN_CHAIN_ID
    token: str = OPENEDEN_TBILL_ADDRESS
    first_seen_at_block: int = OPENEDEN_TBILL_FIRST_SEEN_AT_BLOCK
    first_seen_at: object = OPENEDEN_TBILL_FIRST_SEEN_AT


def main() -> None:
    """Upsert TBILL metadata while preserving all existing price histories.

    :return: ``None``.
    """

    backfill_supply_only_product(OpenEdenBackfillProduct(), ERC4626Feature.openeden_like, "OpenEden TBILL")
