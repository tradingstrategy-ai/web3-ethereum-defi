# Vault scanner RPC reduction plan

Status: implemented in this branch; production rollout and measurements pending. Written 2026-09-30. Scope: implement recommendations 1–6 from the RPC usage investigation, preserve the current counters in a date-stamped backup, reset the counters at rollout, and compare the new measurements after two to three weeks. This document does not authorise or perform a production reset, deployment, provider cancellation or scheduled automation.

The scanner spends heavily on repeated preparation, classification and metadata reads, especially when one deterministic vault error prevents a chain from completing. Restore reliable progress first, then remove unnecessary requests and batch recurring reads. Preserve vault coverage, historical observations and the existing freshness policy while reducing physical RPC attempts. Provider consolidation and changing historical sampling resolution are outside this implementation.

## Evidence and measurement limits

The investigation used a stable-copy snapshot of `/root/.tradingstrategy/rpc-tracking.duckdb` and its WAL around 12:55 UTC on 2026-09-30, copied metadata, reader state, discovery cache documents, retained scanner logs and current container output. The requested `/root/.tradingstategy` path was a typo. The analysis snapshot is temporary evidence, not the verified production backup required below.

The counters span 2026-07-20 through the partial day 2026-09-30: 43,754,823 physical request attempts across persistent cycles 1–776. They count provider attempts and retries, not logical functions inside a Multicall and not provider invoices. They do not attribute requests to vault protocols. Provider preflight verification and some separately constructed clients are outside the phase counters. Native REST feeds are outside this JSON-RPC measurement.

| Method | Recorded requests | Share |
|---|---:|---:|
| `eth_call` | 37,546,438 | 85.8% |
| `eth_getBlockByNumber` | 5,606,025 | 12.8% |
| `web3_clientVersion` | 482,513 | 1.1% |
| `eth_chainId` | 109,138 | 0.25% |
| Other methods | 10,709 | 0.02% |

Lifetime discovery and metadata requests total 39,355,537, or 90%; price-phase requests total 4,399,286. The lifetime split is distorted by a major failure incident:

- Between 10 and 22 September, Base discovery consumed 31,536,057 requests, out of 32,565,237 total requests across chains. Retained logs repeatedly report `NotImplementedError: Unknown Lagoon version v1.0.0`, principally for `0x7eea189f34e10e7fe5386ba8d49ab41f95b7d54f`, with another candidate also present. Failure prevented successful discovery persistence and caused repeated extraction of roughly 12,000 candidates. These exception traces disappear from the retained logs on 23 September; this does not establish that every unsupported-version path is now safe.
- On 30 September, Ethereum, Base and Arbitrum repeatedly fail with `AttributeError: 'NoneType' object has no attribute 'symbol'` in `VaultReaderState.exchange_rate()`, reached through freshness processing or its final audit. Captured container output contains 13 initial and 13 retry failures for each chain. The snapshot already records 845,215 requests today, including 834,682 price-phase requests. Later logs show additional affected chains. This is an incident, not a healthy baseline.

Base accounts for 34,834,610 lifetime requests (79.6%), followed by Ethereum at 3,283,572, Arbitrum at 1,742,240 and HyperEVM at 1,443,192. Lifetime provider totals are dominated by dRPC (34,822,583) and Goldsky (7,806,878). In the 26–29 September window below, Goldsky accounts for 520,985 requests, dRPC 164,992, HyperEVM Alchemy 23,063, Blast Alchemy 1,523 and other providers 220. The ranking changes substantially when the Base replay incident is excluded; do not use lifetime shares alone to choose optimisation priority or providers to retire.

The following 26–29 September window provides recent context, not a comparable healthy baseline. It straddles the freshness implementation dated 28 September, which introduced low-activity TVL probes. Recover the actual deployed image/commit and deployment time from retained logs where possible; otherwise label that boundary unknown. Split pre-change and post-change periods, and identify metadata refreshes from logs rather than attributing the daily increase to them without evidence.

| Chain | Discovery requests | Price requests | Total |
|---|---:|---:|---:|
| Ethereum | 107,099 | 122,261 | 229,360 |
| Base | 92,365 | 43,050 | 135,415 |
| Arbitrum | 59,357 | 32,871 | 92,228 |
| HyperEVM | 12,056 | 60,272 | 72,328 |
| Polygon | 56,670 | 11,623 | 68,293 |
| Monad | 26,041 | 2,980 | 29,021 |
| Remaining chains | 60,482 | 23,656 | 84,138 |
| All chains | 414,070 | 296,713 | 710,783 |

Recent discovery includes 54,328 block requests; the price phase includes only 207. Recent price scanning includes 53,578 client-version requests, 18% of that phase. Today's snapshot includes 279,075 client-version requests, 33% of the incident total. These are measurable opportunities, not additive promises of savings after the retry loop is fixed.

Daily totals were 47,987 on 26 September, 49,415 on 27 September, 301,162 on 28 September and 312,219 on 29 September. Distinguish ordinary scans, full metadata refreshes, failures and backfills in every before/after report.

The production scheduler has a one-hour tick; Ethereum, Base and Arbitrum have eight-hour scan intervals; the default for other EVM chains is 24 hours. `MAX_WORKERS=24`, discovery cache lifetime is seven days, and the default whole-chain retry count is one. Captured low-activity probes per attempt are 5,438 on Base, 3,823 on Ethereum and 875 on Arbitrum. Of 24,467 persisted readers, 18,633 (76%) have weekly categories: tiny, faded or peaked. Historical throttling does not eliminate per-scan preparation or low-activity probing.

