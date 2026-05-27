# Selective CI Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `test.yml` run only the tests for changed subsystems while keeping all current pytest flags, all Step 1 wins, and a single workflow file.

**Architecture:** Two-job workflow — a fast `detect` job (ubuntu-latest) classifies the diff into either a set of changed subsystems or a full-run mode, then a `test` job (Beefy) runs `pytest <targets> --ignore=tests/gmx` with all existing flags. Subsystems are discovered dynamically as the intersection of `eth_defi/*` and `tests/*` directories — no hardcoded list. A new venv cache shaves ~60s off the setup on every cache hit.

**Tech Stack:** GitHub Actions, Python 3, `actions/cache@v4`, pytest, pytest-xdist, Poetry.

**Spec:** [docs/superpowers/specs/2026-05-27-selective-ci-design.md](../specs/2026-05-27-selective-ci-design.md)

---

## File Structure

This plan modifies a single CI file and adds one script + tests.

| File | Action | Responsibility |
|---|---|---|
| `scripts/ci/classify_changes.py` | **Create** | Pure Python: reads a list of changed files + the commit message via env vars, prints `mode=...`, `pytest_targets=...`, `affected_subsystems=...` to stdout. No GitHub Actions specifics. |
| `tests/ci/test_classify_changes.py` | **Create** | Unit tests for the classifier — exhaustive coverage of edge cases. |
| `tests/ci/__init__.py` | **Create** | Empty, so pytest discovers the dir. |
| `.github/workflows/test.yml` | **Modify** | Replace the single-job structure with `detect` + `test` jobs; wire in the classifier script; add the venv cache step. All other steps preserved. |
| `CLAUDE.md` | **Modify** | Document the `[ci full]` commit-message override. |
| `CHANGELOG.md` | **Modify** | Add a one-line entry. |

**Why a separate script?** The classification logic is ~80 lines of branching Python. Inlining it as a YAML heredoc makes it impossible to unit-test, hard to read, and forces re-running CI to validate logic changes. A separate file is unit-testable in seconds and keeps `test.yml` skim-able.

---

## Pre-flight

### Task 0: Confirm working directory and branch

- [ ] **Step 1: Verify branch and clean tree**

```bash
git status
git rev-parse --abbrev-ref HEAD
```

Expected: branch `feat/ci-cost-reduction-step1`, clean working tree (the spec commit `2c325dd2` is the latest).

- [ ] **Step 2: Confirm spec exists**

```bash
ls -la docs/superpowers/specs/2026-05-27-selective-ci-design.md
```

Expected: file present, ~6 KB.

---

## Chunk 1: Classifier script + tests (TDD)

### Task 1: Create the test scaffold and first failing test

**Files:**
- Create: `tests/ci/__init__.py`
- Create: `tests/ci/test_classify_changes.py`

- [ ] **Step 1: Create the empty package init**

```bash
mkdir -p tests/ci
: > tests/ci/__init__.py
```

- [ ] **Step 2: Write the first failing test**

Create `tests/ci/test_classify_changes.py` with this content:

