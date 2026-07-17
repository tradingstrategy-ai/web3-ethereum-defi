"""Reviewed Libeara CMTAT fund-share deployments on Ethereum."""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress


@dataclass(slots=True, frozen=True)
class LibearaProduct:
    """One reviewed Libeara platform tokenised-fund share.

    :param chain_id: EVM chain hosting the proxy.
    :param token: CMTAT proxy address.
    :param symbol: ERC-20 token symbol.
    :param product_name: Issuer-provided product name.
    :param description: Short public product description.
    :param first_seen_at_block: First block with proxy bytecode.
    :param first_seen_at: Proxy deployment timestamp as naive UTC.
    """

    chain_id: int
    token: HexAddress
    symbol: str
    product_name: str
    description: str
    first_seen_at_block: int
    first_seen_at: datetime.datetime


ETHEREUM_CHAIN_ID = 1

#: ChinaAMC USD Digital Money Market Fund Class I USD.
#: Source: https://etherscan.io/token/0x85d38585c3ac08268f598282a84b7c0ddfc0d04f
CUMIU_ETHEREUM = LibearaProduct(
    ETHEREUM_CHAIN_ID,
    HexAddress("0x85d38585c3ac08268f598282a84b7c0ddfc0d04f"),
    "CUMIU",
    "ChinaAMC USD Digital Money Market Fund Class I USD",
    "Permissioned tokenised shares in ChinaAMC's USD digital money market fund.",
    23_038_326,
    datetime.datetime(2025, 7, 31, 6, 34, 35),
)

#: Bosera Liquidity Income Fund SP.
#: Source: https://etherscan.io/token/0x237c717df1b60501f8d029d3fe7385fd090df180
BELIF_ETHEREUM = LibearaProduct(
    ETHEREUM_CHAIN_ID,
    HexAddress("0x237c717df1b60501f8d029d3fe7385fd090df180"),
    "BELIF",
    "Bosera Liquidity Income Fund SP",
    "Permissioned tokenised shares in Bosera's liquidity income fund.",
    23_595_754,
    datetime.datetime(2025, 10, 17, 9, 1, 23),
)

LIBEARA_PRODUCTS = {(p.chain_id, p.token): p for p in (CUMIU_ETHEREUM, BELIF_ETHEREUM)}
LIBEARA_PRODUCTS_BY_TOKEN = {p.token: p for p in LIBEARA_PRODUCTS.values()}
LIBEARA_HARDCODED_LEADS = tuple((p.chain_id, p.token, p.first_seen_at_block, p.first_seen_at) for p in LIBEARA_PRODUCTS.values())
