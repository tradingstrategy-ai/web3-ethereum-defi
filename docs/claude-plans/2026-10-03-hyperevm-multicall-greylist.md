# HyperEVM Multicall greylist design

Branch: `feat-hyperevm-greylist-batches`, created from remote master.
Implemented and validated locally; production rollout is a separate operation.

## Goal and evidence

Keep robust HyperEVM contracts on the standard Multicall path while isolating
reviewed HyperCore-reading contracts into very small requests. Preserve vault
coverage, source blocks, result identity, reader state and existing price history.
The pre-change review recorded 24,192 HyperEVM historical Multicall attempts over
26 sampled blocks. The local scan results below show a lower attempt rate;
matched production savings remain to be established after deployment.

The [gas investigation](../README-hyperevm-hypercore-read-gas.md) establishes that
Hyperdrive HYPED at `0x4d0fF6a0DD9f7316b674Fb37993A3Ce28BEA340e` causes provider-side
batch gas amplification through HyperCore precompiles. Bisection and batch replay
identified that address; other addresses printed in a failed batch are not
therefore implicated. Start with this confirmed address and the other trace/replay-reviewed targets in
existing risk comments; add further entries only after equivalent evidence. Do not infer membership from protocol name alone.

## Minimal policy

Use a manually maintained `HYPEREVM_MULTICALL_GREYLIST` of lower-case target
addresses beside the existing Multicall chain policies. Apply it only on chain
999; the same address on another chain retains normal batching. Membership is
an execution policy, not a blacklist, risk rating or admission exclusion.

Each entry needs an evidence comment identifying the protocol and vault,
the failed selectors, the observed provider behaviour, and the investigation
link. Seed the eight reviewed HyperCore targets documented below. Keep existing blacklists separate; this change does not
implicitly re-enable unrelated blacklisted vaults.

Use a documented function/reader argument `greylist_batch_size=1`. Its units
are encoded subcalls, not vaults: a four-method vault needs four requests at
size one. No environment switch, adaptive registry or operator state database
is necessary. Start at one; increase to two only with repeatable provider tests.

## Execution

Place physical-request planning in `MultiprocessMulticallReader` alongside
`fetch_multicall_with_batch_size()`, which serves both historical reads and
`read_multicall_chunked()` callers, including TVL admission and metadata inputs.

1. Keep current block validity and adaptive scheduling filters. Classify only
   calls already selected for execution; do not increase sampling frequency.
2. Partition selected targets into regular calls and greylisted calls. Execute
   regular calls first with the normal batch limit; never mix either group.
3. Group greylisted calls by target contract, then chunk each target's calls at
   the small limit. Never combine different greylisted addresses even if the
   configured limit would permit it. Initially isolate all selectors of an
   affected target; selector-specific policies would add unnecessary complexity.
4. Carry original call indexes through planning and restore result order before
   decoding. Preserve `EncodedCall` identity, `extra_data`, state, source block
   and timestamp, even though request execution order changes.
5. Reuse existing provider configuration and accounting. Small batches should
   not create new Web3 instances, extra timestamp reads or additional providers.
   Preserve current provider failover rules initially rather than inventing a
   second provider preference policy.

Separate physical requests still use the exact requested block. Never substitute
`latest` for a historical request. HyperCore reads can remain unavailable even
at size one; providers may use live HyperCore state, as documented above.

## Retry and failure boundaries

Fix the missing fallback success exit alongside this work. Retry the failing
physical batch rather than the whole original collection. Retain successful
regular results; a greylisted failure must not replay them, trigger regular
batch shrinking or cause the successful fallback to execute again.

`requireSuccess=False` already represents contract reverts as unsuccessful
subcall results. Preserve that normal path. A whole-request gas/precompile
failure requires explicit handling: after bounded attempts for one isolated
batch, preservation-aware price consumers record an unavailable observation with its contract, selector, block,
provider and reason, and continue other targets. Do not manufacture a successful
result, a Solidity revert payload or zero TVL. Carry an explicit unavailable
status through the reader/coverage layer if existing result types cannot represent
this accurately. Generic historical callers retain strict errors by default;
the price exporter opts in explicitly. Ordinary failures, invalid responses and missing chain-wide
archive state must retain their current hard-error behaviour; do not broadly
catch `Exception` to make a scan appear successful.

Reader state must not mark an unavailable price as a successful fresh observation.
Preserve existing historical rows and report degraded coverage. Prove how the
current chain-row replacement path handles unavailable observations before
allowing greylist transport failures to continue the scan. If that path would
remove saved values, first add address/block-scoped preservation; merely catching
the exception is unsafe.

## Observability and tests

Log block, regular/greylisted subcall counts, distinct isolated targets, and each
lane's batch limit. Record physical attempts, retries and unavailable observations
by lane. Lane counts must partition existing RPC totals, not be added to them.
Keep provider hosts and contract selectors in diagnostics; redact credentials.

Focused tests should exercise real request encoding and assert:

- Normal batches never contain a greylisted target; isolated batches never
  contain regular targets or multiple greylisted targets.
