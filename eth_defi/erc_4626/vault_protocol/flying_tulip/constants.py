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
FLYING_TULIP_SSFTUSD_SHORT_DESCRIPTION = "Flying Tulip's ssftUSD vault automatically reinvests ftUSD rewards from the protocol's lending and other strategy income."

#: Public product description, distinct from the external FT reward position.
FLYING_TULIP_SSFTUSD_DESCRIPTION = "Flying Tulip manages ssftUSD, a vault for ftUSD, its token designed to track the US dollar. Flying Tulip describes earning income on ftUSD's backing assets through stablecoin lending and savings, alongside staking strategies that use borrowing to offset exposure to cryptocurrency prices. Rewards paid in ftUSD are automatically reinvested, increasing the ftUSD represented by each ssftUSD share. Introduced in September 2026, ssftUSD receives a slightly smaller reward allocation than sftUSD, which pays separately claimable FT tokens."

#: Shared, source-linked explanation of the backing strategies, checked 2026-10-29.
FLYING_TULIP_STRATEGY_NOTES = """Flying Tulip manages the money backing ftUSD, its token designed to track the US dollar. Its [product documentation](https://docs.flyingtulip.com/product-suite/ft-usd/#yield-sources) describes three sources of strategy income:

- Stablecoin lending and savings: supply stablecoins to Aave or Spark to earn interest.
- Hedged staking: use stablecoins as collateral to borrow a network's native asset, such as ETH, then stake that asset. Income depends on staking returns exceeding borrowing costs.
- Hedged crypto collateral: deposit a staking asset such as staked ETH, borrow a matching amount of ETH, sell the borrowed ETH for dollars and invest those dollars for income.

The borrowing aims to offset cryptocurrency price movements; Flying Tulip calls this delta-neutral carry. Its [June 2026 update](https://blog.flyingtulip.com/flying-tulip-june-update/) reported deploying this approach on Ethereum and Sonic. The product documentation describes available strategies rather than current allocations on every network. These strategies depend on borrowing costs, staking returns and lenders' collateral requirements; positions can be liquidated if their collateral becomes insufficient. Protocol revenue also contributes to rewards, and Flying Tulip's treasury decides distributions."""

#: Descriptive notes for the reviewed Ethereum ssftUSD deployment.
FLYING_TULIP_SSFTUSD_NOTES = (
    FLYING_TULIP_STRATEGY_NOTES
    + """

Flying Tulip [announced ssftUSD on 22 September 2026](https://x.com/flyingtulip_/status/2102404682685501823). Its [September update](https://blog.flyingtulip.com/flying-tulip-september-update/) explains that rewards are automatically reinvested as ftUSD, increasing the amount represented by each share. The product documentation linked above states that ssftUSD's reward allocation is slightly smaller than sftUSD's allocation of separately claimable FT tokens.

In the [vault version reviewed on 28 October 2026](https://etherscan.io/address/0xD940d531b983557884B31fD915A1Df97bebe6e09#code), each funded reward is gradually added to the share value over eight hours. Holders can withdraw during this period. A withdrawal succeeds only when enough underlying money is available and the protocol's withdrawal rate limit permits it; otherwise the transaction fails without creating a queued claim.

Displayed entry and exit fees cover buying ftUSD with USDC before staking and converting back to USDC after withdrawal. The Ethereum USDC rates checked on 24 August 2026 were 0.07% in each direction. Check the application quote for current charges. Optional transactions submitted by a relayer can also carry an agreed service fee; network fees and other execution costs are excluded.

Returns vary, and ftUSD can trade below its dollar target if backing assets lose value or cannot be sold promptly."""
)

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
FLYING_TULIP_SHORT_DESCRIPTION = "Flying Tulip's sftUSD vault pays separately claimable FT tokens as rewards from the protocol's lending and other strategy income."

#: Single-line public description of the externally rewarded staking product.
FLYING_TULIP_DESCRIPTION = "Flying Tulip manages sftUSD, a vault for ftUSD, its token designed to track the US dollar. Flying Tulip describes earning income on ftUSD's backing assets through stablecoin lending and savings, alongside staking strategies that use borrowing to offset exposure to cryptocurrency prices. Holders receive separately claimable rewards in FT, Flying Tulip's token; rewards do not automatically increase the ftUSD redeemable for each sftUSD share. The treasury decides distributions from strategy income and protocol revenue, so returns vary."

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
FLYING_TULIP_NOTES = (
    FLYING_TULIP_STRATEGY_NOTES
    + """

sftUSD pays rewards in separately claimable FT, Flying Tulip's token. These rewards do not automatically increase the ftUSD redeemable for each share. Their value also depends on FT's market price.

Displayed returns include FT rewards valued through the [Ethereum FT/ftUSD Curve pool](https://etherscan.io/address/0x68102ff5406475881462880a8da3c9bc9181ad6c#code). Each distribution is valued in ftUSD using the latest available pool price and divided by the average amount staked during the period. The return calculation assumes reinvestment of those rewards; the vault does not reinvest them for holders. See the [performance calculation](https://web3-ethereum-defi.readthedocs.io/vaults/flying_tulip/index.html) for details.

Displayed entry and exit fees cover buying ftUSD with USDC before staking and converting back to USDC after withdrawal. Rates checked on 24 August 2026 were 0.07% in each direction on Ethereum and Sonic, and 0.10% on BNB Chain. Check the application quote for current charges. Network fees and the cost of selling FT rewards are excluded.

Withdrawals can be delayed when the protocol's withdrawal rate limit is reached, and ftUSD can trade below its dollar target if backing assets lose value or cannot be sold promptly. Contract addresses are listed in the [official address book](https://flyingtulip.com/address-book/flying-tulip-address-book-prod.json)."""
)
