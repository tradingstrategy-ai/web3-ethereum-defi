"""Bunch of random utilities."""

import calendar
import datetime
import random
import socket
import time
from itertools import islice
from typing import Optional
from urllib.parse import urlparse
import logging
import os
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock
import psutil

from eth_typing import HexAddress, HexStr


logger = logging.getLogger(__name__)


def sanitise_string(s: str, max_length: int | None = None) -> str:
    """Remove null characters."""
    # https://stackoverflow.com/a/18762899/315168
    fixed = s.replace("\x00", "\U0000FFFD")  # fmt: off
    if max_length is not None:
        return fixed[0:max_length]
    return fixed


def is_localhost_port_listening(port: int, host="localhost") -> bool:
    """Check if a localhost is running a server already.

    See https://www.adamsmith.haus/python/answers/how-to-check-if-a-network-port-is-open-in-python

    :return: True if there is a process occupying the port
    """

    a_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    location = (host, port)
    result_of_check = a_socket.connect_ex(location)
    return result_of_check == 0


def find_free_port(min_port: int = 20_000, max_port: int = 40_000, max_attempt: int = 20) -> int:
    """Find a free localhost port to bind.

    Does by random.

    .. note ::

        Subject to race condition, but should be rareish.

    :param min_port:
        Minimum port range

    :param max_port:
        Maximum port range

    :param max_attempt:
        Give up and die with an exception if no port found after this many attempts.

    :return:
        Free port number
    """

    assert type(min_port) == int
    assert type(max_port) == int
    assert type(max_attempt) == int

    for attempt in range(0, max_attempt):
        random_port = random.randrange(start=min_port, stop=max_port)
        logger.info("Attempting to allocate port %d to Anvil", random_port)
        if not is_localhost_port_listening(random_port, "127.0.0.1"):
            return random_port

    raise RuntimeError(f"Could not open a port with a spec: {min_port} - {max_port}, {max_attempt} attempts")


def shutdown_hard(
    process: psutil.Popen,
    log_level: Optional[int] = None,
    block=True,
    block_timeout=30,
    check_port: Optional[int] = None,
) -> tuple[bytes, bytes]:
    """Kill Psutil process.

    - Straight out OS `SIGKILL` a process

    - Log output if necessary

    - Use port listening to check that the process goes down
      and frees its ports

    :param process:
        Process to kill

    :param block:
        Block the execution until the process has terminated.

        You must give `check_port` option to ensure we enforce the shutdown.

    :param block_timeout:
        How long we give for process to clean up after itself

    :param log_level:
        If set, dump anything in Anvil stdout to the Python logging using level `INFO`.

    :param check_port:
        Check that TCP/IP localhost port is freed after shutdown

    :return:
        stdout, stderr as string
    """

    stdout = b""
    stderr = b""

    if process.poll() is None:
        # Still alive, we need to kill to read the output
        process.kill()

    for line in process.stdout.readlines():
        stdout += line
        if log_level is not None:
            logger._log(log_level, "stdout: %s", line.decode("utf-8").strip())

    for line in process.stderr.readlines():
        stderr += line
        if log_level is not None:
            logger._log(log_level, "stderr: %s", line.decode("utf-8").strip())

    if block:
        assert check_port is not None, "Give check_port to block the execution"
        deadline = time.time() + 30
        while time.time() < deadline:
            if not is_localhost_port_listening(check_port):
                # Port released, assume Anvil/Ganache is gone
                return stdout, stderr

        raise AssertionError(f"Could not terminate Anvil in {block_timeout} seconds, stdout is %d bytes, stderr is %d bytes", len(stdout), len(stderr))

    return stdout, stderr


def to_unix_timestamp(dt: datetime.datetime) -> float:
    """Convert Python UTC datetime to UNIX seconds since epoch.

    Example:

    .. code-block:: python

        import datetime
        from eth_defi.utils import to_unix_timestamp

        dt = datetime.datetime(1970, 1, 1)
        unix_time = to_unix_timestamp(dt)
        assert unix_time == 0

    :param dt:
        Python datetime to convert

    :return:
        Datetime as seconds since 1970-1-1
    """
    # https://stackoverflow.com/a/5499906/315168
    return calendar.timegm(dt.utctimetuple())


def from_unix_timestamp(timestamp: float) -> datetime.datetime:
    """Convert UNIX seconds since epoch to naive Python datetime."""
    assert type(timestamp) in (int, float), f"Got {type(timestamp)}: {timestamp}"
    return datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc).replace(tzinfo=None)


def get_url_domain(url: str) -> str:
    """Redact URL so that only domain is displayed.

    Some services e.g. infura use path as an API key.
    """
    parsed = urlparse(url)
    if parsed.port in (80, 443, None):
        return parsed.hostname
    else:
        return f"{parsed.hostname}:{parsed.port}"