```python
"""Unit tests for ``scripts.ci.classify_changes``.

The classifier decides which tests CI should run for a PR by reading a
list of changed files and a commit message, then emitting either a set
of subsystem directories (``mode=subset``) or a sentinel ``tests/``
(``mode=full``).

These tests cover the classification logic only — they do not exercise
``git diff`` or the GitHub Actions wiring, which live in the workflow
YAML.
"""

from pathlib import Path

import pytest

from scripts.ci.classify_changes import (
    Classification,
    classify,
    discover_subsystems,
)


def test_subsystem_only_change_runs_only_that_subsystem(tmp_path: Path) -> None:
    """A diff confined to one subsystem's eth_defi/ + tests/ runs only that
    subsystem's tests."""
    _make_subsystem(tmp_path, "lagoon")
    _make_subsystem(tmp_path, "safe")

    result = classify(
        changed_files=["eth_defi/lagoon/vault.py"],
        commit_message="feat: lagoon vault",
        repo_root=tmp_path,
        is_master_push=False,
    )

    assert result == Classification(
        mode="subset",
        pytest_targets="tests/lagoon/",
        affected_subsystems=["lagoon"],
    )


def _make_subsystem(root: Path, name: str) -> None:
    """Create a fake ``eth_defi/<name>/__init__.py`` and ``tests/<name>/``."""
    (root / "eth_defi" / name).mkdir(parents=True, exist_ok=True)
    (root / "eth_defi" / name / "__init__.py").touch()
    (root / "tests" / name).mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 3: Run the test to verify it fails for the right reason**

```bash
source .local-test.env && poetry run pytest tests/ci/test_classify_changes.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.ci.classify_changes'` (or similar import error). If pytest cannot find `tests/ci/` at all, double-check `tests/ci/__init__.py` exists.

- [ ] **Step 4: Commit the failing test**

```bash
git add tests/ci/__init__.py tests/ci/test_classify_changes.py
git commit -m "test: scaffold for ci change classifier (red)"
```

---

### Task 2: Implement the minimum classifier to make the first test pass

**Files:**
- Create: `scripts/ci/classify_changes.py`
- Create: `scripts/ci/__init__.py` (if missing)

- [ ] **Step 1: Ensure scripts/ci/ is an importable package**

```bash
mkdir -p scripts/ci
[ -f scripts/ci/__init__.py ] || : > scripts/ci/__init__.py
ls scripts/ci/
```

Expected: at least `__init__.py` present.

- [ ] **Step 2: Write the minimum classifier**

Create `scripts/ci/classify_changes.py`:

```python
"""Classify changed files into a pytest target list for CI.

This module is imported from the GitHub Actions ``detect`` job and reads:

- ``CHANGED_FILES``: newline-separated list of files changed against the
  PR base (or empty for master pushes).
- ``COMMIT_MESSAGE``: head commit message; the substring ``[ci full]``
  forces a full run.
- ``IS_MASTER_PUSH``: ``"true"`` when pushing to master; the diff is
  ignored and a full run is emitted.

The pure :func:`classify` function takes those inputs as arguments so it
can be unit-tested without touching the environment.

Outputs are written as ``KEY=value`` lines to ``GITHUB_OUTPUT`` and also
echoed to stdout for log readability.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import sys
from typing import Iterable


@dataclasses.dataclass(slots=True, frozen=True)
class Classification:
    """Result of classifying a diff.

    :param mode: ``"subset"`` to run only ``affected_subsystems`` or
        ``"full"`` to run every test directory.
    :param pytest_targets: A space-separated string of test directories,
        or ``"tests/"`` for the full-run mode.
    :param affected_subsystems: Sorted list of subsystem names that
        matched the diff (empty when ``mode == "full"``).
    """

    mode: str
    pytest_targets: str
    affected_subsystems: list[str]


def discover_subsystems(repo_root: pathlib.Path) -> set[str]:
    """Return the set of subsystem names eligible for selective runs.

    A subsystem is eligible iff:

    - ``eth_defi/<name>/__init__.py`` exists (it's a real package), and
    - ``tests/<name>/`` exists (it has a test directory).

    :param repo_root: Repository root containing ``eth_defi/`` and
        ``tests/``.
    :return: Set of subsystem directory names.
    """
    eth_defi_root = repo_root / "eth_defi"
    tests_root = repo_root / "tests"
    if not eth_defi_root.is_dir() or not tests_root.is_dir():
        return set()
    eth_defi_subs = {
        p.name
        for p in eth_defi_root.iterdir()
        if p.is_dir() and (p / "__init__.py").exists()
    }
    tests_subs = {
        p.name
        for p in tests_root.iterdir()
        if p.is_dir() and not p.name.startswith("__")
    }
    return eth_defi_subs & tests_subs


def classify(
    *,
    changed_files: Iterable[str],
    commit_message: str,
    repo_root: pathlib.Path,
    is_master_push: bool,
) -> Classification:
    """Classify a diff into a pytest target list.

    See module docstring for the input contract.

    :param changed_files: Iterable of paths relative to ``repo_root``.
    :param commit_message: Head commit message used for ``[ci full]``
        detection.
    :param repo_root: Repository root used to discover subsystems.
    :param is_master_push: When ``True``, the diff is ignored and a
        full run is emitted unconditionally.
    :return: A :class:`Classification`.
    """
    if is_master_push:
        return _full()

    subsystems = discover_subsystems(repo_root)
    affected: set[str] = set()
    for path in changed_files:
        first, _, rest = path.partition("/")
        if first in {"eth_defi", "tests"} and rest:
            sub = rest.split("/", 1)[0]
            if sub in subsystems:
                affected.add(sub)
                continue
            # eth_defi/<unknown>/... or tests/test_root.py → full
            return _full()
    if not affected:
        return _full()
    sorted_subs = sorted(affected)
    return Classification(
        mode="subset",
        pytest_targets=" ".join(f"tests/{s}/" for s in sorted_subs),
        affected_subsystems=sorted_subs,
    )


def _full() -> Classification:
    return Classification(
        mode="full",
        pytest_targets="tests/",
        affected_subsystems=[],
    )


def _main() -> int:
    """Entry point for the workflow ``detect`` job."""
    raise NotImplementedError("CLI wiring is added in a later task")


if __name__ == "__main__":
    sys.exit(_main())
```

