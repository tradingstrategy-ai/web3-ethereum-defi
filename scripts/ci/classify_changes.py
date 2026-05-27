"""Classify pull request changes into pytest targets.

The GitHub Actions test workflow uses this script to avoid running the
full test suite for narrow subsystem-only pull requests. The classifier is
conservative: ambiguous or shared changes return ``tests/`` so coverage is
not silently lost.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


FULL_TARGET = "tests/"

SHARED_PREFIXES = (
    ".github/",
    "contracts/",
    "docs/source/conf.py",
)

SHARED_FILES = {
    "pyproject.toml",
    "poetry.lock",
    "Makefile",
}


@dataclass(slots=True, frozen=True)
class Classification:
    """Classification result for a changed-file set.

    :param mode: ``full`` or ``subset``.
    :param pytest_targets: Space-separated pytest target paths.
    :param affected_subsystems: Sorted subsystem names for subset mode.
    """

    mode: str
    pytest_targets: str
    affected_subsystems: list[str]

    @classmethod
    def full(cls) -> "Classification":
        """Return a full-suite classification.

        :return: Full-suite classification.
        """

        return cls(mode="full", pytest_targets=FULL_TARGET, affected_subsystems=[])


def discover_subsystems(repo_root: Path) -> set[str]:
    """Discover source/test subsystem pairs in the repository.

    A subsystem exists when both ``eth_defi/<name>/__init__.py`` and
    ``tests/<name>/`` exist. This keeps the mapping dynamic and avoids a
    stale hardcoded protocol list.

    :param repo_root: Repository root.
    :return: Set of subsystem names.
    """

    source_root = repo_root / "eth_defi"
    test_root = repo_root / "tests"
    if not source_root.is_dir() or not test_root.is_dir():
        return set()

    source_subsystems = {path.name for path in source_root.iterdir() if path.is_dir() and (path / "__init__.py").is_file()}
    test_subsystems = {path.name for path in test_root.iterdir() if path.is_dir() and not path.name.startswith(".")}

    return source_subsystems & test_subsystems


def classify(
    *,
    changed_files: Iterable[str],
    commit_message: str,
    repo_root: Path,
    is_master_push: bool,
) -> Classification:
    """Classify changed files into a pytest target set.

    :param changed_files: Paths relative to the repository root.
    :param commit_message: Commit message used for ``[ci full]`` override.
    :param repo_root: Repository root for subsystem discovery.
    :param is_master_push: Whether this is a push to ``master``.
    :return: Classification result.
    """

    if is_master_push or "[ci full]" in commit_message.lower():
        return Classification.full()

    subsystems = discover_subsystems(repo_root)
    affected: set[str] = set()

    for raw_path in changed_files:
        path = raw_path.strip()
        if not path:
            continue

        if path in SHARED_FILES or any(path.startswith(prefix) for prefix in SHARED_PREFIXES):
            return Classification.full()

        top_level, separator, rest = path.partition("/")
        if top_level not in {"eth_defi", "tests"}:
            return Classification.full()

        if not separator:
            return Classification.full()

        subsystem = rest.split("/", 1)[0]
        if subsystem not in subsystems:
            return Classification.full()

        affected.add(subsystem)

    if not affected:
        return Classification.full()

    affected_subsystems = sorted(affected)
    return Classification(
        mode="subset",
        pytest_targets=" ".join(f"tests/{subsystem}/" for subsystem in affected_subsystems),
        affected_subsystems=affected_subsystems,
    )


def parse_changed_files(value: str) -> list[str]:
    """Parse newline-separated changed files.

    :param value: Raw changed-files environment variable.
    :return: Clean path list.
    """

    return [line.strip() for line in value.splitlines() if line.strip()]


def write_github_output(classification: Classification) -> None:
    """Write classification fields to GitHub Actions output if available.

    :param classification: Classification to emit.
    """

    output_path = os.environ.get("GITHUB_OUTPUT")
    lines = [
        f"mode={classification.mode}",
        f"pytest_targets={classification.pytest_targets}",
        f"affected_subsystems={','.join(classification.affected_subsystems)}",
    ]

    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            for line in lines:
                output.write(f"{line}\n")

    for line in lines:
        print(line)


def main() -> int:
    """Read environment inputs and emit GitHub Actions outputs.

    :return: Process exit code.
    """

    changed_files = parse_changed_files(os.environ.get("CHANGED_FILES", ""))
    commit_message = os.environ.get("COMMIT_MESSAGE", "")
    is_master_push = os.environ.get("IS_MASTER_PUSH", "").lower() == "true"

    classification = classify(
        changed_files=changed_files,
        commit_message=commit_message,
        repo_root=Path.cwd(),
        is_master_push=is_master_push,
    )
    write_github_output(classification)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
