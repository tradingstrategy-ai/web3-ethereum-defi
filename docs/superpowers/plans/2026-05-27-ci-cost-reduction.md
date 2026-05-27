# CI Cost Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one PR that reduces pull request CI cost through staged workflow controls and selective test targeting, with a cost comparison after every stage.

**Architecture:** Keep one branch and one PR, but use separate commits for cheap workflow controls, CI cost measurement tooling, and selective test targeting. The workflow stays conservative: ambiguous changes run the full suite, while subsystem-only changes run matching subsystem tests.

**Tech Stack:** GitHub Actions, Python 3.14, Poetry, pytest, Ruff, Foundry, GitHub CLI.

---

## File structure

- Modify `.github/workflows/test.yml`: add draft/path controls, measurement-friendly job outputs, and selective test targeting.
- Create `.github/workflows/lint.yml`: run Ruff format checks on `ubuntu-latest`.
- Create `scripts/ci/classify_changes.py`: classify changed files into pytest targets.
- Create `scripts/ci/measure_actions_cost.py`: summarise recent Actions runs for before/after comparison.
- Create `tests/ci/test_classify_changes.py`: unit tests for the classifier.
- Modify `CHANGELOG.md`: add one dated CI entry.

## Task 1: Baseline cost measurement

**Files:**
- Create: `scripts/ci/measure_actions_cost.py`

- [ ] **Step 1: Write measurement script**

Create `scripts/ci/measure_actions_cost.py` as a small Python script that reads GitHub Actions run JSON from stdin and prints count, median seconds, and median minutes. It must not call GitHub directly.

- [ ] **Step 2: Fetch recent PR runs**

Run:

```bash
gh api '/repos/tradingstrategy-ai/web3-ethereum-defi/actions/workflows/test.yml/runs?per_page=50&event=pull_request' > /tmp/web3-defi-test-runs-before.json
poetry run python scripts/ci/measure_actions_cost.py < /tmp/web3-defi-test-runs-before.json
```

Expected: a baseline table to copy into the PR description.

- [ ] **Step 3: Commit measurement tooling**

Commit only the measurement script:

```bash
git add scripts/ci/measure_actions_cost.py
git commit -m "ci: add actions cost measurement helper"
```

## Task 2: Cheap workflow controls

**Files:**
- Modify: `.github/workflows/test.yml`
- Create: `.github/workflows/lint.yml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update `test.yml` trigger**

Add `pull_request` types and `paths-ignore`:

```yaml
  pull_request:
    branches: [master]
    types: [opened, synchronize, reopened, ready_for_review]
    paths-ignore:
      - 'docs/**'
      - '**.md'
      - 'eth_defi/data/vaults/**'
      - '.github/workflows/!(test.yml)'
```

- [ ] **Step 2: Add draft PR skip**

Add this to the `test-python` job:

```yaml
    if: github.event.pull_request.draft == false || github.event_name == 'push'
