# Sharded CI Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the diff-based `detect` job with two parallel jobs — `test-heavy` on Beefy (16×) and `test-light` as a 2-shard `ubuntu-latest` matrix using `pytest-split` — to cut billable minutes by ~80% while keeping full test coverage.

**Architecture:** Workflow `test.yml` has two parallel jobs. `test-heavy` runs a hand-curated list of fork-RPC-heavy subsystems on a single Beefy runner. `test-light` runs everything else (excluding the heavy dirs and `tests/gmx`) on `ubuntu-latest` split across 2 shards by `pytest-split`. All caches preserved.

**Tech Stack:** GitHub Actions, `pytest`, `pytest-split`, `pytest-xdist`, Poetry, `actions/cache@v4`.

**Spec:** [docs/superpowers/specs/2026-05-27-sharded-ci-design.md](../specs/2026-05-27-sharded-ci-design.md)

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `.github/workflows/test.yml` | **Modify** | Remove `detect` job; replace `test-python` with `test-heavy` + `test-light` (2-shard matrix). All setup steps (caches, deps) duplicated across the two jobs since they run on different runners. |
| `pyproject.toml` | **Modify** | Add `pytest-split` to the `test` extra so CI and local installs both get it. |
| `.test_durations` | **Create** | JSON file at repo root with measured test durations per node-id. Seeded locally before the first CI run. |
| `CLAUDE.md` | **Modify** | Remove the `[ci full]` subsection added in the previous (now superseded) selective-CI design. |
| `CHANGELOG.md` | **Modify** | Replace the previous "selective test execution" entry with a "sharded test execution" entry. |
| `scripts/ci/` | **Delete** | Entire directory — `__init__.py` and `classify_changes.py`. |
| `tests/ci/` | **Delete** | Entire directory — `__init__.py` and `test_classify_changes.py`. |

The workflow file grows ~30 lines (two jobs sharing most steps) but the
net code change is negative once the deleted classifier is counted.

---

## Pre-flight

### Task 0: Confirm working directory and branch

- [ ] **Step 1: Verify branch and that the latest commit is the spec**

```bash
git status
git log --oneline -3
```

Expected: branch `feat/ci-cost-reduction-step1`, head commit is the spec
doc (`docs(ci): spec for sharded CI design`), working tree clean.

- [ ] **Step 2: Confirm the spec is present**

```bash
ls -la docs/superpowers/specs/2026-05-27-sharded-ci-design.md
```

Expected: file present, ~6 KB.

---

## Chunk 1: Tear down selective-CI artifacts

### Task 1: Delete the classifier script and its tests

**Files:**
- Delete: `scripts/ci/__init__.py`
- Delete: `scripts/ci/classify_changes.py`
- Delete: `tests/ci/__init__.py`
- Delete: `tests/ci/test_classify_changes.py`

- [ ] **Step 1: Remove the directories**

```bash
git rm -r scripts/ci tests/ci
```

- [ ] **Step 2: Verify they're gone**

```bash
ls scripts/ tests/ | grep -E "^ci$" || echo "removed OK"
```

Expected: prints `removed OK`.

- [ ] **Step 3: Commit**

```bash
git commit -m "chore(ci): remove diff-based classifier (superseded by sharded design)"
```

---

### Task 2: Strip the `detect` job from `test.yml`

**Files:**
- Modify: `.github/workflows/test.yml`

We keep the file in place but reduce it to the original single-job form
before adding the new jobs in Chunk 3. This intermediate state means CI
still works after this commit.

- [ ] **Step 1: Open `test.yml` and remove the entire `detect:` job block (lines 16-61) and the `needs: detect` line on `test-python`**

The `detect` job spans from `# ── Detect which subsystems the diff touches ─` down to the closing of the `Classify changes` step. Delete that whole block.

Then on the `test-python` job, delete the line `    needs: detect`.

- [ ] **Step 2: Restore the pytest target to `tests/`**

In `test-python`'s `Run tests (parallel)` step, change:

```yaml
          poetry run pytest ${{ needs.detect.outputs.pytest_targets }} \
            --ignore=tests/gmx \
            --timeout-method=thread --tb=native -n auto -v -s --capture=no
```

to:

```yaml
          poetry run pytest tests/ \
            --ignore=tests/gmx \
            --timeout-method=thread --tb=native -n auto -v -s --capture=no
```

