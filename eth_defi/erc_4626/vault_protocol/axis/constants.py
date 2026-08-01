"""Reviewed Axis StakedUSDx deployment constants."""

import datetime

from eth_typing import HexAddress

#: Plasma mainnet chain id.
AXIS_CHAIN_ID = 9745

#: Axis's sole reviewed StakedUSDx rewards vault on Plasma.
#:
#: https://plasmaexplorer.com/address/0x13A099765B34b3aAFedb8698CF7fd418E7730012
AXIS_STAKED_USDX_VAULT = HexAddress("0x13a099765b34b3aafedb8698cf7fd418e7730012")

#: One-line description used by the vault list.
AXIS_SHORT_DESCRIPTION = "USDx rewards vault with an asynchronous redemption request and claim flow."

#: Protocol-owned Markdown note that explains the redemption lifecycle.
AXIS_NOTES = """Axis's StakedUSDx vault accepts USDx and issues sUSDx shares whose USDx value grows as rewards vest.

Redemptions are asynchronous: request redemption, wait for the documented cooldown, then claim once Axis services the request. This vault must not be treated as immediately redeemable ERC-4626 liquidity.

- [Axis StakedUSDx documentation](https://docs.axis.to/susdx-the-rewards-vault/susdx)
- [Axis staking and unstaking guide](https://docs.axis.to/susdx-the-rewards-vault/stake-and-unstake)"""

#: First block at which the production scanner observed the Axis vault.
AXIS_STAKED_USDX_FIRST_SEEN_AT_BLOCK = 18_204_445

#: Timestamp of :data:`AXIS_STAKED_USDX_FIRST_SEEN_AT_BLOCK`, stored as naive UTC.
AXIS_STAKED_USDX_FIRST_SEEN_AT = datetime.datetime(2026, 4, 2, 13, 25, 8)  # noqa: DTZ001 - vault pipeline timestamps are naive UTC

#: Hardcoded discovery lead for Axis's only reviewed production vault.
AXIS_HARDCODED_LEADS = (
    (
        AXIS_CHAIN_ID,
        AXIS_STAKED_USDX_VAULT,
        AXIS_STAKED_USDX_FIRST_SEEN_AT_BLOCK,
        AXIS_STAKED_USDX_FIRST_SEEN_AT,
    ),
)
