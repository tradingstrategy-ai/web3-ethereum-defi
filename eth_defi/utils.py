"""Bunch of random utilities."""
import socket


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
