# Sharded CI — design

**Date:** 2026-05-27
**Branch:** `feat/ci-cost-reduction-step1` (continuation)
**Predecessors:**

- [2026-05-27-ci-cost-reduction-design.md](./2026-05-27-ci-cost-reduction-design.md) (Step 1 — draft skip, lint job, caches)
- [2026-05-27-selective-ci-design.md](./2026-05-27-selective-ci-design.md) (Step 2 — diff-based detect job, **superseded by this design**)

## Motivation

The diff-based `detect` job from the selective-CI design proved too brittle:
cross-cutting changes in shared modules (`eth_defi/chain.py`, ABI files,
helpers) regularly break tests in subsystems that weren't classified as
"affected", so PRs ship with hidden regressions only caught on master.

We need a CI workflow that:

1. Always runs the full test surface (no diff-based filtering).
2. Costs less than today's single-job baseline (~112 billable Beefy min).
3. Finishes faster than today's ~7 min wall-clock.
4. Stays maintainable — no per-subsystem workflow file proliferation.

The lever is **parallelism on cheaper runners**: split the suite across
three parallel shards, keep the heavy fork-RPC tests on a single Beefy
runner, and put the rest on free `ubuntu-latest` runners (1× billing vs.
Beefy's 16×).

## Goals

- Replace `test-python` with two jobs running in parallel: one `test-heavy`
  on Beefy and one `test-light` matrix with two ubuntu-latest shards.
- Keep all existing pytest flags:
  `--timeout-method=thread --tb=native -n auto -v -s --capture=no`.
- Keep all existing caches (poetry venv, Foundry, soldeer, npm).
- `fail-fast: true` within the `test-light` matrix.
- 15-minute job timeout on each shard.
- Cut total billable minutes by ≥70% on green runs.
- Delete the previous `detect` job and its supporting code (`scripts/ci/`,
  `tests/ci/`) — clean slate.

## Non-goals

- Diff-based selective execution (removed; was too brittle).
- Auto-refresh of `.test_durations` in CI (deferred — refresh manually
  when shards drift).
- Cross-subsystem dependency tracking.
- Touching `test-gmx.yml` (GMX has its own constraints and fork conflicts).
- Removing tests in this PR — covered in Phase 2 below.

## Architecture

Two parallel jobs in `test.yml`:

```
test-heavy  (Beefy, timeout-minutes: 15)
   └─ pytest <heavy subsystem dirs> <existing flags>

test-light  (ubuntu-latest, matrix shard=[1,2], fail-fast: true,
              timeout-minutes: 15)
   └─ pytest tests/ --ignore=tests/gmx --ignore=<each heavy dir>
            --splits 2 --group ${{ matrix.shard }}
            <existing flags>
```

Total parallelism = 3 (1 Beefy + 2 ubuntu). Wall-clock is bounded by the
slowest shard.

### Heavy subsystems (initial list)

Hand-curated; tests that depend on Anvil mainnet-fork against archive RPC,
or that historically need the Beefy core count:

```
tests/lagoon  tests/enzyme  tests/erc_4626  tests/hyperliquid
tests/derive  tests/morpho  tests/uniswap_v3  tests/aave_v3
tests/guard   tests/vault   tests/safe-integration  tests/ipor
```

The light shards run everything else (`tests/rpc`, `tests/cctp`, `tests/feed`,
`tests/lighter`, `tests/research`, `tests/one_delta`, `tests/uniswap_v2`,
`tests/safe`, `tests/token_analysis`, `tests/hypersync`, `tests/grvt`,
`tests/gains`, `tests/lifi`, `tests/velora`, `tests/hibachi`, `tests/velvet`,
`tests/usdc`, `tests/orderly`, `tests/event_reader`, `tests/provider`,
root `tests/*.py`).

### Light-shard balancing — `pytest-split`

The `test-light` job uses [`pytest-split`](https://github.com/jerry-git/pytest-split)
with `--splits 2 --group $N`. Initial seeding:

```bash
poetry run pytest tests/ --ignore=tests/gmx \
  --ignore=tests/lagoon --ignore=tests/enzyme ... \
  --store-durations -n auto
git add .test_durations
```

`.test_durations` is committed to the repo. Refresh manually when shard
duration drift becomes visible (no auto-refresh in CI — that path was
already shown to be fragile).

### Caching

Each job has its own venv cache keyed on `runner.os` (so Beefy and
ubuntu caches don't collide):

```yaml
key: venv-${{ runner.os }}-py3.14-${{ hashFiles('poetry.lock', 'pyproject.toml') }}
```

Foundry, soldeer, and npm caches are unchanged.

## Cost projection

Beefy multiplier = 16×; ubuntu-latest = 1×.

| | Today | After |
|---|---|---|
| `test-heavy` (Beefy) | — | ~1 min wall × 16× = **16 billable min** |
| `test-light` shard 1 (ubuntu) | — | ~3 min wall × 1× = **3 billable min** |
| `test-light` shard 2 (ubuntu) | — | ~3 min wall × 1× = **3 billable min** |
| Old single Beefy job | ~7 min × 16× = **112 billable min** | — |
| **Total** | **~112 min** | **~22 min** |

Expected saving: **~80% per push** on green runs. `fail-fast` makes red
runs cheaper still.

Wall-clock improves from ~7 min → ~3-4 min (bound by slowest shard).

## File changes

- **Modify:** `.github/workflows/test.yml`
- **Modify:** `pyproject.toml` (add `pytest-split` to `test` extra)
- **Modify:** `CLAUDE.md` (remove `[ci full]` section)
- **Modify:** `CHANGELOG.md`
- **Create:** `.test_durations` (seeded locally before commit)
- **Delete:** `scripts/ci/__init__.py`, `scripts/ci/classify_changes.py`
- **Delete:** `tests/ci/__init__.py`, `tests/ci/test_classify_changes.py`

## Failure modes

| Scenario | Behaviour |
|---|---|
| A "light" test fails on ubuntu-latest (CPU / memory difference) | Move its subsystem to the heavy list, or `@flaky.flaky` mark it, in a follow-up PR. |
| `.test_durations` missing | `pytest-split` falls back to count-based splitting. Shards still run, just less balanced. |
| Heavy shard exceeds 15 min | Job times out, workflow fails. Investigate the stalled test (likely a hanging RPC). |
| Light shard exceeds 15 min | Same as above; consider moving slow tests to heavy. |
| pytest-split misses a new test | New tests default to a random shard; durations refresh on next manual seed. |

## Iteration plan

This design ships in a single PR but the heavy list is expected to
evolve over the next 2-3 PRs as live CI tells us which "light" tests
actually fail on ubuntu-latest. Each adjustment is one workflow edit.

## Phase 2 (separate work, not in this PR)

Audit unnecessary tests:

1. `grep -rn "pytest.mark.skip\|pytestmark.*skipif" tests/` → propose
   skip-marked tests for deletion.
2. Identify "stupid unit tests" with low signal.
3. For each candidate, the user runs it locally before approving deletion.
4. Each removal is its own commit for easy revert.

This work happens after the CI sharding lands.

## Rollout

1. Implement on the existing `feat/ci-cost-reduction-step1` branch.
2. Seed `.test_durations` locally, commit.
3. Push, observe one round of CI on PR #1035.
4. If shards mis-balance or ubuntu fails, adjust the heavy list and push again.
5. Merge when stable.
