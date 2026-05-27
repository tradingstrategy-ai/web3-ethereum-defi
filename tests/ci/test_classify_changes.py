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
)
from scripts.ci.classify_changes import (
    main as ci_main,
)


def _make_subsystem(root: Path, name: str) -> None:
    """Create a fake ``eth_defi/<name>/__init__.py`` and ``tests/<name>/``."""
    (root / "eth_defi" / name).mkdir(parents=True, exist_ok=True)
    (root / "eth_defi" / name / "__init__.py").touch()
    (root / "tests" / name).mkdir(parents=True, exist_ok=True)


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

    rc = ci_main()

    assert rc == 0
    output = github_output.read_text()
    assert "mode=subset" in output
    assert "pytest_targets=tests/lagoon/" in output
    assert "affected_subsystems=lagoon" in output