- [ ] **Step 3: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))" && echo OK
```

Expected: `OK`.

- [ ] **Step 4: Confirm the file no longer references the classifier**

```bash
grep -n "detect\|classify\|pytest_targets" .github/workflows/test.yml || echo "clean"
```

Expected: `clean` (no matches).

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "chore(ci): remove detect job from test.yml"
```

---

### Task 3: Remove `[ci full]` section from `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Locate the section**

```bash
grep -n "Forcing a full CI test run\|\\[ci full\\]" CLAUDE.md
```

Note the line range — the section starts at `### Forcing a full CI test run` and ends just before the next `###` heading.

- [ ] **Step 2: Delete the section**

Open `CLAUDE.md` and delete from the line `### Forcing a full CI test run` through (and including) the closing fenced code block of that section. Leave the preceding "If you need extra output …" paragraph and the following `### Environment variable configuration and RPC URL format` heading intact.

- [ ] **Step 3: Verify**

```bash
grep -n "ci full" CLAUDE.md || echo "removed"
```

Expected: `removed`.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: remove [ci full] override (no longer applicable)"
```

---

## Chunk 2: pytest-split + durations seeding

### Task 4: Add `pytest-split` to the `test` extra

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Locate the `test` extra**

```bash
grep -nA 20 '^\[tool\.poetry\.extras\]\|^test = \[' pyproject.toml | head -40
```

Find the entry that defines `test = [...]` under `[tool.poetry.extras]` (or the equivalent for the build system used — Poetry 2.x uses `[project.optional-dependencies]`).

- [ ] **Step 2: Add the dependency declaration**

Add a new dependency entry in the dependencies table (where `pytest`, `pytest-xdist`, etc. live):

```toml
# Shards the test suite into N balanced groups for parallel CI execution.
# https://github.com/jerry-git/pytest-split
pytest-split = {version = "^0.10", optional = true}
```

Then add `"pytest-split"` to the list of names under the `test` extra so `poetry install -E test` picks it up.

- [ ] **Step 3: Refresh the lock file**

```bash
poetry lock --no-update
```

If that fails with "unknown option", use `poetry lock` plain.

- [ ] **Step 4: Install the new extra**

```bash
poetry install -E test
```

Expected: installs `pytest-split` and a couple of small dependencies.

- [ ] **Step 5: Verify the plugin loads**

```bash
poetry run pytest --help 2>&1 | grep -i "split\|durations" | head -5
```

Expected: shows `--splits`, `--group`, `--store-durations` options.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml poetry.lock
git commit -m "deps: add pytest-split for sharded CI execution"
```

---

### Task 5: Seed `.test_durations` locally

**Files:**
- Create: `.test_durations`

The light shards need a baseline timings file. We generate it locally
against the same test set the light shard will run — i.e. `tests/` minus
GMX minus the heavy subsystems.

- [ ] **Step 1: Run the full light-shard set with `--store-durations`**

```bash
source .local-test.env && poetry run pytest tests/ \
  --ignore=tests/gmx \
  --ignore=tests/lagoon \
  --ignore=tests/enzyme \
  --ignore=tests/erc_4626 \
  --ignore=tests/hyperliquid \
  --ignore=tests/derive \
  --ignore=tests/morpho \
  --ignore=tests/uniswap_v3 \
  --ignore=tests/aave_v3 \
  --ignore=tests/guard \
  --ignore=tests/vault \
  --ignore=tests/safe-integration \
  --ignore=tests/ipor \
  --store-durations \
  --timeout-method=thread --tb=native -n auto -v -s --capture=no
```

This takes ~3-5 min locally. Tests don't need to all pass — `pytest-split` records durations regardless. If something hangs, abort and re-run with `--ignore` for the hanging directory.

- [ ] **Step 2: Confirm the file was created**

```bash
ls -la .test_durations
head -20 .test_durations
```

Expected: JSON-like file with `"tests/foo/test_bar.py::test_baz": 0.42` entries.

- [ ] **Step 3: Sanity-check the split plan**

```bash
poetry run pytest tests/ \
  --ignore=tests/gmx \
  --ignore=tests/lagoon --ignore=tests/enzyme --ignore=tests/erc_4626 \
  --ignore=tests/hyperliquid --ignore=tests/derive --ignore=tests/morpho \
  --ignore=tests/uniswap_v3 --ignore=tests/aave_v3 --ignore=tests/guard \
  --ignore=tests/vault --ignore=tests/safe-integration --ignore=tests/ipor \
  --splits 2 --group 1 --collect-only -q 2>&1 | tail -20
```

