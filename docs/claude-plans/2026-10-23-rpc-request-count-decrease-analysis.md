# RPC request count decrease analysis

Status: **Analysis complete; selected optimisations implemented**. Written 2026-10-23. This document preserves the original investigation and estimates. The [implementation plan](2026-10-23-rpc-request-count-decrease-plan.md) selects the first two opportunities and larger regular Multicall batches; the [validation record](2026-10-23-rpc-request-count-decrease-validation.md) reports measured results and limits. The remaining opportunities are not implemented here.

The strongest opportunities are correcting repeated historical polling within a scheduling buffer and extending metadata batching. This investigation follows the [earlier vault scanner RPC reduction work](2026-09-30-vault-scanner-rpc-reduction.md); its measurements describe a later production window and should not be added to that earlier baseline.

## Evidence and measurement limits

The investigation read `/root/vault-scanner/web3-ethereum-defi/logs` through the available output on 23 October 2026 and queried `/root/.tradingstrategy/rpc-tracking.duckdb` using a read-only DuckDB connection. No production state was changed. The primary comparison window is the seven complete UTC dates **16–22 October 2026**. Examples from 23 October are individual scans from a partial day.

The active scanner output was found in `export-protocol-metadata.log`, which contains discovery, admission and historical-reading messages as well as export messages. `scan-all-chains.log` ends on 14 October and would give an incomplete picture if examined alone.

The counters describe physical JSON-RPC attempts, including unsuccessful attempts and retries, rather than encoded subcalls inside Multicall, provider billing units or invoices. The operation table attributes the same requests recorded in the API-call table: summing both tables would double-count usage. Some work, including provider capability preflight and separately constructed clients, is outside the phase counters. Native REST and Hypersync requests are outside these JSON-RPC totals.

Operation names are broad accounting labels. In particular, `historical_multicall` also includes preparation and token warmup performed inside the historical scan; it does not mean every request was a batched historical `eth_call`. Existing counters do not provide per-protocol or per-selector attribution.

Code inspection used repository revision `7f6671c64316f468b9c57c3afb4d4d8389bb5a63`. The investigation did not establish an exact deployed-image revision for every measured cycle. The scheduling replay below demonstrates the inspected code's behaviour; it is not a production replay or a measured fleet-wide saving.

## Recorded usage

The scanner recorded **459,609 physical RPC requests** during 16–22 October. Ethereum, Base and Arbitrum together accounted for **317,837 requests, or 69.2%**.

| Operation | Requests | Share |
|---|---:|---:|
| Historical reading stage | 217,053 | 47.2% |
| Per-vault metadata extraction | 167,672 | 36.5% |
| TVL admission probes | 35,456 | 7.7% |
| Classification, preparation and other work | 39,428 | 8.6% |
| Total | 459,609 | 100.0% |

The final category comprises feature probes (13,208), discovery preparation (10,427), reader preparation (10,417), metadata inputs (4,233), greylisted historical batches (632) and timestamp requests (511).

| UTC date | Requests |
|---|---:|
| 2026-10-16 | 49,960 |
| 2026-10-17 | 45,590 |
| 2026-10-18 | 39,995 |
| 2026-10-19 | 35,417 |
| 2026-10-20 | 36,050 |
| 2026-10-21 | 35,256 |
| 2026-10-22 | 217,341 |

The 22 October logs explicitly report discovery-cache expiry after seven days and renewed classification/metadata work. That day's per-vault metadata extraction used **63,122 requests on Ethereum**, **52,414 on Base** and **22,613 on Arbitrum**. These refreshes explain much of the increase over the preceding days. Increasing the refresh interval would change freshness policy; the first optimisation target is the work performed during a refresh.

The totals can be reproduced with the following read-only queries against the production accounting database:

```sql
SELECT phase, operation, SUM(call_count) AS requests
FROM vault_rpc_operation_calls
WHERE cycle_started BETWEEN DATE '2026-10-16' AND DATE '2026-10-22'
GROUP BY phase, operation
ORDER BY requests DESC;

SELECT cycle_started, SUM(call_count) AS requests
FROM vault_rpc_api_calls
WHERE cycle_started BETWEEN DATE '2026-10-16' AND DATE '2026-10-22'
GROUP BY cycle_started
ORDER BY cycle_started;
```

## Opportunities

The code descriptions in this section refer to inspected baseline `7f6671c64`, before this PR. Source links are pinned to that revision. Subsequent implementation and measurements are recorded separately above.

### 1. Fix repeated polling within historical batches

**Confirmed code behaviour:** the [stateful scheduling loop](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/7f6671c64316f468b9c57c3afb4d4d8389bb5a63/eth_defi/event_reader/multicall_batcher.py) calls `state.should_invoke()` for multiple sample blocks before flushing its buffered tasks and yielding results. `VaultReaderState.should_invoke()` compares each timestamp against `last_call_at`, which changes when results are processed. A daily or weekly reader that is already due can therefore be selected repeatedly within the same buffer. The default buffer parameter is 48; the baseline `len(chunk) > chunk_size` condition flushes after the 49th scheduled task.

A small offline replay extracted the actual `VaultReaderState.should_invoke()` method from the inspected source and evaluated nine hourly slots. With the initial state held until the buffer completed, an already-due daily reader and an already-due weekly reader each accepted all nine slots. Updating `last_call_at` after each simulated successful result reduced both to one accepted slot. The hourly reader accepted nine in both cases. The replay did not perform network reads or modify scanner state.

The 23 October Ethereum scan at 03:32 logged nine sample blocks and 83,286 accepted encoded subcalls. Its final scheduling counters included daily and weekly categories. These counters are subcall-state counts, not distinct vaults, and do not identify exactly how many production requests were redundant.

