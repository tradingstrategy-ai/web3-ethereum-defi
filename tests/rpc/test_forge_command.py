"""Bound Forge subprocess output and failures without launching a blockchain."""

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import psutil
import pytest

from eth_defi.foundry.forge import ForgeFailed, _exec_cmd  # noqa: PLC2701 - exercise the subprocess boundary directly


@pytest.fixture
def process_file(tmp_path: Path) -> Iterator[Path]:
    """Record fixture subprocesses and clean them up even against broken code.

    Regression failures must not leave the deliberately blocked child running.

    :param tmp_path: Private directory for the child process identifiers.
    :return: JSON path written by the child before exercising pipe behaviour.
    """
    path = tmp_path / "processes.json"
    yield path
    if path.exists():
        for pid in reversed(json.loads(path.read_text())):
            try:
                process = psutil.Process(pid)
                if process.status() == psutil.STATUS_ZOMBIE:
                    continue
                process.kill()
                psutil.wait_procs([process], timeout=2)
            except psutil.NoSuchProcess:
                pass


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_forge_drains_output_before_waiting(stream: str, process_file: Path) -> None:
    """Drain output exceeding the operating system pipe capacity.

    A real Forge invocation can emit large compiler or Git diagnostics before
    printing the deployment identifiers. Waiting first blocks its writer.

    :param stream: Output pipe flooded before successful deployment output.
    :param process_file: Fixture-owned process identifiers for failure cleanup.
    :return: ``None`` after checking both parsed deployment identifiers.
    """
    program = f"import json, os, sys; from pathlib import Path; Path(sys.argv[1]).write_text(json.dumps([os.getpid()])); sys.{stream}.write('x' * 1048576 + '\\n'); sys.{stream}.flush(); print('Deployed to: 0x123'); print('Transaction hash: 0x456')"
    assert _exec_cmd([sys.executable, "-c", program, str(process_file)], "synthetic forge", timeout=5) == ("0x123", "0x456")


@pytest.mark.parametrize("parent_exits", [False, pytest.param(True, marks=pytest.mark.skipif(os.name != "posix", reason="Orphan cleanup requires POSIX process groups"))])
def test_forge_timeout_reaps_command_and_child(process_file: Path, *, parent_exits: bool) -> None:
    """Stop the entire timed-out command and retain a credential-free error.

    A Git child can inherit Forge's output pipes, so killing Forge alone is
    insufficient to release the readers or stop the deployment machinery.

    :param process_file: Parent and child identifiers written before waiting.
    :param parent_exits: Whether the child retains pipes after Forge exits.
    :return: ``None`` after checking timeout reporting and process termination.
    """
    program = "import json, os, subprocess, sys, time; from pathlib import Path; child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); Path(sys.argv[1]).write_text(json.dumps([os.getpid(), child.pid])); " + ("pass" if parent_exits else "time.sleep(30)")
    with pytest.raises(ForgeFailed, match="timed out") as error:
        _exec_cmd([sys.executable, "-c", program, str(process_file), "fixture-private-key"], "forge create [redacted]", timeout=2)
    assert "fixture-private-key" not in str(error.value)
    pids = json.loads(process_file.read_text())
    processes = []
    for pid in pids:
        try:
            processes.append(psutil.Process(pid))
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(processes, timeout=2)
    for process in processes:
        try:
            assert not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            pass


def test_forge_parse_failure_uses_censored_command() -> None:
    """Keep private command arguments out of malformed-output errors.

    Forge's real invocation includes a private key, unlike the diagnostic
    command supplied separately by the deployment helper.

    :return: ``None`` after checking that only the censored command is shown.
    """
    with pytest.raises(ForgeFailed) as error:
        _exec_cmd([sys.executable, "-c", "pass", "fixture-private-key"], "forge create [redacted]")
    assert "fixture-private-key" not in str(error.value)
    assert "forge create [redacted]" in str(error.value)


@pytest.mark.skipif(os.name != "posix", reason="Send SIGINT to the Python caller on POSIX")
def test_forge_interrupt_stops_command(process_file: Path) -> None:
    """Stop the isolated command when its caller is interrupted.

    The child signals its Python parent after recording its identifier, so
    interruption is exercised inside the real blocking communicate call.

    :param process_file: Child identifier retained for failure cleanup.
    :return: ``None`` after checking interruption propagation and termination.
    """
    program = "import json, os, signal, sys, time; from pathlib import Path; Path(sys.argv[1]).write_text(json.dumps([os.getpid()])); os.kill(os.getppid(), signal.SIGINT); time.sleep(30)"
    with pytest.raises(KeyboardInterrupt):
        _exec_cmd([sys.executable, "-c", program, str(process_file)], "synthetic interrupt", timeout=5)
    for pid in json.loads(process_file.read_text()):
        assert not psutil.pid_exists(pid)
