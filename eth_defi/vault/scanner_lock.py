"""Process-lifetime exclusion between looping scanners and migrations.

Linux ``flock`` on the shared pipeline directory also works across Docker
bind mounts. Unlike a PID or lock file it leaves no persistent preview output.
See https://man7.org/linux/man-pages/man2/flock.2.html.
"""

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class ScannerDaemonRunning(RuntimeError):
    """The daemon or another migration owns the pipeline lifecycle guard."""


@contextmanager
def acquire_scanner_lifecycle_guard(data_dir: Path) -> Iterator[None]:
    """Exclude daemon activity for the complete lifetime of an operation.

    Keep this guard across sleep periods and acquire it before any pipeline
    writer lock. The directory must already exist. Errors fail closed; the
    kernel releases the lock when its owning descriptors are closed.

    :param data_dir: Shared, existing pipeline directory.
    :return: Context manager holding the exclusive directory lock.
    :raises ScannerDaemonRunning: If another process owns the guard.
    :raises OSError: If opening or locking the directory fails.
    """
    descriptor = os.open(data_dir.resolve(), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ScannerDaemonRunning("Migration/scanner blocked: vault-scanner-looped or another migration owns the lifecycle guard. Stop the scanner daemon before retrying.") from exc
        yield
    finally:
        os.close(descriptor)
