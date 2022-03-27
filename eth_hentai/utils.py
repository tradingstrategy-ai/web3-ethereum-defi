"""Bunch of random utilities."""
import itertools
import socket
from typing import Iterable, List


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


def grouper(size: int, iterable: Iterable) -> Iterable[List]:
    """Split a long list to iterable chunks.

    `See this StackOverflow answer for more information <https://stackoverflow.com/a/10791887/315168>`_.

    :param size: The chunk size
    :param iterable: Any Python iterable
    :return: Iterable of list of chunks
    """
    it = iter(iterable)
    while True:
        group = tuple(itertools.islice(it, None, size))
        if not group:
            break
        yield group
