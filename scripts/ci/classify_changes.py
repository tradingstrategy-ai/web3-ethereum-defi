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
from collections.abc import Iterable


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
    eth_defi_subs = {p.name for p in eth_defi_root.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    tests_subs = {p.name for p in tests_root.iterdir() if p.is_dir() and not p.name.startswith("__")}
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
    if "[ci full]" in commit_message:
        return _full()

    subsystems = discover_subsystems(repo_root)
    affected: set[str] = set()
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


def main() -> int:
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


if __name__ == "__main__":
    sys.exit(main())
