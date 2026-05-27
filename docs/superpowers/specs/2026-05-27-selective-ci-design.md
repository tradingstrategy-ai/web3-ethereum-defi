# Selective CI — design

**Date:** 2026-05-27
**Branch:** `feat/ci-cost-reduction-step1` (continuation)
**Predecessor:** [2026-05-27-ci-cost-reduction-design.md](./2026-05-27-ci-cost-reduction-design.md)

## Motivation

After the Step 1–3 CI cost reduction work landed, the test suite still runs
all non-GMX tests in a single ~7-minute job (~117 billable Beefy min per
push) regardless of which subsystem actually changed.  Touching one file in
`eth_defi/lagoon/` re-runs uniswap, aave, enzyme, every other subsystem.

The Step 3 attempt to split this into 32 per-subsystem workflow files was
rejected as unmaintainable.  This design pursues the same goal — run only
affected tests — without growing the workflow file count.

## Goals

- Run only the tests for subsystems whose code (or tests) changed.
- Keep all current pytest flags untouched:
  `--timeout-method=thread --tb=native -n auto -v -s --capture=no`.
- Stay within the existing workflow files: `test.yml`, `test-gmx.yml`,
  `lint.yml`.  No proliferation.
- Self-maintaining: adding a new `eth_defi/foo/` and `tests/foo/` must
  not require updating CI config.
- Safe fallback: any change that cannot be classified must trigger a full run.
- Keep the Step 1 wins: draft-skip, cancel-in-progress, paths-ignore,
  Foundry/soldeer caches.

## Non-goals

- Cross-subsystem dependency tracking on PRs.  Master push always runs
  full, which catches cross-dep regressions at merge time.
- Sharding / matrix splitting for wall-clock speed.
- Replacing `actions/cache` with artifact upload/download dance.
- Touching `test-gmx.yml` (GMX has its own constraints and fork conflicts).

## Architecture

Two jobs in one workflow file (`test.yml`):

```
detect (ubuntu-latest, ~30s)
   │
   │ outputs: mode (subset|full), pytest_targets (e.g. "tests/lagoon/ tests/safe/")
   ▼
test (Beefy)
   • restores venv / Foundry / soldeer caches
   • runs: pytest $pytest_targets --ignore=tests/gmx <existing flags>
```

### Component 1 — `detect` job

Runs on `ubuntu-latest` (1× billing).  Computes which test directories to
run based on the diff against the PR base (or always emits `full` for
master pushes).

**Classification algorithm:**

1. Dynamically derive the set of known subsystems at runtime:

   ```python
   eth_defi_subs = {p.name for p in Path("eth_defi").iterdir()
                    if p.is_dir() and (p / "__init__.py").exists()}
   tests_subs = {p.name for p in Path("tests").iterdir()
                 if p.is_dir() and not p.name.startswith("__")}
   SUBSYSTEMS = eth_defi_subs & tests_subs
   ```

   Only subsystems present in **both** `eth_defi/` and `tests/` are
   eligible for selective runs.  Anything else triggers a full run.

2. For each changed file in `git diff origin/$BASE...HEAD`:

   - `eth_defi/<sub>/...` or `tests/<sub>/...` where `<sub>` ∈ SUBSYSTEMS
     → add `<sub>` to the affected set.
   - Any other file under `eth_defi/`, `tests/`, `contracts/`,
     or `pyproject.toml` / `poetry.lock` / `.github/workflows/test.yml`
     → set `mode=full` and stop.
   - Anything else (docs, scripts, `.md`, `.gitignore`, etc.)
     → ignore.  These are already filtered by `paths-ignore`,
     but the script is defensive.

3. If the commit message contains `[ci full]` → force `mode=full`.

4. If `mode == full` → `pytest_targets = "tests/"`.
   Else → `pytest_targets = "tests/<sub1>/ tests/<sub2>/ ..."`.

5. If `pytest_targets` is empty (no subsystems matched and no full trigger
   — should not happen in practice) → fall back to `tests/`.

**Master-branch behaviour:** when `github.ref == 'refs/heads/master'`,
skip the diff entirely and emit `mode=full`, `pytest_targets=tests/`.
Selective runs are PR-only.