Protocol attribution is inferred from populations and code, not measured request totals. Base has 2,496 Morpho vaults, including 2,049 below the normal deposit-activity threshold; generic ERC-4626 populations are 5,350 on Base and 4,345 on Ethereum. Ethereum and Polygon have 1,751 and 1,924 Enzyme vaults respectively. Reviewed Enzyme factory leads already bypass broad feature probing. Preserve this optimisation. HyperEVM readers accessing HyperCore need special gas and consensus handling.

## Source and documentation context

Read the [request accounting documentation](../source/api/provider/rpcdb.rst) alongside `eth_defi/provider/rpcdb.py`. It defines append-only physical counters, error aggregation, zero-call markers and cycle allocation. `items_scanned` is repeated on method/provider rows; sum it only after deduplicating by chain, phase, date and cycle with `max()`. An early failed discovery may record zero items despite substantial work, so old counters cannot supply every useful denominator.

The [Multicall tutorial](../source/tutorials/multicall3-with-python.rst) explains batching. The [vault scanning tutorial](../source/tutorials/erc-4626-scan-prices.rst) and [freshness implementation plan](2026-09-28-vault-price-freshness.md) describe the USD thresholds and source-time contract. The [reader state guide](../../eth_defi/erc_4626/vault_protocol/README-reader-states.md) describes call status; its corrected warmup narrative records that the common historical invocation is currently commented out. Do not assume warmup is active or enable it indiscriminately.

Use the [discovery cache plan](2026-07-26-lead-discovery-cache.md) for current seven-day behaviour. A cache miss currently combines incremental Hypersync events, reclassification of existing leads and metadata extraction. Read the [HyperEVM consensus note](../README-hyperevm-goldsky-failure.md) and [HyperCore gas note](../README-hyperevm-hypercore-read-gas.md) before changing batching or retries. Read `eth_defi/abi/README.md` before loading or changing ABI files.

Primary implementation areas:

- `scripts/erc-4626/scan-vaults-all-chains.py`: documented environment interface; implementation delegates to `eth_defi/vault/scan_all_chains.py`.
- `eth_defi/vault/scan_all_chains.py`: chain phases, low-activity TVL checks, whole-chain retries, success-only scheduler persistence and the shared pipeline lock.
- `eth_defi/erc_4626/vault.py` and `eth_defi/vault/historical.py`: denomination conversion, reader cadence, source freshness, Parquet publication and reader-state return.
- `eth_defi/erc_4626/lead_scan_core.py`, `discovery_base.py`, `classification.py`, `scan.py` and `lead_discovery_state.py`: candidate selection, feature probes, metadata extraction and successful cache persistence.
- `eth_defi/provider/anvil.py`, `broken_provider.py` and `multi_provider.py`: Anvil detection, cached block resolution and provider verification.
- `eth_defi/event_reader/multicall_batcher.py`: batch timestamps, physical request accounting, splitting and provider fallback.
- Morpho v1/v2, IPOR and Euler adapters: metadata economics and protocol-specific conversion/liquidity behaviour.

## Data and behaviour to preserve

- Keep all existing price, metadata, reader-state and dense timestamp-cache files. Counter reset must affect only accounting tables and their accounting metadata. Never reset discovery or reader progress as part of measurement.
- Keep naive UTC datetimes, actual source `timestamp`, and publication-only `written_at`. Unknown denomination conversion cannot certify USD eligibility. Do not substitute a USD rate of one or fabricate an observation.
- Keep $1,500 eligibility entry, $1,000 exit, $10,000 hourly polling boundary, seven-day unchanged-row retention and the 14-day source-age audit. Keep current historical sampling resolution and dedicated/contextual reader semantics.
- Preserve historical Monad rows before the provider's dynamically detected state boundary. No archive-complete Monad assumption, genesis-to-head state scan or retries of evicted state.
- Use Hypersync for event discovery and cached historical timestamps. Never introduce `eth_getLogs` bulk fallback.
- Preserve total physical-attempt accounting, including retries. Add operation labels without counting the same request once in a parent and again in a child total. Mixed-protocol Multicalls have one physical request; record logical protocol calls separately rather than attributing one physical request to every protocol.
- Put new backoff, admission-probe, candidate-queue, feature-cache, conversion-resolution and Monad-boundary state in versioned sidecar files under the mounted pipeline directory. Keep existing `reader-state.pickle`, vault metadata pickle and timestamp-valued scan-cycle JSON schemas readable by the previous release. Do not add reader serialisation keys, new pickle classes or nested backoff objects to these files. Use atomic sidecar publication and the shared writer lock; missing sidecars initialise pending work without changing authoritative historical progress. Record a rollback floor explicitly if a later implementation cannot preserve this compatibility.

For legacy conversion evidence, consult the stored denomination address and cached supported token symbol before trusting historic USD TVL. Values derived from the unknown-rate sentinel are not verified evidence. When evidence cannot be recovered, retain the vault in unverified diagnostics without certifying USD eligibility. Store negative-resolution deadlines in a sidecar unless the token-cache entry format is demonstrably readable by the previous release. On roll-forward after a rollback, reconcile stale probe/queue sidecars with authoritative reader and lead history.

## Implementation sequence