def setup_console_logging(
    default_log_level="warning",
    simplified_logging=False,
    log_file: str | Path = None,
    std_out_log_level: Optional[int] = None,
    only_log_file=False,
    clear_log_file=True,
) -> logging.Logger:
    """Set up coloured log output.

    - Helper function to have nicer logging output in tutorial scripts.
    - Tune down some noisy dependency library logging

    :param log_file:
        Output both console and this log file.

    :return:
        Root logger
    """

    level = os.environ.get("LOG_LEVEL", default_log_level).upper()
    numeric_level = getattr(logging, level.upper(), None)

    if not std_out_log_level:
        std_out_log_level = numeric_level

    if simplified_logging:
        # Simplified logging format for tutorials
        fmt = "%(message)s"
        date_fmt = "%H:%M:%S"
    else:
        fmt = "%(asctime)s %(name)-44s %(message)s"
        date_fmt = "%H:%M:%S"

    try:
        # Optional dev dependency
        import coloredlogs

        coloredlogs.install(level=std_out_log_level, fmt=fmt, date_fmt=date_fmt)
    except ImportError as e:
        # non-ANSI e.g. Docker

        assert numeric_level, f"No level: {level}"
        logging.basicConfig(level=std_out_log_level, format=fmt, datefmt=date_fmt)

    if log_file:
        assert isinstance(log_file, Path), "log_file must be a string path"

        log_file.parent.mkdir(parents=True, exist_ok=True)

        # When using a file, the file is always logged with INFO level and
        # env var controls only terminal output
        min_level = min(logging.INFO, numeric_level)
        if clear_log_file:
            mode = "w"
        else:
            mode = "a"

        file_handler = logging.FileHandler(log_file, mode=mode, encoding="utf-8")
        file_handler.setLevel(min_level)
        file_handler.setFormatter(logging.Formatter(fmt, date_fmt))

        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(min_level)
        root.addHandler(file_handler)

        if not only_log_file:
            stream_handler = logging.StreamHandler()
            stream_handler.setLevel(numeric_level)
            stream_handler.setFormatter(logging.Formatter(fmt, date_fmt))
            root.addHandler(stream_handler)

    else:
        logging.basicConfig(level=level, handlers=[logging.StreamHandler()])

    # Mute noise
    logging.getLogger("web3.providers.HTTPProvider").setLevel(logging.WARNING)
    logging.getLogger("web3.RequestManager").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    logging.getLogger("eth_defi.token").setLevel(logging.WARNING)
    return logging.getLogger()


def chunked(iterable, chunk_size):
    iterator = iter(iterable)  # Ensure we have an iterator
    while True:
        chunk = list(islice(iterator, chunk_size))
        if not chunk:  # Break if no more items
            break
        yield chunk


def addr(address: str | HexAddress | HexStr) -> HexAddress:
    """
    Convert various address formats to HexAddress.

    Args:
        address: Can be a string, HexAddress, or HexStr

    Returns:
        HexAddress object
    """
    if isinstance(address, (str, HexStr)):
        return HexAddress(HexStr(address))
    else:
        return address


@contextmanager
def wait_other_writers(path: Path | str, timeout: int = 120):
    """Wait other potential writers writing the same file.

    - Work around issues when parallel unit tests and such
      try to write the same file

    Example:

    .. code-block:: python

        import urllib
        import tempfile

        import pytest
        import pandas as pd


        @pytest.fixture()
        def my_cached_test_data_frame() -> pd.DataFrame:
            # Al tests use a cached dataset stored in the /tmp directory
            path = os.path.join(tempfile.gettempdir(), "my_shared_data.parquet")

            with wait_other_writers(path):
                # Read result from the previous writer
                if not path.exists(path):
                    # Download and write to cache
                    urllib.request.urlretrieve("https://example.com", path)

                return pd.read_parquet(path)

    :param path:
        File that is being written

    :param timeout:
        How many seconds wait to acquire the lock file.

        Default 2 minutes.

    :raise filelock.Timeout:
        If the file writer is stuck with the lock.
    """

    if isinstance(path, str):
        path = Path(path)

    assert isinstance(path, Path), f"Not Path object: {path}"

    assert path.is_absolute(), f"Did not get an absolute path: {path}\nPlease use absolute paths for lock files to prevent polluting the local working directory."

    # If we are writing to a new temp folder, create any parent paths
    os.makedirs(path.parent, exist_ok=True)

    # https://stackoverflow.com/a/60281933/315168
    lock_file = path.parent / (path.name + ".lock")

    lock = FileLock(lock_file, timeout=timeout)

    if lock.is_locked:
        logger.info(
            "Parquet file %s locked for writing, waiting %f seconds",
            path,
            timeout,
        )

    with lock:
        yield