**Outputs:** `mode`, `pytest_targets`, `affected_subsystems` (for logging).

### Component 2 — `test` job

Single Beefy job, depends on `detect`.  Identical setup to today's
`test.yml` with one addition: a venv cache.

**New cache step (before `poetry install`):**

```yaml
- uses: actions/cache@v4
  id: venv-cache
  with:
    path: ${{ env.POETRY_VENV_PATH }}
    key: venv-${{ runner.os }}-py3.14-${{ hashFiles('poetry.lock', 'pyproject.toml') }}
```

The venv path is resolved by `poetry env info --path` after Python+poetry
are installed.  On hit, `poetry install` is a no-op (~5s).  On miss,
falls back to today's full install.

**Run step:**

```yaml
- name: Run tests (parallel)
  run: |
    poetry run pytest ${{ needs.detect.outputs.pytest_targets }} \
      --ignore=tests/gmx \
      --timeout-method=thread --tb=native -n auto -v -s --capture=no
```

`--ignore=tests/gmx` always present — GMX runs in `test-gmx.yml`.

### Failure modes

| Scenario | Behaviour |
|---|---|
| `detect` script errors | Workflow fails immediately, no test run.  Investigate, do not silently skip. |
| `git diff` returns no files | Defensive fall-through to `mode=full`. |
| Subsystem in `eth_defi/` without corresponding `tests/` (or vice versa) | Not in SUBSYSTEMS → triggers full run. |
| New file `eth_defi/foo/bar.py` added without tests | Triggers full run (defensive). |
| `[ci full]` in commit message | Forces full run regardless of diff. |
| Push to master | Always full, detect skipped. |
| Cache miss on venv | Pays normal `poetry install` cost (~60s), saves cache. |
| Cache hit on venv | `poetry install` runs in ~5s. |

## Cost projection

Beefy multiplier = 16×.  Wall numbers from recent CI history.

| PR change pattern | Today | With selective + venv cache |
|---|---|---|
| `eth_defi/lagoon/` only | ~117 min | **~32 min** (3 min wall × 16, ~15 min cache saving) |
| `tests/uniswap_v3/` only | ~117 min | **~32 min** |
| Shared file (`eth_defi/chain.py`, abi) | ~117 min | **~95 min** (full + cache hit) |
| `pyproject.toml` | ~117 min | ~117 min (cache miss + full) |
| `docs/` only | 0 | 0 |
| Draft push | 0 | 0 |
| Superseded push | 0 | 0 |
| `[ci full]` override | n/a | ~95 min |
| Master push | ~117 min | ~117 min |

Typical PR savings: **~70–80% per push** for subsystem-only changes.
Even full runs save ~15 billable min via the venv cache.

## File changes

- **Modify:** `.github/workflows/test.yml`
- **Untouched:** `.github/workflows/test-gmx.yml`,
  `.github/workflows/lint.yml`, all other workflows.
- **New:** none (the detect logic lives inline in `test.yml` as a Python
  step; small enough to inline, large enough that a separate `.py`
  script under `scripts/ci/` is justifiable — final placement decided
  during implementation).

## Testing strategy

The detect logic is the only new code.  Test it by:

1. **Local dry-run:** invoke the detect Python with sample diffs
   (synthetic file lists) and assert outputs.
2. **First CI run:** push a PR that touches a single subsystem; verify
   only that subsystem's tests run.
3. **Forced full:** push a commit with `[ci full]`; verify full run.
4. **Shared-file PR:** touch `eth_defi/chain.py`; verify full run.
5. **Brand-new subsystem:** as a sanity check, add a no-op
   `eth_defi/test_dummy_module/` and `tests/test_dummy_module/`,
   verify it gets auto-discovered.  Remove before merge.

## Rollout

1. Implement in a new commit on the existing PR branch.
2. Push and observe one round of CI on the existing PR.
3. If detect mis-classifies anything, fix and push again before merge.
4. Document the `[ci full]` keyword in `CLAUDE.md` and CHANGELOG.
