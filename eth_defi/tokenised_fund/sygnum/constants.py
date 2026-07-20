"""Reviewed Sygnum FILQ tokenised-fund deployments."""

import datetime

from eth_typing import HexAddress

#: Ethereum mainnet chain id.
SYGNUM_ETHEREUM_CHAIN_ID = 1

#: Fidelity USD Digital Liquidity Fund accumulating share class (FILQ-A).
#:
#: https://www.sygnum.com/filq/
FILQ_A_ETHEREUM_ADDRESS = HexAddress("0x54a4fc78431f9201824643e99bec891bb7462a1d")

#: Fidelity USD Digital Liquidity Fund distributing share class (FILQ-D).
FILQ_D_ETHEREUM_ADDRESS = HexAddress("0xf0db6f529581e7f6ebac7a7f6882923c00fc3a66")

#: First Ethereum block containing the FILQ-A proxy.
FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK = 24_972_132

#: FILQ-A deployment timestamp, stored as naive UTC.
FILQ_A_ETHEREUM_FIRST_SEEN_AT = datetime.datetime(2026, 4, 27, 2, 19, 35, tzinfo=datetime.UTC).replace(tzinfo=None)

#: First Ethereum block containing the FILQ-D proxy.
FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK = 24_972_189

#: FILQ-D deployment timestamp, stored as naive UTC.
FILQ_D_ETHEREUM_FIRST_SEEN_AT = datetime.datetime(2026, 4, 27, 15, 10, 59, tzinfo=datetime.UTC).replace(tzinfo=None)

#: Current SygToken implementation shared by the reviewed FILQ share classes.
#:
#: https://sourcify.dev/server/v2/contract/1/0x7030fe438be6ed196b8886616bbf5a245c267339?fields=all
FILQ_SYGTOKEN_IMPLEMENTATION = HexAddress("0x7030fe438be6ed196b8886616bbf5a245c267339")

#: Reviewed FILQ token addresses keyed by EVM chain.
SYGNUM_PRODUCTS_BY_CHAIN: dict[int, frozenset[HexAddress]] = {
    SYGNUM_ETHEREUM_CHAIN_ID: frozenset({FILQ_A_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_ADDRESS}),
}

#: Explicit discovery leads for the independently reviewed FILQ share classes.
SYGNUM_HARDCODED_LEADS = (
    (SYGNUM_ETHEREUM_CHAIN_ID, FILQ_A_ETHEREUM_ADDRESS, FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, FILQ_A_ETHEREUM_FIRST_SEEN_AT),
    (SYGNUM_ETHEREUM_CHAIN_ID, FILQ_D_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK, FILQ_D_ETHEREUM_FIRST_SEEN_AT),
)