- Identical addresses on other chains follow ordinary batching.
- Explicit small limits, duplicate inputs, all-greylisted and no-greylisted
  inputs preserve result identity/order and source block.
- A successful fallback exits immediately; failure of a later isolated batch
  does not repeat preceding successful regular requests.
- Contract reverts and unavailable transport observations remain distinct;
  unavailable reads do not claim freshness or delete saved rows.
- Metadata, admission and historical paths share the same policy, with no extra
  block timestamp RPCs and correct physical counters.

Use a bounded real HyperEVM check with the supplied configured providers and
currently obtainable state, comparing mixed and isolated requests for the reviewed
address. Read `eth_defi/abi/README.md` before binding ABI fixtures. Record the exact
manual command, redacted provider, date and result in the eventual PR. Do not use
unavailable old HyperCore state as a fixed-success integration assertion.

## Rollout

Implement the planner and retry correction, then prove failure isolation and
history preservation before a production scan. Update the gas investigation
and scanner README. Compare matching sampled blocks, vault coverage, physical
attempts and retries before/after; continue the complete-day accounting review.
Greylist additions/removals require reviewed source changes and evidence.

## Opus 5.5 review and implementation decisions

Claude CLI plan review completed with the explicit `claude-opus-5-5` model on
3 October 2026. Main findings: gate unavailable handling on source-row
preservation; distinguish provider unavailability from Solidity reverts; reject
partial vault observations; isolate failover state; preserve result positions;
validate real Core availability rather than relying on Anvil.

Implemented those safeguards together, with file-backed Parquet coverage proving
that unavailable/unsampled keys survive, successful exact keys replace once,
normal rows retain existing replacement semantics, and reader state does not
advance on partial unavailable observations. Only isolated exhausted gas errors
are deferred; archive and unrelated transport failures still abort atomically.
Generic HyperEVM admission and metadata batching already exclude chain 999 and
retain their existing specialised adapter reads, so no new unavailable marker
is passed to those metadata writers. The shared raw chunked reader applies the
same routing whenever a caller does use Multicall on HyperEVM.

The initial greylist now has eight trace/replay-reviewed targets from prior risk
comments; seven retain existing blacklist precedence. No mixed-batch suspect was
automatically included. A bounded current-state trace of the first six targets
printed in recent rejected batches found no HyperCore precompile calls, reinforcing
why batch membership alone is inadequate evidence.

A real-provider check using the first three supplied endpoints ran successfully:
ordinary USDt0 reads remained available at all nine provider/block combinations.
Goldsky rejected the mixed payload, while isolated requests preserved the ordinary
read. Core-dependent HYPED availability differed between providers and successive
requests, including head. Neither successful transport nor a stable raw value
proves historical Core state. Historical block numbers are never replaced with
latest, and no deep repair/backfill is claimed. Any separate near-head-only NAV
source policy requires protocol-specific accounting review.

## Final review and validation

The final grounded Claude CLI review used `claude-opus-5-5` on 3 October 2026
and reported **no blocking correctness findings** after the first code review's
findings were corrected. In particular, unavailable handling is opt-in only for
the preservation-aware historical price path; metadata and feature consumers
retain hard failures. Provider restoration runs once after the isolated lane,
and restoration failure cannot mask completed results or the original failure.

The initial focused regression suite passed **81 tests**. Subsequent cleanup
and review added coverage; the final suite passed **99 tests** using this command:

```shell
source .local-test.env && PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" timeout 180s poetry run pytest \
  tests/event_reader/test_hyperevm_multicall_greylist.py \
  tests/event_reader/test_multicall_batcher.py \
  tests/event_reader/test_multicall_timestamp_rpc_usage.py \
  tests/vault/test_rpc_batch.py tests/vault/test_price_freshness.py \
  tests/vault/test_scan_all_chains_rpc_usage.py tests/vault/test_rpc_scan_state.py -q
```

The manual real-provider integration check passed at **18:01 UTC on 3 October
2026**, using the supplied Alchemy, Goldsky and dRPC endpoints with credentials
redacted. All nine provider/block combinations retained the robust USDt0 read.
Goldsky rejected all mixed payloads; isolation retained ordinary reads. Alchemy
served all nine subcalls at block 47,573,800 but only the pure EVM reads at
head minus 200 and 20,000 blocks. dRPC served the mixed payload at its head,
47,573,802, while subsequent isolated Core reads were unavailable. This was a
manual run, not CI, and changed no persistent scanner state.

```shell
source .local-test.env && PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}" timeout 180s poetry run python scripts/erc-4626/check-hyperevm-greylist.py
```

Non-blocking limitations from the final review remain visible for rollout:
preserved Parquet rows may follow new rows physically, so consumers must sort
by source timestamp; required-read reverts can keep an observation stale;
provider failover/restoration incurs verification requests; explicit isolated
limits above one conservatively defer an entire exhausted batch. An archive
gap remains a hard error even when reached during gas-error failover. Compare
production physical counters and coverage after deployment before claiming a
measured reduction. No production deployment has been performed for this branch.

