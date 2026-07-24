# Test suite performance plan

Plan to make the test suite leaner and faster **without** removing Anvil
integration coverage and **without** introducing complex mock/replay paths.
We keep forking real chains; we stop paying for the same fork many times over.

> This plan was reviewed by Codex (`gpt-5.6-sol`, grounded read-only) on
> 2026-07-24. The findings are folded in below — most importantly the existing
> repository warning that repeated snapshot/revert under xdist has hung CI
> (`eth_defi/provider/anvil.py:1351`), the required-check implications of path
> filters, and the coverage hole path-gating would open.

## Implementation status (2026-07-24)

- **Lever 5 (docs schedule) — DONE.** `docs.yml` now runs on `17 6 * * 1,3,6`
  (Mon/Wed/Sat) + `workflow_dispatch`, with the publish/deploy steps guarded to
  `refs/heads/master`.
- **Lever 2 (CI caching) — DONE inline.** All four workflows key the built-in
  `actions/setup-python` Poetry cache on `poetry.lock` and rely on it as the
  single venv-cache mechanism (the redundant explicit `Cache poetry venv` steps
  were removed everywhere). Foundry toolchain split from a rolling
  month-namespaced `~/.foundry/cache/rpc` cache in `test.yml`, `test-gmx.yml` and
  `test-vault-protocol.yml`; `test.yml` checkout bumped to v4. **Deferred:** the
  DRY composite action and the submodule/ganache caching (documented below) —
  needs CI iteration.
- **Lever 3 (vault gating) — DONE (code).** New always-triggered
  `test-vault-protocol.yml` (job-level path skip via `dorny/paths-filter`, weekly
  + manual fallback, always-run `gate` job as the required check); `test.yml` now
  `--ignore=tests/erc_4626/vault_protocol`; `vault_characterisation` marker
  registered and auto-applied via `tests/erc_4626/vault_protocol/conftest.py`.
  **Manual follow-up:** switch the required status check in branch protection
  from the old job to this workflow's `gate` job.
- **Lever 4 (flaky) — NO EDITS; static classification found no clearly-safe
  removals (see the revised lever below).** Every `@flaky` test inspected reaches
  the network (body or fixture); validation of any removal needs test execution /
  failure history, so no test files were changed.
- **Lever 1 (shared forks) — INFRA + VALIDATED read-only PoC.**
  `eth_defi/testing/anvil_fork_pool.py` provides the reusable `AnvilForkPool`;
  `tests/conftest.py` exposes it as an opt-in session-scoped `anvil_fork_pool`
  fixture. To let many tests share one fork, characterisation tests are
  **normalised onto a canonical midnight block per chain**
  (`eth_defi/testing/fork_blocks.py`, `ARBITRUM_MIDNIGHT_BLOCK` = the last block
  at/before 2026-07-24 00:00 UTC — recent enough that the PoC vaults have state,
  fixed and cache-friendly; each vault is validated before normalising its test).
  **11 read-only Arbitrum characterisation tests** now share one fork via an
  `xdist_group("fork:arbitrum:midnight")` marker: `test_goat`, `test_harvest`,
  `test_autopool`, `test_dolomite`, `test_llama_lend`, `test_nashpoint`,
  `test_superform`, `test_truefi`, `test_untangle`, `test_usdai`,
  `test_yearn_yvault`. They previously forked **five different blocks**
  (392M/409M/422M/430M/478M) — normalising them onto one midnight block collapses
  five-plus cold forks into one.

  **Measured locally** (anvil 1.7.1, real Arbitrum archive): a serial run launches
  **one** Anvil for the group (vs one per file); tests co-locate on one worker
  under `-n2 --dist loadgroup`. Of the migrated set, 9 passed at the midnight
  block **with no assert changes** (metadata/tolerant asserts); `test_goat`'s
  exact `fetch_pnl` was refreshed from the new block. The runs also hit (and
  `@flaky`-retried) transient RPC timeouts, confirming these tests genuinely need
  their retry decorator (Lever 4).

  **Not normalised** (kept on their own forks): `test_silo` (its
  `utilisation <= 1.0` invariant no longer holds at the later block — the vault is
  over-utilised there), `test_plutus` (master actively evolved it with
  deposit-manager assertions), and `test_d2` (epoch/phase-dependent). This is the
  expected per-vault-validation caveat: not every test can be normalised.

  To stop the isolated vault workflow rate-limiting the archive provider,
  `test-vault-protocol.yml` caps pytest workers (`-n ${MAX_WORKERS:-4}`) instead
  of `-n auto`; the shared-fork pool further cuts the total fork count.

  Being read-only, this PoC validates fork-sharing + xdist co-location but does
  **not** exercise the snapshot/revert-under-xdist hang risk. Converting any
  *mutating* test to a shared fork still requires a separate CI PoC with
  snapshot/revert (via
  `eth_defi.testing.evm_snapshot_fixture.evm_snapshot_revert`) before rollout.

