# Preserve Shift share prices when the valuation feed expires

## The 25 November ltLLP incident

On 25 November 2026, Trading Strategy sampled the Shift Lighter LLP Wrapper
(ltLLP) on Arbitrum One (42161) at block **527,445,008**, 14:14:58 UTC. The
contract returned zero for `getSharePrice()`. The page showed zero TVL and a
100% lifetime drawdown. The stored feed valuation at that block was still
**262,645.912431 USDC**, with a share supply snapshot of
**244,389.801066482356659602 ltLLP**. The displayed loss came from interpreting
the contract's stale-price response as a valuation. The cause of the missed
feed updates is unknown.
[Vault page](https://tradingstrategy.ai/vaults/shift-lighter-llp-wrapper?period=max) ·
[Sampled block](https://arbiscan.io/block/527445008) ·
[Verified vault source](https://sourcify.dev/server/v2/contract/42161/0x956bdd9C18B786b082fd50C52722d254f0CB6964?fields=sources) ·
[Verified feed source](https://sourcify.dev/server/v2/contract/42161/0x4062c539D09A76D79e366F8C2a2e2F987EAD1FE0?fields=sources)

| UTC on 25 November | Arbitrum block | Observation |
| --- | ---: | --- |
| 13:00:11 | 527,427,680 | Feed publishes 262,645.912431 USDC. The share price is 1.074700 USDC. |
| 14:10:11 | 527,443,920 | The quote is exactly 4,200 seconds old and still accepted. |
| 14:10:12 | 527,443,921 | The quote exceeds the configured 70-minute freshness window. The contract returns zero. The feed tuple is unchanged. |
| 14:14:58 | 527,445,008 | Trading Strategy records the zero response. |
| 16:00:12 | 527,469,821 | The feed posts 262,736.192149 USDC, restoring the contract price to 1.075070 USDC. Share supply is unchanged across the incident. |

The onchain updates bracket the 1 hour 50 minute zero window.
[13:00 feed update](https://arbiscan.io/tx/0x34c61a75da435d413825c05095050709f6b5169199bde4a0b638846524c7d58d) ·
[16:00 feed update](https://arbiscan.io/tx/0x3e8a76aef8881bc2ae68f7841e61e1031e27fb1c3a0207f47815bfcd6b6abe26)

## Read the published valuation

- Obtain the complete verified feed ABI from Sourcify and add it to the Shift ABI inventory. Verify the interface and feed resolution for each supported Shift v1 deployment before rollout. Bind the feed returned by each vault's `tvlFeed()` and establish whether the binding and decimal scale can be cached across blocks from the verified implementation. Resolve them at the sampled block if they can change.
  [ABI guide](../../eth_defi/abi/README.md)
- In the [Shift adapter](../../eth_defi/erc_4626/vault_protocol/shift/vault.py) and its [multicall historical reader](../../eth_defi/erc_4626/vault_protocol/shift/historical.py), use `getLastTvl()` as the valuation source. Decode `(value, timestamp, supplySnapshot)` with the verified ABI. Read the tuple and required companion state at the same block. Pin a head request to one block if it needs multiple state calls.
- Use a shared calculation for both paths. The verified ltLLP implementation reads `lastTvl.value` in raw base-token units and divides by `lastTvl.supplySnapshot`, even while the quote is fresh. For the reviewed 18-decimal shares, compute `raw_price = value * 10**18 // supplySnapshot` with integer floor rounding. Convert to human-readable units once with `Decimal(raw_price) / Decimal(10**feed_decimals)`. The incident feed uses six decimals, so the raw result at block 527,443,920 must equal the contract getter's integer `1074700`. Verify that the other supported deployments follow the same scale and rounding before enabling them.
- Retain the historical row's current ERC-20 `total_supply` as a separate measurement. Use the snapshot supply to calculate price even when the outstanding supply changes after the publication. Use the feed's denomination-scaled `value` as last published NAV for `fetch_total_assets()`, `fetch_nav()` and the historical row. This makes TVL a published observation at `nav_timestamp` and deliberately replaces the old rounded price multiplied by current supply. Update the existing exact TVL assertion. Audit ranking, TVL-change metrics, adaptive reader state, scanner metadata, and all deposit, redemption and portfolio estimation callers. If a caller needs current assets or an execution quote, use its existing protocol-specific path and verify behaviour with an expired feed.
- Treat a missing feed or feed code at the sampled block, an unpublished tuple, zero snapshot supply, required-call failure or malformed return as unavailable. A published timestamp must be positive, at or after the resolved feed's deployment and at or before the sample block time. A feed replacement starts its own publication history. Preserve any existing valid early history when an archived reread is unavailable, and require point-in-time feed resolution before repairing across a replacement. Keep valid zero NAV with a published timestamp and nonzero snapshot supply as an economic observation. For historical failure, follow Buoy's `preserve_unavailable_price_history` behaviour and keep invalid reads out of adaptive reader-state valuation updates. Define the direct reader's error through its existing caller contract and verify scanner handling so an unavailable read cannot reappear as a numeric zero. Keep any execution or settlement freshness checks on their own contract path.
  [Published NAV reader guidance](../../.claude/docs/vault-integration.md#published-nav-readers)
- Keep the sample time and block as history keys. Store the original publication time in `nav_timestamp` and preserve it through cleaning and resampling. A repeated quote can appear as a flat price across scan samples, with the same publication time. Update scanner source metadata from `shift_getSharePrice` to the feed source. Verify the read and publication times reach the served dataset. Expose quote age and a stale indicator in the current vault metrics. Read `freshness()` in seconds from the vault at the same block as the current quote and include it in scanner metadata for the live metric. A quote is stale when its age is strictly greater than the configured window. Historical classification uses the window at each sampled block. Leave the Shift quote usable in historical return calculations when stale. The existing Buoy-only rule in [vault metrics](../../eth_defi/research/vault_metrics.py) also applies a generic age limit and marks period metrics with an error, so reuse the timestamp plumbing and give Shift its own status handling. Emit a deduplicated stale-quote warning for live scans with vault identity, publication time and age. Repeated history samples should retain the existing sparse-row optimisation.

## Record the incident in the implementation

Include the date, vault, sampled block and plan link in the adapter docstring
next to the feed read. Explain why a successful `getSharePrice()` call can
produce a zero price while the most recent published NAV is positive. Mention
the 70-minute ltLLP setting, the 14:10–16:00 UTC interval and the false 100%
drawdown on Trading Strategy. Give the plan path
`docs/claude-plans/2026-11-26-shift-stale-share-prices.md` so maintainers can
find the full timeline and evidence.

At the shared calculation, document why the 14:14 sample needs the feed's
paired supply snapshot and original 13:00 publication time. In the historical
reader, explain that carrying the quote across successive sample blocks
preserves continuity while the original publication time exposes its age.
The migration module docstring should cite the same incident, affected sample
and restoring feed update, and explain why a generic zero-price rewrite would
also change valid zero valuations.

## Repair and verification

1. Add a numbered migration for the persisted accessor change. Inspect a copy
   of the current raw price Parquet for the registered Shift vaults. Re-read
   candidate historical zero rows at their original blocks and verify the
   stored tuple and freshness condition. Include the confirmed ltLLP window
   `[527443921, 527469821)`. Freeze any additional confirmed vault and block
   scopes in the migration. Backfill missing samples within confirmed gaps
   at the scanner's ordinary cadence using point-in-time reads. If the audit
   finds missing early history caused by the old accessor, freeze that
   additional range explicitly.
2. Apply the same valuation helper and write only verified corrections. Keep
   genuine zero valuations, unrelated rows, original block/sample keys and
   Parquet metadata. Valid legacy rows outside the repair scope keep their
   derived TVL and unknown publication timestamp. Document that boundary in
   the migration docstring and compare the old and new TVL calculations on
   representative blocks, including supply changes. If the change creates a
   material break in rankings or TVL-change metrics, freeze the affected
   history in a verified reconstruction scope before rollout. Inspect the
   production-copy reader state for stale-zero effects on last TVL,
   qualification and polling cadence. Repair confirmed effects only for the
   affected vaults using valid observations and preserve scan checkpoints. Use the existing nullable
   `nav_timestamp` schema path for older files. Fail on unreadable inputs or
   ambiguous reconstruction, and confirm idempotence on the repaired copy.
   Check migration discovery and runner preview, then rebuild and validate
   raw and cleaned history, ranking metrics and published chart output on a
   copy. Include the run reminder in the implementation PR.
   [Migration guide](../../.claude/docs/migration-scripts.md)
3. Add a deterministic regression with recorded Arbitrum data for the last
   accepted block, first expired block, sampled block and recovery block.
   Exercise public adapter and historical result paths. Assert raw integer
   parity with the fresh contract getter and check 4,200 seconds accepted
   versus 4,201 seconds expired. Verify 1.074700 USDC through the expired interval and 1.075070 after recovery, the publication
   time, NAV, absent drawdown and supply unchanged in the incident fixture.
   Add a fresh sample where current supply differs from the feed snapshot and
   assert raw parity with the contract getter. Cover a valid zero,
   uninitialised or missing feed, future timestamp and failed or malformed
   feed return. Assert that cleaning and resampling preserve the original
   publication time and that the served metric reports the expected age and
   stale status. Update
   the existing Base ltPARA test that currently expects zero on expiry using
   a verified fixture and retain the extUSD fresh-price coverage.
4. Verify preservation of a previously saved valid row on a failed reread and
   test the migration on representative rows through the packaged runner.
   Run the focused Shift and migration tests. Follow the production runbook
   for deployment and validate that the incident no longer drives lifetime
   drawdown, TVL or abandonment status. Include this plan in the
   implementation PR so the code comments can link to the incident.

## Migration run reminder

Run migration 110 with the implementation image using the guarded Compose
preview and apply procedure in the [production runbook](../../.claude/docs/migration-scripts.md#production-runbook).
Compare inspected zero keys and repaired rows on a copy of the current
production Parquet before applying. Legacy corrections are restricted to the
three reviewed deployments and frozen block bounds. The migration uses the
archive RPC transport's retries and supports the scanner's `MAX_WORKERS`
setting, which defaults to four.

Apply queues normal post-processing. Recreate the scanner and post-scanner
with the same stamped image and keep cleaning, ranking exports and uploads
enabled. The cleaner detects the changed raw revision and regenerates the
affected histories, or rebuilds the baseline if the schema changed. Confirm
the pending marker clears after successful publication. Check that the served
ltLLP share-price series carries the 13:00 UTC quote through the outage, that
the corrected TVL reaches the chart, and that the false drawdown is gone from
rankings. Retain the raw backup until these checks pass. Repeated samples of
the same quote can be suppressed by the sparse writer, so the chart can show
a flat price between observed publications.

## Archive verification

The previous block had no deployed vault code for all three addresses. The
first deployed block and first feed publication were checked independently at
each deployment using the vault registry and the feed's `getTvlEntry(0)`.

| Vault | Deployment UTC | First publication UTC |
| --- | --- | --- |
| ltPARA | 18 September 2025, 13:15:19 | 20 September 2025, 18:00:05 |
| extUSD | 2 December 2025, 08:13:27 | 2 December 2025, 09:00:27 |
| ltLLP | 20 February 2026, 19:00:51 | 20 February 2026, 20:00:18 |

These checks establish that a vault-deployment bound preserves every
publication recorded by the reviewed immutable feed. A version of the raw
Parquet with the synthetic incident row was previewed against the archive RPC.
The current production Parquet and reader state are needed to verify the
complete repair scope and production output before deployment.
