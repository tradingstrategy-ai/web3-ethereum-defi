"""Reviewed KAIO CASHx Ethereum deployment metadata."""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress


@dataclass(slots=True, frozen=True)
class KaioProduct:
    """Static KAIO fund-share product record."""

    chain_id: int
    token: HexAddress
    symbol: str
    product_name: str
    description: str
    first_seen_at_block: int
    first_seen_at: datetime.datetime


CASHX_ETHEREUM = KaioProduct(
    1,
    HexAddress("0x42975aae7a124257e7fda7f5e8382f51449b784a"),
    "CASHx",
    "BlackRock ICS US Dollar Liquidity Fund",
    "Permissioned KAIO-tokenised shares in the BlackRock ICS US Dollar Liquidity Fund.",
    22_347_365,
    datetime.datetime(2025, 4, 25, 16, 55, 59, tzinfo=datetime.UTC).replace(tzinfo=None),
)

KAIO_PRODUCTS = {(CASHX_ETHEREUM.chain_id, CASHX_ETHEREUM.token): CASHX_ETHEREUM}
KAIO_PRODUCTS_BY_TOKEN = {CASHX_ETHEREUM.token: CASHX_ETHEREUM}
KAIO_HARDCODED_LEADS = ((CASHX_ETHEREUM.chain_id, CASHX_ETHEREUM.token, CASHX_ETHEREUM.first_seen_at_block, CASHX_ETHEREUM.first_seen_at),)
