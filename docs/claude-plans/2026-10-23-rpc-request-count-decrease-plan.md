# RPC request count decrease implementation plan

Status: **Implemented; focused checks passed, deployment validation pending, 2026-10-23.** See the [validation results](2026-10-23-rpc-request-count-decrease-validation.md), including HyperEVM's partial failure and the unmeasured full-chain duration. Earlier reviews are recorded in the [review history](2026-10-23-rpc-request-count-decrease-review.md); their verdicts apply to those revisions. Based on `9d366717a`, this plan addresses findings 1, 2 and 4 in the [RPC usage analysis](2026-10-23-rpc-request-count-decrease-analysis.md), tracked by [PR #1650](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1650).

## Scope and order

Implement three focused changes, in separate commits. Prototype the historical feedback change first: its effect on scan duration is the main uncertainty.

| Change | Complexity | Expected RPC impact |
|---|---|---|
| Stop repeated daily/weekly polling within historical buffers | Medium | Removes redundant observations; the nine-slot example falls from nine observations to one per due low-frequency reader. Fleet-wide physical savings need measurement. |
| Batch metadata inputs and reuse lending reads | Medium | Targets the 167,672-request weekly metadata stage; Morpho v2 reuse alone removes two duplicate lending reads per eligible metadata observation. |
| Increase regular Multicall batches from 40 to 100 | Low–medium | About 60% fewer physical requests for large compatible batches, before rounding, retries and other overhead. |

Preserve sampling policy, protocol economics, unavailable-data handling and saved progress. Admission backoff and persistent capability caching remain outside scope. No production state changes, migrations, deployment or historical backfill are included.

## 1. Historical scheduling

`read_multicall_historical_stateful()` selects several blocks before yielding results. Reader timestamps advance only when the caller processes those results, so a due daily/weekly reader can be selected repeatedly within the buffer.

- Add concrete `BatchCallState.requires_result_feedback() -> bool`, defaulting to `False`. `VaultReaderState` requests feedback for a first observation or a polymorphic `get_frequency()` interval greater than one hour. A run-local observation marker suppresses repeated requests when failed feedback leaves progress unchanged; it is not persisted and never advances reader progress. Unknown TVL retains its existing hourly buffering after the first attempt. Existing disabled-state and subclass policies continue to apply.
- Collect all accepted methods for one block. If any accepted state requires feedback, flush the buffer and yield complete ordered block results before selecting the next block. The real `VaultHistoricalReadMulticaller` consumer already processes/restores all reader states before pulling the next combined result; preserve and document that contract.
- Keep normal buffering for established hourly readers. Preserve the existing `loky` process backend and reusable worker pool. Do not change concurrency architecture or persist speculative reader progress. A failed observation remains eligible under the existing failure policy.
- Validate positive scheduling `chunk_size` and use `>=` for the buffer ceiling. This parameter counts block tasks, independently of physical Multicall size and Parquet row buffering. Keep owned-resource cleanup correct when touching the generator.

Test the generator through the real historical consumer with a recording worker. Cover mixed hourly/daily/weekly readers, complete multi-method observations, cadence boundaries and irregular timestamps, first reads, recovery to hourly, and unavailable/incomplete observations that must not advance state. Check exact request-counter merging and retain existing greylist, freshness and persistence coverage. Established hourly readers may still perform bounded speculative work when their cadence changes inside a buffer.

Before expanding the implementation, compare request counts and elapsed time on representative staggered daily/weekly readers, including readers whose TVL stays unknown. Record how often feedback flushes occur. Frequent barriers may serialise the scan; assess the measured duration against the scanner's actual cycle requirements and revise if it no longer fits. No arbitrary percentage gate or separate benchmarking framework is needed.

## 2. Metadata batching and reuse

Extend the existing snapshot path for the following reviewed adapters, excluding HyperEVM metadata. Other adapters and custom overrides retain their current reads.

| Adapter | First pass, alongside `asset`, `totalAssets`, `totalSupply`, `share` | Second pass |
|---|---|---|
| Ordinary ERC-4626 | `paused` | None |
| Morpho v1 | `paused`, `fee` | Eligible idle-token balance |
| IPOR | `paused` | Eligible idle-token balance |
| Morpho v2 | Add core support, `paused`, `performanceFee`, `managementFee` | Eligible idle-token balance |

### Preparation

Add one shared `prepare_metadata_batch()` helper in `eth_defi/vault/rpc_batch.py` for normal discovery and pending recovery. Retain `fetch_metadata_snapshots()` compatibility and reuse its batching machinery. Both input passes use `timestamped_results=False` and explicit `max_workers`.

