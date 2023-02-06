"""Bunch of random utilities."""
import logging
import socket
import time
from typing import Optional, Tuple

import psutil


logger = logging.getLogger(__name__)


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
