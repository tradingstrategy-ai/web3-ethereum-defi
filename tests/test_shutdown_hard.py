"""Unit tests for graceful vs hard process shutdown.

Covers :func:`eth_defi.utils.shutdown_hard`, which now optionally sends
``SIGTERM`` and waits for a flush before ``SIGKILL`` — this is what lets Anvil
persist its fork RPC cache (it only writes ``storage.json`` on a graceful exit).
"""

from typing import Sequence
from unittest.mock import Mock

import psutil

from eth_defi.utils import shutdown_hard


def _mock_process(poll_sequence: Sequence[object]) -> Mock:
    """A fake process whose ``poll()`` returns the given sequence."""
    proc = Mock()
    proc.poll.side_effect = list(poll_sequence)
    proc.stdout = None
    proc.stderr = None
    return proc


def test_shutdown_hard_default_is_sigkill() -> None:
    """graceful_timeout=0 (default) hard-kills immediately, never SIGTERM."""
    proc = _mock_process([None, None, 0])
    shutdown_hard(proc, block=False)
    proc.terminate.assert_not_called()
    proc.kill.assert_called_once()


def test_shutdown_graceful_lets_process_exit() -> None:
    """graceful_timeout>0 SIGTERMs and, if the process exits, does not SIGKILL."""
    proc = _mock_process([None, 0, 0])
    shutdown_hard(proc, block=False, graceful_timeout=5.0)
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once()
    proc.kill.assert_not_called()


def test_shutdown_graceful_falls_back_to_sigkill() -> None:
    """If the process ignores SIGTERM within the budget, SIGKILL follows."""
    proc = _mock_process([None, None, 0])
    proc.wait.side_effect = psutil.TimeoutExpired(5)
    shutdown_hard(proc, block=False, graceful_timeout=5.0)
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()
