"""Check if we have zombine Ganache around"""

from eth_defi.utils import is_localhost_port_listening
import socket
from contextlib import closing

port = 19999
host = "127.0.0.1"

with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
    if sock.connect_ex((host, port)) == 0:
        print("Port is listening")
    else:
        print("Port is closed")


if is_localhost_port_listening(19999):
    print(f"{port} is listening")
else:
    print(f"{port} is closed")
