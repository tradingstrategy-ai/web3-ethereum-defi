"""Local revision checks for the vault scanner's atomic artefacts."""

import json
import os
from pathlib import Path
from typing import Any

from atomicwrites import atomic_write

#: Raw Parquet footer records the revisions that produced its native scopes.
NATIVE_REVISIONS_KEY = b"vault.native_revisions"


def file_revision(path: Path) -> list[int | str] | None:
    """Identify a local file generation without reading its contents.

    Include the inode and change time so atomic replacements, copies and
    in-place writes invalidate previously applied state.

    :param path: Scanner input or output file.
    :return: File identity, or `None` when absent.
    """
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return [str(path.resolve()), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def duckdb_revision(path: Path) -> list[list[int | str] | None]:
    """Include committed data still present in the DuckDB write-ahead log.

    Scanner callers capture this under the pipeline writer lock.

    :param path: DuckDB source file.
    :return: Revisions of the database and its write-ahead log.
    """
    return [file_revision(path), file_revision(path.with_suffix(path.suffix + ".wal"))]


def read_publication_state(path: Path) -> dict[str, Any]:
    """Read an optional checkpoint, using a rebuild for unrecognised state.

    :param path: Local checkpoint beside the output.
    :return: Saved state or an empty dictionary.
    """
    try:
        state = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def write_publication_state(path: Path, state: dict[str, Any]) -> None:
    """Record completed output generations after their atomic publication.

    :param path: Local checkpoint beside the output.
    :param state: JSON-serialisable revision and output identities.
    """
    with atomic_write(str(path), overwrite=True) as output:
        json.dump(state, output, separators=(",", ":"))


def publish_verified_parquet(staged: Path, destination: Path) -> None:
    """Flush a verified sibling file and publish it atomically.

    The directory flush orders the rename before a caller acknowledges the
    new generation in a separate checkpoint.

    :param staged: Verified temporary Parquet on the destination filesystem.
    :param destination: Path at which readers open the published file.
    """
    with staged.open("rb") as source:
        os.fsync(source.fileno())
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.replace(staged, destination)
        os.fsync(directory)
    finally:
        os.close(directory)