1. Fetch core, paused and fee inputs together at one numeric source block. Preserve served subcall reverts separately from malformed data and transport failures.
2. Deduplicate and warm denomination/share-token metadata using the supplied cache, source block, worker count and configured batch size. Preserve separate ERC-7575 share-token handling.
3. Fetch idle balances only when valid core inputs establish eligibility under the existing `ACTIVITY_STATUS_MIN_NAV` comparison. Pass the existing `Decimal` threshold into preparation; do not duplicate it or introduce a reverse import into `scan`.
4. Return ephemeral snapshots with raw inputs and source-block, chain, vault and adapter identity. Idle results also identify the denomination token. Preserve existing core keys and attach snapshots only to compatible adapters.

Comment the supported-token assumption beside the eligibility check: scanner denomination tokens are expected to have positive decimals. Cached `decimals == 0` is an expected unavailable-metadata condition here. Skip idle enrichment and retain adapter fallback; do not add cache provenance tracking, zero-decimal-token support or a migration.

Normal discovery keeps its supplied block; pending recovery captures a fresh numeric head on each attempt. Preparation and fallback reads use that same block. Preserve queue-before-network and catalogue-before-queue-removal ordering. Exhausted physical-request failures abort preparation, discard partial snapshots and leave pending work and previous economics intact. Served individual missing/malformed/reverted fields may use their existing bounded direct fallback; transport failure must not trigger a per-vault retry burst.

### Consumers

- Limit snapshot reuse to the reviewed adapter implementations. Preserve Morpho/IPOR flag overrides that call `super()`; only the base paused probe consumes the corresponding cached field. Keep IPOR-specific fees, permissions, deposit gates and other dynamic checks unchanged.
- Add optional `block_identifier="latest"` to `VaultBase.get_fee_data()`. Pass the numeric metadata block only for reviewed Morpho v1/v2 implementations that use this base assembly; retain other adapters' no-argument overrides. Fee getters reuse matching inputs or read directly at the requested block, preserving scaling and `None` semantics.
- Extend `fetch_idle_lending_snapshot()` to reuse raw total assets and idle balance at the same block. Add Morpho v2's combined lending opt-in to remove its duplicate reads. Preserve exact raw arithmetic, zero-assets behaviour, unavailable-token handling and the existing unclamped utilisation formula.
- Reject mismatched snapshots and preserve explicit `latest` semantics, separate share-token supply and stale-economics retention. No persistent snapshot fields or catalogue schema changes.

Use recording-transport tests for direct-versus-batched economic parity across all four families, partial chunks, snapshot mismatches, optional reverts and failed transport. Cover normal discovery and recovery, old pending candidates using a fresh block, the exact NAV threshold, and cached zero decimals through `TokenDiskCache`. Count preparation, constructors and fallbacks as well as batch requests.

## 3. Regular Multicall batches: 40 to 100

Introduce a shared regular batch-size default and raise it to 100. Update both physical reader limits and upstream chunks; changing only `MultiprocessMulticallReader` would leave the helpers dispatching 40-call tasks.

- Thread the configured size through historical entrypoints, `MulticallHistoricalTask`, worker construction, chunked dispatch, metadata/admission helpers and **`TokenDiskCache.load_token_details_with_multicall()`**, including its historical and metadata callers. Include the size in the worker reader-cache key. Preserve explicit caller overrides.
- Expose positive `MULTICALL_BATCH_SIZE`, default 100, on scanner entrypoints. Setting 40 must bound every regular request, including cold-cache token warmup. Log the effective size.
- Retain Mantle/Gnosis caps of 16 and greylisted isolation at its existing default of one. Keep regular HyperEVM at 40 for this change. Apply `min(requested_size, applicable_cap)` for these exceptions; other chains use the configured size. Do not introduce a universal chain allowlist or provider registry.
- Preserve bounded retry/provider rotation and completed-prefix recovery. Retry sizes derive from the effective physical limit, remain at least one, and never exceed applicable caps. Successful fragments must not be replayed. Preserve Monad's recent-state boundary and old rows.

Test physical payload boundaries, 40/100 overrides, worker reuse, chain exceptions, cold token caches and partial retry recovery. Reuse existing failure-path tests rather than duplicating the full provider-error matrix.

For 1,000 regular subcalls at one block, without retries or other overhead, 40 requires 25 requests and 100 requires 10: 15 fewer, or 60%. Count each block, task, lane and metadata pass separately. This is not a 60% saving across the whole scanner, and it must not be added to scheduling/reuse estimates.