Prepare accounting backup/reset/reporting before rollout. Reset at the crash-fixed rollout boundary and collect at least seven complete healthy UTC days with `VAULT_RPC_OPTIMISATIONS=false`, then snapshot before enabling the additional flag-controlled reductions. The implemented baseline includes common coverage/recovery safeguards, HyperEVM head safety, daily Monad boundary measurements and negative-token retries; it does not isolate issue 1 alone. Record this boundary and use the original pre-reset backup only as incident-inclusive context. Keep compatible measurement labels before and after enabling the flag, with unchanged legacy top-level totals. Do not delay an urgent crash fix for larger discovery changes.

### Issue 1 Restore progress and bound retries

1. Make the common exchange-rate/freshness path handle unavailable denomination metadata explicitly, without dereferencing a missing token. Use a distinct `denomination_unavailable` resolution outcome for absent `asset()`, missing symbol and provider/decoding failure; reserve `UNKNOWN_EXCHANGE_RATE` for a successfully resolved token whose symbol has no supported conversion. Neither outcome can certify current USD TVL. Preserve protocol-specific overrides. Cover static readers, contextual readers, low-activity probes and the final freshness audit. Keep previously verified qualified vaults in the expected-live set using their authoritative last/max USD observations and prior verified conversion evidence, even when current token resolution fails; report overdue/unverified with the distinct reason rather than making `freshness_qualified=False` silently remove them. Include affected vault identities in diagnostics. Inspect existing denomination and `TokenDiskCache` caching before changing it: memoise failed resolution within the scan and ensure any persisted negative results have a finite retry deadline. Remove the broad `AttributeError` catch in `fetch_current_vault_tvl_usd()` once this expected case is explicit. Retain available real observations; unavailable metadata is not proof that a formerly meaningful vault is now small. Document the distinction from genuinely unsupported conversion in the freshness tutorial and API.
2. Contain expected unsupported-adapter/configuration errors per candidate in metadata extraction and both active and low-activity price selection. `create_vault_instance()` currently runs before `create_vault_scan_record()`'s main error handling, and the active price branch is also unguarded. Introduce a dedicated unsupported-version exception raised by version resolution; do not catch generic `NotImplementedError`, which can identify unimplemented programmer paths. Record the unresolved candidate and reason, preserve its previous valid metadata and successful unrelated results, and arrange a bounded retry. Keep unresolved previously qualified vaults in expected-live/overdue reporting. Catch specific expected errors; unexpected programmer failures remain visible and fail the phase.
3. Represent transient provider errors, deterministic candidate errors and unexpected internal failures separately. Whole-chain immediate retries apply only where retry can plausibly help. Unexpected internal failures get no immediate whole-chain retry and enter persisted backoff. For any repeatedly failed chain, persist `last_attempt_at`, failure category, consecutive count and `next_retry_at` in a new backoff sidecar, independently from `last_success_at`; keep the existing scan-cycle JSON timestamp-only. Proposed transient and internal-failure delays are 1h, 2h, 4h, then at most 8h on major chains and 24h on others. Log retryable failures as concise warnings; log unexpected internal failures with a traceback when suspending that attempt, without repeating immediate attempts. Deterministic candidate failures use a separate 24h retry queue and can be made due explicitly after a supported-version/code change. Maintenance lock contention is a scheduler deferral, never a chain failure. An operator force flag can bypass backoff without resetting historical state.
4. A partial metadata refresh may advance its event cursor only if every discovered lead and unresolved candidate is durably retained for future work. Keep successful refresh time separate from last attempt and pending candidates. Candidate degradation must remain visible in diagnostics; do not report complete freshness merely because the chain's useful work finished.
5. Correct the publication/progress order. The existing writer can replace the price Parquet before a final freshness-audit exception prevents reader-state persistence. Compute conversion/audit decisions before publication where possible. Return durable-publication status, reader states and a structured audit outcome together, so the caller can persist progress after durable Parquet publication even when the audit reports failure. Surface that failure/degradation without claiming freshness or replaying the already committed range. Do not blanket-catch diagnostic exceptions. Cover restart between Parquet replacement and reader-state replacement with an explicit replay-safe recovery path. Never advance reader progress ahead of durable price data; a bounded duplicate-safe replay is preferable to missing rows. Do not attempt a two-file atomic rename or discard history to recover.

Short historical-state windows need separate limits. Record Monad's dynamically probed oldest executable state block/time on each attempt, and bound any additional retry delay against the observed retention window with a margin. If an outage or the existing scan schedule already exceeds that window, report irrecoverable gaps explicitly rather than promising repair or retrying evicted state. HyperCore-dependent values also cannot be reconstructed after their short read window closes; retain existing real observations and label such gaps. This plan does not promise complete hourly history for those readers or increase their normal sampling gaps silently.

Acceptance: missing denomination or an unsupported Lagoon candidate cannot cause hourly replay of all unrelated vaults; a transient provider outage remains retryable; a restart honours persisted backoff; successful price observations and reader progress survive recovery without data loss or falsely fresh rows.

### Issue 2 Remove unused classification timestamps

`probe_vaults()` consumes features only, but `read_multicall_chunked()` defaults to `timestamped_results=True`, causing a block read for every chunk at the same block. Pass `timestamped_results=False` in feature probing after confirming every live consumer. Keep the generic batcher's default for callers that need source timestamps. Other metadata-only batch callers can be changed individually where timestamp consumption is absent. Never use the synthetic timestamp from this mode as a source price timestamp.

