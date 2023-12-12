"""Bunch of random utilities."""
import calendar
import datetime
import logging
import random
import socket
import time
from typing import Optional, Tuple
from urllib.parse import urlparse

import psutil


logger = logging.getLogger(__name__)


#: Ethereum 0x0000000000000000000000000000000000000000 address as a string
ZERO_ADDRESS_STR = "0x0000000000000000000000000000000000000000"


def sanitise_string(s: str) -> str:
    """Remove null characters."""
    # https://stackoverflow.com/a/18762899/315168
    return s.replace("\x00", "\U0000FFFD")


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
) -> Tuple[bytes, bytes]:
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


def get_url_domain(url: str) -> str:
    """Redact URL so that only domain is displayed.

    Some services e.g. infura use path as an API key.
    """
    parsed = urlparse(url)
    if parsed.port in (80, 443, None):
        return parsed.hostname
    else:
        return f"{parsed.hostname}:{parsed.port}"