Expected: prints a list of node-ids for shard 1, ending with `N tests collected`.

- [ ] **Step 4: Commit**

```bash
git add .test_durations
git commit -m "ci: seed .test_durations for pytest-split"
```

---

## Chunk 3: Workflow rewrite — two jobs

### Task 6: Add the heavy-subsystem list as a workflow-level env var

**Files:**
- Modify: `.github/workflows/test.yml`

We're going to reference the same heavy-subsystem list in two places
(the `test-heavy` pytest command and the `test-light` `--ignore` list).
Define it once as workflow-level env to keep them in sync.

- [ ] **Step 1: Insert `env:` block at workflow top-level**

Just under the `on:` block and above `jobs:`, add:

```yaml
env:
  # Subsystems that need Anvil mainnet-fork against archive RPC, or are
  # otherwise too heavy for ubuntu-latest. Kept in sync between
  # test-heavy (which runs them) and test-light (which ignores them).
  HEAVY_DIRS: >-
    tests/lagoon
    tests/enzyme
    tests/erc_4626
    tests/hyperliquid
    tests/derive
    tests/morpho
    tests/uniswap_v3
    tests/aave_v3
    tests/guard
    tests/vault
    tests/safe-integration
    tests/ipor
```

The `>-` folded scalar joins the lines into a single space-separated
string ready for shell expansion.

- [ ] **Step 2: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))" && echo OK
```

Expected: `OK`.

---

### Task 7: Rename `test-python` → `test-heavy` and update its pytest command

**Files:**
- Modify: `.github/workflows/test.yml`

- [ ] **Step 1: Rename the job key**

In `test.yml`, change:

```yaml
  test-python:
```

to:

```yaml
  test-heavy:
```

- [ ] **Step 2: Update the matrix name**

The `name:` directive under the job currently reads `Python ${{ matrix.python-version }}`. Change to:

```yaml
    name: heavy (Python ${{ matrix.python-version }})
```

- [ ] **Step 3: Add a 15-minute job timeout**

Directly under `runs-on:` add:

```yaml
    timeout-minutes: 15
```

- [ ] **Step 4: Update the pytest command to use HEAVY_DIRS**

In the `Run tests (parallel)` step, change the command to:

```yaml
      - name: Run tests (parallel)
        run: |
          # Heavy fork-RPC subsystems that need Beefy cores. List comes from
          # the HEAVY_DIRS workflow-level env to stay in sync with test-light's
          # --ignore list. NOTE: GMX tests run separately in test-gmx.yml.
          poetry run pytest $HEAVY_DIRS \
            --timeout-method=thread --tb=native -n auto -v -s --capture=no
```

(The `env:` block under that step is unchanged — keep all the RPC and GCP secrets.)

- [ ] **Step 5: Update the concurrency group label**

Change:

```yaml
    concurrency:
      group: ${{ github.workflow }}-${{ github.ref }}-${{ matrix.python-version }}
```

to:

```yaml
    concurrency:
      group: ${{ github.workflow }}-heavy-${{ github.ref }}-${{ matrix.python-version }}