Acceptance: identical feature classification at the same block with zero timestamp RPCs attributable to the probe stage. Assert physical calls at a recording/mock transport as well as counters: subprocess statistics propagation must not allow this check to pass vacuously. Price/contextual timestamps remain real. The 54,328 recent discovery block reads are an upper bound on the available reduction, not a claim that all originate in this caller.

### Issue 3 Cache Anvil detection

Cache the result of `is_anvil()` per Web3 connection, not globally by chain ID. Check whether a locally known Anvil launch can resolve it without RPC. Cache only successful detection and make invalidation explicit if a connection's endpoint identity changes. An Anvil fork and a remote node on the same chain ID must never share detection or cached head state. Keep wrong-chain rejection and fallback-provider verification intact; do not confuse these with optional client-version reads.

Pass a safe numeric metadata block to adapters within a bounded batch where appropriate so `_get_block_identifier()` does not repeatedly enter the cached-head path. Give it a maximum age and refresh between batches, with a tight chain-999 limit based on the documented HyperEVM execution window. Do not pin one head block for a multi-minute HyperEVM scan. Preserve special adapters that deliberately need another source/block context.

Acceptance: after initialisation, repeat metadata reads issue no further client-version requests on a stable connection; Anvil tests still use fork-safe block behaviour; fallback rotation and incorrect chain-ID responses remain correctly handled.

### Issue 4 Batch and schedule low activity TVL probes

1. Add a dedicated persisted probe state, separate from authoritative historical reader progress. Suggested fields: vault identity, source block/time, probe attempt time, successful raw TVL and conversion result, error category, and next probe time. Do not persist a second qualification flag: compute eligibility through the shared `is_meaningful_usd_tvl()` hysteresis using authoritative reader history and verified current observations. Decide probe due-ness from persisted scheduling and detection data before adapter construction. Instantiate only due unqualified candidates, while still constructing readers needed for qualified historical scanning. Include construction and preparation in probe-stage accounting. Load legacy installations without this state by scheduling a bounded initial pass; absence of probe state must never reset reader state.
2. For ordinary supported ERC-4626 adapters, use reviewed stored denomination information and batch `totalAssets()` at a numeric safe block subject to the per-batch age limit in issue 3. Keep protocol conversion overrides and dedicated `fetch_nav()` paths for non-standard, contextual and native readers. Identify known HyperCore/oracle-dependent readers through reviewed protocol/address configuration and persisted capability evidence from `ReadFailure`/`-32003` signatures; isolate them from shared ordinary batches without permanently blacklisting a transient failure. Missing mappings, stale implementation identity, failed calls and unknown conversion produce explicit outcomes, not a zero TVL.
3. Reuse a valid observation already obtained by the same scan before issuing another probe. Proposed cadence: immediately for new candidates; at most daily for unqualified vaults during their first 14 days; at most weekly afterwards when persistently tiny; and no later than the normal chain scan interval for previously qualified or near-threshold vaults. Recent known deposit/configuration activity can make a candidate due, but do not add a new expensive event scan solely to schedule probes. Retain coverage when event information is stale or unavailable.
4. Qualified vaults remain in historical/freshness scanning between probe refreshes, including in `expected_live_vaults` when unavailable or not due for admission probing. Failed probes must preserve last-known eligibility and become diagnostics with bounded retry. Set a margin so probe scheduling cannot push a previously qualified vault past its real observation deadline. Apply the short-state-window rules in issue 1; a skipped tiny-vault check cannot later recreate unavailable HyperCore or Monad values. This changes the recheck latency for formerly tiny vaults; document the maximum daily/weekly admission latency rather than implying instantaneous qualification.
5. Make batch size and worker count configurable through environment variables, using the existing Multicall gas/fallback handling. Separate HyperCore-heavy readers and avoid a generic large-batch increase. All threaded `joblib.Parallel` wrappers expose `max_workers`; log candidates, due probes, cache hits, batch sizes, failures and qualification changes.

Acceptance: ordinary TVL requests scale approximately with due batch count instead of candidate count; batching matches individual reads at the same block; new/meaningful vaults remain represented; unknown conversion and intermittent failures do not remove qualified vaults from overdue reporting; historical boundaries and rows are unchanged.

### Issue 5 Separate discovery classification and metadata refresh

1. Give incremental Hypersync event discovery, classification refresh and metadata refresh distinct completion state. Initially preserve the existing seven-day new-event discovery cadence unless a separate product decision changes it. This refactor must not accidentally multiply event API usage.
2. Persist cumulative lead activity and new candidates after completed event chunks. The cursor describes covered events, never completed metadata. Queue incomplete classification/metadata explicitly and resume that queue on restart. Persist a lead/queue record before advancing its cursor; the shared metadata pickle stays under the existing writer lock.
3. Classify newly discovered or due/invalidated candidates instead of probing every stored lead. Cache known features with a version and refresh deadline, including a finite TTL for negative/broken classifications. Do not turn a cached failure into permanent exclusion. Preserve reviewed factory attribution for Enzyme and the independent GMX/YieldBasis catalogue reconciliation on generic cache hits.
4. Separate static metadata (names, denomination/share-token identity, protocol attribution) from dynamic metadata (permissions, fees, liquidity and activity state). Preserve each field's existing refresh behaviour initially; only adopt a longer deadline after defining acceptable staleness. Selective upgrades and explicit invalidation must refresh potentially mutable proxy mappings.
5. Replace the all-enabled-chain signature with per-chain discovery inputs and explicit protocol/metadata versions. Preserve correctness dependencies of events, factories and chain-specific classifiers. Changing one chain's configuration should not invalidate unrelated chains. Migration honours valid legacy cache documents until natural expiry, preserves every lead/row/cursor and queues required work without a genesis scan. Stagger necessary forced refreshes, record invalidations as measurement events, and avoid incidental changes to hashed discovery functions or global metadata versions during comparison windows.
6. Write successful metadata in bounded batches, retaining prior valid rows for pending candidates. Pending work and successful completion are separate; retry one unresolved vault instead of the entire catalogue. Add fresh API/docs stubs for any new public modules.