**Candidate change:** make scheduling account for observations already pending for a reader. Keep all methods belonging to one vault observation together, preserve hourly sampling, and ensure an unsuccessful pending observation cannot falsely advance durable progress or suppress recovery. The exact scheduling mechanism and failure semantics remain to be designed.

**Expected benefit:** fewer redundant calls for daily and weekly readers. The nine-to-one replay is a behavioural example, not a fleet-wide reduction estimate.

### 2. Expand metadata batching and reuse reads

**Measured opportunity:** per-vault metadata extraction consumed 167,672 requests during the week, including the large refreshes above.

The [shared metadata snapshot path](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/7f6671c64316f468b9c57c3afb4d4d8389bb5a63/eth_defi/vault/rpc_batch.py) batched four raw inputs at that revision (`asset`, `totalAssets`, `totalSupply` and `share`) for a narrow adapter allowlist. The [record assembly path](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/7f6671c64316f468b9c57c3afb4d4d8389bb5a63/eth_defi/erc_4626/scan.py) still invokes adapter methods for fees, flags, permissions and lending fields. Extending shared inputs requires adapter-specific semantic checks; implementing the ERC-4626 interface alone does not establish that an adapter can consume a generic snapshot.

One concrete duplication was present in [Morpho v2 lending reads](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/7f6671c64316f468b9c57c3afb4d4d8389bb5a63/eth_defi/erc_4626/vault_protocol/morpho/vault_v2.py): `fetch_available_liquidity()` reads the idle balance, and `fetch_utilisation_percent()` reads that balance again and also reads total assets. The metadata caller already has total assets. Morpho v1 has an existing combined lending snapshot that illustrates the reuse opportunity.

**Candidate change:** reuse economic inputs at the same source block, expand batching for explicitly supported adapters, and share immutable or suitably cached metadata. Preserve protocol-specific accounting and continue refreshing dynamic flags and permissions. Add selector/protocol attribution before claiming which additional adapter work dominates the remaining cost.

**Expected benefit:** reduce the large weekly refresh cost. The current measurements identify the cost pool but do not establish a safe percentage reduction.

### 3. Back off persistent admission failures selectively

**Measured opportunity:** TVL admission consumed 35,456 requests during the week. The 23 October Ethereum scan reported `due=980`, `cached=2682`, `qualified=0`, `unverified=981` and `blacklisted=104`. The unverified count includes constructor failures, so it is not a one-to-one denominator for the due-probe count.

The [admission scheduling helper](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/7f6671c64316f468b9c57c3afb4d4d8389bb5a63/eth_defi/vault/rpc_scan_state.py) schedules every error for another probe after one day. Persistent unsupported denomination conversions therefore share the same interval as temporary failures.

**Candidate change:** distinguish persistent unsupported conversions and deterministic adapter failures from transient failures. Give mature persistent failures a longer bounded retry interval, with invalidation when relevant metadata or adapter capabilities change. Preserve ordinary scanning for previously qualified vaults and the opportunity for new vaults to become active.

**Expected benefit:** reduce repeated checks that cannot yet qualify a vault. The exact interval, invalidation policy and saving remain open for implementation planning. Most production admission sidecars were unreadable under the investigation account, so no fleet-wide breakdown of persistent failure reasons was established.

### 4. Benchmark larger regular Multicall batches

**Confirmed baseline configuration:** the regular Multicall batch default was 40 encoded subcalls. Certain chains use smaller limits, and greylisted targets have separate isolation rules.

**Candidate change:** benchmark regular batches of 100 and 200 against the current limit, using compatible workloads and chain/provider-specific acceptance checks. Keep expensive HyperEVM targets isolated and retain smaller limits where needed. Compare returned observations, failed attempts and physical requests, rather than successful batch count alone.

**Estimated benefit:** changing 40 to 100 reduces the number of physical requests needed for a large, compatible, unchanged subcall workload by approximately 60%, ignoring rounding and retries. This estimate applies to those batches, not the whole historical stage or total scanner usage. Scheduling and batching savings overlap and must not be added independently.

### 5. Cache unsupported optional-method capabilities

**Measured opportunity:** the RPC error table recorded 55,255 code-3 reverts during the week. Optional method probes are one possible source, but the existing counters do not attribute these errors precisely enough to identify the avoidable proportion. A revert is not proof of a retry or of a permanently unsupported method.

The generic ERC-4626 metadata path, for example, probes `paused()`. The historical reader also has an optional-method warmup mechanism, but the common scanner currently leaves automatic warmup disabled. Enabling individual warmup calls indiscriminately could increase requests.

**Candidate change:** reuse evidence from existing reads to cache confirmed unsupported optional capabilities with finite expiry and appropriate invalidation. Continue refreshing values for supported methods. Do not classify state-dependent reverts, provider failures or expensive calls as permanently unsupported.

**Expected benefit:** fewer repeated optional probes. Measure selector-level outcomes before setting a reduction target.

## Original priorities and open decisions

The investigation prioritised scheduling behaviour, followed by metadata batching and reuse. Larger regular batches subsequently joined the implementation scope, with bounded experiments recorded in the validation results. Admission backoff still needs explicit recovery/freshness decisions; optional-method caching needs better error attribution. Provider verification and timestamp requests are smaller measured opportunities in this window.

The eventual production before/after comparison should cover a complete metadata-refresh cycle and compare physical requests alongside vault coverage, failed observations and source freshness. No global saving is claimed from adding the individual opportunities. This document records the analysis; subsequent runtime and additive API changes are described in the implementation plan. Production deployment and historical-data migration remain outside scope.
