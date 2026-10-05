# Test suite optimisation plan

Date: 2026-10-03. Status: validated reduction batches and fixture-sharing pilots published in PR #1623; full-batch CI confirmation pending. First reduction batch is in [PR #1623](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1623).

## Objective

Reduce runner work, provider traffic and merge feedback time by eliminating tests with no useful assertions, consolidating duplicate integration checks, bounding live datasets and avoiding repeated deployment work. Preserve distinct security, transaction lifecycle, external-provider and data-preservation regressions.

This is an implementation backlog, not permission to delete every slow test. Rank changes by evidence of redundant work and measure their actual effect. Moving tests to another workflow improves feedback time but does not itself reduce total work.

## Evidence and baseline

Primary-agent observations come from direct GitHub Actions API queries, downloaded job logs and targeted source inspection. Baseline master: `2407b6d557028fc0307974766b7e3b38754a5e95`. Initial `gh run list` output showed old runs; verify timestamps and commit SHAs directly when establishing a baseline.

| Workflow | Run | Pytest time | Job time | Passed / skipped |
|---|---|---:|---:|---:|
| Main | [3 October](https://github.com/tradingstrategy-ai/web3-ethereum-defi/actions/runs/37117298855) | 208.46s | 319s | 2,822 / 228 |
| Vault | [3 October](https://github.com/tradingstrategy-ai/web3-ethereum-defi/actions/runs/37117298840) | 119.42s | 148s heavy job | 610 / 39 |
| Slow | [3 October](https://github.com/tradingstrategy-ai/web3-ethereum-defi/actions/runs/37117298860) | 154.11s | 201s | 10 / 7 |
| GMX | [2 October](https://github.com/tradingstrategy-ai/web3-ethereum-defi/actions/runs/37006142560) | 285.59s | 372s | 949 / 7 |

All were successful. GMX did not run on the latest master change; lint passed. The preceding ForgeYields metadata failure was fixed separately by #1622. These timings describe elapsed time, not CPU consumption. Top-40 duration tables are a partial inventory, not a complete accounting of work.

### Recurring expensive main tests

| Test | Latest phase cost | Previous run where checked | Next investigation |
|---|---:|---:|---|
| `tests/lagoon/test_lagoon_ember.py::test_lagoon_safe_ember_redemption_cycle` | 93.84s call | 93.47s | Deployment, transactions, archive reads and private historical fork |
| `tests/guard/test_lagoon_lighter_bootstrap.py::test_lighter_bootstrap_uses_the_configured_settlement_budget` | 53.12s call | 48.23s | Deployment baseline and settlement stages |
| `tests/gains/test_ostium_v15_lagoon.py::test_lagoon_ostium_v15_deposit_withdraw` | 29.80s call | 32.17s | Deployment reuse and transaction stages |
| `tests/safe/test_safe_deterministic_deploy.py::test_lagoon_deterministic_safe_base_and_arbitrum` | 22.08s call | 21.03s | Preserve actual multichain deployment checks |
| `tests/guard/test_guard_hypercore_vault_lagoon.py` permissive-vault case | 21.74s call | 20.90s | Preserve guard behaviour; reduce fixture duplication |

The vault suite includes Ember lifecycle at 27.66s, two operator-liquidity refusal cases at 22.17s and 21.18s, and Upshift lifecycle at 15.40s. Those prove meaningful behaviours; identify duplicated setup before removing cases.

### Other confirmed observations

- GMX's top-40 table contains 14 setup entries exceeding 20 seconds, totalling 285.34s of accumulated worker setup time. Do not interpret that sum as a wall-clock saving estimate.
- Lagoon flow-analysis tests each paid approximately 14s setup. Their session deployment cache is per worker, and the default pooled fork has no common xdist group in these modules.
- The main run emitted 1,450 logging errors, chiefly `ValueError: I/O operation on closed file`, and produced an 18.8 MB log. Fix handler lifetime/restoration; do not merely suppress the diagnostic.
- Main checkout took 46s and Aave setup 26s. The slow workflow collected thousands of tests to run ten.
- Lighter scanner cycle took 48.74s, Hyperliquid normal-account reconstruction 30.30s, and Hyperliquid resume 19.10s.
- GMX APY period checks took 28.65s; liquidity consistency 21.67s; its purported error-handling test took 9.45s without intentionally producing an error.

## First batch already implemented

The initial batch in PR #1623 contained test changes and a changelog entry:

1. Remove `tests/erc_4626/vault_protocol/test_superform.py`: its test body was entirely commented out but still requested a fork fixture.
2. Remove the assertion-free `test_arbitrum_gmx_fetch_tickers`. The existing endpoint integration retains symbol and numeric-price assertions.
3. Move wallet identity and native-balance assertions into the existing GMX collateral-approval deployment regression, eliminating two separate Lagoon/Safe deployments.
4. Use the existing one-day window for normal-account Hyperliquid reconstruction and change the tautological trade-count assertion from `>= 0` to `> 0`.

Focused manual real-provider runs on 2026-10-03 passed without flaky retries. The original three GMX checks took 70.56s; the consolidated deployment regression plus retained ticker integration took 22.62s. Normal-account reconstruction call time fell from 28.70s to 3.28s; both account reconstruction tests passed in 7.39s. These are local observations, not guaranteed CI savings.

## Decision rules for removing or reducing tests

| Category | Required evidence | Action |
|---|---|---|
| Empty test or tautology | Body cannot detect a meaningful regression | Delete empty test; replace ineffective assertion where coverage is useful |
| Duplicate live smoke | Another real test covers the same endpoint and behaviour | Consolidate assertions and remove repeated downloads |
| Accessor with costly environment | Behaviour does not require its deployment/scan | Smaller fixture or assertion in an existing integration |
| Repeated policy assertion across protocols | Rejection occurs before protocol-specific execution | Canonical-block policy coverage; retain distinct lifecycle integrations |
| Oversized dataset | Same behaviour is exercised by a bounded subset | Reduce pools, windows or pages while retaining necessary boundaries |
| Skipped unsupported integration | Feature is retired and no unique useful coverage remains | Remove after confirming support status and replacement coverage |
| High-value slow regression | Distinct security, lifecycle, migration or provider contract | Keep; optimise setup and execution |

An absent inline `assert` is only a screening signal: assertions in helpers and intentional no-raise tests can be useful. Produce a before/after coverage map for each deletion or consolidation. Do not hide deterministic regressions behind retries or skips.

## Phase 1: remove infrastructure waste and establish measurements

### Logging isolation

Inspect script tests that configure global logging, handlers holding captured streams and their cleanup. Start with the first observed logging error around `tests/erc_4626/test_migrate_vault_deposit_permissions.py`, while tracing earlier logging configuration in the same worker; the first visible victim is not necessarily the cause. Also inspect `eth_defi/vault/scan_all_chains.py` and console-logging helpers.

Restore handlers/streams at fixture teardown and avoid retaining temporary captured output. Verify the affected tests together in the order that reproduces the fault, then under the actual xdist configuration. Acceptance: logging errors disappear through correct isolation, failure diagnostics remain visible, and log size/work are recorded before and after.

### Timing and resource reporting

Persist complete per-test setup/call/teardown timing as an artifact, alongside worker/group identity and flaky retries. Retain a concise slowest-test summary. Record job preparation separately from pytest execution.

Measure selected offline and integration hotspots with process CPU time, peak memory and relevant child-process work, including Anvil. Parent-process CPU alone misses EVM work. Record RPC/API request counts where available. Avoid building intrusive suite-wide instrumentation before a focused prototype proves useful.

Compare unchanged baseline and candidate runs using the same runner/toolchain, warm cache and provider configuration. Account for rolling API data, concurrent runs, failures and retries. Prefer repeated comparable observations before setting hard budgets. Acceptance: distinguish elapsed-time latency, CPU work, memory, repeated fixture cost and external traffic; no unsupported CPU rankings.

## Phase 2: deployment and fork reuse

Read the canonical module docstring in `eth_defi/testing/anvil_fork_pool.py` before changing fixtures.

1. Pilot grouping the compatible Lagoon flow-analysis tests onto one worker. Preserve the block required by existing state rather than mechanically changing it. Compare duplicate setup saved against serialisation added.
2. Extend shared deployed baselines only where deployment parameters match. Keep tests whose subject is deployment, custom parameters or deployment failure independent.
3. Pilot GMX module/session baseline reuse separately: its tests mutate oracle bytecode, balances, approvals and positions. Exercise mixed test order and xdist, with snapshot/revert restoring all EVM mutations and explicit reset of Python-side wallet nonces/caches where needed.
4. Account for pooled-fork recycling: a relaunched process loses deployed state. Rebuild cached baselines when the underlying fork generation changes; a stable URL alone is not proof that a deployment still exists.
5. Move reusable GMX helpers from `tests.*` into `eth_defi.testing` when undertaking the fixture refactor; do not import test modules as implementation dependencies.

Acceptance: one compatible baseline per intended worker/group, unchanged assertions, no cross-test contamination, robust handling of fork restart and no snapshot/revert hangs. Do not group all tests on a chain if the saved setup is outweighed by serial execution.

## Phase 3: reduce repeated live data work

### GMX structure and error tests

Inspect `tests/gmx/test_available_liquidity.py`, funding/open-interest tests, and `tests/gmx/ccxt/test_ccxt_gmx_markets.py`.

- Replace repeated live fetches whose only purpose is output structure with one representative real fetch and controlled-data transformation tests.
- Cover APY period selection/mapping offline for all supported periods; retain a real endpoint test and distinct symbol-specific behaviour where needed.
- Replace live-with-10%-tolerance consistency checks with deterministic consistency expectations where the behaviour being tested permits it.
- Make error-handling tests induce defined missing/failed responses instead of invoking a healthy endpoint.
- Strengthen the decoder tests named `returns_none`, which currently only log results.
- Reduce Enzyme `test_repr` dependencies to the contract bindings needed for formatting, or fold the no-raise check into a meaningful existing transaction test.

Acceptance: at least one minimal real successful integration for each distinct external provider path, fewer redundant calls and explicit failure-path assertions. Keep fallback backends covered; do not assume REST success proves GraphQL or RPC correctness.

### Scanner and trade-history volume

Bound `tests/lighter/test_scan_loop_integration.py` to representative pools/deployments through existing supported configuration. Retain the real loop, successful cycle state, DuckDB persistence and metadata checks. Add stronger success-state expectations if the current checks only demonstrate file presence.

Reduce Hyperliquid resume volume only while preserving a demonstrably non-empty initial segment, a later extension, retained original IDs and no duplicates. A moving interval may have no activity: do not weaken assertions until an empty resume passes as success. Keep deterministic pagination and persistence edge cases alongside the real provider smoke.

Acceptance: bounded request/page/entity counts, actual real-provider success, and unchanged meaningful persistence/resume coverage. Keep database handles closed and production-scale file-backed DuckDB migration/duplicate-ingestion regressions intact.

## Phase 4: remove legacy clutter and enforce fork practices

Static audit found 79 direct `fork_network_anvil()` calls in 70 files. Fourteen omit a block: five manual GMX debug scripts, three Monad tests, five skipped legacy BNB/Velvet call sites, and the active HyperEVM warm-up test. These are not fourteen active CI violations.

- Pin the HyperEVM warm-up test to a canonical block if that still proves the launch behaviour; retain a separately justified tip test if the behaviour requires it.
- Audit literal blocks, pool keys, launch options, matching xdist groups and actual committed cache coverage. Configuration differences can legitimately produce different pool instances.
- Preserve exceptional historical lifecycle blocks and document their necessity. Keep generic guard/policy checks separate from lifecycle exceptions.
- Keep Monad tests current-state/state-relative: arbitrary historical state is unavailable. Never classify their moving boundary as a defect to fix with archive backfills.
- Replace loose bounds with exact fixed-state assertions where meaningful; do not replace genuine invariants with brittle values unnecessarily.
- Remove permanently skipped unsupported token-tax/Velvet coverage after confirming support status. Distinguish unconditional skips from locally runnable CI-only skips; audit the latter for recovery before deletion.

Acceptance: a classified inventory of active, skipped, manual, justified exceptional and noncompliant forks. Legacy cleanup should claim maintainability/collection savings, not eliminated runtime for fixtures that never ran.

## Phase 5: workflow burden and scheduling

Inspect checkout/submodule and Aave setup needs before narrowing them. Do not remove build prerequisites from jobs that still exercise those integrations. Consider narrower slow-test collection with explicit paths/markers, while ensuring new slow tests cannot fall out of CI silently.

Use job-level change detection and an always-run required gate when introducing selective integration workflows; include shared providers, token/ABI helpers, fixtures, dependencies and the workflow itself. Retain scheduled/manual fallback where filters may miss dependencies. Check required-status configuration before changing workflow triggers.

Review push concurrency: `${github.head_ref || github.run_id}` makes master pushes use distinct groups, so overlapping master runs are not cancelled. Decide explicitly whether only the latest master result is needed, and use a branch-stable concurrency key only after checking workflow matrix/gate interactions.

Acceptance: reduced actual runner work and provider requests, no missing required check, no accidental loss of shared-code coverage, and no silently orphaned slow tests. Keep the existing 15-minute cap; moving costs or extending timeouts is not optimisation.

## Proposed delivery sequence

| Batch | Scope | Evidence to attach |
|---|---|---|
| A | PR #1623: empty tests, GMX accessor consolidation, bounded normal-account reconstruction | Completed local real-provider checks; follow CI result |
| B | Logging isolation and complete timing artifact prototype | Reproduction, handler fix, log counts, comparable timing |
| C | Lagoon grouping pilot | Setup count, worker placement, mixed-order/xdist isolation |
| D | GMX live-check consolidation and error assertions | Before/after coverage map, real endpoint success, request counts |
| E | Lighter/resume bounds and historical-fork audit | Volume limits, meaningful persistence checks, classified inventory |
| F | GMX baseline sharing and selective workflow improvements | State-isolation/recycling proof, runner/provider savings, required-gate check |

Deliver small independent PRs. Record focused commands, date and redacted provider environment for real integration checks. Do not run the whole local suite or Sphinx builds for plan-only work. Do not push follow-up implementation or update PRs without authorisation.

## Initial independent Claude review (historical findings)

Requested model: Claude Opus 5.5. The reviewer must receive this plan and the primary-agent observations above, then inspect repository tests independently for additional removals, reductions and incorrect assumptions. Require file/line evidence, distinguish confirmed findings from candidates and report residual risks. The primary agent will verify suggestions before adding them to the implementation backlog.

Review status: completed with CLI model `claude-opus-5-5` on 2026-10-03. The smoke test and focused review both returned successful final `result` events, confirming the model and worktree. The initial broad review reached its turn limit without a final verdict; it is not treated as a completed review. The successful focused pass read this entire plan, the GMX core/CCXT tests, relevant fixtures, both Hyperliquid reconstruction tests, the Ganache test and the main workflow. It did not independently validate every backlog item.

### Corrections incorporated after independent inspection

- GMX core data fixtures use live mainnet, not the fixed fork: `tests/gmx/conftest.py:465` and `:487`. Deterministic expectations require controlled data or explicitly pinned reads. Delete two-fetch market-stability checks rather than interpreting market drift as a software defect.
- GMX parametrisation currently selects only Arbitrum (`tests/gmx/conftest.py:255`). Call-volume estimates must not multiply by the inactive Avalanche branch.
- The two Hyperliquid reconstruction tests now use the same vault address, time window and reconstruction call (`tests/hyperliquid/test_trade_history_integration.py:100` and `:134`). Their account flag differs, but the primary agent checked `sync_account_fills()` at `eth_defi/hyperliquid/trade_history_db.py:374` and found no flag-specific branching; reconstruction also receives no account flag. Do not claim the existing test proves genuine normal-account behaviour. Keep one real reconstruction and move account-flag persistence checks offline, or add a genuinely distinct non-vault path if one needs coverage.
- Skip-on-empty does not activate flaky retries. The primary agent confirmed the installed plugin explicitly uses `not skipped and self.add_failure(...)` in `flaky/flaky_pytest_plugin.py:105`. `funding_fee_data`, `open_interest_data` and the open-interest calculation test can therefore hide permanently empty results as skips. Fix this before claiming reliable integration coverage: absent credentials may skip; unexpected empty successful responses must fail with bounded transient handling.
- Claude's suggestion that module-scoped fixtures would work because GMX runs serially was incorrect. `.github/workflows/test-gmx.yml:160` uses `-n 4 --dist loadgroup`. Any reuse needs deliberate grouping and remains per worker. This suggestion is corrected, not adopted as written.

### Additional verified deletion and consolidation candidates

The primary agent checked the source evidence below. These are proposed follow-ups, not changes made by this plan.

| Source | Independent observation | Coverage-preserving action |
|---|---|---|
| `tests/gmx/test_available_liquidity.py:155` | Sums only positive values, then asserts totals are non-negative; no meaningful aggregation regression can fail these assertions | Delete this live test. Keep numeric/non-empty provider coverage; add deterministic aggregation coverage only if application aggregation exists |
| `tests/gmx/test_available_liquidity.py:117` | Additional live fetch mainly checks expected market membership | Move distinct membership assertions into the retained structure/filter integration |
| `tests/gmx/test_available_liquidity.py:177` | Healthy endpoint wrapped in `except Exception: pytest.fail(...)` does not exercise error recovery | Remove duplicate live call; inject a defined multicall failure in focused offline coverage |
| `tests/gmx/test_funding_fee.py:57` and `:113` | Separate downloads perform overlapping numeric-type checks | Check all markets on one retained real result |
| `tests/gmx/test_funding_fee.py:72` | Two downloads use absolute `0.1` tolerance while describing 10% change | Delete live market-stability assertion; retain calculation/decoding correctness with controlled input |
| `tests/gmx/test_open_interest.py:102` | Two live downloads test market stability rather than a deterministic application contract | Delete/consolidate into a single real-response check |
| `tests/gmx/test_open_interest.py:160` | Repeated symbol lengths and positive/arithmetic checks are derived from supplied constants in a plain dataclass | Remove the repetitive cases if a minimal constructor/schema check remains; expected saving is maintainability, not material runtime |
| `tests/gmx/ccxt/test_ccxt_gmx_markets.py:89`, `:115` and `:201` | APY checks can succeed on empty dictionaries or `None` by bypassing their conditional assertions | Require successful representative data; cover periods and symbol mapping offline using one controlled response |
| `tests/gmx/ccxt/test_ccxt_gmx_markets.py:141` | Disk-cache test compares two results but would pass if both instances fetched from the network | Retain cache regression; assert persisted cache and prevent network access for the second instance |
| `tests/gmx/ccxt/test_ccxt_gmx_markets.py:179` | Live reload performance test duplicates loading coverage and tests provider latency with a flaky decorator | Remove hard live latency assertion; retain loading success elsewhere and report duration separately |
| `.github/workflows/test.yml:94` | Plain dependency install immediately precedes installation with the required extras | Validate replacing both with one complete extras install, with cache-hit and cache-miss checks |
| `.github/workflows/test.yml:131` | Step named contract build only runs `pnpm --version` | Remove diagnostic-only step; inspect all remaining pnpm consumers before removing setup |

Claude estimated roughly 21 live `get_data()` invocations across the three core modules could be consolidated towards six (filtered/unfiltered per module). Treat this as a proposed per-test-path call budget, not measured HTTP/RPC requests: fixture execution, cache use, internal batching and retries change real traffic. Instrument the baseline and preserved paths before accepting the target.

Constructor flag checks and dataclass equality tests are weaker deletion candidates than the tautologies above. They can detect a changed constructor or schema; they are not literally incapable of failure. Keep a minimal meaningful contract check if it protects a relied-on behaviour, and avoid spending effort deleting cheap coverage merely to reduce test count.

### Initial follow-ups (resolved where listed in the implementation record)

- Backend identity: the GraphQL and RPC market-loading tests must demonstrate their selected backend was actually reached. Inspect cache/fallback behaviour, use an isolated cache or explicitly disable it where needed, and observe calls to the intended real backend. A non-empty final result alone can mask fallback. The primary agent confirmed `load_markets()` has fallback branches at `eth_defi/gmx/ccxt/exchange.py:2595`; shared disk-cache masking remains a candidate rather than a proved defect.
- Swap-market filtering: tests use `"SWAP" in market_symbol` at `tests/gmx/test_funding_fee.py:108` and `tests/gmx/test_open_interest.py:137`. Confirm actual key/identity semantics before replacing this potentially vacuous condition.
- Ganache install: `.github/workflows/test.yml:99` installs it, while its direct test is unconditionally skipped (`tests/rpc/test_ganache.py:30`). A primary-agent search found that as the only test import of the Ganache provider. Check indirect production/test entrypoints before removing the CI install; removing the skipped test itself is a separate support-status decision.
- pnpm setup: the inspected Aave installer uses `npm ci` (`eth_defi/aave_v3/deployer.py:455`), not pnpm. Check every remaining job command before removing pnpm setup.
- Main-job GMX Sepolia credentials: the variables at `.github/workflows/test.yml:223` are only referenced by GMX fixtures in the inspected test sources, while the main job ignores GMX. Check indirect consumers before dropping those environment mappings.
- Retry/timeout history: do not automatically reduce the Hyperliquid 180s timeout from one successful fast run, or erase dated flaky history. Re-measure after bounded data changes and update rationale when evidence supports it.

### Revised immediate priorities

1. Fix logging isolation and masked skip-on-empty integration outcomes, with focused reproduction and regression checks.
2. Remove tautological live tests and consolidate GMX core response assertions. Verify fewer actual provider calls and retained non-empty successful results.
3. Make backend/APY/cache tests prove their stated behaviour; remove live provider-latency assertions and redundant workflow setup after consumer checks.
4. Consolidate duplicate Hyperliquid reconstruction and verify account metadata separately; proceed with scanner/resume bounds only with meaningful initial/later segments.
5. Continue the Lagoon/GMX fixture-sharing pilots, preserving per-worker placement, Python cache/nonce reset and fork-recycling safety.

Initial-review risks are recorded here for traceability. Backend identity,
cache masking, swap filtering and setup consumers were checked during
implementation; the coverage map below records the results. Actual HTTP/RPC
traffic and complete CI savings still require comparable CI runs. The broad
review's turn-limit failure supplies no independent final verdict.


## Implementation record

Implemented on 2026-10-03 on `audit-ci-test-burden`. The follow-up batch and simplification were published to the existing PR on
the user's explicit request. The complete suite was not run locally; use the
PR checks and recommendation comments for the latest CI acceptance results.

### Coverage map and changes

| Area | Removed or reduced work | Retained or strengthened coverage |
|---|---|---|
| GMX core | Approximately 21 explicit live `get_data()` calls reduced to six across liquidity, funding and interest modules; remove market-drift comparisons, positive-sum tautology and healthy-endpoint “error handling” | One filtered and one unfiltered response per module, matching sides, representative markets, numeric types, all non-negative interest and positive representative interest; empty successful responses fail |
| GMX APY | Seven period downloads and separate live symbol/latency smoke removed | Every period's actual API routing/validation and case-insensitive symbol mapping offline; missing data and provider failure; non-empty real APY endpoint result |
| GMX backends/cache | Remove discovery-cache masking and needless cold-cache downloads | Real REST, GraphQL and RPC discovery must reach their selected backend; no fallback; disk cache reopened and discovery endpoints prohibited on second instance, with fresh disabled-market validation retained |
| Cache correctness | Empty SQLite stores formerly evaluated false and prevented first writes | Sync and async adapter cache checks now use `is not None`; controlled first-write/APY reuse regressions and real reopened-market-cache integration |
| Decoder | Assertion-free Error/string, unknown and empty cases | Exact decoded message/selector diagnostic and `None` for empty bytes |
| Hyperliquid | Remove duplicate reconstruction of the same account/day under another classification flag; resume seven days reduced to one | Real reconstruction; initial twelve-hour segment non-empty, resume strictly adds fills and advances timestamp, original IDs retained, no duplicates; offline normal classification, persistence and monotonic vault upgrade |
| Lighter | One pool per deployment; explicitly disable unrelated Derive, Xerberus, Hibachi and Apex work | Both real Lighter providers, loop completion timestamps, one/two stored pools, metadata pickle and closed database handles |
| Lagoon | Co-locate two compatible flow-analysis checks; cache includes actual process generation | Existing historical state, full deposit/redemption/refusal assertions and snapshot isolation; one deployment under xdist |
| GMX trading pilot | Three compatible long/short/cancel cases share one deployment, removing two repeated setups | Fresh Python adapters/nonce, strict revert, fixed next timestamp, empty-position/order preconditions, PID/creation-time and deployment-code cache validation; deployment regression and forwarding variant remain independent |
| Helpers | Remove tests-package re-export shim and test-to-test deployment factory import | Existing GMX helper package used directly; reusable Lagoon environment/factories in `eth_defi.testing.gmx_lagoon` with API entry |
| Legacy | Delete unconditionally skipped unsupported token-tax and BNB Velvet modules | Active token, swap and provider integrations retained; this is collection/maintenance cleanup, not claimed runtime saving |
| Fork rule | HyperEVM warm-up now uses canonical fixed block | Private process retained because launch/warm-up is the test subject; exact fork height; Monad moving-state exceptions preserved |
| Logging | Restore worker root handlers/level and the four levels changed by console setup after each test | Subprocess regression reproduces a closed stream followed by logging and confirms cleanup; failure diagnostics retained |
| Reporting | Replace truncated-only accounting with optional full phase and resource artifacts in all four test workflows | Every flaky attempt captured at report creation; separate worker writers, strict-revert failure and actual child CPU exercised by subprocess/xdist regressions |
| Workflow setup | One complete Poetry install, remove unused Ganache/pnpm and diagnostic-only contract step; remove unused main GMX Sepolia mappings | Node/npm/Aave and Lagoon compiler dependencies retained; required extras include R2 and posts; 15-minute limits preserved |
| Workflow scheduling | Branch-stable concurrency cancels superseded master runs; expand existing GMX filters to shared providers/ABI/token/Safe/Lagoon/testing/dependencies | Existing vault change-detection gate retained; no new required-status design or branch-protection change |
| Slow collection | Discover seven modules instead of importing the whole suite to select slow cases | Full collection independently selects the same 13 cases; indirect-marker orphan regression fails even with `-m "not slow"` |
| Local verification | Pytest `pythonpath = ["."]` prevents shared virtualenv importing another checkout | Subsequent production-code checks explicitly exercise the worktree; rename colliding tokenised-fund backfill module without deleting its tests |

The six-call figure is an explicit method-invocation budget, not a measured HTTP
or RPC request count. Internal batching and API caching affect provider traffic.

### Focused manual results

All successful checks below used `source .local-test.env && poetry run pytest`
on 2026-10-03, with supplied RPC environment variables redacted. Manual runs are
not CI results. Earlier production-code checks imported the main editable
checkout; the table records subsequent checks against this worktree.

| Focused command suffix | Result | Observation |
|---|---|---|
| GMX core modules + decoder module + logging module | 40 passed, 89.67s | Real Arbitrum reads; no empty-result skips; variable provider latency means this elapsed time is not a CPU or CI saving claim |
| CCXT markets/APY + disk-cache/TTL + account metadata modules | 43 passed, 6.94s | Real REST/GraphQL/RPC, non-empty APY and reopened disk-cache checks |
| APY mapping/cache + async disambiguation + tokenised-fund backfill | 25 passed, 1.74s | Sync/async first writes and existing transformation/persistence regressions |
| Lighter cycle + Hyperliquid reconstruction/resume + HyperEVM warm-up | 4 passed, 18.19s | Lighter call 9.99s; reconstruction 4.00s; resume 1.30s; both real Lighter providers |
| Lagoon flow-analysis + logging, `-n 2 --dist loadgroup` | 18 passed, 19.68s | Single 14.25s deployment; both flow checks on one worker; no captured-stream logging errors |
| GMX trading + PnL modules, `-n 4 --dist loadgroup` | 12 passed, 110.84s | Security/money-movement coverage retained after helper move and first sharing pilot |
| Reviewed GMX cancel/short/long/deployment mixed order, `-n 4 --dist loadgroup` | 4 passed, 70.50s | Strict revert, fresh adapters, timestamp control and dedicated deployment fixture; subsequent shared setups below one second |
| Reporting + strict snapshot failure modules | 4 passed, 10.56s | Actual pytest subprocesses and xdist; failed retry attempt captured; child CPU accounted; indirect slow marker rejected |
| Temporary real closed-fork/relaunch probe | 1 passed, 43.72s | Rebuilt deployed code and balances after process recycling; kept as manual evidence rather than adding expensive CI work |
| Main workflow collection shape, `--collect-only` | 3,093 selected, 7.55s | Collection only; duplicate backfill basename repaired |
| Slow collection comparison, `--collect-only -m slow` | Same 13 selected | Seven-module collection 1.55s versus full collection 7.64s (3,754 collected); no test-body execution |

The extended probe also passed (1 test, 62.93s): an outer snapshot removed
deployment state without restarting the process, the code check rebuilt it,
and a subsequent closed-process/relaunch also rebuilt it. The expected pool
recycling warning was recorded. Both manual probes remain outside routine CI.

### Resource observations and preservation choices

The unchanged Lighter settlement-budget regression passed locally in 43.15s
(call 41.51s). Process CPU was 5.50s and reaped-child CPU 0.33s, including Anvil.
This warm local observation is dominated by elapsed waiting rather than CPU.
Keep its budget/security assertions; deployment/transaction-stage investigation
is the next justified optimisation, not deleting it because of its CI duration.

Ember initially stopped because this worktree lacked the pinned Lagoon source
submodule and Soldeer dependencies. Initialise those prerequisites before
profiling; that environment failure is not a test regression or a reason to skip
it. After preparation, the unchanged Ember redemption cycle passed in 117.38s
(call 115.91s), with process CPU 5.64s and reaped-child CPU 1.76s. Retain its
historical lifecycle and operator-payout coverage. These are single warm local
observations, not a ranking of the whole suite or guaranteed CI savings.

### Independent implementation review

A broad Opus 5.5 pass timed out before a final verdict and is not counted as a
completed review. A narrower `claude-opus-5-5` Read-only review completed with a
successful final result after independently reading the GMX baseline fixture,
its reusable factories and snapshot helper. It found no blocking defect for the
module's own isolation, but identified interaction risks and suggested guards.

Applied: code-present validation on cache hits; strict snapshot restoration;
position/order preconditions; explicit next timestamp; independent deployment
fixture instead of selecting by test name; corrected generation documentation.
The general GMX fork uses eight unlocked addresses, while this pilot uses only
the two token whales, and the pool key includes all launch kwargs. No other
current consumer matches the pilot configuration, so it does not reuse the
general/read-only fork. The reviewer did not inspect that consumer inventory;
the primary agent verified it before retaining the dedicated configuration.

### Remaining CI acceptance and deliberate boundaries

- Run the implemented jobs on a comparable CI runner before projecting total
  job savings, logging-volume reduction or cache-hit/miss installation time.
- Retain the existing Forge prerequisites, private historical lifecycle forks,
  Monad tip tests, production-scale DuckDB migration and real provider coverage.
- Do not extend one mutable baseline to every GMX/PnL/guard module without
  comparing serialisation cost and testing all custom deployment/state variants.
  The validated three-case pilot fulfils this batch; the broader rollout remains
  a measurement-driven follow-up.
- The legacy Enzyme `test_repr` file is already disabled by its directory's
  retired Blue V4 collection policy. Fixture trimming there would not reduce
  active CI runtime; no speculative rewrite was made.
- Existing CI-only skips and dated flaky history remain. Their recovery needs
  actual CI evidence; these reductions do not justify erasing them.


Post-change static fork inventory: 77 direct calls in test/debug sources, with
66 fixed/configured blocks, five manual debug calls at tip, three legitimate
Monad moving-state calls (Accountable, Curvance, WUSDN), and three skipped legacy
calls (`test_decode_tx.py`, `test_revert_reason.py`, `velvet/test_velvet_api.py`).
No active non-Monad tip fork remains in that inventory. “Configured” is not a
claim that every direct fixture is pooled or every literal has a committed warm
seed: those remaining fixtures need behaviour-specific rollout measurements.

### Reproduction commands

Manual real-provider checks used the operator-supplied `JSON_RPC_ARBITRUM`,
`JSON_RPC_BASE`, `JSON_RPC_ETHEREUM` and `JSON_RPC_HYPERLIQUID` fallback providers
(redacted), GMX REST/Subsquid, Hyperliquid and both Lighter deployments. Run each
focused command separately with a three-minute outer timeout:

```shell
source .local-test.env && timeout 180s poetry run pytest tests/gmx/test_available_liquidity.py tests/gmx/test_funding_fee.py tests/gmx/test_open_interest.py --timeout=60 -q
source .local-test.env && timeout 180s poetry run pytest tests/gmx/ccxt/test_ccxt_gmx_markets.py tests/gmx/ccxt/test_ccxt_gmx_apy_mapping.py --timeout=100 -q
source .local-test.env && timeout 180s poetry run pytest tests/lighter/test_scan_loop_integration.py tests/hyperliquid/test_trade_history_integration.py::test_trade_history_sync_resume tests/hyperliquid/test_trade_history_integration.py::test_reconstruct_vault_trade_history tests/rpc/test_anvil_hyperliquid_warmup.py --timeout=90 -q
source .local-test.env && timeout 180s poetry run pytest tests/lagoon/test_lagoon_flow_analysis.py -n 2 --dist loadgroup --timeout=90 -q
source .local-test.env && timeout 180s poetry run pytest tests/gmx/lagoon/test_gmx_lagoon_integration.py tests/gmx/lagoon/test_gmx_close_pnl_token.py -n 4 --dist loadgroup --timeout=90 -q
```

The final offline/reporting/pool/account batch passed 30 tests in 11.96s after
all review fixes. YAML parsing, shell syntax, artifact placement, Ruff formatting
and whitespace checks passed. The expected synthetic single-provider warning
and intentional real restart warning are retained as diagnostics.


### Simplification and documentation review (2026-10-03)

Consolidated standard and fee-forwarding setup into one parameterised factory, preserving
independent deployment and zero Safe ETH in the forwarding case. Removed a
redundant Safe balance write and nonce sync; token transfers now use decimal
amounts through the shared token helper. Clarified strict snapshot failure
semantics and backend-dependent timestamp behaviour. Updated stale performance
guidance on Ganache, marker registration, retry counts and committed RPC seeds.
This reduces maintenance duplication; it is not a measured CI speed claim.


The final grounded Opus 5.5 review found an empty REST discovery-cache write
risk; added a non-empty write guard and ignored old empty cache hits, with two
file-backed regressions. A strict GMX revert failure now clears the deployment
cache and closes the process so the pool relaunches on the next request.
Branch-stable concurrency intentionally cancels superseded master runs to reduce
runner work; intermediate commits may therefore lack completed CI results.
The slow-discovery guard needs broad main collection; the regression exercises
that contract even when the slow test would otherwise be deselected.


Two final grounded read-only Opus 5.5 passes completed successfully. The first
covered deployment/cache/reporting infrastructure and workflows; the second
covered the rewritten live/offline checks and verified the cache/disposal fixes.
No blocking defects remained. Corrected docstring indentation, added missing
parameter/return documentation and type hints, removed a redundant positive
resume-count assertion, and removed the redundant fee-forwarding factory wrapper.
Focused final checks: nine GMX/reporting cases passed in 101.88s; 21 real-backend,
APY/cache and reporting/snapshot cases passed in 17.35s. These are manual local
runs on 2026-10-03 using supplied Arbitrum RPC and public GMX endpoints.


After removal of the redundant forwarding wrapper and addition of failed-revert
process disposal, all five GMX Lagoon cases passed under `-n 4 --dist loadgroup`
in 103.99s, including shared trading, independent deployment and fee forwarding.
Formatting passed for all 44 surviving changed Python files; workflow YAML,
embedded Bash syntax and whitespace checks passed.


### First complete-batch CI observation (head f81d4303d)

All non-documentation checks passed. Against the recorded master baseline,
pytest elapsed changed: main 208.46s to 233.32s (slower), GMX 285.59s to 237.20s,
slow 154.11s to 48.23s and vault 119.42s to 96.47s. Total job elapsed changed:
main 319s to 376s, GMX 372s to 337s, slow 201s to 135s, vault 148s to 164s.
These are single observations with variable providers/cache preparation; GMX's
baseline is the latest preceding master run. Main had more executed coverage,
including 19 R2 checks previously skipped without the optional dependency.
Those R2 checks were cheap; this does not explain the main slowdown by itself.

Main closed-stream errors fell from 1,450 to zero and its log from 17.93 MiB to
0.97 MiB. Remaining costs include Ember redemption (100.27s), Lighter settlement
budget (47.86s), Ostium (29.84s), independent GMX PnL setups (146.43s accumulated
worker setup) and Aave/checkout preparation. Do not remove these meaningful
checks simply for duration. Worker CPU reports establish a new baseline, not a
CPU reduction: old CI had no equivalent resource reports.

Artifact completeness inspection found relative timing paths followed pytester
working-directory changes: setup/call reports for three reporting regressions
were written outside the uploaded artifact. Resolve timing paths at configure
and exercise a relative path plus directory-changing xdist tests. Revalidate on
CI before treating full reports as complete.


A third focused grounded Opus 5.5 review completed successfully and confirmed
that the relative-path xdist regression fails on the old writer (four rows
instead of six). Its residual resource-directory concern was addressed too:
resolve that directory at configure and verify session resources remain at the
original path even when a probe leaves its working directory changed.


### Completed CI baseline and next implementation batch (2026-10-03)

Published head `d5ee0ddb4` passed all four test workflows. Pytest elapsed was
main 224.20s, GMX 229.52s, slow 47.82s and vault 117.33s. Relative to the
recorded master baseline, GMX improved 19.6% and slow improved 69.0%; main was
7.6% slower and vault roughly unchanged. Worker CPU reports provide a new
baseline only. Complete phase/resource artifacts were retained after fixing
relative paths. The [CI comparison comment](https://github.com/tradingstrategy-ai/web3-ethereum-defi/pull/1623#issuecomment-5974135099)
records the runs and remaining bottlenecks.

The next local batch implements the measured GMX PnL setup and fork-order query
candidates. Seven distinct payout/NAV regressions remain. Their seven private
deployments become three shared mutable partitions at the canonical block,
with matching xdist markers, group-specific generation caches, strict snapshot
restoration and fresh Python adapters. Snapshot creation or strict restoration
failure clears the affected cache and closes the process. Independent
deployment and native-fee forwarding retain their own deployments.

Three overlapping create/open/stop-loss cases become one cancellation lifecycle
with their original shape and dispatch assertions. Nonexistent-key and
empty-account cases remain. Unjustified cancellation retries are removed; the
existing dated PnL flakiness history remains. Public indexers cannot see private
fork transactions, so these local tests use real RPC position/order reads and
bypass public history. Separate live provider coverage remains, and a focused
offline regression explicitly exercises the production pending/cache/Subsquid
merge, increase filtering and execution-key deduplication. This does not claim
that a mocked history response checks provider availability.

Local before/after: the same two integration modules under four loadgroup
workers passed 12 cases in 98.45s before and 10 in 65.77s after (33.2% less
elapsed). Displayed PnL setup total fell from 149.35s to 65.30s; these omit
sub-second durations suppressed by pytest and are accumulated worker seconds.
An initial single-group attempt passed 27 checks but regressed elapsed to
127.60s; three partitions retain parallelism while reducing deployment work.
The final combined PnL, trading, cancellation, pool and reader run passed 32
cases in 100.50s with no retry attempts. The history-merge check passed in
0.10s. Manual tests used the supplied Arbitrum RPC fallback providers
(redacted), live GMX market/oracle APIs and actual Anvil keeper execution.

At this stage the batch was local; the publication and measured CI status below supersede that observation.
Next candidates are the main-suite Ember redemption, Lighter settlement-budget
and Ostium transaction stages, plus CI checkout/Aave preparation. Preserve
those distinct lifecycle and budget/security assertions; profile stages before
changing them. Repeated GMX keeper transaction costs now remain after reducing
deployment setup.


Two grounded read-only Claude CLI Opus 5.5 reviews completed successfully for
this batch. The first prompted status-aware pending/history assertions and
explicit offline merge coverage; the final pass found no blocking defects.
The empty-account exchange fixture is function-scoped, so its strict empty
history assertion does not inherit the lifecycle cache. Transport errors still
rely on the pool's next-request liveness probe; four-worker timings should not
be extrapolated to runs with fewer workers. Formatting and whitespace checks
passed.


### Main-suite follow-up and authorised Ostium retirement (2026-10-03)

The user explicitly overrode the previous preservation recommendation for
Ostium: mark its tests skipped because unsupported after the hack. All 15
Ostium cases now carry the dated, unconditional reason
`Ostium unsupported after the hack`, including offline ETA/withdrawal/policy
and mock-guard cases. Shared modules retain other protocols; Gains' policy
case is separately parametrised and remains enabled. This is intentional
coverage retirement, not evidence that the tests were redundant or flaky.

Implemented the other measured main-suite candidates:

- Skip only Anvil's unconditional two-second nonce propagation waits after
  confirmed Lagoon configuration transactions. Live-network delays, nonce
  synchronisation and receipt success checks remain.
- Deploy Ember's Lagoon vault through the existing factory at its historical
  lifecycle block rather than rebuilding the protocol from source. All exact
  Ember shares/ticket/payout assertions remain; a separate real Gains lifecycle
  still passes with fresh source deployment.
- Cache Aave's installed dependency tree plus Solidity artifacts/cache and
  TypeChain output. Exact key includes OS, architecture, shared Node-version
  setting, pinned submodule commit and lockfile hash; no broad restore keys.
  Verify non-empty lockfile and generated outputs, retaining the existing
  installer on every run. Mutable deployment outputs are excluded.

Baseline and candidate manual Ethereum-provider runs under two loadgroup
workers passed both Ember/Lighter tests: 109.13s before, 71.76s after (34.2%
less elapsed). Calls: Ember 102.51s to 65.42s; Lighter 41.66s to 29.22s.
Gains' independent fresh-protocol lifecycle passed in 46.09s call time.
Final focused installation/module/policy checks passed six cases with 15
Ostium skips in 1.90s.

The initial Aave installation check failed because this worktree lacked its
submodule; it was initialised at the committed revision. The host default Node
24 then correctly failed the package's engine requirement. Installation and
focused checks passed under Node 18, matching CI. A clean source-tree probe
restoring only the cache's installation/build outputs loaded Hardhat
successfully; the warm in-process installer completed as a no-op in 0.001s.
This measures local preparation only; CI archive transfer costs are unknown.

Checkout narrowing remains deferred until a complete source/build prerequisite
inventory exists. A new Ember historical seed also requires capture with CI's
pinned Anvil 1.3.2, not the local 1.7.1-dev binary. Keep elapsed, CPU, total job
preparation and intentional coverage retirement separate in the next CI report.
These batches were subsequently published at `733a53b07`; see the latest measured status below.


Two grounded read-only Opus 5.5 reviews completed successfully. The final
follow-up approved the complete Aave cache and workflow ordering. It noted
that missing generated outputs fail clearly rather than trigger reinstall,
and that full warm Hardhat deployment/compile behaviour and archive-transfer
costs still require CI measurement. Workflow YAML, embedded Bash, Ruff
formatting and whitespace checks passed.


### Remaining recommendations 1–5: local results (2026-10-04)

1. **Profile Ember:** transaction success/trace paths dominated. A method-only
   proxy profile identified ten upstream receipt attempts taking 62.51s in a
   65.92s seeded run. Bound this fork's attempt timeout to two seconds, retaining
   one attempt per provider, zero backoff and warning-level failover logs.
   Exact shares/tickets/payout
   checks passed twice in 12.77s and 13.63s; receipt attempts took 7.54s and
   7.93s respectively. This is provider-dependent local elapsed evidence,
   around 79–81% below the seeded baseline, not a CPU or CI result.
2. **Refresh historical state:** correct the earlier suggestion that Ember's
   seed was absent: the existing seed had 19 accounts and 47 storage slots.
   Captured a denser seed with the official CI-pinned Anvil v1.3.2 Alpine amd64
   binary (34 accounts, 61 slots). Cold full lifecycle passed in 74.18s;
   repository-seeded full lifecycle passed in 65.92s before timeout tuning.
   Preserve the fixed historical implementation block and exact assertions.
   State caching does not cover every receipt lookup.
3. **Narrow checkout:** omit all submodules only in the GMX workflow, whose
   collected paths use committed artifacts and existing factories. Keep main
   unchanged because Enzyme adapter/guard source deployments, fresh Lagoon
   deployments and Aave preparation need source prerequisites. A narrower main
   selection remains deferred pending complete cold-run validation.
4. **Measure Aave cache:** Node 18 cold install/build took 29.77s; a 64.4 MB
   compressed four-path archive took 1.02s to create and 1.23s to restore at the
   same absolute source path. Warm installer was a no-op; warm Hardhat compile
   took 4.42s with nothing to compile. Relocation caused recompilation (21.71s).
   No additional cache changes were justified; CI transfer/net savings remain
   unknown.
5. **Reduce GMX metadata work:** per-test actual token-list reuse cuts the
   profiled case from 18 provider fetches (3.499s) to one (0.176s). Deep copies
   prevent mutation leaks; exceptions are uncached and cleanup restores the
   original reader. Prices remain live; provider integrations remain direct.
   Seventeen focused integration/helper cases passed in 47.67s with four
   loadgroup workers. Four follow-up helper/actual-endpoint checks also passed,
   including exceptional cleanup. Do not attribute all combined-run savings
   solely to metadata reuse because earlier batches changed deployment waits.

The first grounded read-only Claude CLI Opus 5.5 review found no blocking
metadata or checkout defects and suggested exceptional cleanup coverage, which
was added and passed. A separate review covers Ember's bounded failover and
seed provenance. It identified that an explicit proxy config must preserve the
automatic policy's zero backoff and warning logging; these were restored, with
one attempt per usable provider. All-slow providers can now fail uncached state
reads instead of succeeding slowly; the local comparison has one baseline
sample. No tests were removed in this batch; it was subsequently published at `733a53b07`.
An additional full lifecycle starting from the committed seed with no live
block cache passed in 12.35s. After restoring the automatic proxy's diagnostic
and zero-backoff policy, the final full lifecycle passed in 10.68s (9.43s call),
83.8% below the single 65.92s seeded baseline. The original local cache was
restored after the non-destructive capture and verification probes.
The grounded Opus 5.5 follow-up approved the code policy. Its timing-label
finding was addressed by distinguishing the initial timeout probes from the
final 10.68s policy result, and endpoint splitting now exactly matches launch.
Ruff, workflow YAML and whitespace checks passed; no remote changes were made.


## Published representative coverage and CI status (2026-10-05)

Head `35afe950d` reduced the guarded synchronous lifecycle matrix from 16 to
seven representatives and removed one duplicate Ember liquidity refusal.
Main passed 2,847 tests in 169.41s versus 201.86s in the previous complete green
sample; vault passed 607 in 98.63s versus 120.72s. These are 16.1% and 18.3%
elapsed improvements. Main CPU increased, so do not infer overall CPU savings.
The guard matrix accumulated 104.50s versus 183.42s and main workers are now
balanced; Ethereum vault work still accumulated 87.86s versus 48–54s peers.
See [the performance guide](../../docs/README-test-suite-performance.md) for
coverage choices, timing boundaries and the remaining bottlenecks.

Unchanged slow CI failed the Hyperliquid resume test because all daily fills
preceded its fixed noon cutoff. The failure reproduced locally. The follow-up
uses real observed fill timestamps to choose the partial-sync boundary while
retaining strict added-row, watermark, preservation and deduplication checks.
A focused real-provider run passed in 2.68s; final CI acceptance is pending.


### Final review follow-ups

Claude CLI Opus 5.5 completed a grounded review of the reductions, simplification
and sampled fork/cache helpers. Resume now reopens the database and spies on
real API calls to verify the exact stored watermark is used; inserted-count and
oldest-timestamp assertions distinguish correct resume from a deduplicated full
rescan. The cutoff uses the same datetime conversion as the bounded window.
Ten focused real integration checks passed together in 109.46s before these
resume-only strengthening checks. Gearbox's overridden deposit-closure reader
loses incidental happy-path coverage with its retired guarded lifecycle; this
is an explicitly accepted deployment-coverage trade-off, not equivalence of
all removed contracts. Final CI acceptance is pending.

The strengthened resume test passed against the real provider in 2.64s and
in 2.95s with `TZ=America/New_York`. A final grounded Claude CLI Opus 5.5
follow-up returned no blocking findings.