Acceptance: adding a chain does not trigger unrelated full refreshes; one unsupported candidate survives restart as pending work; classification reads depend on new and due candidates rather than the full catalogue. Dynamic metadata reads still depend on vaults due under unchanged field deadlines. Metadata deadlines are met and new candidates are eventually admitted; incremental event counts are not duplicated.

### Issue 6 Reuse metadata reads and stored mappings

Start with the demonstrated duplicate economics reads in Morpho v1 and IPOR. `create_vault_scan_record()` fetches total assets, then available liquidity, then utilisation; utilisation separately fetches total assets and idle balance again. Provide a block-scoped economic snapshot or combined adapter operation and derive supported fields from its shared values. Apply the same pattern to v2/Euler only where equivalent semantics are confirmed. Idle balance is not automatically redeemable liquidity; preserve each protocol's existing definition.

Batch ordinary `asset()`, `totalAssets()`, `totalSupply()` and supported token fields across due vaults with explicit per-call failure handling. The shared economic snapshot catches specific RPC/decoding errors, replacing the adapters' blanket exception swallowing in touched paths. Derive ratios from raw integer values before decimal conversion. Preserve previous valid metadata with a stale/unverified status on failure. Cache denomination/share-token mappings with chain, vault, mapping version and implementation/refresh provenance. Reuse `TokenDiskCache` rather than building a second token metadata cache. Do not permanently cache mutable proxy mappings or reuse values across different numeric blocks. Invalidate on detected upgrade, expired deadline, explicit force, or incompatible adapter version.

Acceptance: repeated metadata fields share one read per economic input and source block; cached token mappings avoid per-scan relationship reads when valid; an upgrade forces refresh; failures preserve previous valid metadata with a stale/unverified indication; fee/liquidity/permission values retain their meanings.

## Counter backup reset and comparison

Implement a focused accounting maintenance helper and environment-driven script, proposed as `scripts/erc-4626/reset-rpc-counters.py`. The default mode only reports the proposed backup/reset and summary; an explicit `RESET_RPC_COUNTERS=true` selects mutation. Accept `RPC_TRACKING_DATABASE_PATH`, `PIPELINE_DATA_DIR` and `RPC_COUNTER_BACKUP_DIR` through environment variables. Require a stable operator-supplied `RPC_COUNTER_RESET_ID` for mutation and reuse it for recovery; never generate a new reset ID automatically on a retry. No new command-line parser or credentials in output.

At the rollout boundary, perform the following sequence:

1. Inspect the deployed Compose configuration and effective mounted paths. Wait for `vault-scanner-looped` to be idle, stop that service without interrupting a running scan, and confirm the pipeline lock is free. Confirm whether `post-scanner` or another service opens the accounting database; any accounting writer must be idle and closed. Run maintenance through the profile-only oneshot service with an overridden entrypoint, using the same `/root/.tradingstrategy` mount. Do not execute its default scan entrypoint, change `HOME`, or create an unmounted database. Display progress while waiting. If idle/exclusive access cannot be established, abort without mutation.
2. Acquire `wait_other_writers(PIPELINE_DATA_DIR / 'scan-pipeline')`, the same lock the scanner holds for its accounting connection lifetime. Also require DuckDB exclusive access so an unexpected writer that ignores the pipeline lock causes an abort. Drain completed phase accounting before the reset; do not reset an open connection in another process. Maintenance shares this lock until backup verification and reset have completed.
3. Checkpoint and close the source accounting database, confirming no live `.wal` remains. Create an exclusive, never-overwritten backup under `/root/.tradingstrategy/backups/rpc-counters/`, named `rpc-tracking-before-rpc-reduction-YYYY-MM-DDTHHMMSSZ.duckdb` using the actual UTC time. For example, a rollout on this date uses `rpc-tracking-before-rpc-reduction-2026-09-30T143000Z.duckdb`. Set private mode 0600. A byte copy is valid only after checkpoint/close; never copy only the main file while a live WAL is present. Keep this dated directory outside rolling-backup rotation; the per-tick backup is not a substitute. Recommend a verified off-host copy for protection against volume loss.
4. Reopen the backup read-only and verify every table present, including the two legacy tables and any new detail/epoch tables: schemas, canonical row-content digests, row counts, total calls/errors, date range, per-chain/phase/method/provider aggregates and maximum cycle ID must match the source where applicable. Compute SHA-256 and record a companion private date-stamped manifest with verified summaries/digests, source/backup paths, UTC boundary, deployed commit/image, DuckDB version, measurement version and redacted configuration. Preserve errors privately because provider messages can contain sensitive URLs. Flush the backup, manifest and parent directory durably before reset. Any copy, verification, disk-space or checksum failure aborts with the old counters intact.
5. Reopen the source exclusively and start the reset transaction. Before any schema change or delete, recompute all verified per-table source summaries and canonical row-content digests and compare them with the manifest; also recheck the main-file checksum/size and WAL status captured after checkpoint. Any change since backup, including an append by a writer that ignored the pipeline lock during the closed-file interval, aborts and rolls back with all rows intact. Only after this check, clear `vault_rpc_api_calls`, `vault_rpc_api_errors` and this plan's new operation-detail table if present; leave unrelated tables untouched. Insert an authoritative epoch record containing reset ID, manifest ID, UTC boundary and old maximum cycle in the same transaction. Preserve cycle monotonicity for both upgraded and rolled-back code by inserting one zero-call marker into the legacy calls table: `phase='counter_reset'`, `api_call=ZERO_CALL_MARKER`, `cycle_number=<old maximum>`, `call_count=0`, `items_scanned=0`, and reserved chain/provider values documented in the accounting API. Existing `allocate_cycle()` then still returns a number above the old maximum. Exclude the reset marker from scan/item denominators and reports. The epoch record is the authoritative reset-committed receipt; external completion files are supplementary. No price, metadata, reader, scan-cycle, lead-discovery or timestamp-cache reset is permitted. Existing counter tables stay unconstrained: do not add ART-backed primary/unique indexes or change their positional-insert schema.
6. Confirm the calls table contains only the zero-call reset marker, the errors/detail tables are empty, critical state-file hashes are unchanged, the backup is readable and both current and rollback allocators remain monotonic. Restart normal scheduling, observe new non-zero counts and confirm accounting errors do not trigger a price rescan. Make interruption recovery idempotent: read the in-database reset ID before any delete, and never reset a second time merely because an external receipt was lost. Retain maintenance records distinguishing backup prepared, reset committed and verified. A retry with the same reset ID resumes verification; a new reset ID requires a separate intentional maintenance invocation and fresh backup.

