# Vectis Finance curator

Research date: 9 November 2026.

## Identity and evidence

The live [Accountable detail endpoint](https://yield.accountable.capital/api/loan/608280921)
returned HTTP 200 and identified `company_name: Vectis Finance`, company URL
`https://vectis.finance`, and Ethereum share vault
`0x05ed3da1deeaaa57c8e09986e95ac271572dcf89`. The loan address is
`0x1Bf9863234168844A14D9dC3Df5cfae104A34262`; it is not the share token.
The fully paginated public listing returned HTTP 200, with 38 entries and only
one Vectis match. This was a manual live metadata check,
not a CI test or a new API integration.

The [official Ethereum vault documentation](https://docs.vectis.finance/vaults/ultrax-arb-vault-v2-eth)
confirms Accountable infrastructure, hedged perpetual positions, automated
allocation and funding-rate/basis arbitrage across Binance, Hyperliquid and OKX.
These support arbitrage, delta-neutral, funding-rate arbitrage, perpetual futures
and algorithmic trading tags. Futures linked to stocks or commodities do not
establish ownership of real-world assets, so no RWA tag is added.

The official homepage links `https://x.com/vectis_finance`; the handle is not
inferred. No RSS feed has been configured without verifying a working feed.
Public curator descriptions were checked against the description writing guide.

## Scope and publication

The local metadata snapshot dated 28 October contains one Vectis vault, matching
the supplied [vault page](https://tradingstrategy.ai/vaults/vectis-ultrax-arb).
The name fallback matches `Vectis UltraX`, avoiding blanket attribution of any
organisation using the word Vectis. The exact Accountable manager alias covers
manager-based resolution independently of the vault name.

Curator attribution is resolved at export time: no curator migration is needed.
Migration 044 refreshes only this Ethereum vault's persisted `_strategy_tags`.
It must be run after deploying the updated image, followed by normal cleaning,
curator metadata/logo uploads and vault exports. Follow the
[production migration runbook](../../.claude/docs/migration-scripts.md#production-runbook),
including inspection of Compose mounts and stopping writers for preview and apply.
No production migration was executed during authoring.

## Price history

The local metadata records first discovery at block 25,888,215 on
2 September 2026 at 07:48:23 UTC. The local raw hourly price parquet starts at
block 25,933,064 on 8 September 2026 at 13:52:47 UTC (96 rows in this snapshot).
Consider a separately reviewed, fixed-scope Ethereum backfill for that six-day
gap after checking the current production data and whether the vault held assets.
Migration 044 deliberately performs no price scanning and preserves existing rows.