- [ ] **Step 3: Run the test to verify it passes**

```bash
source .local-test.env && poetry run pytest tests/ci/test_classify_changes.py -v
```

Expected: PASS (`1 passed`).

- [ ] **Step 4: Commit the minimum implementation**

```bash
git add scripts/ci/__init__.py scripts/ci/classify_changes.py
git commit -m "feat(ci): minimum classifier covering single-subsystem case (green)"
```

---

### Task 3: Edge case — multiple subsystems

- [ ] **Step 1: Add failing test for multiple subsystems**

Append to `tests/ci/test_classify_changes.py`:

```python
def test_multiple_subsystems_run_together(tmp_path: Path) -> None:
    """Changes spanning two subsystems run both in one job."""
    _make_subsystem(tmp_path, "lagoon")
    _make_subsystem(tmp_path, "safe")

    result = classify(
        changed_files=[
            "eth_defi/lagoon/vault.py",
            "tests/safe/test_safe.py",
        ],
        commit_message="refactor: lagoon + safe",
        repo_root=tmp_path,
        is_master_push=False,
    )

    assert result.mode == "subset"
    assert result.affected_subsystems == ["lagoon", "safe"]
    assert result.pytest_targets == "tests/lagoon/ tests/safe/"
```

- [ ] **Step 2: Run the test and verify it passes**

The current implementation already supports this; run to confirm:

```bash
source .local-test.env && poetry run pytest tests/ci/test_classify_changes.py -v
```

Expected: PASS (`2 passed`).

- [ ] **Step 3: Commit**

```bash
git add tests/ci/test_classify_changes.py
git commit -m "test: classifier handles multiple subsystems"
```

---

### Task 4: Edge case — unknown subsystem triggers full run

- [ ] **Step 1: Add failing test**

Append to `tests/ci/test_classify_changes.py`:

```python
def test_unknown_eth_defi_subdir_triggers_full(tmp_path: Path) -> None:
    """A change under eth_defi/ that is not a known subsystem (e.g. a new
    module added without a corresponding tests/ dir) triggers a full run."""
    _make_subsystem(tmp_path, "lagoon")
    # Note: NO tests/foo/, so 'foo' is not a known subsystem.
    (tmp_path / "eth_defi" / "foo").mkdir(parents=True)
    (tmp_path / "eth_defi" / "foo" / "__init__.py").touch()

    result = classify(
        changed_files=["eth_defi/foo/bar.py"],
        commit_message="feat: new module",
        repo_root=tmp_path,
        is_master_push=False,
    )

    assert result.mode == "full"
    assert result.pytest_targets == "tests/"
    assert result.affected_subsystems == []


def test_root_level_test_file_triggers_full(tmp_path: Path) -> None:
    """A change to tests/test_*.py (a root-level cross-cutting test) is
    not a known subsystem path → triggers a full run."""
    _make_subsystem(tmp_path, "lagoon")

    result = classify(
        changed_files=["tests/test_balances.py"],
        commit_message="fix: balances",
        repo_root=tmp_path,
        is_master_push=False,
    )

    assert result.mode == "full"
```

- [ ] **Step 2: Run tests**

```bash
source .local-test.env && poetry run pytest tests/ci/test_classify_changes.py -v
```

Expected: PASS (4 tests total). The classifier already handles these because the inner `sub` is not in `subsystems` → falls through to `_full()`.

- [ ] **Step 3: Commit**

```bash
git add tests/ci/test_classify_changes.py
git commit -m "test: unknown subdir and root tests trigger full run"
```

---

### Task 5: Edge case — `[ci full]` override and master push

- [ ] **Step 1: Add failing test for the override**

Append to `tests/ci/test_classify_changes.py`:

```python
def test_commit_message_ci_full_override(tmp_path: Path) -> None:
    """A '[ci full]' substring in the commit message forces a full run
    even when only one subsystem changed."""
    _make_subsystem(tmp_path, "lagoon")

    result = classify(
        changed_files=["eth_defi/lagoon/vault.py"],
        commit_message="fix(lagoon): tricky cross-cutting change [ci full]",
        repo_root=tmp_path,
        is_master_push=False,
    )

    assert result.mode == "full"


def test_master_push_always_full(tmp_path: Path) -> None:
    """Pushes to master always run the full suite regardless of diff."""
    _make_subsystem(tmp_path, "lagoon")

    result = classify(
        changed_files=["eth_defi/lagoon/vault.py"],
        commit_message="merge: lagoon work",
        repo_root=tmp_path,
        is_master_push=True,
    )

    assert result.mode == "full"
```

- [ ] **Step 2: Run tests — expect the master test to PASS but the override test to FAIL**

```bash
source .local-test.env && poetry run pytest tests/ci/test_classify_changes.py -v
```

Expected: `test_master_push_always_full` PASS (already implemented), `test_commit_message_ci_full_override` FAIL.

- [ ] **Step 3: Implement the override check**

In `scripts/ci/classify_changes.py`, modify the `classify` function:

```python
def classify(
    *,
    changed_files: Iterable[str],
    commit_message: str,
    repo_root: pathlib.Path,
    is_master_push: bool,
) -> Classification:
    """..."""
    if is_master_push:
        return _full()
    if "[ci full]" in commit_message:
        return _full()
    # ... (existing logic)
```

Place the `[ci full]` check after the master-push check and before the subsystem loop.

- [ ] **Step 4: Run tests, verify all pass**

```bash
source .local-test.env && poetry run pytest tests/ci/test_classify_changes.py -v
```

Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/ci/classify_changes.py tests/ci/test_classify_changes.py
git commit -m "feat(ci): [ci full] commit-message override + master always full"
```

---

### Task 6: Edge case — empty diff and docs-only diff

- [ ] **Step 1: Add tests**

Append to `tests/ci/test_classify_changes.py`:

```python
def test_empty_diff_falls_back_to_full(tmp_path: Path) -> None:
    """Empty changed_files (rare, but possible if paths-ignore matched
    everything) falls back to full to avoid silently skipping tests."""
    _make_subsystem(tmp_path, "lagoon")

    result = classify(
        changed_files=[],
        commit_message="docs: whatever",
        repo_root=tmp_path,
        is_master_push=False,
    )

    assert result.mode == "full"