```

- [ ] **Step 3: Add Foundry and soldeer caches**

Insert an `actions/cache@v4` step for `~/.foundry` before Foundry installation and a cache for `contracts/lagoon-v0/dependencies` before the soldeer smoke test.

- [ ] **Step 4: Enable fail-fast tests**

Set workflow strategy `fail-fast: true` and add `-x` to pytest commands so a broken commit stops after the first failing test.

- [ ] **Step 5: Move Ruff to `lint.yml`**

Create `.github/workflows/lint.yml` with `ubuntu-latest`, the same `pull_request` types, draft skip, Python 3.14 setup, pinned Ruff install, and `ruff format --check --diff` scoped to CI-owned files changed by this PR.

Remove the trailing Ruff step from `test.yml`.

- [ ] **Step 6: Verify YAML**

Run:

```bash
poetry run python -c "import yaml; [yaml.safe_load(open(f)) for f in ['.github/workflows/test.yml', '.github/workflows/lint.yml']]; print('YAML OK')"
```

- [ ] **Step 7: Compare estimated cost**

Run the measurement script again against the same baseline JSON and document expected savings:

- docs/markdown/data-only PRs should skip `test.yml`
- draft PR pushes should skip `test.yml`
- Ruff no longer adds time to the expensive test runner
- cache hits should reduce setup time
- failed commits stop after the first pytest failure

- [ ] **Step 8: Commit**

```bash
git add .github/workflows/test.yml .github/workflows/lint.yml CHANGELOG.md
git commit -m "ci: add cheap cost controls for pull requests"
```

## Task 3: Selective CI classifier

**Files:**
- Create: `scripts/ci/classify_changes.py`
- Create: `scripts/ci/__init__.py`
- Create: `tests/ci/__init__.py`
- Create: `tests/ci/test_classify_changes.py`

- [ ] **Step 1: Write classifier tests first**

Cover:

- subsystem source change maps to `tests/<subsystem>/`
- subsystem test change maps to `tests/<subsystem>/`
- multiple subsystem changes produce sorted targets
- root tests force full suite
- shared files force full suite
- unknown subsystems force full suite
- `[ci full]` forces full suite
- `master` push forces full suite

- [ ] **Step 2: Run failing test**

Run:

```bash
poetry run pytest tests/ci/test_classify_changes.py -v
```

Expected: import failure before implementation.

- [ ] **Step 3: Implement classifier**

Implement `classify()` as a pure function and `_main()` that writes `mode`, `pytest_targets`, and `affected_subsystems` to `$GITHUB_OUTPUT`.

- [ ] **Step 4: Run unit tests**

Run:

```bash
poetry run pytest tests/ci/test_classify_changes.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/ci/classify_changes.py scripts/ci/__init__.py tests/ci/__init__.py tests/ci/test_classify_changes.py
git commit -m "ci: classify changed files for selective test runs"
```

## Task 4: Wire selective CI into `test.yml`

**Files:**
- Modify: `.github/workflows/test.yml`

- [ ] **Step 1: Add detect job**

Add an `ubuntu-latest` `detect` job that checks out with `fetch-depth: 0`, computes changed files for PRs with `git diff --name-only`, and runs `poetry run python scripts/ci/classify_changes.py`.

- [ ] **Step 2: Use classifier output in test job**

Make `test-python` depend on `detect` and replace the pytest path with:

```bash
poetry run pytest ${{ needs.detect.outputs.pytest_targets }} -x --timeout-method=thread --tb=native -n auto -v -s --capture=no --ignore=tests/gmx
```

- [ ] **Step 3: Preserve full fallback**

Confirm full mode emits `tests/`, preserving current non-GMX coverage.

- [ ] **Step 4: Verify YAML and classifier**

Run:

```bash
poetry run python -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml')); print('YAML OK')"
poetry run pytest tests/ci/test_classify_changes.py -v
```

- [ ] **Step 5: Compare estimated cost**

Use example diffs:

```bash
CHANGED_FILES='eth_defi/lagoon/vault.py' COMMIT_MESSAGE='test' IS_MASTER_PUSH=false poetry run python scripts/ci/classify_changes.py
CHANGED_FILES='pyproject.toml' COMMIT_MESSAGE='test' IS_MASTER_PUSH=false poetry run python scripts/ci/classify_changes.py
```

Document that subsystem-only PRs run one target while shared changes still run full tests.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "ci: run targeted tests for subsystem-only changes"
```

## Task 5: Final verification and PR notes

**Files:**
- Modify: PR description only after push/open PR

- [ ] **Step 1: Run all local verification**

Run:

```bash
poetry run ruff format --check --diff scripts/ci tests/ci
poetry run pytest tests/ci/test_classify_changes.py -v
poetry run python -c "import yaml; [yaml.safe_load(open(f)) for f in ['.github/workflows/test.yml', '.github/workflows/lint.yml']]; print('YAML OK')"
```

- [ ] **Step 2: Inspect diff**

Run:

```bash
git diff origin/master...HEAD --stat
git diff origin/master...HEAD
```

- [ ] **Step 3: Prepare PR body**

Use repo commentary format:

```markdown
## Why

Reduce pull request GitHub Actions usage for issue #1034 while keeping full fallback coverage for shared or ambiguous changes.

## Lessons learnt

May 4-10 Actions history shows many medium-cost runs and cancellations, so avoiding unnecessary runs is more useful than only optimising individual test execution.

## Summary

- Added measurement tooling for recent Actions run durations.
- Added draft/path controls, caches, and separate lint workflow.
- Added tested selective CI classifier and wired it into `test.yml`.
- Full test suite still runs for shared files, dependency files, workflow files, master pushes, and `[ci full]`.
```
