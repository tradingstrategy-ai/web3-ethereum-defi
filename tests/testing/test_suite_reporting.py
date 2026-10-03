"""Exercise suite isolation and reporting through actual pytest subprocesses."""

import json
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _configure_probe(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install the actual suite configuration in an isolated test repository.

    Use subprocesses so captured stream lifetime, flaky hooks and xdist reports
    follow their real pytest lifecycle instead of calling hooks directly.

    :param pytester:
        Isolated pytest repository fixture.
    :param monkeypatch:
        Subprocess environment isolation.
    :return:
        Directory containing the probe test modules.
    """
    monkeypatch.setenv("PYTHONPATH", str(REPOSITORY_ROOT))
    monkeypatch.delenv("TEST_TIMINGS_FILE", raising=False)
    monkeypatch.delenv("TEST_RESOURCES_DIR", raising=False)
    pytester.makeconftest((REPOSITORY_ROOT / "tests/conftest.py").read_text())
    directory = pytester.path / "tests"
    directory.mkdir()
    return directory


def test_logging_retry_and_resource_reporting(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    """Capture retry phases and child CPU without leaking a closed log stream.

    Run the actual suite hooks in a subprocess, including a suppressed failed
    flaky attempt and a child process with measurable CPU work.

    :param pytester:
        Isolated repository and actual pytest subprocess runner.

    :param monkeypatch:
        Restore modified environment or provider objects after the test.

    :return:
        None; assertions validate the behaviour.
    """
    directory = _configure_probe(pytester, monkeypatch)
    timings = pytester.path / "timings.jsonl"
    resources = pytester.path / "resources"
    monkeypatch.setenv("TEST_TIMINGS_FILE", str(timings))
    monkeypatch.setenv("TEST_RESOURCES_DIR", "resources")
    (directory / "test_probe.py").write_text("""
import io
import logging
import os
import subprocess
import sys
from flaky import flaky
from eth_defi.utils import setup_console_logging

original_level = logging.getLogger("eth_defi.token").level
attempts = 0

def test_configure_logging():
    stream = io.StringIO()
    setup_console_logging(stream=stream)
    stream.close()

@flaky(max_runs=2, min_passes=1)
def test_following_test(capsys):
    global attempts
    attempts += 1
    assert attempts == 2
    assert logging.getLogger("eth_defi.token").level == original_level
    logging.getLogger().error("Stream lifetime probe")
    assert "Logging error" not in capsys.readouterr().err
    subprocess.run([sys.executable, "-c", "sum(range(1000000))"], check=True)

def test_last_changes_directory(tmp_path):
    os.chdir(tmp_path)
""")
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=3)
    phases = [json.loads(line) for line in timings.read_text().splitlines()]
    assert {row["phase"] for row in phases} == {"setup", "call", "teardown"}
    assert any(row["outcome"] == "failed" for row in phases)
    assert max(row["attempt"] for row in phases) == 2
    resource_data = json.loads((resources / "master.json").read_text())
    assert resource_data["exitstatus"] == 0
    assert resource_data["reaped_children_user_cpu_seconds"] > 0


def test_indirect_slow_marker_cannot_be_orphaned(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail even when the main workflow would deselect an undiscoverable test.

    Use an indirect marker that static discovery cannot see and verify the
    collection guard catches it before main-workflow deselection hides it.

    :param pytester:
        Isolated repository and actual pytest subprocess runner.

    :param monkeypatch:
        Restore modified environment or provider objects after the test.

    :return:
        None; assertions validate the behaviour.
    """
    directory = _configure_probe(pytester, monkeypatch)
    (directory / "test_indirect.py").write_text("""
import pytest
pytestmark = getattr(pytest.mark, "slow")
def test_new_slow_case():
    pass
""")
    result = pytester.runpytest_subprocess("-m", "not slow", "-q")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*Slow workflow cannot discover these modules*tests/test_indirect.py*"])


def test_xdist_phase_reports_have_separate_worker_writers(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep all three phases per case with actual worker and group identity.

    Exercise loadgroup placement and relative output paths while tests change
    directory. All phases must remain in the original worker output files.

    :param pytester:
        Isolated repository and actual pytest subprocess runner.

    :param monkeypatch:
        Restore modified environment or provider objects after the test.

    :return:
        None; assertions validate the behaviour.
    """
    directory = _configure_probe(pytester, monkeypatch)
    timings = pytester.path / "timings.jsonl"
    monkeypatch.setenv("TEST_TIMINGS_FILE", "timings.jsonl")
    (directory / "test_parallel.py").write_text("""
import pytest
pytestmark = pytest.mark.xdist_group("report-probe")
@pytest.mark.parametrize("value", [1, 2])
def test_worker(value, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert value > 0
""")
    result = pytester.runpytest_subprocess("-n", "2", "--dist", "loadgroup", "-q")
    result.assert_outcomes(passed=2)
    phases = [json.loads(line) for path in pytester.path.glob("timings-gw*.jsonl") for line in path.read_text().splitlines()]
    assert timings.read_text() == ""
    assert len(phases) == 6
    assert len({row["worker"] for row in phases}) == 1
    assert all(row["worker"].startswith("gw") for row in phases)
    assert all("@report-probe" in row["nodeid"] for row in phases)