def test_only_outside_paths_triggers_full(tmp_path: Path) -> None:
    """A diff that touches only files outside eth_defi/ and tests/ also
    triggers full, because we can't prove it's harmless."""
    _make_subsystem(tmp_path, "lagoon")

    result = classify(
        changed_files=[
            "pyproject.toml",
            ".github/workflows/test.yml",
            "Makefile",
        ],
        commit_message="chore: build",
        repo_root=tmp_path,
        is_master_push=False,
    )

    assert result.mode == "full"
```

- [ ] **Step 2: Run tests**

```bash
source .local-test.env && poetry run pytest tests/ci/test_classify_changes.py -v
```

Expected: `test_empty_diff_falls_back_to_full` PASS (already covered by the "no affected" fallthrough). `test_only_outside_paths_triggers_full` FAIL — the loop currently skips paths whose first segment is not `eth_defi`/`tests`, so the loop exits with `affected = set()` and falls into `_full()`. Run to confirm — if both already pass, skip Step 3.

- [ ] **Step 3: If the outside-paths test fails, fix the logic**

The intention: any file outside `eth_defi/` and `tests/` should also trigger full (defensive). Modify `classify`:

```python
for path in changed_files:
    first, _, rest = path.partition("/")
    if first not in {"eth_defi", "tests"}:
        # Out-of-tree changes (build files, workflows, Makefile, etc.) → full.
        return _full()
    if not rest:
        return _full()
    sub = rest.split("/", 1)[0]
    if sub not in subsystems:
        return _full()
    affected.add(sub)
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
source .local-test.env && poetry run pytest tests/ci/test_classify_changes.py -v
```

Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/ci/classify_changes.py tests/ci/test_classify_changes.py
git commit -m "feat(ci): out-of-tree changes also trigger full run"
```

---

### Task 7: CLI entry point that reads env vars and writes GITHUB_OUTPUT

- [ ] **Step 1: Add a test for the CLI**

Append to `tests/ci/test_classify_changes.py`:

```python
def test_main_writes_github_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``_main`` reads env vars and writes KEY=value lines to the file
    pointed at by ``GITHUB_OUTPUT``."""
    _make_subsystem(tmp_path, "lagoon")

    github_output = tmp_path / "gh_out.txt"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHANGED_FILES", "eth_defi/lagoon/vault.py\n")
    monkeypatch.setenv("COMMIT_MESSAGE", "feat: lagoon")
    monkeypatch.setenv("IS_MASTER_PUSH", "false")
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))

    from scripts.ci.classify_changes import _main
    rc = _main()

    assert rc == 0
    output = github_output.read_text()
    assert "mode=subset" in output
    assert "pytest_targets=tests/lagoon/" in output
    assert "affected_subsystems=lagoon" in output
```

- [ ] **Step 2: Run test, expect failure**

```bash
source .local-test.env && poetry run pytest tests/ci/test_classify_changes.py::test_main_writes_github_output -v
```

Expected: FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement `_main`**

Replace `_main` in `scripts/ci/classify_changes.py`:

```python
def _main() -> int:
    """Entry point for the workflow ``detect`` job.

    Reads ``CHANGED_FILES`` (newline-separated, possibly empty),
    ``COMMIT_MESSAGE``, and ``IS_MASTER_PUSH`` from the environment,
    classifies the diff, and writes the result to ``GITHUB_OUTPUT``.

    Echoes the result to stdout for log readability.
    """
    repo_root = pathlib.Path.cwd()
    raw_changed = os.environ.get("CHANGED_FILES", "")
    changed = [line for line in raw_changed.splitlines() if line.strip()]
    commit_message = os.environ.get("COMMIT_MESSAGE", "")
    is_master_push = os.environ.get("IS_MASTER_PUSH", "false").lower() == "true"

    result = classify(
        changed_files=changed,
        commit_message=commit_message,
        repo_root=repo_root,
        is_master_push=is_master_push,
    )

    print(f"mode={result.mode}")
    print(f"pytest_targets={result.pytest_targets}")
    print(f"affected_subsystems={','.join(result.affected_subsystems)}")

    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as fh:
            fh.write(f"mode={result.mode}\n")
            fh.write(f"pytest_targets={result.pytest_targets}\n")
            fh.write(f"affected_subsystems={','.join(result.affected_subsystems)}\n")
    return 0
```