The mutation utility and operator instructions are implementation deliverables, not commands to execute during this planning task. Counter reset is a rollout step after the safety checks are implemented and reviewed. If issues are deployed in stages, record every deployment boundary and retain counters between stages; do not repeatedly wipe the measurement window.

After **14 and 21 full UTC days from enabling the reductions**, take further date-stamped, verified snapshots without clearing counters. Compare the original pre-reset evidence and the prospective flag-off baseline separately. These are manual follow-up checkpoints, not scheduled automations. Exclude partial days, incidents and the first optimised week if it contains mapping/cache migration. In that case day 14 is interim, and day 21 can supply two complete steady-state metadata periods. Delay the primary efficiency verdict when staged releases, invalidations or outages leave less comparable data. Use actual deployment, enablement and snapshot dates in each report.

Comparison rules:

- Exclude partial reset/follow-up and deployment days, and separate the 10–22 September replay incident, the 30 September incident, pre/post-freshness deployment, ordinary scans, metadata-refresh cycles and backfills. The 26–29 September counters cannot establish a comparable baseline for the 30% target. Use the prospective flag-off baseline for that target, with old-counter comparisons as secondary context. Include at least two complete seven-day post-optimisation metadata periods, and record deployments, cache invalidations, downtime and provider changes.
- Compare requests/day, requests/attempt, requests/successful phase and requests/new valid price observation; show completed/skipped/failed/degraded scans beside them. Use optimisation-independent catalogue/metadata population counts and vaults with real rows written as coverage denominators. Show requests/logical scanned item as a diagnostic whose meaning can change when only due candidates are selected, rather than headline savings evidence. A lower total achieved by scanning less or losing coverage is a regression. Old zero-item failures have unavailable item denominators, not zero cost.
- Count items with a cycle-level `max(items_scanned)` before summing cycles; do not multiply them by method/provider rows or retries. Document the existing probed-lead meaning of `items_scanned`; add due/cache/population detail separately rather than redefining it silently. Aggregate total physical attempts once. Retain comparable old `lead_discovery`/`price_scan` labels and leave both legacy table schemas unchanged because older code inserts positionally. Add operation detail only in a separate table. Report accounting-write failures and allocated/logged cycles with missing rows beside every comparison, so a silent accounting regression cannot appear as savings.
- Report chain, provider and method savings and the new subphase breakdown: feature probes, metadata, TVL admission probes, reader preparation, historical Multicall and validation/audit. Capture submitted/due/cached candidates and scan outcome prospectively. Mixed-protocol physical requests stay mixed; protocol logical-call counts are a separate measure.
- Reconcile coverage/freshness: retained vault set, candidate backlog, qualified and overdue counts, source row age, real rows written, discovery lag and metadata age. Compare identical fixed-block read fixtures for code efficiency and real production windows for operational savings.
- State request reduction and provider-specific cost estimates separately. Use actual provider statements/rates for invoice claims. Existing counters omit preflight/some clients; either account for these prospectively in a separate bucket or label the gap without pretending the historical data contains them.

Proposed acceptance target: remove the observed deterministic whole-chain replay patterns; virtually eliminate classification timestamp and repeated client-version requests in their targeted paths; cut ordinary low-activity TVL physical reads by at least 80% on fixed candidate sets; and aim for at least 30% fewer physical requests on comparable successful whole-chain workloads after issues 2–6. These are targets to validate, not savings derived from the unhealthy incident totals. Report measured results even if the target is missed.

## Tests and rollout

