# Test suite performance

Use the [fork testing guide](../eth_defi/testing/README.md) for fixture setup and the [HyperSync test guide](README-hypersync-tests.md) for CI coverage. The [documentation workflow](../.github/workflows/docs.yml) runs weekly on Monday and on manual dispatch. The dated sections below record earlier investigations and measurements.

## Retrying the vault protocol workflow

Retry the whole [vault protocol workflow](../.github/workflows/test-vault-protocol.yml) with `gh run rerun <run-id>`. Its required gate depends on the protocol job. Retrying failed jobs alone has allowed the gate to finish before that job completes.

## Forge deployment output deadlock (2026-11-12)

A local run of the main CI selection reached 4,571 passes but did not finish
within ten minutes. The Forge deployment test blocked in `_exec_cmd()` while
its Git child waited to write to stderr: 64,591 of the pipe's 65,536 bytes were
occupied. Waiting for Forge before reading its pipes creates this deadlock.
A standalone child writing 256 KiB to stderr reproduced the same failure
without Anvil or any network requests.

Drain both streams with `communicate(timeout=...)` while the command runs.
On POSIX, give the invocation its own session and kill its process group on
timeout or interruption, including children that retain pipe handles after Forge exits.
Windows cleanup is limited to descendants still attached to Forge; terminating
orphaned descendants there would require a Windows Job Object.
Keep errors limited to the censored command, since the actual invocation can
include a private key. Focused subprocess coverage checks large stdout and
stderr, timeout cleanup and credential-free errors; the existing two real
Forge-to-Anvil deployment tests passed after the fix.

The same full run stalled in Kaleido chart rendering. The affected chart tests
passed individually, consecutively and with two workers afterwards. The green
CI baseline skipped them because Chrome was absent. This does not establish Kaleido
as the CI timeout cause. Hypersync also returned HTTP 429 responses. Separate
Hyperliquid and Centrifuge assertion mismatches reproduced on unchanged
`master`; none of these observations identifies a specific failure in the
unavailable CI logs.

The assertion fixes preserve production behaviour. Hyperliquid's test now
checks that a price-only update retains the last coherent policy snapshot and
its original clocks, while a newer actual response replaces it, including
explicitly missing capacity. Centrifuge's scan test uses a temporary token
cache so an operator's cached historical name cannot affect the live check.
These test-only corrections require no persisted-data migration.

## Neutral Trade test reporting and isolation (2026-11-12)

