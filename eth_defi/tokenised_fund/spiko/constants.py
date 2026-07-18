"""Verified Spiko USTBL deployment constants."""

import datetime

from eth_typing import HexAddress

#: Ethereum mainnet chain id.
SPIKO_CHAIN_ID = 1

#: Spiko US T-Bills Money Market Fund ERC-20 proxy on Ethereum.
#:
#: https://etherscan.io/address/0xe4880249745eac5f1ed9d8f7df844792d560e750
USTBL_TOKEN_ADDRESS = HexAddress("0xe4880249745eac5f1ed9d8f7df844792d560e750")

#: Spiko's verified ``Oracle`` proxy publishing USTBL/USD NAV with the
#: Chainlink ``AggregatorV3Interface`` surface.
#:
#: https://github.com/spiko-tech/contracts/blob/main/subgraph/config/mainnet.json
USTBL_PRICE_ORACLE_ADDRESS = HexAddress("0x021289588cd81dc1ac87ea91e91607eef68303f5")

#: First Ethereum block containing the USTBL proxy bytecode.
USTBL_FIRST_SEEN_AT_BLOCK = 19_690_265

#: Naive UTC timestamp of :py:data:`USTBL_FIRST_SEEN_AT_BLOCK`.
USTBL_FIRST_SEEN_AT = datetime.datetime(2024, 4, 19, 15, 6, 11, tzinfo=datetime.UTC).replace(tzinfo=None)

#: First Ethereum block containing the official USTBL oracle.
USTBL_ORACLE_FIRST_SEEN_AT_BLOCK = 19_690_267

#: Scan-record source label for the issuer-published NAV oracle.
USTBL_NAV_SOURCE = "spiko_ustbl_oracle_latestRoundData"

#: Hardcoded discovery lead for the non-ERC-4626 USTBL tokenised fund.
SPIKO_HARDCODED_LEADS = ((SPIKO_CHAIN_ID, USTBL_TOKEN_ADDRESS, USTBL_FIRST_SEEN_AT_BLOCK, USTBL_FIRST_SEEN_AT),)