Extend focused tests in `tests/vault/test_price_freshness.py`, `tests/vault/test_scan_all_chains_rpc_usage.py`, `tests/erc_4626/test_vault_reader_state.py`, `tests/erc_4626/test_lead_discovery_state.py`, `tests/erc_4626/test_scan_rpc_usage.py`, `tests/event_reader/test_multicall_timestamp_rpc_usage.py` and `tests/provider/test_rpcdb.py`. Add adjacent test functions for Anvil connection identity, admission probe persistence, partial metadata queues and economic read reuse as necessary.

Required cases include missing token/symbol across all freshness paths and finite negative-cache retry; a previously verified qualified vault remaining expected-live/unverified when token resolution fails, with no new USD certification; protocol conversion overrides; unsupported Lagoon constructor with another successful candidate in both metadata and active price paths; a generic `NotImplementedError` remaining fatal; transient versus deterministic/internal failure and restart/backoff; lock contention not becoming a chain failure; Monad retention-aware delay and explicit irrecoverable gaps; failures before/after Parquet publication and reader-state persistence; unused probe timestamps measured at transport level; remote and Anvil connections sharing a chain ID; fallback identity; refresh of stale chain-999 batch blocks and isolation of HyperCore readers; exact-boundary admission and stale/failed probes; due selection before low-activity construction; partial classification queues and legacy cache expiry migration; changed mapping/implementation; duplicate read counts; and non-duplicated request/item accounting. Write new sidecars and refreshed legacy files, then load the legacy files with previous-release loaders or equivalent compatibility fixtures; also test roll-forward with stale probe/queue sidecars. Inject an audit failure after `os.replace`: reader progress must advance for durable rows, the audit failure stays visible, and `(chain, address, block_number)` rows remain duplicate-free on restart.

For accounting maintenance, use a file-backed DuckDB with committed rows and WAL activity. Test lock conflict, verified full-database backup equality, backup-name collision, insufficient space/copy failure, failure before reset, transaction rollback, idempotent recovery, error/detail tables cleared and only the zero marker retained in calls, preserved allocation using current and rollback code, and unaffected critical pipeline files. Insert a row between backup verification and reset and assert the reset aborts without deleting it. Exercise real process death in a subprocess at backup-copy, verification, reset-commit and pre-receipt boundaries; recovery uses the in-database epoch record. Use explicitly closed connections. No large file-backed primary/unique indexes. If any price schema changes become necessary, add only nullable columns and verify migration on a copy of the production Parquet; the preferred implementation avoids price schema changes entirely.

Provide a minimal manual real-provider parity/count check for the changed batching path, guarded by the existing `JSON_RPC_*` environment variables. Compare batched and individual TVL/metadata at the same safe numeric block for representative ordinary ERC-4626, Morpho and IPOR adapters, plus explicit specialised fallbacks. Use isolated output/state and do not run a full production scan. All URLs come from the supplied environment through `create_multi_provider_web3()`; do not print credentials. Record exact command, date, provider domains, result and whether manual or CI in any implementation PR comment. A rate limit produces a bounded retry plan, not repeated bursts.

Run Python through Poetry and focused pytest commands through `source .local-test.env && poetry run pytest ...`; copy the main checkout's gitignored env file into the worktree if needed without editing it. Follow the shared fixed-block Anvil fork pool pattern if a fork test is added. Use extended test-command timeouts and progress output. Format changed Python with `poetry run ruff format`; do not build Sphinx locally.

Before rollout, back up critical pipeline state through the existing production backup process as well as the independent accounting backup above. Canary changes on bounded vault selections with isolated state, then ordinary Ethereum/Base/Arbitrum cycles, followed by specialised chains. Preserve dense timestamp caches, historical provider boundaries, provider assignments and existing price frequency. Validate scan completion, qualification/freshness diagnostics and error backoff before increasing scope. Roll back code/settings if coverage or source freshness regresses; restore compatible new scheduling state selectively and retain historical data and all counter backups.

## Review record

Claude Opus 5.5 (`claude-opus-5-5`) reviewed the complete initial plan on 2026-09-30 and returned **approve with required changes**. The no-tools inline packet included the measured counter tables and incident evidence, accounting/freshness/discovery documentation, HyperEVM failure notes, repository instructions and numbered source excerpts for the relevant scanner, adapter, provider, batching and state paths. The CLI returned a successful final result with the requested canonical model.

This revision incorporates the review's reset/rollback safety, prospective baseline, unchanged legacy accounting schemas, active-vault constructor containment, dedicated version exception, internal-failure backoff, short-state-window limits, per-batch HyperEVM block refresh, due selection before construction, explicit post-publication audit outcomes, negative-token retry, shared qualification semantics, stable comparison denominators, idle maintenance, private full-database backup verification, staggered cache migration and process-death recovery tests. A second review confirmed all 20 initial findings resolved and requested three further safeguards: a distinct unavailable-denomination outcome retaining prior qualified coverage, transaction-time backup/source equality, and sidecar-only new state with rollback-loader tests.

The final closure review on 2026-09-30 returned **no blocking findings; approve**, confirming all initial findings and the three further safeguards resolved. Non-blocking implementation notes on legacy conversion evidence, compatible negative-cache entries and stale sidecars on roll-forward are included above. Each review received the full updated plan and the original counts/documentation/source context, used the exact `claude-opus-5-5` model with tools disabled, and completed with a successful final result. This is a plan review, not a code implementation verdict. Remaining uncertainties are mixed-protocol attribution, uncounted legacy clients, daily/weekly admission latency, HyperEVM provider variance and whether measured savings reach the proposed target.

