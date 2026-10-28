"""Reviewed Flying Tulip staking deployment metadata.

The official address book is a maintenance reference, not a runtime dependency:
https://flyingtulip.com/address-book/flying-tulip-address-book-prod.json
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

#: Reviewed auto-compounding deployments from the official address book.
#: https://flyingtulip.com/address-book/flying-tulip-address-book-prod.json
FLYING_TULIP_SSFTUSD_BY_CHAIN: dict[int, HexAddress] = {
    1: to_checksum_address("0x232128844e5af56fd4b3f8bf97e08ab477282ab5"),
}

#: Concise public summary of the auto-compounding staking product.
FLYING_TULIP_SSFTUSD_SHORT_DESCRIPTION = "Flying Tulip's auto-compounding ftUSD staking vault. Yield is distributed in ftUSD and reflected in the value of ssftUSD shares."

#: Public product description, distinct from the external FT reward position.
FLYING_TULIP_SSFTUSD_DESCRIPTION = """[Flying Tulip](https://flyingtulip.com/ftusd/) operates ssftUSD, an auto-compounding staking position for ftUSD. Its backing generates yield through lending and delta-neutral strategies. Distributed ftUSD rewards increase the amount of ftUSD represented by each ssftUSD share.

Flying Tulip [introduced ssftUSD in September 2026](https://blog.flyingtulip.com/flying-tulip-september-update/). Unlike sftUSD, which pays separately claimable FT rewards, ssftUSD retains its staking yield as ftUSD. The [product documentation](https://docs.flyingtulip.com/product-suite/ft-usd/) describes a slightly lower reward allocation for ssftUSD than sftUSD; returns are variable and depend on realised strategy returns and protocol policy."""

#: Descriptive notes for the reviewed Ethereum ssftUSD deployment.
FLYING_TULIP_SSFTUSD_NOTES = """Flying Tulip [announced ssftUSD on 22 September 2026](https://x.com/flyingtulip_/status/2102404682685501823), with further detail in its [September update](https://blog.flyingtulip.com/flying-tulip-september-update/). Staking yield compounds as ftUSD within the position; there is no separate FT claim for this yield.

In the [reviewed vault implementation](https://etherscan.io/address/0xD940d531b983557884B31fD915A1Df97bebe6e09#code), funded rewards vest into the share price over eight hours. This reward schedule is not a withdrawal lockup. Withdrawals settle immediately only when underlying liquidity and circuit-breaker capacity permit; otherwise the withdrawal reverts rather than creating a queued claim for the ssftUSD holder.

The fee fields model the separate USDC -> ftUSD -> ssftUSD entry and reverse exit route. The previously reviewed Ethereum USDC configuration charged 0.07% in each direction; this is a conversion cost, not a direct ssftUSD staking fee or a guarantee of current pricing. Check the application quote before transacting. Optional relayed transactions can charge an authorised relayer fee. Network fees and other execution costs are excluded.

Returns are variable. Holders remain exposed to ftUSD backing and peg risks, the underlying strategies and available redemption liquidity. See the [ftUSD product documentation](https://docs.flyingtulip.com/product-suite/ft-usd/) for yield allocation and risk context. Older recorded zero-address deposit-capacity observations do not establish whether deposits were available to eligible users."""

#: FT reward-token addresses for the reviewed sftUSD proxy registry.
#:
#: Flying Tulip currently uses the same CREATE2 FT address on every reviewed
#: chain. This address map, together with :data:`FLYING_TULIP_SFTUSD_BY_CHAIN`,
#: is the authority for externally rewarded sftUSD equivalents: do not add
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
#:
#: Plain text only: Markdown is not allowed in a vault short description,
#: because listings and tables render it verbatim. Put documentation links in
#: :py:data:`FLYING_TULIP_NOTES` instead.
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