```

so a future `test-light` job has its own concurrency group rather than cancelling the heavy job.

- [ ] **Step 6: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))" && echo OK
```

Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "ci: rename test-python to test-heavy, run only heavy subsystems"
```

---

### Task 8: Add the `test-light` job (2-shard ubuntu-latest matrix)

**Files:**
- Modify: `.github/workflows/test.yml`

The light job is structurally a sibling of `test-heavy`. It needs most
of the same setup (Python, Poetry, npm, pnpm, deps) but **does not** need:

- Foundry (no mainnet-fork tests in the light dirs)
- Soldeer dependencies (Lagoon-only)
- `install-aave-for-testing` (Aave-only)
- Web3.py version verification (debug-only, optional)

We drop those to keep the light job lean.

- [ ] **Step 1: Append the `test-light` job after `test-heavy`**

At the end of `.github/workflows/test.yml`, append the entire job:

```yaml
  test-light:
    # Free 1× billing on public repos. 2-way shard via pytest-split.
    runs-on: ubuntu-latest
    timeout-minutes: 15

    if: github.event.pull_request.draft == false || github.event_name == 'push'

    concurrency:
      group: ${{ github.workflow }}-light-${{ github.ref }}-${{ matrix.shard }}
      cancel-in-progress: true

    strategy:
      fail-fast: true
      matrix:
        shard: [1, 2]

    name: light shard ${{ matrix.shard }}/2

    steps:
      - uses: actions/checkout@v4
        with:
          submodules: false

      - name: Install pnpm
        uses: pnpm/action-setup@v4
        with:
          version: 10.21.0

      - name: Install poetry
        run: pipx install "poetry>=2.3"

      - name: Set up Python 3.14
        uses: actions/setup-python@v5
        with:
          python-version: "3.14"
          cache: "poetry"
          cache-dependency-path: |
            **/pyproject.toml

      - name: Resolve poetry venv path
        id: venv-path
        run: |
          venv_path=$(poetry env info --path 2>/dev/null || true)
          if [ -z "$venv_path" ]; then
            poetry env use 3.14
            venv_path=$(poetry env info --path)
          fi
          echo "path=$venv_path" >> "$GITHUB_OUTPUT"

      - name: Cache poetry venv
        uses: actions/cache@v4
        with:
          path: ${{ steps.venv-path.outputs.path }}
          key: venv-${{ runner.os }}-py3.14-${{ hashFiles('poetry.lock', 'pyproject.toml') }}

      - name: Install dependencies
        run: |
          poetry install -E test -E data -E hypersync -E ccxt -E duckdb

      - name: Run tests (parallel, shard ${{ matrix.shard }}/2)
        run: |
          # The HEAVY_DIRS env is space-separated; convert each entry to a
          # --ignore flag so the light shards never collect heavy tests.
          ignore_args=""
          for d in $HEAVY_DIRS; do
            ignore_args="$ignore_args --ignore=$d"
          done
          # shellcheck disable=SC2086
          poetry run pytest tests/ \
            --ignore=tests/gmx \
            $ignore_args \
            --splits 2 --group ${{ matrix.shard }} \
            --timeout-method=thread --tb=native -n auto -v -s --capture=no
        env:
          BNB_CHAIN_JSON_RPC: ${{ secrets.BNB_CHAIN_JSON_RPC }}
          JSON_RPC_POLYGON_ARCHIVE: ${{ secrets.JSON_RPC_POLYGON_ARCHIVE }}
          JSON_RPC_POLYGON: ${{ secrets.JSON_RPC_POLYGON }}
          JSON_RPC_ETHEREUM: ${{ secrets.JSON_RPC_ETHEREUM }}
          JSON_RPC_BASE: ${{ secrets.JSON_RPC_BASE }}
          JSON_RPC_BINANCE: ${{ secrets.JSON_RPC_BINANCE }}
          JSON_RPC_ARBITRUM: ${{ secrets.JSON_RPC_ARBITRUM }}
          JSON_RPC_PLASMA: ${{ secrets.JSON_RPC_PLASMA }}
          JSON_RPC_HYPERLIQUID: ${{ secrets.JSON_RPC_HYPERLIQUID }}
          ETHEREUM_JSON_RPC: ${{ secrets.JSON_RPC_ETHEREUM }}
          HYPERSYNC_API_KEY: ${{ secrets.HYPERSYNC_API_KEY }}
```

(Light shards don't need GCP/KMS secrets since those are Lagoon/Safe — heavy-only.)

- [ ] **Step 2: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))" && echo OK
```

Expected: `OK`.

- [ ] **Step 3: Sanity-check the rendered shell substitution**

The `$HEAVY_DIRS` expansion inside the run step relies on the workflow-level `env:` block. Verify it's still there:

```bash
grep -n "HEAVY_DIRS:" .github/workflows/test.yml | head -3
```