## Why

The suite is an integration suite behaving like a unit suite:

- A recent green `test.yml` run: **1750 passed, 235 skipped in 331s** wall on
  the Beefy runner (`-n auto --dist loadgroup`).
- **162 of ~492 test files launch their own Anvil mainnet fork**, each replaying
  archive state on setup.
- The **slowest 20 durations are 100% Anvil fork setup/call**, 18–46s each
  (`tests/lagoon/test_deploy_base.py`, `tests/guard/test_guard_simple_vault_erc_4626.py`,
  `tests/ipor/test_ipor_deposit.py`, `tests/cctp/test_cctp_lagoon_fork.py`, …).
- Many forks target the **same `(chain, block)`**: 11 tests fork Arbitrum at
  block `392_313_989`, 7 at `375_216_652`, and so on. Before this work each paid
  a full cold fork.
- **`--dist loadgroup` was configured but no test used `xdist_group`** — the
  grouping mechanism that lets tests share a fork safely was unused, so
  module-scoped forks could be rebuilt on multiple xdist workers. (The Lever 1
  PoC below normalises two of these onto a shared midnight block.)
- **394 `@flaky` decorators across 158 files** (173 files reference `flaky` at
  all). Blanket retries do not multiply runtime for *passing* tests — they only
  add time when an attempt fails — but on non-network tests they still mask real
  regressions (a test passing 1-in-3 reports green) and slow down genuine
  failures.

The five levers below are independent and can land as separate PRs. Rollout
order and the mandatory proof-of-concept gate for Lever 1 are at the end.

## Lever 1 — session-scoped Anvil forks that are xdist-safe

### Goal

One Anvil process per `(chain_id, fork_block_number, launch-config)` per xdist
worker, reused across every test that needs that fork, with per-test state reset.
Tests keep using a real fork; they stop launching one each.

### ⚠️ Known risk this lever must clear first

`eth_defi/provider/anvil.py:1341-1361` (the `AnvilSnapshotState` docstring)
already documents two failure modes from exactly this pattern:

1. **CI hangs under xdist.** "module-scoped Anvil forks combined with repeated
   snapshot/revert cycles can hang on CI runners under `pytest-xdist` parallel
   execution, likely due to Anvil process responsiveness degradation after many
   revert cycles." A shared long-lived fork is *not* transparently equivalent to
   a fresh fork, contrary to the naive framing.
2. **`ScopeMismatch` from shared conftest.** An `autouse=True` restore fixture in
   a shared `conftest.py` breaks when sibling modules override `web3` at function
   scope (e.g. a different chain). So the shared-fork fixture **must be opt-in per
   module**, not a blanket autouse in a top-level conftest.

**This lever does not land on assertion alone.** It requires a bounded CI
proof-of-concept (see rollout) proving no hang across many revert cycles, with
launch-count / revert-count caps and periodic Anvil recycling as a safety valve.

### Design

1. **Reuse the existing helpers — do not reinvent snapshot/revert.**
   `create_anvil_snapshot_state(web3)` (anvil.py:1399) and
   `reset_anvil_snapshot(web3, state)` (anvil.py:1412) already handle the subtle
   part: `evm_revert` *consumes* the snapshot it restores, so `reset_anvil_snapshot`
   immediately re-snapshots after each reset. Raw `snapshot()`/`revert()`
   (anvil.py:1333/1387) must not be used directly for reuse — and note `revert()`
   only *returns* its result, so teardown must assert `revert(...) is True`.

2. **Shared fork registry (session-scoped factory), opt-in per module.**
   A session-scoped factory keyed on the full launch configuration (see step 4)
   launches Anvil once per worker per key and returns the existing `AnvilLaunch`
   on repeat requests; a session finaliser closes all launches. Modules opt in by
   requesting the factory — no autouse restore in a shared conftest.

