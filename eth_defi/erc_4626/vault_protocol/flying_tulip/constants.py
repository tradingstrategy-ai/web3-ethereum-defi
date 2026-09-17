"""Reviewed Flying Tulip sftUSD deployment metadata.

The official registry is a maintenance cross-check, not a runtime dependency:
https://api.flyingtulip.com/ftusd/contracts/all
"""

import datetime

from eth_typing import HexAddress
from eth_utils import to_checksum_address

from eth_defi.types import Percent

#: ftUSD uses CREATE2 and therefore has the same address on reviewed chains.
FLYING_TULIP_FTUSD: HexAddress = to_checksum_address("0xF7D85EC4E7710F71992752EAC2111312E73E9C9C")

#: FT token on the Ethereum chain which supplies the canonical FT/ftUSD price.
FLYING_TULIP_FT_ETHEREUM: HexAddress = to_checksum_address("0x5dD1A7A369E8273371D2dBf9D83356057088082C")

#: Curve FT/ftUSD market on Ethereum.
#:
#: ``coins(0)`` is FT and ``coins(1)`` is ftUSD. ``price_oracle()`` returns
#: FT per ftUSD, scaled by 1e18; the reward reader records its reciprocal.
FLYING_TULIP_FT_FTUSD_CURVE_POOL: HexAddress = to_checksum_address("0x68102ff5406475881462880a8da3c9bc9181ad6c")

#: Ethereum deployment block of the canonical FT/ftUSD Curve market.
#:
#: Determined by an archive ``eth_getCode`` binary search on 2026-08-24.
FLYING_TULIP_CURVE_CANONICAL_START_BLOCK = 25_531_725

#: Naive UTC Unix timestamp of :data:`FLYING_TULIP_CURVE_CANONICAL_START_BLOCK`.
#:
#: Cross-chain reward tracking excludes settlements before this timestamp.
FLYING_TULIP_CURVE_CANONICAL_START_TIMESTAMP = 1_784_042_255

#: Maximum accepted age of the canonical Curve EMA observation.
#:
#: The first complete real backfill observed a maximum 4.7-day no-update
#: interval, so seven days accommodates the market's actual trading cadence
#: while still preventing an indefinitely old price from bridging a gap.
FLYING_TULIP_MAX_ORACLE_AGE_SECONDS = 7 * 24 * 60 * 60

#: Initial price equivalence at the first post-Curve settlement.
FLYING_TULIP_INITIAL_SHARE_PRICE_EQUIVALENCE = 1

#: Epoch reward formula denominator.
FLYING_TULIP_RATE_RAY = 10**27

#: Official sftUSD proxies keyed by EVM chain ID.
FLYING_TULIP_SFTUSD_BY_CHAIN: dict[int, HexAddress] = {
    1: to_checksum_address("0xeb48218a4c35C814C7678cBcae88C6Ee037F7625"),
    56: to_checksum_address("0xe1716796d6Bf37e4049bdb6e1150Cb713800FeEe"),
    146: to_checksum_address("0xD1E5A86f1005F6356Bd022C587dE0f430CD2aeb1"),
}

#: FT reward-token addresses for the reviewed sftUSD proxy registry.
#:
#: Flying Tulip currently uses the same CREATE2 FT address on every reviewed
#: chain. This address map, together with :data:`FLYING_TULIP_SFTUSD_BY_CHAIN`,
#: is the sole authority for routing Flying Tulip vault equivalents: do not add
#: ABI-selector probing to discover or classify further deployments.
FLYING_TULIP_FT_BY_CHAIN: dict[int, HexAddress] = {
    1: FLYING_TULIP_FT_ETHEREUM,
    56: FLYING_TULIP_FT_ETHEREUM,
    146: FLYING_TULIP_FT_ETHEREUM,
}

#: Reviewed deployment with no epoch or supply events at the research snapshot.
#: Its empty source history is valid and must not fail the structural examiner.
FLYING_TULIP_DORMANT_CHAIN_IDS = frozenset({56})

#: Proxy deployment blocks and naive UTC timestamps, determined by archive
#: ``eth_getCode`` binary search on 2026-08-24. The dormant BNB deployment is
#: included so lead discovery materialises it before it emits an ERC-4626
#: deposit event.
FLYING_TULIP_HARDCODED_LEADS = (
    (1, FLYING_TULIP_SFTUSD_BY_CHAIN[1], 24_501_008, datetime.datetime(2026, 2, 20, 22, 19, 11)),
    (56, FLYING_TULIP_SFTUSD_BY_CHAIN[56], 108_860_159, datetime.datetime(2026, 7, 8, 21, 31, 12)),
    (146, FLYING_TULIP_SFTUSD_BY_CHAIN[146], 64_888_390, datetime.datetime(2026, 3, 10, 17, 7, 25)),
)

#: Human-readable scanner description.
FLYING_TULIP_SHORT_DESCRIPTION = "ftUSD staking vault with externally distributed FT rewards and conditional queued redemptions."

#: USDC ``MintAndRedeem`` fees used to model Flying Tulip vault equivalents.
#:
#: The fee is configured per collateral in the MintAndRedeem engine. At the
#: reviewed USDC configurations, Ethereum and Sonic charge seven basis points
#: while BNB Chain charges ten basis points, in both directions. The sftUSD
#: wrapping step has no separate fee in this model.
FLYING_TULIP_USDC_MINT_REDEEM_FEE_BY_CHAIN: dict[int, Percent] = {
    1: 7 / 10_000,
    56: 10 / 10_000,
    146: 7 / 10_000,
}

#: Protocol-owned note surfaced in vault metadata.
FLYING_TULIP_NOTES = """Performance assumes entry and exit fees USDC -> ftUSD -> sftUSD.\n\nPerformance includes FT rewards valued through the [Ethereum FT/ftUSD Curve pool](https://etherscan.io/address/0x68102ff5406475881462880a8da3c9bc9181ad6c#code). At each reward settlement, distributed FT is converted to ftUSD using the latest available Curve oracle price, divided by the average amount staked during the period, and compounded into the displayed returns. See the [Flying Tulip integration documentation](https://web3-ethereum-defi.readthedocs.io/vaults/flying_tulip/index.html) for details.\n\nWithdrawals may be subject to a variable exit delay or cooldown; see the [Flying Tulip ftUSD documentation](https://docs.flyingtulip.com/product-suite/ft-usd/).\n\nSee the [official deployment registry](https://api.flyingtulip.com/ftusd/contracts/all) for contract addresses."""
