"""Coordinate timestamp cache access without importing optional DuckDB."""

import contextlib
import fcntl
import os
from collections.abc import Iterator
from pathlib import Path


@contextlib.contextmanager
def timestamp_access_lock(folder: Path, *, exclusive: bool = False) -> Iterator[None]:
    """Exclude replacement while cooperating cache connections remain open.

    The stable directory lock survives database inode replacement. Migration
    callers already own the pipeline lifetime guard and then the writer lock.

    :param folder: Existing cache directory.
    :param exclusive: Refuse concurrent cache users instead of sharing access.
    :return: Context holding a non-blocking advisory lock.
    """
    descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BlockingIOError(f"Timestamp cache in use: {folder}; close all cache users before maintenance") from error
        yield
    finally:
        os.close(descriptor)
