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

#: Chainlink bundle proxy configured for FILQ-A.
FILQ_A_BUNDLE_PROXY_ADDRESS = HexAddress("0x0c6c789a375cc4ee9ce6008715c915a91da5ac5c")

#: Chainlink bundle proxy configured for FILQ-D.
FILQ_D_BUNDLE_PROXY_ADDRESS = HexAddress("0x7484379d1af1b718dccc6bb5e58aadbcb6e4866a")

#: Shared Chainlink DataFeedsCache that stores and emits both FILQ feeds.
#:
#: https://etherscan.io/address/0x16b53825c8ceaea593507274d4c1aaec9e261433#code
FILQ_BUNDLE_AGGREGATOR_ADDRESS = HexAddress("0x16b53825c8ceaea593507274d4c1aaec9e261433")

#: FILQ-A Chainlink bundle data identifier.
FILQ_A_BUNDLE_DATA_ID = bytes.fromhex("02000001220700030000000000000000")

#: FILQ-D Chainlink bundle data identifier.
FILQ_D_BUNDLE_DATA_ID = bytes.fromhex("02000001230700030000000000000000")

#: First accepted FILQ-A ``BundleReportUpdated`` event.
FILQ_A_BUNDLE_FIRST_SEEN_AT_BLOCK = 25_038_639

#: First accepted FILQ-D ``BundleReportUpdated`` event.
FILQ_D_BUNDLE_FIRST_SEEN_AT_BLOCK = 25_139_026

#: FILQ's NAV/share is the second numeric word in both reviewed bundle schemas.
FILQ_NAV_BUNDLE_INDEX = 1

#: Reviewed current bundle decimal metadata keyed by FILQ token address.
FILQ_BUNDLE_DECIMALS_BY_TOKEN: dict[HexAddress, tuple[int, ...]] = {
    FILQ_A_ETHEREUM_ADDRESS: (0, 4, 9, 9, 0, 0),
    FILQ_D_ETHEREUM_ADDRESS: (0, 2, 9, 9, 0, 0),
}

#: Bundle proxy keyed by reviewed FILQ token address.
FILQ_BUNDLE_PROXY_BY_TOKEN: dict[HexAddress, HexAddress] = {
    FILQ_A_ETHEREUM_ADDRESS: FILQ_A_BUNDLE_PROXY_ADDRESS,
    FILQ_D_ETHEREUM_ADDRESS: FILQ_D_BUNDLE_PROXY_ADDRESS,
}

#: Bundle data identifier keyed by reviewed FILQ token address.
FILQ_BUNDLE_DATA_ID_BY_TOKEN: dict[HexAddress, bytes] = {
    FILQ_A_ETHEREUM_ADDRESS: FILQ_A_BUNDLE_DATA_ID,
    FILQ_D_ETHEREUM_ADDRESS: FILQ_D_BUNDLE_DATA_ID,
}

#: First bundle report block keyed by reviewed FILQ token address.
FILQ_BUNDLE_FIRST_SEEN_AT_BLOCK_BY_TOKEN: dict[HexAddress, int] = {
    FILQ_A_ETHEREUM_ADDRESS: FILQ_A_BUNDLE_FIRST_SEEN_AT_BLOCK,
    FILQ_D_ETHEREUM_ADDRESS: FILQ_D_BUNDLE_FIRST_SEEN_AT_BLOCK,
}

#: FILQ token deployment block keyed by reviewed token address.
FILQ_FIRST_SEEN_AT_BLOCK_BY_TOKEN: dict[HexAddress, int] = {
    FILQ_A_ETHEREUM_ADDRESS: FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK,
    FILQ_D_ETHEREUM_ADDRESS: FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK,
}

#: Reviewed FILQ token addresses keyed by EVM chain.
SYGNUM_PRODUCTS_BY_CHAIN: dict[int, frozenset[HexAddress]] = {
    SYGNUM_ETHEREUM_CHAIN_ID: frozenset({FILQ_A_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_ADDRESS}),
}

#: Explicit discovery leads for the independently reviewed FILQ share classes.
SYGNUM_HARDCODED_LEADS = (
    (SYGNUM_ETHEREUM_CHAIN_ID, FILQ_A_ETHEREUM_ADDRESS, FILQ_A_ETHEREUM_FIRST_SEEN_AT_BLOCK, FILQ_A_ETHEREUM_FIRST_SEEN_AT),
    (SYGNUM_ETHEREUM_CHAIN_ID, FILQ_D_ETHEREUM_ADDRESS, FILQ_D_ETHEREUM_FIRST_SEEN_AT_BLOCK, FILQ_D_ETHEREUM_FIRST_SEEN_AT),
)