## Implementation record

Implementation addresses issues 1–6 with crash/recovery fixes and staged reductions, separate operation accounting, safe
counter snapshots/reset/recovery, read-only dated-window comparisons and a
manual real-provider parity script. Production commands are in
[`README-vault-scripts.md`](../../scripts/erc-4626/README-vault-scripts.md).
No production counters have been reset or scanner configuration deployed.

The audit now executes before publication and returns an explicit failure
outcome while preserving published rows and progress. A new publication
journal is durably prepared before Parquet rename; its exact local file
identity allows monotonically recovering reader progress if the subsequent
legacy pickle write is interrupted. The journal does not change legacy reader
state schemas and is ignored by an older release. After rollback, preserve the
journal and resolve any outstanding publication before ordinary scanning.

The first implementation review with Claude Opus 5.5 found excessive daily
negative-metadata retries, hidden constructor outages, stale metadata after
reclassification, overly broad transport classification and incorrect use of
incremental scan windows as Monad retention evidence. These were corrected.
Known unsupported versions and persistent negatives now receive weekly checks;
transient metadata retries are capped at three daily attempts. Real eviction
boundary observations supply Monad retry caps and explicit gap diagnostics.
Programming errors remain fatal and receive persistent backoff. The second implementation review prompted narrow VM-error containment, a full
classifier signature and forced refresh, a retention budget anchored to durable
reader progress, journal consumption, orphan reconciliation and connection-local
HyperEVM caching. The final bounded Claude Opus 5.5 closure review confirmed
no remaining blocking correctness bugs in those eight resolutions. Counter
maintenance was reviewed in the earlier implementation passes; its protected-state
hashes and failure recovery also have focused tests.

Compose defaults `VAULT_RPC_OPTIMISATIONS=false` for the seven-day prospective
baseline. Direct all-chain script calls also default to the baseline setting; other
library calls default to enabled. Turning the flag on/off does
not change per-chain discovery signatures. Crash/recovery fixes, unavailable
conversion diagnostics, negative ERC-20 retries, critical history retention and
prior-qualified coverage remain active in both modes. Cache sidecars remain
bounded and rollback-compatible; ordinary batching, admission scheduling,
classification reuse and unused timestamp elimination are controlled by the
flag. Provider removal and the 14-/21-day measurement after enabling reductions
are operational follow-ups. The initial seven healthy days and their snapshot
precede enabling the flag. Outcome reports count cycles with each status, not
attempts; one retried cycle can have both failed and completed status.

Manual integration evidence on 2026-09-30: `source .local-test.env &&
PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" RPC_PARITY_CHECK=true poetry run
python scripts/erc-4626/check-rpc-batch-parity.py` passed sDAI, Morpho v1 and
IPOR metadata and eligible admission parity at Ethereum block 26,092,727,
using isolated temporary token caches and 117 physical requests through
`edge.goldsky.com` and `lb.drpc.org`. This was a
manual supplied-provider run, not CI; no production state was touched.

Final checks also cover protected-state changes during reset, stale negative
token/mapping cache retry, bounded Monad capability measurements and guarded
manual backfills when publication progress is outstanding. Event discovery
requires Hypersync and refuses JSON-RPC event fallback.

Local verification after rebase and the first final review: 118 focused tests passed across counter maintenance,
request accounting, price freshness, reader state, admission/metadata batches,
queue recovery, discovery caching, timestamp accounting, chain configuration
and Monad retention. Ruff formatting and changed-file import/undefined-name
checks passed, as did `git diff --check`. An isolated CLI smoke check passed
read-only inventory, reset and same-ID recovery; the next cycle remained 777
above the original 776. No production maintenance command was executed.

The final simplification pass centralises the rollout switch in the provider
configuration module and shares metadata-success handling between discovery and
queue recovery. Retained economic values carry the same stale-field provenance
on both paths. Single-chain examples now require Hypersync and reject the obsolete
RPC backend before network access. API module lists use their existing autosummary
blocks. None of these changes establishes production savings or invoice costs.

The final grounded Opus 5.5 review requested changes for unsupported Hypersync
chains, candidate outage containment, legitimate null economics, admission-batch
failures, operation labels and known provider availability errors. These are
addressed with explicit Hemi/Katana discovery opt-outs while retaining known-vault
prices, candidate-scoped transport deferrals, same-protocol unavailable-field
provenance, consistent process labels and bounded transient classification.
Programming errors remain fatal as required by the plan; active-reader outages
still fail visibly. Documentation now names all common baseline changes and
migration periods. Broad classifier invalidations are logged, repeated lead-map
rebuilds are removed, and explicit historical metadata blocks stay pinned.
The reader-state guide no longer recommends deleting critical state, and its
helper supports current dictionaries and legacy objects. The frozen closure review approved with a required narrow fix for the production
fallback provider's error wrappers and noted a failed-reclassification provenance
edge case. Both were fixed with actual ``ExtraValueError`` and
``ProbablyNodeHasNoBlock`` regression coverage. The final bounded Opus 5.5
review of that delta returned **no blocking findings; both resolved** on
2026-09-30. It inspected the correct worktree and non-empty committed diff and
completed with a successful result. This closes the earlier findings; it is not
a claim that every unrelated retry or provider path was exhaustively reviewed.

Comparison windows use recorded UTC cycle-start dates, not individual request
execution timestamps. Long scans can cross midnight; take snapshots after the
included cycles finish and check completion logs before comparing windows.