## Validation and completion

Use the existing focused suites: `tests/event_reader/test_multicall_batcher.py`, `tests/erc_4626/test_vault_reader_state.py`, `tests/vault/test_rpc_batch.py` and `tests/erc_4626/test_rpc_metadata_recovery.py`, plus relevant adapter and existing greylist/freshness cases. Run through the parent Poetry environment with `.local-test.env` sourced and a 180-second timeout per command. Follow repository ABI, typing and formatting guidance; update affected API/environment documentation and the implementation changelog. Do not build Sphinx.

Extend the existing guarded `scripts/erc-4626/check-rpc-batch-parity.py`:

- Compare direct and prepared metadata at fixed numeric blocks using its Ethereum generic/Morpho v1/IPOR targets, the existing Arbitrum Morpho v2 test target, and the Base IPOR test target. Use supplied `JSON_RPC_*` configuration through `create_multi_provider_web3()` and independent temporary caches.
- Compare representative full 100-call payloads against 40 on the relevant providers, using real distinct targets and actual adapter calls. Count all attempts and report elapsed time and returned values. Boundary sizes and error combinations belong in deterministic tests; no mandatory 101-vault-per-chain or every-fallback-provider matrix.
- Add the bounded Arbitrum manual scan below through the real historical consumer. Reuse existing persistence tests; do not build a second live serial-reference/resumption framework.

Record commands, UTC date, blocks, redacted providers and pass/fail in the implementation PR. Missing credentials or timeouts are incomplete checks. Use the explicit lower batch override where provider measurements show regressions. Never run these checks against production output/state.

### Manual Arbitrum scan

During implementation, add a small `RPC_SCAN_CHECK=true` mode to the existing parity script. This mode requires only `JSON_RPC_ARBITRUM` and the configured Hypersync credentials. It must assert chain ID 42161 and call `scan_historical_prices_to_parquet()` through the production consumer, rather than merely testing adapter getters.

1. Use two to five existing Arbitrum vaults, including Morpho v2 `0xbeefff13dd098de415e07f033dae65205b31a894`; validate classification and deployment before the selected range. Fetch their metadata with the new preparation path. Keep token caches, reader state, RPC counters and Parquet output in a temporary test directory.
2. Capture `end_block = web3.eth.block_number - 16` once, then set `start_block = end_block - 129600`, `step=14400` and hourly frequency: nine sampled blocks over roughly nine hours at the configured Arbitrum block time. The end is exclusive. Log both numeric bounds and actual Hypersync timestamps before scanning. Honour explicit `START_BLOCK`/`END_BLOCK` for a repeat run and reject an invalid or wider-than-test range; never fall back to genesis or an unbounded scan. Use the cache-aware Hypersync timestamp path.
3. Run with `MULTICALL_BATCH_SIZE=100` and `MAX_WORKERS=2`. Check successful completion, readable Parquet, matching vault/block identities, no out-of-range rows, non-null expected prices/assets, and reader progress reflecting processed observations. Compare selected values with direct reads at the same sampled blocks. Record actual cadence decisions; fewer than nine reads is expected for a due low-frequency reader, while failed observations must not falsely advance its state.
4. Repeat with size 40 using the exact logged bounds and equivalent fresh temporary caches/state. Require equivalent observations and economic values; record all physical attempts, failures and elapsed time. A small cohort may not fill 100 calls, so this is an end-to-end correctness smoke test, not proof of the large-batch saving.

Manual command (set `PYTHONPATH` when sharing the parent Poetry environment):

```shell
source .local-test.env
PYTHONPATH="$PWD" RPC_SCAN_CHECK=true MULTICALL_BATCH_SIZE=100 MAX_WORKERS=2 timeout 180 poetry run python scripts/erc-4626/check-rpc-batch-parity.py
```

For the 40-call repeat, set `START_BLOCK` and `END_BLOCK` to the printed numbers and change `MULTICALL_BATCH_SIZE` to 40. Include the commands, bounds, vault addresses, output checks and request totals in the implementation PR's manual-test result. A timeout or unavailable historical state is an incomplete/failed check, not a pass.

Completion requires preserved observations/economics, fewer physical requests on representative workloads, working rollback through token warmup, and scan duration compatible with its operating cycle. Retain concise progress/flush summaries and existing request accounting. After a separately authorised deployment, use the existing counter comparison over a full refresh cycle; retain all persistent state if rolling back code.
