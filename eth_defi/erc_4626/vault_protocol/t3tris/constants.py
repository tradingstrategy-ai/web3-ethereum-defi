"""Reviewed T3tris vault discovery constants."""

import datetime

from eth_typing import HexAddress

#: Arbitrum One chain id.
ARBITRUM_CHAIN_ID = 42161

#: Strada Yield T3tris vault on Arbitrum.
#:
#: The T3tris migration-pool setup did not emit vault-local deposit, withdrawal,
#: or request-flow events. Retain this deployment as an explicit discovery lead.
STRADA_YIELD_ARBITRUM_ADDRESS = HexAddress("0x5684b18275c0830dafb0b3cff595ba1beca926bd")

#: Block that deployed and initialised :py:data:`STRADA_YIELD_ARBITRUM_ADDRESS`.
STRADA_YIELD_ARBITRUM_FIRST_SEEN_AT_BLOCK = 483_858_363

#: Deployment block timestamp as a naive UTC datetime.
STRADA_YIELD_ARBITRUM_FIRST_SEEN_AT = datetime.datetime(2026, 7, 14, 18, 57, 55, tzinfo=datetime.UTC).replace(tzinfo=None)

#: T3tris deployments that must be retained even without vault-local flow logs.
T3TRIS_HARDCODED_LEADS = (
    (
        ARBITRUM_CHAIN_ID,
        STRADA_YIELD_ARBITRUM_ADDRESS,
        STRADA_YIELD_ARBITRUM_FIRST_SEEN_AT_BLOCK,
        STRADA_YIELD_ARBITRUM_FIRST_SEEN_AT,
    ),
)