The oversized-response test passed a 4 MiB bytes fixture directly into pytest
parametrisation. Its automatically generated ID produced two verbose output
lines of more than 4.19 million characters each. The original streaming CI jobs
stalled without final logs; these huge IDs are a likely cause. A diagnostic run
with pytest output redirected to a file completed both full collection and the focused tests successfully,
and uploaded snapshots while pytest was running. Those snapshots exposed the
huge IDs. The immediate repair gave the cases short explicit IDs, keeping
JUnit names and per-worker JSONL timing records small. The normal full suite
then passed in [PR #1766](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1766).
The provider-response rejection cases were subsequently removed under the
trusted API testing policy in `CLAUDE.md`; the collection and storage isolation
fixes below remain in place.

Full-suite serial collection exposed three import mismatches introduced by
Neutral Trade's generic `test_scan.py`, `test_vault_metadata.py` and
`test_metrics.py` names. Adding `tests/neutral_trade/__init__.py` gives those
modules package-qualified names without changing pytest's global import mode.
The same main-suite collection then selected 4,864 tests without errors in
13.15 seconds. Collection alone does not execute those tests.

A focused eight-worker run also found a cleaning test opening the operator's
default settlement DuckDB, where another process held its writer lock. All
four Neutral Trade cleaner calls now use a settlement path under `tmp_path`,
including the manual authenticated test. Tests must not depend on or contend
with an operator's persisted pipeline data. These test-only changes require
no persisted-data migration.

A separate CI smoke run on the Beefy runner group with `-n 2` passed the six
Forge subprocess tests in 4.63 seconds. It uploaded `runner-preflight` and
`test-results-test-python-0`, including the pytest log and JUnit XML. Its runner
reported 16 CPUs and 62 GiB RAM. This establishes that a small selection and
the upload path work; it does not establish the cause of the stalled full suite.

## Preserve diagnostics after a stalled main suite (2026-11-12)

The main-suite run for PR #1761 exceeded its 15-minute job limit without
uploading logs or test artefacts. Its retry also exceeded that limit without
exposing diagnostic output. The affected tests passed locally both serially
and with four workers, so this does not identify a failing test or establish
the cause of the CI stall.

Bound the entire pytest-to-tee pipeline to ten minutes, with a further 30 seconds
before forced termination. Timing out pytest alone is insufficient: a detached
child can retain an output pipe and keep tee waiting for EOF after pytest exits.
A local subprocess reproduction with a 0.3-second deadline and a detached child
lasting two seconds held the old pipeline open for 2.06 seconds; bounding the
whole pipeline returned status 124 in 0.30 seconds while retaining its log.
GNU timeout must retain its default process-group signalling (no `--foreground`):
the group now includes tee, so the log reader also stops at the deadline.
Detached processes may survive in their own sessions, but cannot keep this
pipeline open; runner cleanup remains responsible for any surviving processes.
Keep shell pipe failure propagation inside the bounded shell so a timeout
fails the check. Setup took 100 and 95 seconds in the two affected attempts,
leaving roughly three minutes within the unchanged 15-minute job cap for the
existing always-run reporting and uploads, including the full pytest log.
Slower setup can still exhaust the job budget. JUnit XML and the final durations
table may be missing after termination; use the captured test names and any
60-second stall stacks to diagnose the failure before changing tests or their
scheduling.

The first complete-pipeline attempt on the organisation runner also remained
in its test step beyond the ten-minute deadline and 30-second kill grace,
without uploading artefacts. The wrapper reproduction therefore does not
explain the observed runner stall. A comparison on `ubuntu-latest`, already used
by the slow integration workflow, also remained in the test step beyond the
complete-pipeline deadline and kill grace without uploading artefacts. It used
the same test selection and deadlines, with `-n auto` following the runner's
CPU count. Different worker counts make suite duration an indirect comparison;
the diagnostic failure here is that the step did not end at the deadline.
The hosted comparison did not establish a repair, so the main workflow retains
its original organisation runner configuration. Do not repeatedly rerun the
unchanged suite without obtaining new evidence.
For new evidence, rerun once with `gh run rerun RUN_ID --debug` and inspect
`runner-diagnostic-logs` in the downloaded log archive. Repository access that
permits workflow reruns and log downloads is sufficient; an HTTP 403 from the
organisation runner inventory does not mean GitHub authentication or
workflow-log access has failed.
[GitHub's debug logging guidance](https://docs.github.com/en/actions/how-tos/monitor-workflows/enable-debug-logging)
describes the runner and worker diagnostic files available for a debug rerun.
Only investigate host `_diag` and OOM logs directly if the runner is confirmed
to be self-hosted and host access is available.

## Anvil 1.8 fork slowdown (2026-10-27)

After CI moved to Foundry/Anvil `v1.8.6`
([PR #1662](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1662)),
the GMX job went from about 4.5 minutes to hitting its 15-minute limit. Four
reference GMX tests took 95–105 s against 48–60 s on `v1.3.2`. There were two
causes, both now fixed:

- Anvil `v1.8` discards a fork cache whose `meta.source_id` does not match the
  fork URL, so the committed seeds, captured through another proxy port, were
  never loaded.
- Anvil `v1.8` mines asynchronously and asks the fork upstream for every
  receipt it has not mined yet. Goldsky Arbitrum answers those unknown-hash
  lookups in about 2.5 s, which made every transaction take about 2.5 s.

With the `source_id` rewrite and hedged receipt lookups, the four tests take
14–17 s. The hedging was later replaced by the proxy answering those lookups
itself with `null`, since a test's transactions are only ever mined by the
fork; that also removes the dependency on having a second, faster provider. Details, the full behaviour table and a diagnosis checklist are in
[Anvil 1.8 behaviour and mitigations](../eth_defi/testing/README.md#anvil-18-behaviour-and-mitigations).

## Lagoon fork contention (2026-10-22)

The [first main-suite attempt for PR #1646](https://github.com/tradingstrategy-ai/web3-ethereum-defi/actions/runs/39790133698/attempts/1)
recorded a 250-second NAV fixture failure, then exhausted the 15-minute job cap
during teardown. On the [second attempt](https://github.com/tradingstrategy-ai/web3-ethereum-defi/actions/runs/39790133698/attempts/2),
that NAV case passed but a Lagoon deployment call stalled for 252 seconds.
A final YieldBasis test on another worker completed all recorded phases before
its worker stopped responding. No Derive assertion failed in either attempt.

The unchanged NAV case passed locally in 4.00 seconds. The deployment and
YieldBasis cases passed together in 6.48 seconds. Move the two transaction-heavy
Lagoon deployment/valuation modules into the existing serial slow workflow to
reduce parallel fork pressure while retaining CI coverage. A combined local
run also exhausted 180 seconds in post-valuation after deployment cases
passed on the same shared fork. Give the two modules independent pool
partitions and matching xdist groups so deployment snapshot/revert churn does
not carry over into valuation. Keep their fixed block, warm cache and per-test
snapshots. Other Lagoon modules retain their existing shared partition.
The slow workflow's Foundry setup,
RPC secrets and warm cache already support these deployed-ABI tests. Main CI
also dumps Python stacks after a 60-second phase stall; its 15-minute cap is
unchanged. After partitioning, the same combined local run passed eight tests
with three existing skips in 14.71 seconds. A green run of both workflows is
required to validate this change.

Reduce repeated deployments, live data volume and CI setup while retaining
meaningful real-provider and Anvil integration coverage. Controlled responses
cover transformation and failure cases; real integrations verify each provider
path. The dated sections below record earlier decisions, not current acceptance
results. See the measured October CI status below for the latest implementation.

## Measured CI status (2026-10-05)

The reductions are published in [PR #1623](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1623).
At head `35afe950d`, main and vault tests passed with the following elapsed
pytest times, compared with the previous complete green samples at `733a53b07`:

| Suite | Previous | After reduction | Change |
|---|---:|---:|---:|
| Main | 201.86s | 169.41s | 16.1% faster |
| Vault | 120.72s | 98.63s | 18.3% faster |

The guard matrix's accumulated phases fell 183.42s → 104.50s; main workers now
accumulated 141.56–146.06s each. Ember liquidity phases fell 40.25s → 20.44s,
and the serial Ethereum vault group fell 109.93s → 87.86s. Accumulated phase
times are worker time, not additional elapsed savings. The remaining vault
workers accumulated approximately 48–54s, so Ethereum still sets that job's
critical path. Main's Lighter bootstrap case remained around 41s.

Main job duration excluding queue delay fell 335s → 286s and vault fell
169s → 149s. Main's Aave restore/setup took seven seconds versus 26s in the
previous cold successful sample, so total job savings also include warm setup.
Reported main CPU increased 479.60s → 640.61s; vault CPU fell 69.87s → 57.88s.
These single-run observations establish elapsed gains, not a general CPU or
cost reduction. Unchanged GMX passed in 187.01s versus 164.00s previously.

The slow workflow failed its unchanged Hyperliquid resume assertion with 54
fills before and after resume; the same failure reproduced locally. A fixed
noon cutoff assumed activity in both halves of a rolling day. The follow-up
fix chooses its cutoff from distinct real fill timestamps in the same bounded
window, retaining strict new-row, watermark-advancement, preservation and
no-duplicate assertions. It passed locally in 2.68s; review follow-ups also reopen the database and
spy on the real provider to assert the exact stored resume watermark and
insert count. The CI result was pending when this measurement was recorded. An inactive account still fails explicitly rather than being
silently skipped or triggering a broader historical scan.

## Representative guarded vault lifecycles (2026-10-04)

The main-suite synchronous GuardV0 matrix in
[`test_guard_simple_vault_standard_erc4626.py`](../tests/guard/test_guard_simple_vault_standard_erc4626.py)
now contains seven representatives instead of 16 protocol deployments. It keeps
Euler's generic ERC-4626 manager, Euler Earn's one-unit share remainder,
AutoPool's custom redemption estimate, D2's funding-phase admission rules,
IPOR's redemption lock, Plutus' custom deposit estimate and Yearn V3's custom
manager and redemption dust. Fixed historical blocks, real guarded deposit and
redemption calls, event analysis and snapshot isolation remain in place.

Dolomite, Fluid, Gearbox, Kiln, Peapods, Royco, Silo, Superform and YO no longer
repeat this full guarded lifecycle. Their other existing tests are unchanged;
this deliberately reduces deployment-specific coverage rather than claiming
that the contracts are interchangeable. Add another representative only when
it exercises a distinct manager implementation or a known contract quirk.
Gearbox also overrides its deposit-closure reader; removing its guarded
lifecycle retires that incidental happy-path coverage. Its separate metadata
tests do not replace that assertion.
The separate canonical-fork Guard receiver/owner rejection test is unchanged.

This removes 56% of the matrix cases and reduces its distinct historical fork
blocks from 12 to seven. These counts describe reduced work; measured elapsed results are recorded
above. Worker grouping and the other proposed optimisation batches are
unchanged.

Local validation on 2026-10-04 used the supplied Arbitrum archive configuration:
the seven retained lifecycles and the separate Guard rejection test passed
together in 112.87s. The original 16-case matrix exceeded the bounded 180s run
after 11 passing cases, so there is no complete local before/after percentage.
The command was `source .local-test.env && timeout 180s poetry run pytest
tests/guard/test_guard_simple_vault_standard_erc4626.py
tests/guard/test_guard_standard_erc4626_rejection.py --durations=12 -v`.

## Representative Ember liquidity refusal (2026-10-04)

[`test_ember_operator_liquidity.py`](../tests/erc_4626/vault_protocol/test_ember_operator_liquidity.py)
now exercises one affected vault instead of two addresses running the same
deposit, FIFO redemption request and insufficient-liquidity refusal. It retains
the ``0x9be9294722f8aad37b11a9792be2c782182cafa2`` deployment and removes the
duplicate ``0x0b9342c15143e8f54a83f887c280a922f4c48771`` lifecycle. The typed
refusal, redemption direction, queue index and absence of a mined settlement
transaction are still asserted. Full Ember deposit/redemption coverage and the
cheap per-address operator checks in `test_ember_deposit_redeem.py` are unchanged.
The removed case took approximately 20s on the last complete green CI run;
the subsequent successful vault run was 22.09s faster, as recorded above.

The retained real Ethereum fork case passed locally in 20.53s on 2026-10-04
using `source .local-test.env && timeout 180s poetry run pytest
tests/erc_4626/vault_protocol/test_ember_operator_liquidity.py -v --durations=3`.

## Cold-fork timing measurement (2026-07-25)

The vault-protocol failures classify as `failure_mode=read_timeout` at the 60 s
`eth_chainId` fork-setup call — **not** `out_of_credits` (that text was a
misleading canned hint, now corrected in the error message). To size the timeout
correctly, `scripts/measure-cold-fork-time.py` measures real cold/warm fork
setup per chain (clears the Foundry cache for the block, forks, times launch +
first block read).

Measured locally (anvil 1.7.1) against the same CI providers
(`edge.goldsky.com`, `lb.drpc.org`, `arb-mainnet.g.alchemy.com`):

| scenario | Ethereum block 25,598,869 | Arbitrum block 487,039,644 |
|---|---|---|
| single cold fork | ~3.1 s | ~3.1 s |
| warm fork | ~2.5 s | ~3.2 s |
| 6 concurrent cold forks | ~2.4–3.0 s each (3.5 s wall) | — |

The July investigation found that forced shutdown prevented Anvil from flushing its RPC cache. A graceful shutdown wrote the cache to disk. Current runners start with the [committed cache seed](../eth_defi/testing/rpc_cache_seed/README.md). The [fork guide](../eth_defi/testing/README.md) explains how to use shared forks and diagnose provider timeouts.

For the release-compatibility failures observed while testing Foundry updates,
including `anvil_loadState` timeouts and the broad `v1.8.3` regressions, see
the [Anvil failure modes](../eth_defi/testing/README.md#anvil-failure-modes)
section and [PR #1589](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1589).

## October 2026 reduction batch

The implementation consolidates the GMX core checks into two live reads per
module, bounds the Lighter cycle to one pool per deployment, groups Lagoon flow
checks and pilots one isolated GMX trading baseline per worker. Independent
Safe deployment, fee-forwarding and PnL regressions retain their coverage.

The main, GMX, slow and vault protocol workflows attempt to upload JUnit output, complete setup/call/teardown
JSONL reports and process resource reports, after success or failure; files may be absent if setup fails or a runner is terminated. Each worker
writes its own timing file so intermediate flaky attempts are included before
the plugin suppresses terminal reports. To capture a focused local run:

```shell
source .local-test.env && TEST_TIMINGS_FILE=/tmp/test-timings.jsonl TEST_RESOURCES_DIR=/tmp/test-resources poetry run pytest tests/gmx/test_available_liquidity.py
```

Timing and resource paths are resolved at session configuration so tests that change the
working directory cannot divert reports outside the artifact. Timing records
identify the node, worker, phase, outcome and attempt. Resource
reports separate process CPU from reaped-child CPU, including Anvil and Forge.
RSS is a process-lifetime high-water mark, not per-test peak memory or the sum of
concurrent memory. Live children are excluded. The controller can account for
reaped workers: do not add its child CPU to the workers' own CPU again.

The slow workflow discovers directly marked modules before invoking pytest.
Main collection validates the complete pre-deselection item list, including
marks added by other collection hooks. An indirectly marked module missing from
discovery fails with an actionable error instead of silently losing CI coverage.
Keep slow markers explicit when adding tests.


Standard and fee-forwarding GMX setups use one parameterised deployment factory.
The forwarding variant still deploys independently and sets the Safe to zero
native ETH. Token funding uses `TokenDetails.transfer()` with decimal
amounts. Strict snapshot mode exposes failed restoration as a teardown error;
the helper itself does not recycle the process. The GMX trading fixture clears
its deployment cache and closes the affected process on that failure, so the
pool relaunches before the next request.

The follow-up batch is published in [PR #1623](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1623);
its checks and recommendation comments record CI acceptance and comparisons.
The complete published head ``d5ee0ddb4`` passed all four test workflows.
Compared with the recorded master baseline, pytest elapsed was 224.20s for main
(previously 208.46s), 229.52s for GMX (285.59s), 47.82s for slow (154.11s) and
117.33s for vault (119.42s). Main became slower; vault was roughly unchanged.
These single observations and local method-call counts are not suite-wide CPU
or HTTP/RPC savings.


### Next GMX deployment batch (2026-10-03)

Seven PnL payout/NAV scenarios now share three independently scheduled mutable
deployments, each at the canonical Arbitrum midnight block. The pool's optional
``isolation_group`` must match the collection-time ``xdist_group`` marker. Keep
a separate deployment cache per group, strict snapshots and fresh Python
adapters; the trading group and read-only callers retain separate processes.
Independent deployment and fee-forwarding tests still deploy independently.

Three overlapping CCXT stop-loss checks become one real create/execute/query/
cancel lifecycle. Order shape, pending-only dispatch and default-position
dispatch assertions are retained. Private-fork position reads bypass public
REST/GraphQL indexers and fork-order tests bypass public Subsquid history, since
those services cannot index local transactions. The RPC reader and DataStore
calls remain real; market/oracle APIs and separate provider integrations remain
live. A focused offline regression covers the actual pending/cache/Subsquid
history merge and duplicate execution suppression.

Using four workers with ``--dist loadgroup``, the same two integration modules
fell from 12 tests in 98.45s to 10 tests in 65.77s (33.2% less local elapsed
time). PnL deployment count falls from seven to three; the displayed accumulated
PnL setup durations fell from 149.35s to 65.30s, excluding sub-second entries
pytest suppresses. One shared group passed but took 127.60s because it serialised
all seven cases; three groups avoid that bottleneck. These are warm local
observations, not CI or CPU reduction claims. The final combined run including
both Lagoon modules, cancellation and pool/reader regressions passed 32 tests
in 100.50s; the additional history-merge regression passed in 0.10s.


### Main-suite and unsupported-protocol batch (2026-10-03)

At the user's explicit request, all 15 Ostium cases are skipped with the reason
``Ostium unsupported after the hack``. This includes the four dedicated
modules, Ostium-only cases in shared withdrawal/guard/history modules and the
Ostium parameter in deposit-policy coverage. Gains policy and lifecycle checks
remain enabled. This deliberately retires unsupported coverage; count it
separately from optimising retained tests. The integration implementation and
scanner metadata are unchanged.

Lagoon deployment no longer sleeps for two seconds after each confirmed
configuration transaction on Anvil. Receipt confirmation, nonce synchronisation
and live-network propagation delays are retained. Ember's redemption lifecycle
uses the existing Lagoon factory at its pinned historical block instead of
compiling a fresh protocol. Its exact share count, ticket sequence and payout
assertions remain unchanged; the separate Gains lifecycle still exercises
fresh source deployment.

With two loadgroup workers, the same Ember/Lighter pair passed in 109.13s
before and 71.76s after (34.2% less local elapsed). Ember call time changed
102.51s to 65.42s, and Lighter 41.66s to 29.22s. Manual runs used supplied
Ethereum/Arbitrum RPC providers (redacted). Gains' fresh-protocol lifecycle
also passed; final focused deployment/policy checks passed six tests with
15 authorised Ostium skips. These are local elapsed observations, not CI or
CPU reduction measurements.

Main CI caches Aave's installed dependencies, Solidity artifacts/cache and
generated TypeChain factories with an exact OS/architecture/Node-version/
submodule-commit/lockfile key. There are no fallback keys or cached mutable
deployment records. Node configuration and cache key share one variable;
lockfile and generated-output checks fail clearly if prerequisites are absent.
The existing installer still initialises an uncached installation before xdist.
A partial installation with Hardhat present but missing generated files fails
the workflow checks; it is not silently treated as complete.
Only caching ``node_modules`` would omit TypeChain factories loaded by Hardhat
before deployment. A clean source-tree probe with the restored installation
loaded Hardhat successfully under Node 18; the warm in-process installer was
a no-op in 0.001s. CI cache transfer costs and net savings remain unmeasured.

At the end of this batch, checkout narrowing and further Ember transaction-stage
optimisation remained candidates. The next batch below captures the historical
seed with CI's pinned binary and measures the remaining receipt delays.


### Remaining low-cost optimisations (2026-10-04)

The five follow-up recommendations were investigated locally. Retained GMX
fork tests reuse one actual token-list response per chain within each test.
Each caller gets a deep copy; failures propagate without entering the cache,
and cleanup restores the live reader even when a test raises. Prices remain
live because local keeper/oracle operations can change them. The profiled PnL
case made 18 token-list fetches before and one afterwards; time inside the
provider reader fell from 3.499s to 0.176s. Independent provider integration
coverage still uses the original endpoint path. The focused PnL, trading,
cancellation and initial helper checks passed 17 cases in 47.67s with four
loadgroup workers; exception-cleanup and actual token API checks also passed.
That combined duration includes earlier deployment optimisations and is not a
measurement of metadata caching alone.

GMX CI now omits submodule checkout. Its collected tests use committed ABI/
bytecode and existing fork factories rather than source compilation. Main CI
retains submodules: Enzyme adapter/guard deployment, fresh Lagoon deployment
and Aave preparation have source/build dependencies. Narrowing main checkout
needs a validated cold-run inventory before it can be claimed as a saving.

Ember already had a historical seed; it was not missing. The existing
24,496,689 seed was refreshed with the official Anvil v1.3.2 Alpine amd64
release binary, increasing captured accounts from 19 to 34 and storage slots
from 47 to 61. Cold capture passed in 74.18s; a run seeded from the refreshed
repository file passed in 65.92s. Profiling identified the remaining delay:
ten upstream receipt attempts consumed 62.51s. Historical state caching cannot
remove receipt misses. Only this fork now uses a two-second upstream-attempt
timeout, allowing one attempt per supplied provider with no backoff and warning
logs for failover, preserving the automatic proxy's diagnostic policy.
Initial shorter-timeout checks passed in 12.77s and 13.63s, around 79–81% less local
elapsed time than the seeded 65.92s baseline. Upstream receipt-attempt time
fell to 7.54s and 7.93s, including failover attempts. Results depend on the
configured archive providers; a single URL does not gain multi-provider
failover. If every provider exceeds the timeout, uncached state or bootstrap
reads can fail rather than succeed slowly. The comparison has one baseline
sample; CI may have a different provider order or contention. No CPU or CI
improvement is inferred from these local timings.
An additional run starting from the committed seed with no live block cache
passed in 12.35s. After review prompted preservation of zero backoff, warning
logs and one attempt per provider, the final policy passed in 10.68s (9.43s
call time), 83.8% below the single seeded baseline observation.

Aave's four-path cache was measured under CI's Node 18 version. Local cold
`npm ci`, including its build hook, took 29.77s; creating the approximately
64.4 MB compressed archive took 1.02s. Restoring into a fresh source tree at
the same absolute path took 1.23s, the Python installer was a no-op, and a warm
Hardhat compile took 4.42s with nothing to compile. Moving that tree to a
different absolute path caused recompilation and took 21.71s, so path stability
matters. These measurements validate the existing complete-cache approach;
These were local measurements; the subsequent CI cache restore/setup
observations are recorded in the measured status above.

This batch was published at `733a53b07`; subsequent CI elapsed and CPU
observations are recorded above. A safe narrower main checkout remains deferred.


### Final simplification and review (2026-10-05)

Ember uses the canonical Ethereum midnight-block constant and an autouse
snapshot fixture. Guard profile options use explicit keywords, and fixture,
helper and dataclass documentation follows the repository's Sphinx conventions.
The seven guarded lifecycles, separate Guard rejection check, Ember liquidity
refusal and real Hyperliquid resume test passed together: 10 tests in 109.46s.
A grounded Claude CLI review with `claude-opus-5-5` approved the reductions and
sampled surrounding helpers. Its resume follow-ups were addressed: the test
reopens its database, checks the actual provider request starts at the stored
watermark, checks inserted rows against the count delta and derives its cutoff
relative to the supplied window. Provider responses remain real. The strengthened resume check passed in
2.64s and in 2.95s with `TZ=America/New_York`; a second grounded Opus 5.5
review reported no blocking findings. Final CI is pending at the time of
this record.
