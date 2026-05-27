# CI cost reduction design

- **Issue:** [#1034](https://github.com/tradingstrategy-ai/web3-ethereum-defi/issues/1034)
- **Date:** 2026-05-27
- **Status:** Design — awaiting approval before implementation
- **Author:** Saikat K (via Claude Code brainstorming session)

## Why

The Tradingstrategy GitHub org is exceeding its 3000-minute monthly GitHub
Actions budget. `web3-ethereum-defi` runs its full test suite on every PR
push using the **"Beefy runners" group** — confirmed to be GitHub Larger
Runners (paid, ~16× multiplier on 16-core Linux). A typical PR run takes
~10 minutes wall-clock, which costs ~160 billable minutes. A handful of
PRs per day drains the org budget within days. Cost growth has been
exponential since 2026-05-04 as PR cadence increased.

The repository is also a submodule of `freqtrade-strategies`,
`gmx-strategies`, and `gmx-strategies-livebt`, so CI savings here
cascade to downstream repos.

## Goal

Drop `web3-ethereum-defi` PR CI billable minutes by ≥60% within three
rollout steps, without sacrificing master-branch coverage. Each step is
shipped as its own PR with a measurement gate before proceeding.

### Success exit criteria

- PR median billable minutes ≤ 60 (vs ~160 baseline)
- Docs-only / scripts-only / metadata-only PRs ≤ 5 billable minutes
- Org monthly Actions usage back under 3000 min with margin
- Master-branch coverage unchanged — every workflow still runs on push to master
- Every non-draft PR commit still triggers CI (savings come from running
  *less work* per run, not skipping runs)

## Constraints (from session decisions)

1. **Each non-draft commit must run CI.** Concurrency cancel-in-progress
   keeps cost bounded by killing superseded in-flight runs; we do not
   skip commits.
2. **Draft PRs skip CI.** They run only on `ready_for_review`.
3. **Keep Beefy runners group** for test jobs (user decision — not
   switching to ubuntu-latest for unit tier).
4. **Master push behaviour unchanged.** Full coverage stays.
5. **Per-subsystem split** mirroring existing `tests/<subsystem>/` layout.
6. **No fixture refactor** (shared session-fork) in this spec — out of scope.
7. **No git commits without explicit user approval** (global rule).

## Measurement protocol

### Baseline capture (before Step 1)

```bash
gh api repos/tradingstrategy-ai/web3-ethereum-defi/actions/workflows/test.yml/runs \
   --paginate --jq '.workflow_runs[] | select(.created_at >= "2026-05-13") |
     {event, conclusion, started: .run_started_at, ended: .updated_at, sha: .head_sha}'
```

Compute: median wall-clock per `pull_request` run × 16 (Beefy multiplier)
= baseline billable-min/PR. Record the number in this spec's appendix.

### Between-step comparison

After each step lands on master:

1. Wait 5 non-draft merged PRs.
2. Re-run the same `gh api` query, filter to `created_at >= step_landed_at`.
3. Compute new median; record delta vs baseline and vs previous step.
4. If delta < 15% of expected, stop and diagnose before the next step.

## Final-state architecture

```
.github/workflows/
├── lint.yml                  NEW   ubuntu-latest, ~30s, every non-draft PR push
├── test.yml                  CHANGED  Beefy, unit tier + catch-all integration
├── test-integration-lagoon.yml      NEW (Step 3.1)
├── test-integration-hyperliquid.yml NEW (Step 3.2)
├── test-integration-erc_4626.yml    NEW (Step 3.3)
├── test-integration-aave_v3.yml     NEW (Step 3.4)
├── test-integration-enzyme.yml      NEW (Step 3.5)
├── test-integration-guard.yml       NEW (Step 3.6)
├── test-integration-misc.yml        NEW (Step 3.7: cctp, gains, grvt, usdc)
├── test-gmx.yml              KEEP  already path-filtered (precedent)
├── docs.yml                  KEEP  push-only
├── dependency-audit.yml      KEEP  cron-only
├── dependency-review.yml     KEEP  PR + paths filter on poetry.lock
└── claude.yml                KEEP  @claude trigger only

tests/
├── unit-manifest.txt         NEW (Step 2) — list of files run by unit job
├── unit/                     NEW (Step 3 cleanup) — fast, no fork, no live RPC
├── integration/              NEW (Step 3) — fork + live RPC, per-subsystem
│   ├── gmx/                  moved from tests/gmx/      (Step 3.x — last, optional)
│   ├── lagoon/               moved from tests/lagoon/   (Step 3.1)
│   ├── hyperliquid/          moved from tests/hyperliquid/ (Step 3.2)
│   ├── erc_4626/             moved from tests/erc_4626/ (Step 3.3)
│   ├── aave_v3/              moved from tests/aave_v3/  (Step 3.4)
│   ├── enzyme/               moved from tests/enzyme/   (Step 3.5)
│   ├── guard/                moved from tests/guard/    (Step 3.6)
│   ├── cctp/, gains/, grvt/, usdc/  moved as one batch  (Step 3.7)
│   └── (each keeps its own conftest.py)
└── (legacy tests/ root empty by end of Step 3; deleted)
```

### Job triggering rules

- `lint.yml` — every non-draft PR push + master push; ubuntu-latest;
  `ruff format --check`.
- `test.yml` (unit + catch-all integration) — every non-draft PR push +
  master push, with workflow-level `paths-ignore` for docs/scripts/
  metadata so non-code PRs do not trigger it.
- `test-integration-<subsystem>.yml` — every non-draft PR push that
  touches the subsystem's paths + master push (no path filter on master).
- All workflows keep `concurrency: cancel-in-progress: true`.

## Step 1 — cheap wins (one PR, no test reorg)

### Changes

#### 1a. Skip drafts and trigger on `ready_for_review`

In `test.yml`:

```yaml
on:
  push:
    branches: [master]
  pull_request:
    branches: [master]
    types: [opened, synchronize, reopened, ready_for_review]
    paths-ignore:
      - 'docs/**'
      - 'scripts/**'
      - '**.md'
      - 'eth_defi/data/vaults/**'
      - '.github/workflows/!(test.yml)'

jobs:
  test-python:
    if: github.event.pull_request.draft == false || github.event_name == 'push'
    ...
```

#### 1b. Extract lint to its own workflow

New `.github/workflows/lint.yml`:

```yaml
name: Lint
on:
  push: { branches: [master] }
  pull_request:
    branches: [master]
    types: [opened, synchronize, reopened, ready_for_review]
jobs:
  ruff:
    if: github.event.pull_request.draft == false || github.event_name == 'push'
    runs-on: ubuntu-latest
    concurrency:
      group: ${{ github.workflow }}-${{ github.ref }}
      cancel-in-progress: true
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.14" }
      - run: pipx install ruff
      - run: ruff format --check --diff
```

Remove the trailing `Ruff lint check` step from `test.yml`.

#### 1c. Foundry binary cache

Add before `Install Foundry`:

```yaml
- name: Cache Foundry
  uses: actions/cache@v4
  with:
    path: ~/.foundry
    key: foundry-v1.2.3-${{ runner.os }}
```

#### 1d. Soldeer dependency cache

Add before the Lagoon dependency step:

```yaml
- name: Cache Lagoon soldeer deps
  uses: actions/cache@v4
  with:
    path: contracts/lagoon-v0/dependencies
    key: soldeer-${{ hashFiles('contracts/lagoon-v0/soldeer.lock', 'contracts/lagoon-v0/foundry.toml') }}
```

### Not changed in Step 1

- Test selection (still full suite minus GMX).
- Runner (still Beefy).
- master push behaviour.

### Expected impact

- Docs/metadata/scripts PRs: 160 → ~5 billable min (lint only).
- Code PRs: 160 → ~140 billable min (cache savings + lint extracted).
- Draft PRs: 0 until marked ready.

### Step 1 measurement gate

Land, wait 5 non-draft merged PRs. Expected ≥30% drop in median PR
billable minutes. If <15%, diagnose before Step 2.

## Step 2 — unit vs integration split (one PR, no test file moves)

### Pre-step: build unit manifest

`scripts/ci/list-unit-tests.sh`:

```bash
#!/usr/bin/env bash
# Unit = files that don't import fork fixtures or live RPC envs.
set -euo pipefail
cd "$(dirname "$0")/../.."
poetry run pytest tests/ --collect-only -q --ignore=tests/gmx 2>/dev/null \
  | grep -E '^tests/' \
  | awk -F'::' '{print $1}' | sort -u \
  | xargs -I{} sh -c '
      if ! grep -lE "mainnet_fork|web3_fork|anvil|JSON_RPC_|HYPERSYNC_API_KEY|GCP_ADC" "{}" >/dev/null; then
        echo "{}"
      fi'
```

Run once locally, commit output to `tests/unit-manifest.txt`. CI consumes
this file directly — no fragile inline grep.

### Workflow change in `test.yml`

Replace the single `test-python` job with two jobs:

```yaml
jobs:
  test-unit:
    if: github.event.pull_request.draft == false || github.event_name == 'push'
    runs-on: { group: Beefy runners }
    concurrency:
      group: ${{ github.workflow }}-unit-${{ github.ref }}
      cancel-in-progress: true
    steps:
      # ... checkout, poetry, foundry cache, install (unchanged) ...
      - name: Run unit tests
        run: |
          poetry run pytest $(cat tests/unit-manifest.txt | tr '\n' ' ') \
            --timeout-method=thread --tb=native -n auto -v
        env: { ... same env as before ... }

  test-integration:
    if: github.event.pull_request.draft == false || github.event_name == 'push'
    runs-on: { group: Beefy runners }
    concurrency:
      group: ${{ github.workflow }}-int-${{ github.ref }}
      cancel-in-progress: true
    steps:
      # ... same setup ...
      - name: Run integration tests
        run: |
          # Complement of unit manifest.
          # Implementation: build a --ignore list from directories whose tests
          # are NOT in unit-manifest, OR use a small conftest.py hook to
          # deselect manifested paths. Decided in writing-plans skill.
          poetry run pytest tests/ --ignore=tests/gmx \
            $(awk '{print "--ignore="$0}' tests/unit-manifest.txt | tr '\n' ' ') \
            --timeout-method=thread --tb=native -n auto -v
```

### Not changed in Step 2

- Test files remain in original locations.
- Both jobs still on Beefy.
- master push runs both jobs.

### Expected impact

- Unit-only PRs (touching only files in unit-manifest): ~20-40 billable min.
- Integration-touching PRs: ~140-160 min (small split overhead).
- Net win: only unit-only PRs, but Step 2 establishes the topology for Step 3.

### Step 2 measurement gate

Land, wait 5 merged PRs. Classify each as unit-only / integration /
mixed. Confirm unit-only PRs hit ~20-40 min target. Fix manifest if
classifier mis-classifies (e.g. unit test fails because it actually
needs RPC).

## Step 3 — per-subsystem integration split (multiple small PRs)

### Subsystem inventory

From `tests/` directory listing:

```
aave_v3, cctp, enzyme, erc_4626, gains, gmx (already split),
grvt, guard, hyperliquid, lagoon, usdc
```

Plus flat `tests/*.py` files — classified per-file in the final
cleanup PR.

### Per-subsystem migration recipe (one PR per subsystem)

Each PR does only the following:

1. `git mv tests/<subsystem>/ tests/integration/<subsystem>/`.
2. Update `tests/integration/<subsystem>/conftest.py` imports if any
   reference `tests/...` paths (rare — most use `eth_defi.testing.*`).
3. Add new workflow `.github/workflows/test-integration-<subsystem>.yml`
   modeled on `test-gmx.yml`:

   ```yaml
   name: Integration — <subsystem>
   on:
     push:
       branches: [master]
       paths:
         - 'eth_defi/<subsystem>/**'
         - 'tests/integration/<subsystem>/**'
         - '.github/workflows/test-integration-<subsystem>.yml'
         - 'pyproject.toml'
         - 'poetry.lock'
     pull_request:
       branches: [master]
       types: [opened, synchronize, reopened, ready_for_review]
       paths:
         - 'eth_defi/<subsystem>/**'
         - 'tests/integration/<subsystem>/**'
         - '.github/workflows/test-integration-<subsystem>.yml'
         - 'pyproject.toml'
         - 'poetry.lock'
   jobs:
     test:
       if: github.event.pull_request.draft == false || github.event_name == 'push'
       runs-on: { group: Beefy runners }
       concurrency:
         group: ${{ github.workflow }}-${{ github.ref }}
         cancel-in-progress: true
       steps:
         # ... same setup as test.yml ...
         - run: poetry run pytest tests/integration/<subsystem>/ -n auto -v
           env: { ... same env as before ... }
   ```
4. Update `test.yml` integration job to exclude the migrated subsystem:

   ```yaml
   - run: poetry run pytest tests/ --ignore=tests/gmx \
       --ignore=tests/integration/<subsystem> \
       ... (all previously migrated subsystems also ignored) ...
       --timeout-method=thread --tb=native -n auto -v
   ```
5. Run locally before push:

   ```bash
   source .local-test.env && poetry run pytest tests/integration/<subsystem>/
   ```

### Migration order (biggest cost wins first)

| Order | Subsystem | Rationale |
|-------|-----------|-----------|
| 3.1 | lagoon | Heavy contract deploys, soldeer deps, slow |
| 3.2 | hyperliquid | Live API + RPC, frequent flake |
| 3.3 | erc_4626 | Largest subsystem, many vault protocol tests |
| 3.4 | aave_v3 | Fork-heavy |
| 3.5 | enzyme | Fork-heavy, infrequently touched |
| 3.6 | guard | Fork + contract deploy |
| 3.7 | cctp + gains + grvt + usdc | Smaller, batch together |

### Final cleanup PR (Step 3.8)

- Classify flat `tests/*.py` files: import-grep `mainnet_fork` /
  `web3_arbitrum_fork` / `JSON_RPC_*` / `HYPERSYNC_API_KEY` → move to
  best-fit `tests/integration/<subsystem>/`. Else move to `tests/unit/`.
- Delete legacy `tests/` root entries.
- `test.yml` becomes purely the unit-tier workflow; the catch-all
  integration job is removed because no untiered tests remain.

### Not changed in Step 3

- Runner choice (still Beefy).
- master push still runs every workflow.
- pytest invocation flags.
- `test-gmx.yml` (stays as-is — already correct).

### Expected impact (cumulative after Step 3 done)

| PR type | Billable min |
|---------|--------------|
| Touches one subsystem | 30-80 |
| Touches two subsystems | 80-130 |
| Unit-classified files only | 20-40 |
| Docs/scripts/metadata only | ~5 (lint only) |
| Draft PR | 0 |
| master push (full suite) | unchanged (~160+) — bounded, one per merge |

### Step 3 measurement gate (per subsystem)

After each subsystem migration PR lands, wait 3 PRs matching its filter.

- Confirm PRs NOT touching that subsystem no longer run its integration job.
- Confirm PRs touching that subsystem still pass.
- If flake rate >2× post-migration, investigate (path filter too narrow,
  missed transitive dep).

## Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Unit-manifest misclassifies an RPC-dependent test as unit | Medium | Step 2 measurement gate catches via failure. Manifest is plain text — fix is 1-line removal. |
| Path filter on subsystem misses transitive coupling (e.g. lagoon test depends on `eth_defi/uniswap_v3/`) | Medium | Per-subsystem PR includes broader paths in filter when ambiguous: `grep -r "from eth_defi" tests/integration/<sub>/` to discover transitive imports and add them to `paths:`. master push still runs everything — final safety net. |
| Concurrency cancel kills a run mid-fork-deploy, leaving orphan Anvil | Low | Already current behaviour, no change. Anvil per-test fixture teardown handles it. |
| Draft-skip means a draft PR's first non-draft mark triggers CI on a stale base | Low | `ready_for_review` event is in the trigger types list. |
| Foundry cache key collision when bumping Foundry version | Low | Cache key includes version `foundry-v1.2.3-...`. Bump = new key = clean install. |
| `git mv` breaks an obscure relative import in conftest | Low-Medium | Per-subsystem PR runs local pytest before push. master push catches it cluster-wide. Easy revert. |
| Two integration workflows race on shared Foundry/soldeer cache | Low | `actions/cache@v4` is concurrent-safe. |
| GitHub Actions usage report lags ~24h | Operational | Measurement gate uses `gh api .../runs` wall-clock × 16, not the billing report. Same-day visibility. |

## Rollback (per step)

- **Step 1:** revert workflow PR. Single file edit.
- **Step 2:** revert workflow PR + delete `tests/unit-manifest.txt`. No test files moved.
- **Step 3:** per-subsystem `git mv` is symmetric — revert PR restores layout. Each subsystem PR is independent, so partial rollback is safe.

## Out of scope (deliberate)

- Switching from Beefy to ubuntu-latest for unit tier.
- Pytest fixture refactor (shared session-fork). Separate spec later if Step 3 doesn't hit the 60% target.
- `pytest-testmon` / changed-files-only selection. Brittle in fork test world.
- Moving heavy tier to self-hosted physical runner.
- Touching `test-gmx.yml`, `docs.yml`, `dependency-audit.yml`, `dependency-review.yml`, `claude.yml`.

## Appendix A — baseline measurement record

To be filled after the baseline query is run:

```
Baseline window: 2026-05-13 → 2026-05-27
Sample size: <N> pull_request runs of test.yml
Median wall-clock: <M> minutes
Implied billable min/PR: <M × 16>
```

## Appendix B — subsystem path-filter checklist

For each subsystem PR in Step 3, fill before opening:

```
Subsystem: <name>
Source paths included:
  - eth_defi/<sub>/**
  - tests/integration/<sub>/**
  - .github/workflows/test-integration-<sub>.yml
  - pyproject.toml
  - poetry.lock
Transitive deps discovered (run grep):
  - <paste output>
Additional source paths added to filter:
  - <list>
Local pytest run: PASS / FAIL
Migration PR #: <number>
```

## Next action

Per the brainstorming workflow, the next skill to invoke is
`superpowers:writing-plans` to break Step 1 into an executable
implementation plan with checklist tasks. The user reviews the plan
before any workflow file edits, and approves git commits explicitly
per the global rule.