## Local HyperEVM scan results

On 3 October 2026 at 19:49–19:50 UTC, the branch's normal
`scan-vaults-all-chains.py` coordinator completed a HyperEVM-only incremental
scan using private copies of production metadata, price Parquet, reader state,
TVL/lead caches and the dense chain-999 timestamp cache. `TEST_CHAINS` and
`CHAIN_ORDER` were `Hyperliquid`, `SCAN_PRICES=true`, `MAX_WORKERS=8`, retries
at coordinator level were disabled, and post-processing/uploads were skipped.
Unrelated native vault feeds were disabled. The currency-rate stage also ran
after the chain scan; its HTTP work is outside these EVM RPC counts.
Production state, containers and published feeds were not changed.

The scanner selected 746 vaults, accepted 3,806 encoded subcalls across six
hourly samples from block 47,560,406 to 47,578,406, and wrote 205 price rows.
The Parquet grew from 22,915,960 to 22,916,165 rows without deleting existing
rows. All 598 saved HYPED rows, including timestamps and `written_at`, matched
the baseline with a NaN-aware comparison. HYPED's six unavailable observations
were deferred: diagnostic invocation/error counters changed, while its stored
freshness and block cursor did not advance.

| Recorded operation | Physical attempts |
|---|---:|
| Regular historical Multicall | 281 |
| Isolated historical Multicall | 24 |
| Reader preparation | 30 |
| Discovery preparation | 6 |
| Total | 341 |

These are the existing instrumented physical-attempt counters, including
failures and verification calls within those operations. The 24 isolated
requests are four HYPED subcalls at each of six blocks; seven other greylist
entries retained blacklist precedence. Discovery and TVL caches were warm.

Historical work used **305 attempts**, or **50.8 per sampled block**, compared
with **24,192 attempts / 26 blocks = 930.5 per block** in the earlier production
scan. This is an observed **94.5% lower attempt rate per sampled block**.
Normalising by accepted subcalls gives **0.080 versus 0.657 attempts per
subcall**, an **87.8% reduction**. This is not a controlled A/B experiment:
different block ranges, scheduled readers, cache warmth and worker counts can
affect the comparison. The result supports the retry correction and isolation,
but is not a guaranteed production saving or evidence that fewer providers
are sufficient. Compare matched production windows after deployment.

No hard chain failure or traceback occurred. Coverage remains degraded for the
same eight stale-source vaults. Five vaults still lack denominations and 50
have unverified USD conversion. HYPED's historical Core reads remained
unavailable. Regular batches also encountered provider gas rejections and
recovered through bounded fallback; some targets beyond the initial greylist
may need tracing or bisection. Rejected mixed-batch membership alone is not
enough evidence to add them. The next useful optimisation is to identify those
remaining offenders, and consider a cheap chain/provider capability policy
that avoids repeatedly rediscovering a known unsuitable gas limit without
conflating it with missing historical state.

Private operator evidence, including the console log, copied baseline,
operation counters and output comparisons, is retained under
`/home/mikko/.local/state/hyperevm-scan-2026-10-03/`. Raw logs may contain provider
diagnostics and are not committed. The branch has still not been deployed.

## PR cleanup and review follow-up

The cleanup shares archive rotation between initial/reduced attempts, removes
unused helper arguments and dead comments, replaces repeated retry payload dumps
with concise warnings, and documents tuple keys and function contracts. Cached
verified chain identity replaces logging/assertion RPC reads. Network helpers
use `fetch_` names; the internal worker API now uses `rate_limit_sleep` rather
than the former misspelt argument. These helper renames have no compatibility
aliases; repository callers were updated together.

The grounded Opus 5.5 review found that greylisted non-gas failures inherited the
small gas budget. Only gas symptoms now receive that budget; timeout, rate-limit
and consensus failures retain normal recovery. Generic historical helpers and
the public vault reader default to strict errors; only the atomic exporter opts
in. Deferral and preservation use the vault address consistently, and an
unavailable greylisted helper routed to an unlisted vault aborts before any saved
history can be replaced. Required NAV selectors are named and documented.

Tests exercise non-gas recovery/exhaustion, each required served revert, real
reader-state mutation followed by rollback, and provider restoration while an
exception propagates. The obsolete string-message exception classifier was
removed; tests now use the actual exhaustion exception and chained cause.
The final grounded Opus 5.5 pass confirmed all six earlier findings fixed and
reported no blocking correctness regression. Its remaining logging/statistics
nits were corrected, and three-provider gas exhaustion and timeout-to-gas
transitions gained explicit coverage. The final suite passed 99 tests.

The final script also passed its manual real-provider run at **20:58 UTC on
3 October 2026**: ordinary USDt0 reads succeeded at all nine Alchemy/Goldsky/dRPC
provider/block combinations. Alchemy served all head subcalls; dRPC served the
mixed head payload but only six isolated head subcalls; older Core views remained
unavailable. Goldsky rejected each mixed payload while the isolated requests
preserved the control read. This is local manual validation, not CI.