- [ ] **Step 4: Run tests**

```bash
source .local-test.env && poetry run pytest tests/ci/test_classify_changes.py -v
```

Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/ci/classify_changes.py tests/ci/test_classify_changes.py
git commit -m "feat(ci): _main reads env, writes GITHUB_OUTPUT"
```

---

## Chunk 2: Workflow wiring

### Task 8: Refactor `test.yml` to add `detect` job

**Files:**
- Modify: `.github/workflows/test.yml`

- [ ] **Step 1: Read current `test.yml`**

```bash
wc -l .github/workflows/test.yml
```

Note line count (~175 lines today).

- [ ] **Step 2: Add the `detect` job above `test-python`**

Open `.github/workflows/test.yml`. After the `jobs:` line and before `test-python:`, insert:

```yaml
  # ── Detect which subsystems the diff touches ───────────────────────────────
  # Cheap classifier on ubuntu-latest (1× billing) that turns the diff into
  # either a list of subsystem dirs (mode=subset) or 'tests/' (mode=full).
  detect:
    runs-on: ubuntu-latest
    if: github.event.pull_request.draft == false || github.event_name == 'push'
    outputs:
      mode: ${{ steps.classify.outputs.mode }}
      pytest_targets: ${{ steps.classify.outputs.pytest_targets }}
      affected_subsystems: ${{ steps.classify.outputs.affected_subsystems }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Compute changed files
        id: diff
        run: |
          set -euo pipefail
          if [ "${{ github.event_name }}" = "pull_request" ]; then
            base="${{ github.event.pull_request.base.sha }}"
            head="${{ github.event.pull_request.head.sha }}"
            git diff --name-only "$base" "$head" > /tmp/changed.txt
          else
            # push to master: list files changed in the head commit
            git diff --name-only HEAD~1 HEAD > /tmp/changed.txt || true
          fi
          echo "Changed files:"
          cat /tmp/changed.txt || true

      - uses: actions/setup-python@v5
        with:
          python-version: "3.14"

      - name: Classify changes
        id: classify
        env:
          COMMIT_MESSAGE: ${{ github.event.head_commit.message || github.event.pull_request.title }}
          IS_MASTER_PUSH: ${{ github.event_name == 'push' && github.ref == 'refs/heads/master' }}
        run: |
          export CHANGED_FILES="$(cat /tmp/changed.txt)"
          python -m scripts.ci.classify_changes
```

- [ ] **Step 3: Wire `test-python` to depend on `detect`**

In the same file, locate the `test-python:` job header.  Add `needs: detect` directly under it:

```yaml
  test-python:
    needs: detect
    runs-on:
      group: Beefy runners
    # ... existing fields unchanged
```

- [ ] **Step 4: Replace the pytest invocation with the dynamic targets**

In `test-python`'s `Run tests (parallel)` step (currently calling
`pytest tests/ --ignore=tests/gmx ...`), change the command to:

```yaml
      - name: Run tests (parallel)
        run: |
          # NOTE: GMX tests run separately in test-gmx.yml due to fork state conflicts.
          # ``needs.detect.outputs.pytest_targets`` is either a list of subsystem
          # directories or the literal ``tests/`` for a full run.  Always use
          # verbose pytest output so stalled CI runs show the latest test names
          # in the Actions log.
          poetry run pytest ${{ needs.detect.outputs.pytest_targets }} \
            --ignore=tests/gmx \
            --timeout-method=thread --tb=native -n auto -v -s --capture=no
```

(The `env:` block below the run command stays unchanged.)

- [ ] **Step 5: Validate YAML syntax**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))" && echo OK
```

Expected: prints `OK`.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "feat(ci): add detect job that selects pytest targets dynamically"
```

---

### Task 8b: Flip `fail-fast` to `true`

**Files:**
- Modify: `.github/workflows/test.yml`

Currently `test-python` has `fail-fast: false`, which keeps running every
matrix entry even after one fails.  The matrix is a single Python
version today, so this is a no-op — but the moment a second dimension is
added (e.g. Python 3.15), we want CI to abort the whole strategy as soon
as one entry fails.  Cheaper, faster feedback.

- [ ] **Step 1: Edit `test.yml`**

In the `test-python` job, change:

```yaml
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.14"]
```

to:

```yaml
    strategy:
      fail-fast: true
      matrix:
        python-version: ["3.14"]
```

- [ ] **Step 2: Validate YAML syntax**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))" && echo OK
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "ci: enable fail-fast on test matrix"
```

---

### Task 9: Add the venv cache step

**Files:**
- Modify: `.github/workflows/test.yml`

- [ ] **Step 1: Insert the cache step**

In `test.yml`, locate the `Install dependencies` step (it runs `poetry install`).  Insert this block *immediately before* the install step:

```yaml
      - name: Resolve poetry venv path
        id: venv-path
        run: |
          # Resolve where poetry will install the venv so we can cache it.
          # We must do this AFTER setup-python+poetry are installed and
          # BEFORE 'poetry install' so a cache hit lets the install step
          # skip wheel building.
          venv_path=$(poetry env info --path 2>/dev/null || true)
          if [ -z "$venv_path" ]; then
            # Pre-create the venv so its path is stable across the cache step.
            poetry env use 3.14
            venv_path=$(poetry env info --path)
          fi
          echo "path=$venv_path" >> "$GITHUB_OUTPUT"
          echo "Resolved venv path: $venv_path"

      - name: Cache poetry venv
        uses: actions/cache@v4
        with:
          path: ${{ steps.venv-path.outputs.path }}
          key: venv-${{ runner.os }}-py3.14-${{ hashFiles('poetry.lock', 'pyproject.toml') }}
```

- [ ] **Step 2: Validate YAML syntax**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/test.yml'))" && echo OK
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "perf(ci): cache poetry venv to skip ~60s of install on hit"
```

---

### Task 10: Smoke-test the classifier locally

- [ ] **Step 1: Simulate a single-subsystem PR**

```bash
CHANGED_FILES="eth_defi/lagoon/vault.py
tests/lagoon/test_lagoon.py" \
COMMIT_MESSAGE="feat: lagoon" \
IS_MASTER_PUSH=false \
GITHUB_OUTPUT=/tmp/out.txt \
poetry run python -m scripts.ci.classify_changes
cat /tmp/out.txt
```

Expected stdout includes:

```
mode=subset
pytest_targets=tests/lagoon/
affected_subsystems=lagoon
```

And `/tmp/out.txt` contains the same `KEY=value` lines.

- [ ] **Step 2: Simulate a shared-file PR**

```bash
CHANGED_FILES="eth_defi/chain.py" \
COMMIT_MESSAGE="refactor: chain helpers" \
IS_MASTER_PUSH=false \
GITHUB_OUTPUT=/tmp/out.txt \
poetry run python -m scripts.ci.classify_changes
```

Expected:

```
mode=full
pytest_targets=tests/
affected_subsystems=
```

- [ ] **Step 3: Simulate the `[ci full]` override**

```bash
CHANGED_FILES="eth_defi/lagoon/vault.py" \
COMMIT_MESSAGE="fix: lagoon (also touches shared paths) [ci full]" \
IS_MASTER_PUSH=false \
GITHUB_OUTPUT=/tmp/out.txt \
poetry run python -m scripts.ci.classify_changes
```

Expected:

```
mode=full
```

If any of these diverge from the expected output, debug before
continuing.

---

## Chunk 3: Docs and rollout

### Task 11: Document `[ci full]` override

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Find the right section**

```bash
grep -n "Running tests\|pytest" CLAUDE.md | head -5
```

- [ ] **Step 2: Append a new subsection**

Open `CLAUDE.md`.  Under the existing "## Running tests" section (or
"## Pull requests" if that's a better fit — pick whichever exists),
add this paragraph at the end of the section:

```markdown
### Forcing a full CI test run

On a PR, the `detect` job in `.github/workflows/test.yml` only runs the
tests for subsystems whose files changed.  When a change is cross-cutting
and you want the full suite to run anyway, include the literal substring
`[ci full]` anywhere in the commit message of the head commit:

```bash
git commit -m "fix(lagoon): also affects safe and erc_4626 indirectly [ci full]"
```

Master pushes always run the full suite; the override is only needed
on PRs.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: explain [ci full] commit message override"
```

---

### Task 12: Changelog entry

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Inspect the existing format**

```bash
head -20 CHANGELOG.md
```

- [ ] **Step 2: Add a new bullet**

Per the project's CHANGELOG convention (each entry suffixed with the PR
date in YYYY-MM-DD), add this line to the top of the changelog:

```markdown
- CI: selective test execution — only run tests for changed subsystems on PRs, with `[ci full]` commit-message override (2026-05-27).
```

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog entry for selective CI"
```

---

### Task 13: End-to-end CI dry run

This is a *human-in-the-loop* step that cannot be automated locally
because it requires GitHub Actions to actually execute the workflow.

- [ ] **Step 1: Push the branch and trigger CI**

```bash
git push
gh pr view --json url
```

- [ ] **Step 2: Watch the `detect` job logs**

```bash
gh run watch
```

Verify:

- `detect` succeeds in <60s on ubuntu-latest.
- Its log shows the expected `mode=` / `pytest_targets=` lines.
- `test-python` waits for `detect`, then runs only the expected subsystems.

- [ ] **Step 3: Add a `[ci full]` commit and push**

```bash
git commit --allow-empty -m "ci: force full run [ci full]"
git push
```

Confirm the next CI run executes `pytest tests/ --ignore=tests/gmx ...`.

- [ ] **Step 4: Reset the empty commit (optional)**

If you don't want the empty commit in the merge, drop it:

```bash
git reset --hard HEAD~1
git push --force-with-lease
```

(Skip this if you'd rather keep the empty commit as a marker.)

---

## Final Task: Self-review

- [ ] **Step 1: Run the full test suite for the new module locally**

```bash
source .local-test.env && poetry run pytest tests/ci/ -v
```

Expected: all 9 tests PASS.

- [ ] **Step 2: Lint formatting**

```bash
poetry run ruff format scripts/ci/ tests/ci/
poetry run ruff format --check scripts/ci/ tests/ci/
```

Expected: clean.

- [ ] **Step 3: Look back at the spec**

```bash
cat docs/superpowers/specs/2026-05-27-selective-ci-design.md
```

Walk through each component and failure mode in the spec.  For each,
confirm the implementation actually exercises that path — either with a
unit test (classifier) or visibly in the workflow YAML (detect job
wiring, venv cache step).  Note any divergences in the PR description.

- [ ] **Step 4: Open / update the PR**

Pre-flight check: `gh pr list --head feat/ci-cost-reduction-step1`.
If a PR already exists (#1035), this branch updates it.  If you need to
update the description with the selective-CI summary, use:

```bash
gh pr edit 1035 --body "$(cat <<'EOF'
## Why
... (existing PR body) ...

## Update — selective CI

Selective test execution via a cheap ``detect`` job that classifies the
diff into either a list of subsystem directories or a full-run sentinel.
A new venv cache shaves ~60s off the setup on cache hit.

See `docs/superpowers/specs/2026-05-27-selective-ci-design.md`.
EOF
)"
```

(Pushing is the user's call — do not push without explicit approval.)