3. **Per-test EVM reset, and know its limits.**
   Use `create_anvil_snapshot_state` after the expensive baseline (fork +
   any post-fork deployments), and `reset_anvil_snapshot` in a module-local
   restore fixture. **Snapshot/revert resets EVM state only** — it does *not*
   reset Python-side state: cached contract objects, provider middleware, nonce
   managers, background threads/proxies, or fixture caches. Tests relying on those
   must not share a fork. A test that crashes or times out mid-teardown must force
   fork **disposal and recreation**, not reuse of a possibly-wedged Anvil.

4. **Registry key must capture all state-affecting launch options.**
   `fork_network_anvil` is a thin alias of the fully-configurable `launch_anvil`
   (anvil.py:1702). `(chain_id, block)` is insufficient: the key must also include
   hardfork, gas/code-size limits, block time, unlocked/impersonated accounts,
   tracing, and upstream/failover RPC configuration — or the factory must reject
   incompatible requests for an already-launched key. Forks that also perform
   expensive post-launch baseline deployments should snapshot *after* that
   baseline so it is captured once.

5. **Pin same-fork tests to one worker with `xdist_group` — at collection time.**
   `--dist loadgroup` sends all tests sharing an `xdist_group` to one worker.
   Crucially, **a fixture cannot add the group at fixture-execution time** — by
   then the scheduler has already assigned tests. The group must be a static
   marker (`@pytest.mark.xdist_group("fork:arbitrum:392313989")`), a marked
   fixture parameter, or applied via a collection hook (`pytest_collection_modifyitems`).
   Keep groups at `(chain, block, config)` granularity so distinct forks still
   spread across workers.

6. **Register the marker.** `pyproject.toml:244` currently registers only `live`
   and `slow`; add any new marker there to avoid `PytestUnknownMarkWarning`.

### Acceptance

- Bounded CI PoC shows no xdist hang across the target group's revert cycles.
- Distinct Anvil launches per run fall from ~162 towards the number of distinct
  launch-config keys; the slowest-20 list stops being 100% fork setup.

## Lever 2 — proper CI caching (runners and actions)

### Goal

Cut cold-run wall time and archive RPC traffic by fixing every cache layer in
the CI workflows, not just the Anvil fork cache. Today the three test workflows
(`test.yml`, `test-gmx.yml`, `test-slow.yml`) cache inconsistently: keys drift,
the main job rebuilds its whole virtualenv every run, and the Foundry cache is
immutable so it never accumulates new fork state.

### Shared GitHub Actions cache semantics (apply to every step below)

- Caches are **immutable**: a step saves only on a key *miss*, and only **after
  the job succeeds**. An exact-key hit therefore never updates contents — use a
  rolling key + `restore-keys` prefix whenever the cache must grow.
- Caches are **branch-scoped**: a run restores from caches created on its own
  branch/merge-ref then falls back to the base branch. A PR *can* create caches in
  its own scope, but the broadly-shared cache most cold PRs rely on is the one
  warmed by base-branch (master) runs.
- The repo cache limit is a **default of ~10 GB** (may be raised by admins), with
  LRU eviction — do not cache large immutable toolchains under rolling keys (that
  re-uploads them every run and evicts useful entries).

### Design

1. **Standardise on the built-in Poetry cache, keyed on `poetry.lock`. (done)**
   Every workflow's `actions/setup-python` step uses `cache: "poetry"`. This
   project uses Poetry's **default (non-in-project) virtualenvs** — no
   `virtualenvs.in-project` config exists — so venvs live under
   `~/.cache/pypoetry/virtualenvs`, which is inside the directory `setup-python`
   caches; the built-in cache therefore already restores the venv. The defect was
   the key hashing `**/pyproject.toml` only, so it neither invalidated cleanly on
   a locked-dependency change nor hit when only source changed. Fixed by adding
   `poetry.lock` to `cache-dependency-path`.

   **Confirm against CI logs** that `poetry install` is a near no-op on a warm
   cache; if the built-in cache turns out not to restore the venv here, re-add a
   single explicit venv-cache step (keyed on `poetry.lock`) rather than relying on
   `setup-python`.

2. **Remove the redundant explicit venv caches. (done)**
   `test.yml` had none; `test-gmx.yml`, `test-slow.yml` and `docs.yml` each added
   an **explicit** venv cache (`poetry env info --path`) **on top of** the
   built-in `setup-python` poetry cache — double-caching the same directory under
   drifting keys (`gmx-venv-…`, `slow-venv-…`, `docs-venv-…`). All four explicit
   steps were removed so the built-in cache is the single mechanism everywhere.

