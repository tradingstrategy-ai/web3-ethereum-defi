"""Unit tests for ``scripts.ci.classify_changes``."""

from __future__ import annotations

from pathlib import Path

from scripts.ci.classify_changes import Classification, classify, discover_subsystems


def test_discover_subsystems_requires_source_and_test_dirs(tmp_path: Path) -> None:
    """Only matching ``eth_defi/<name>`` packages and ``tests/<name>`` dirs count."""

    make_subsystem(tmp_path, "lagoon")
    (tmp_path / "eth_defi" / "safe").mkdir(parents=True)
    (tmp_path / "eth_defi" / "safe" / "__init__.py").touch()
    (tmp_path / "tests" / "orphan").mkdir(parents=True)

    assert discover_subsystems(tmp_path) == {"lagoon"}


def test_subsystem_source_change_runs_matching_tests(tmp_path: Path) -> None:
    """A subsystem source change runs only matching subsystem tests."""

    make_subsystem(tmp_path, "lagoon")
    make_subsystem(tmp_path, "safe")

    result = classify(
        changed_files=["eth_defi/lagoon/vault.py"],
        commit_message="feat: lagoon change",
        repo_root=tmp_path,
        is_master_push=False,
    )

    assert result == Classification(
        mode="subset",
        pytest_targets="tests/lagoon/",
        affected_subsystems=["lagoon"],
    )


def test_subsystem_test_change_runs_matching_tests(tmp_path: Path) -> None:
    """A subsystem test-only change runs the matching subsystem tests."""

    make_subsystem(tmp_path, "gmx")

    result = classify(
        changed_files=["tests/gmx/test_order.py"],
        commit_message="test: update gmx order test",
        repo_root=tmp_path,
        is_master_push=False,
    )

    assert result == Classification(
        mode="subset",
        pytest_targets="tests/gmx/",
        affected_subsystems=["gmx"],
    )


def test_multiple_subsystems_are_sorted(tmp_path: Path) -> None:
    """Multiple subsystem changes produce deterministic sorted pytest targets."""

    make_subsystem(tmp_path, "lagoon")
    make_subsystem(tmp_path, "aave_v3")

    result = classify(
        changed_files=[
            "eth_defi/lagoon/vault.py",
            "tests/aave_v3/test_aave_v3_loan.py",
        ],
        commit_message="feat: update integrations",
        repo_root=tmp_path,
        is_master_push=False,
    )

    assert result == Classification(
        mode="subset",
        pytest_targets="tests/aave_v3/ tests/lagoon/",
        affected_subsystems=["aave_v3", "lagoon"],
    )


def test_root_test_file_forces_full_suite(tmp_path: Path) -> None:
    """Loose root-level test files can touch shared behaviour, so run all tests."""

    make_subsystem(tmp_path, "lagoon")

    result = classify(
        changed_files=["tests/test_token.py"],
        commit_message="test: token helper",
        repo_root=tmp_path,
        is_master_push=False,
    )

    assert result == Classification.full()


def test_shared_file_forces_full_suite(tmp_path: Path) -> None:
    """Dependency and workflow changes are shared and must run the full suite."""

    make_subsystem(tmp_path, "lagoon")

    result = classify(
        changed_files=["pyproject.toml"],
        commit_message="chore: update dependency",
        repo_root=tmp_path,
        is_master_push=False,
    )

    assert result == Classification.full()


def test_unknown_subsystem_forces_full_suite(tmp_path: Path) -> None:
    """Unknown packages are ambiguous and must run the full suite."""

    make_subsystem(tmp_path, "lagoon")

    result = classify(
        changed_files=["eth_defi/new_protocol/client.py"],
        commit_message="feat: new protocol",
        repo_root=tmp_path,
        is_master_push=False,
    )

    assert result == Classification.full()


def test_ci_full_commit_message_forces_full_suite(tmp_path: Path) -> None:
    """The ``[ci full]`` marker gives maintainers an explicit full-run override."""

    make_subsystem(tmp_path, "lagoon")

    result = classify(
        changed_files=["eth_defi/lagoon/vault.py"],
        commit_message="feat: lagoon [ci full]",
        repo_root=tmp_path,
        is_master_push=False,
    )

    assert result == Classification.full()


def test_master_push_forces_full_suite(tmp_path: Path) -> None:
    """Master push coverage must stay full regardless of the changed files."""

    make_subsystem(tmp_path, "lagoon")

    result = classify(
        changed_files=["eth_defi/lagoon/vault.py"],
        commit_message="feat: lagoon",
        repo_root=tmp_path,
        is_master_push=True,
    )

    assert result == Classification.full()


def make_subsystem(root: Path, name: str) -> None:
    """Create a fake source package and matching test directory."""

    (root / "eth_defi" / name).mkdir(parents=True, exist_ok=True)
    (root / "eth_defi" / name / "__init__.py").touch()
    (root / "tests" / name).mkdir(parents=True, exist_ok=True)
