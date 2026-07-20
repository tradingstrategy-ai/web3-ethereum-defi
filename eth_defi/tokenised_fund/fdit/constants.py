"""Reviewed Fidelity FDIT Ethereum deployment metadata."""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress


@dataclass(slots=True, frozen=True)
class FditProduct:
    """Static FDIT product record.

    :param chain_id: EVM chain hosting the token dispatcher.
    :param token: FDIT ERC-20 dispatcher address.
    :param symbol: ERC-20 symbol.
    :param product_name: Public product name.
    :param description: Plain-language fund description.
    :param first_seen_at_block: First block containing runtime bytecode.
    :param first_seen_at: Deployment timestamp as naive UTC.
    """

    chain_id: int
    token: HexAddress
    symbol: str
    product_name: str
    description: str
    first_seen_at_block: int
    first_seen_at: datetime.datetime


FDIT_ETHEREUM = FditProduct(
    1,
    HexAddress("0x48ab4e39ac59f4e88974804b04a991b3a402717f"),
    "FDIT",
    "Fidelity Digital Interest Token",
    "Permissioned tokenised shares in Fidelity Treasury Digital Fund's OnChain class.",
    22_588_721,
    datetime.datetime(2025, 5, 29, 13, 31, 35, tzinfo=datetime.UTC).replace(tzinfo=None),
)

FDIT_PRODUCTS = {(FDIT_ETHEREUM.chain_id, FDIT_ETHEREUM.token): FDIT_ETHEREUM}
FDIT_PRODUCTS_BY_TOKEN = {FDIT_ETHEREUM.token: FDIT_ETHEREUM}
FDIT_HARDCODED_LEADS = ((FDIT_ETHEREUM.chain_id, FDIT_ETHEREUM.token, FDIT_ETHEREUM.first_seen_at_block, FDIT_ETHEREUM.first_seen_at),)