3. **Split the Foundry cache: immutable toolchain vs rolling RPC cache. (done)**
   Previously every workflow cached all of `~/.foundry` under the static key
   `foundry-v1.2.3-${{ runner.os }}`, so the toolchain and the fork RPC cache
   shared one immutable key and **new fork reads under `~/.foundry/cache/rpc` were
   never saved**.
   - Keep the *toolchain* (`~/.foundry/bin`) under an immutable
     `foundry-toolchain-v1.2.3-*` key. (`foundry-rs/foundry-toolchain` also caches
     the binary itself — the explicit toolchain cache is a belt-and-braces.)
   - Put **`~/.foundry/cache/rpc` in its own `actions/cache` step** with a rolling
     key namespaced by Foundry version, OS and **month**:
     `foundry-rpc-v1.2.3-${{ runner.os }}-<month>-${{ github.run_id }}`, with a
     `restore-keys` prefix that includes the month
     (`foundry-rpc-v1.2.3-${{ runner.os }}-<month>-`) **and no cross-month
     fallback**. Within a month the cache accumulates via `run_id` + prefix; at a
     new month it starts cold and rebuilds, bounding growth. (A cross-month
     restore prefix would defeat the rebuild — it would restore last month's cache
     and republish it under the new key forever.)
   - Anvil writes fork reads under `~/.foundry/cache/rpc/`. The observed layout is
     `rpc/<network-name>/<block>/storage.json` (e.g. `rpc/base/48956940/storage.json`)
     — keyed by Foundry's **network name, not chain id**, with a per-block
     directory. Cache the whole `~/.foundry/cache/rpc` tree rather than a guessed
     sub-path, and **re-validate the exact layout against pinned Foundry v1.2.3**
     before relying on it (it has changed across versions). Note the network-name
     keying: confirm two different upstream endpoints for the same chain cannot
     collide before sharing this cache. At a fixed `fork_block_number` the entries
     are stable and reusable; do **not** cache mutable-tip forks. This is what
     makes Lever 1's shared forks also fast on a cold worker.

4. **Cache git submodules / avoid re-cloning heavy ones. (deferred)**
   `test.yml`'s checkout uses `submodules: true`, re-cloning large submodules
   (e.g. `contracts/aave-v3-deploy`) every run. `test.yml` checkout was bumped to
   `actions/checkout@v4` (done); still to do — cache the submodule working trees
   or fetch only the submodules tests actually need (the workflow notes it only
   needs `contracts/aave-v3-deploy`). Pair with the existing npm cache (the
   `Setup Node.js` step) so the Aave npm install is not redone cold.

5. **Cache the remaining per-run installs. (deferred)**
   - The `Install Ganache` step does `yarn global add ganache` every run — pin the
     version and cache the global yarn/npm dir, or drop it if unused.
   - The `Cache Lagoon soldeer deps` step is already correct (keyed on lockfiles +
     `restore-keys`) — keep it as the template for lockfile-keyed caches.

6. **DRY the setup to prevent future drift. (deferred)**
   The Python/Poetry/Foundry setup is duplicated across the workflows and has
   already diverged. Extract it into a local **composite action**
   (`.github/actions/setup-test-env`) or a reusable workflow so the cache keys,
   Foundry split, and submodule handling are defined once and stay consistent.

### Acceptance

- Warm-cache runs restore the Poetry venv from the built-in cache (`poetry
  install` is a near no-op) with no separate venv-cache step.
- Warm-cache runs show reduced archive RPC traffic and faster fork setup on
  repeat blocks; the immutable toolchain cache size stays flat while the RPC
  cache grows across runs within a month.
- All workflows share one caching approach (ideally via a shared composite
  action once deferred item 6 lands).

### Local dev

`~/.foundry/cache/rpc` and the Poetry venv persist between local runs already, so
a developer's second run of a fork test is fast with no extra work.

## Lever 3 — run vault protocol tests only when relevant code changes

### Goal

`tests/erc_4626/vault_protocol/` is 98 files (the largest group), mostly
single-vault characterisation tests (see `test_altura.py`). Reduce how often they
run on the every-commit path — **without opening a permanent coverage hole and
without breaking required checks.**