Expected: at least one match showing the env block at workflow level.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "ci: add test-light 2-shard ubuntu-latest matrix"
```

---

### Task 9: Local dry-run of both pytest invocations

Before pushing, run both shapes locally to confirm command syntax.

- [ ] **Step 1: Simulate `test-heavy`'s pytest line**

```bash
export HEAVY_DIRS="tests/lagoon tests/enzyme tests/erc_4626 tests/hyperliquid tests/derive tests/morpho tests/uniswap_v3 tests/aave_v3 tests/guard tests/vault tests/safe-integration tests/ipor"
source .local-test.env && poetry run pytest $HEAVY_DIRS --collect-only -q 2>&1 | tail -10
```

Expected: prints `N tests collected` with N > 0 and no `ERROR collecting` lines.

- [ ] **Step 2: Simulate `test-light` shard 1**

```bash
ignore_args=""
for d in $HEAVY_DIRS; do ignore_args="$ignore_args --ignore=$d"; done
source .local-test.env && poetry run pytest tests/ --ignore=tests/gmx $ignore_args --splits 2 --group 1 --collect-only -q 2>&1 | tail -10
```

Expected: prints a different (smaller) `N tests collected` count.

- [ ] **Step 3: Simulate `test-light` shard 2**

```bash
source .local-test.env && poetry run pytest tests/ --ignore=tests/gmx $ignore_args --splits 2 --group 2 --collect-only -q 2>&1 | tail -10
```

Expected: shard 2's collected count + shard 1's collected count ≈ total light-eligible tests.

- [ ] **Step 4: If either collection step errors, fix before continuing**

Common failure: a directory in `HEAVY_DIRS` does not actually exist (typo). Fix the workflow env value.

---

## Chunk 4: Docs and rollout

### Task 10: Update `CHANGELOG.md`

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Replace the previous selective-CI entry**

The previous entry says "CI: selective test execution …". Replace that line (or add a new one if you want both for history) with:

```markdown
- CI: sharded test execution — full test suite split into a Beefy `test-heavy` job and two `ubuntu-latest` `test-light` shards via `pytest-split` (~80% billable-min reduction) (2026-05-27).
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog entry for sharded CI"
```

---

### Task 11: Push, observe one full CI cycle

This is a human-in-the-loop step.

- [ ] **Step 1: Push the branch**

```bash
git push
```

- [ ] **Step 2: Watch CI**

```bash
gh run watch
```

- [ ] **Step 3: Note any failures by category**

For each failed test in `test-light` shards, classify:

- **Genuine regression**: fix the code; don't blame the runner.
- **Resource-shortage failure on ubuntu-latest** (OOM, CPU pegged, timeout): add that test's subsystem to `HEAVY_DIRS`.
- **Live-API flakiness** (Derive, Morpho, Hyperliquid): `@flaky.flaky(max_runs=3, min_passes=1)` if not already marked.

- [ ] **Step 4: Record any moves in a follow-up commit**

If you move any subsystem to heavy, edit the `HEAVY_DIRS` env block in `.github/workflows/test.yml`, then:

```bash
git add .github/workflows/test.yml
git commit -m "ci: move <subsystem> to heavy after ubuntu-latest failure"
git push
```

Then re-run `.test_durations` seeding (Task 5) locally with the updated heavy list and commit that file too.

- [ ] **Step 5: Repeat until CI is green twice in a row**

The goal is two consecutive green runs with the same heavy list. That's the signal the partition has stabilised.

---

## Final Task: Self-review

- [ ] **Step 1: Walk the spec against the implementation**

```bash
cat docs/superpowers/specs/2026-05-27-sharded-ci-design.md
```

For each goal and non-goal in the spec, confirm an artefact in the diff that addresses it. Note any divergence in the PR description.

- [ ] **Step 2: Confirm caches survived**

```bash
grep -n "actions/cache@v4" .github/workflows/test.yml
```

Expected: at least 4 cache uses (npm built into setup-node, venv-heavy, venv-light, Foundry, soldeer).

- [ ] **Step 3: Confirm GMX workflow untouched**

```bash
git diff master -- .github/workflows/test-gmx.yml
```

Expected: empty output (no changes).

- [ ] **Step 4: Update the PR description**

```bash
gh pr edit 1035 --body "$(cat <<'EOF'
## Why

Reduce CI cost (~80% billable-min saving) without the brittleness of
diff-based selective execution. The previous selective-CI attempt was
reverted because cross-cutting changes in shared modules regularly
broke tests in subsystems the classifier marked as "unaffected".

## Lessons learnt

- Diff-based selective CI assumes a dependency graph the codebase
  doesn't actually have. Shared helpers and ABI updates cross every
  subsystem boundary.
- Running the full suite on cheap runners costs less than running a
  filtered subset on expensive ones.
- `pytest-split` plus a committed `.test_durations` file is enough
  balancing for a 2-shard setup — no auto-refresh needed.

## Summary

- New `test-heavy` job on Beefy runs Anvil-fork subsystems.
- New `test-light` 2-shard matrix on `ubuntu-latest` runs everything
  else via `pytest-split`.
- `pytest-split` added to the `test` extra.
- `.test_durations` committed.
- Diff-based `detect` job + `scripts/ci/` + `tests/ci/` deleted.
- All existing caches preserved.

See `docs/superpowers/specs/2026-05-27-sharded-ci-design.md`.
EOF
)"
```

(Don't push or merge without explicit approval.)
