"""Reviewed Superstate tokenised-fund deployments.

The registry is deliberately chain-aware.  A token contract address alone is
not a globally unique vault identifier and must not classify a contract on a
different EVM chain as a Superstate fund.
"""

import datetime

from eth_typing import HexAddress

#: Ethereum mainnet chain id.
SUPERSTATE_ETHEREUM_CHAIN_ID = 1

#: Invesco Short Duration US Government Securities Fund (USTB) Ethereum proxy.
#:
#: Source: https://docs.superstate.com/welcome-to-superstate/smart-contracts
USTB_ETHEREUM_ADDRESS = HexAddress("0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e")

#: First Ethereum block with USTB proxy bytecode.
#:
#: Determined with an archive-RPC binary search on 2026-07-17.  The preceding
#: block has no code at :py:data:`USTB_ETHEREUM_ADDRESS`.
USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK = 18_725_909

#: USTB deployment timestamp as a naive UTC datetime.
USTB_ETHEREUM_FIRST_SEEN_AT = datetime.datetime(2023, 12, 5, 17, 11, 35, tzinfo=datetime.UTC).replace(tzinfo=None)

#: Superstate's documented USTB continuous-price oracle on Ethereum.
#:
#: https://docs.superstate.com/welcome-to-superstate/smart-contracts
USTB_ETHEREUM_CONTINUOUS_PRICE_ORACLE = HexAddress("0xe4fa682f94610ccd170680cc3b045d77d9e528a8")

#: The verified USTB oracle uses six decimal places.
#:
#: Direct and historical reads use this reviewed deployment constant so each
#: historical point remains reproducible when token configuration changes.
USTB_ETHEREUM_ORACLE_DECIMALS = 6

#: Reviewed Superstate fund addresses keyed by chain id.
SUPERSTATE_PRODUCTS_BY_CHAIN: dict[int, frozenset[HexAddress]] = {
    SUPERSTATE_ETHEREUM_CHAIN_ID: frozenset({USTB_ETHEREUM_ADDRESS}),
}

#: Hardcoded discovery leads for reviewed Superstate fund-token deployments.
SUPERSTATE_HARDCODED_LEADS = (
    (
        SUPERSTATE_ETHEREUM_CHAIN_ID,
        USTB_ETHEREUM_ADDRESS,
        USTB_ETHEREUM_FIRST_SEEN_AT_BLOCK,
        USTB_ETHEREUM_FIRST_SEEN_AT,
    ),
)