### Two hard constraints the naive version gets wrong

1. **These tests are not only about protocol integrations.** They also exercise
   shared provider, Anvil, ABI, ERC-20/token, Web3, dependency and fixture
   behaviour. A path list of just `eth_defi/erc_4626/**` + `eth_defi/data/vaults/**`
   would let regressions in `eth_defi/provider/**`, shared ABI/token code,
   `tests/conftest.py`, `pyproject.toml`, or `poetry.lock` reach master
   indefinitely. Any path list **must** include those shared paths, and there
   **must** be a periodic/manual full-suite fallback (workflow_dispatch and/or a
   scheduled full run) so nothing is permanently un-gated.

2. **`on.pull_request.paths` breaks required checks.** A workflow skipped by a
   top-level path filter stays **pending** if its check is required, blocking
   merge; a same-name "stub workflow" risks duplicate/ambiguous status contexts.
   Instead **keep the workflow always triggered** and conditionally skip a *job or
   step* (using `dorny/paths-filter` or `git diff` on changed files) — a skipped
   job reports success and satisfies branch protection. Confirm the exact required
   job name, and include `merge_group` if the repo uses a merge queue.

### Design

1. Keep the vault group out of the main every-commit run with
   `--ignore=tests/erc_4626/vault_protocol` (mirroring the existing
   `--ignore=tests/gmx`).
2. Add an **always-triggered** workflow whose vault job runs only when a changed-
   files filter matches the (broad) path set above; otherwise the job no-ops and
   reports success.
3. Add a `workflow_dispatch` trigger and/or scheduled full run as the fallback.
4. Optionally tag the group `@pytest.mark.vault_characterisation` (registered in
   `pyproject.toml`) for local `-m` selection.

### Acceptance

- Commits touching none of the relevant paths skip the group while the required
  check still reports success. Full coverage remains reachable via the fallback.

## Lever 4 — remove `flaky` from non-network tests

### Goal

Retries only where genuine network non-determinism justifies them; everywhere
else tests fail loudly.

### Finding (2026-07-24): static classification found no clearly-safe removals

An implementation pass classified all 158 files carrying `@flaky` (394
decorators) by **static inspection**. This cannot prove a retry is unnecessary
(that needs failure history / test runs), but it strongly suggests the lever's
premise does not hold here:

- **0 of 158** flaky files are non-network at file level.
- The **17** `@flaky` tests that take no fixtures all make live network calls in
  their bodies (GMX/Hyperliquid/Subsquid APIs, archive RPC, Hypersync).
- Tests that fork a chain reach the network via a fixture even when the body
  looks pure — e.g. `gmx_config` depends on `web3_mainnet` (a live connection).
- The genuinely pure tests that *do* exist (e.g. `test_client_initialization`,
  `test_from_fixed_point` in `tests/gmx/test_graphql_client.py`) are **already
  not** decorated with `@flaky`. The authors scoped `flaky` correctly.

Conclusion: no removal was clearly safe from static inspection alone, so
**no test files were edited for this lever** — stripping `@flaky` blindly would
risk removing legitimate retry protection for no coverage gain. Any future
trimming must be validated per-test in a CI environment that runs the tests,
targeting only a rare pure function inside an otherwise-network module — not a
bulk sweep.

### Design (only if run with test execution)

1. **Classify by observed failure origin, not by "uses Anvil".** Note that a
   fixed-block Anvil test still issues **live archive RPC reads** during lazy fork
   execution and can hit transient provider failures — so "forked" does not imply
   "deterministic". Remove `flaky` where the test does no real network I/O (pure
   logic, ABI decoding, math/PnL, fixture-backed classification) or where history
   shows no genuine transient network failures.
2. **Strip the decorator** from those tests (`@flaky.flaky` /
   `@flaky(max_runs=..., min_passes=...)`).
3. **Keep and narrow** it on genuinely network-flaky tests, with a comment naming
   the network dependency that justifies the retry; prefer retrying the specific
   call over the whole test.
4. **Sweep** the 158 files with `@flaky` decorators (394 occurrences); triage each
   by whether its body does live network I/O beyond a fixed-block fork.

### Constraints / gotchas

- Removal will surface pre-existing genuinely-flaky tests — triage each (fix the
  root cause or re-add a narrow, justified retry); do not blanket-restore.

### Acceptance

- `@flaky` count drops sharply; every remaining usage carries a network-dependency
  justification. No net coverage loss; previously-hidden failures are triaged.

