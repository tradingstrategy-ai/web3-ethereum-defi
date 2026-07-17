"""Circle USYC production-contract metadata."""

import datetime

from eth_typing import HexAddress

#: Ethereum mainnet chain id.
USYC_CHAIN_ID = 1

#: US Yield Coin (USYC) ERC-20 proxy on Ethereum.
#:
#: https://usyc.docs.hashnote.com/overview/smart-contracts
USYC_TOKEN_ADDRESS = HexAddress("0x136471a34f6ef19fe571effc1ca711fdb8e49f2b")

#: Circle USYC Oracle, which has the Chainlink aggregator interface.
#:
#: https://usyc.docs.hashnote.com/overview/smart-contracts
USYC_PRICE_ORACLE_ADDRESS = HexAddress("0x74f2199aeb743f68f05943e5715a33eaf2b61f53")

#: Circle USYC Teller for permissioned USDC subscriptions and redemptions.
#:
#: https://usyc.docs.hashnote.com/overview/smart-contracts
USYC_TELLER_ADDRESS = HexAddress("0xee35f963bfc71b51ec95147f26c030d674ea30e6")

#: Native USDC token on Ethereum mainnet, USYC's subscription currency.
USYC_DENOMINATION_TOKEN_ADDRESS = HexAddress("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")

#: Scanner label for the official Chainlink-compatible USYC price oracle.
USYC_NAV_SOURCE = "circle_usyc_oracle_latestRoundData"

#: First Ethereum block containing USYC proxy bytecode.
USYC_FIRST_SEEN_AT_BLOCK = 17_381_914

#: Timestamp of :py:data:`USYC_FIRST_SEEN_AT_BLOCK`, stored as naive UTC.
USYC_FIRST_SEEN_AT = datetime.datetime(2023, 5, 31, 22, 48, 59, tzinfo=datetime.UTC).replace(tzinfo=None)

#: First Ethereum block containing the current official USYC Oracle address.
USYC_ORACLE_FIRST_SEEN_AT_BLOCK = 20_530_942

#: Hardcoded discovery lead for the non-ERC-4626 USYC product.
USYC_HARDCODED_LEADS = (
    (
        USYC_CHAIN_ID,
        USYC_TOKEN_ADDRESS,
        USYC_FIRST_SEEN_AT_BLOCK,
        USYC_FIRST_SEEN_AT,
    ),
)