## Lever 5 — build docs on a schedule, not on every merge

### Goal

`docs.yml` previously ran on **every push to master**, on a Beefy runner with a
30-minute cap. Documentation rarely needs rebuilding per merge, and `CLAUDE.md`
already treats "Build documentation" as non-blocking for merges. It was moved to
a fixed schedule — **Monday, Wednesday and Saturday** — plus on-demand.

### Design

1. **Replace the `push` trigger with a cron schedule and add manual dispatch.**
   The workflow previously triggered only on `push` with no `workflow_dispatch`,
   so this *adds* one.

   ```yaml
   on:
     schedule:
       # 06:17 UTC on Mon (1), Wed (3), Sat (6). Non-zero minute on purpose:
       # top-of-hour cron is a GitHub high-load window and runs may be delayed
       # or dropped.
       - cron: "17 6 * * 1,3,6"
     workflow_dispatch: {}
   ```

   `workflow_dispatch` is important: it lets a docs fix be published immediately
   without waiting for the next scheduled slot.

2. **Guard the publish/deploy steps to master — the build has production side
   effects.** `docs.yml` does not just build: its `Upload docs zip to GitHub
   Release` step clobbers the shared `docs-latest` asset, `Deploy to Cloudflare
   Pages` publishes the site, and `Trigger Read the Docs build` kicks RTD.
   Previously only `push: [master]` reached those steps. Adding `workflow_dispatch`
   lets a **feature branch overwrite production docs**. Gate the publishing steps
   (or check out master for manual runs), e.g.
   `if: github.ref == 'refs/heads/master'`, so a manual/preview build can render
   docs without replacing the live site. Decide explicitly whether preview-branch
   publication is ever wanted; default to **no**.

3. **Keep the rest as-is** — the same `build-docs` job, Beefy runner,
   `contents: write` permission, and the rolling `docs-latest` asset RTD consumes.

### Constraints / gotchas

- **Scheduled workflows only run on the default branch** and use its workflow
  definition — this job already targets master, so that is fine.
- GitHub **disables scheduled workflows after ~60 days of repository
  inactivity**; not a concern for an active repo, but note it.
- Scheduled runs can be **delayed or dropped under platform load**, especially at
  the top of the hour — hence the `:17` minute above; the exact time is
  best-effort, so do not depend on precise timing.
- **Trade-off:** published docs on Read the Docs will lag up to a few days behind
  master between scheduled builds. This is the accepted cost of removing per-merge
  builds; use `workflow_dispatch` (from master) when a doc change must go out sooner.

### Acceptance

- `docs.yml` no longer triggers on push to master; it runs on the Mon/Wed/Sat
  cron and can be launched manually via `workflow_dispatch`.

## Rollout order

1. **Lever 5** (docs on a Mon/Wed/Sat schedule) — trivial one-file trigger change,
   immediately removes a per-merge Beefy job.
2. **Lever 4** (remove flaky from non-network tests) — lowest risk, restores
   signal quality so the suite can be trusted while optimising.
3. **Lever 2** (proper CI caching) — CI config only, no test edits. Do it in
   order of payoff: add the missing Poetry venv cache to `test.yml` and
   standardise the venv key first (largest cold-run win), then split the Foundry
   toolchain/RPC caches, then submodule/checkout and the DRY composite action.
4. **Lever 3** (change-aware vault gating) — only after the always-triggered
   required-check job and the periodic/full fallback are in place, with the broad
   path set.
5. **Lever 1** (session-scoped shared forks) — landed as a **read-only** bounded
   PoC (two Arbitrum tests normalised onto the midnight block, sharing one
   fork — validated locally: one Anvil for both, co-located under
   `--dist loadgroup`), which validates fork-sharing and xdist co-location but
   **not** the snapshot/revert hang risk.
   Converting any *mutating* test additionally requires a bounded CI PoC that
   proves no xdist hang across revert cycles, using the existing
   `create_anvil_snapshot_state`/`reset_anvil_snapshot` (or
   `evm_snapshot_revert`) helpers. Only then roll out incrementally.

## Out of scope

- Mock/VCR replay of contract reads — explicitly rejected; we keep real Anvil
  forks.
- A nightly-only workflow *as the sole home* for vault protocol tests — rejected;
  a scheduled/manual run is retained only as a coverage fallback for Lever 3, not
  as the primary trigger.
