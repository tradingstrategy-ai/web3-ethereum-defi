import sys


# Minimum required Python version
_MIN_VERSION = (3, 9)


def _check_python_version():
    """Try early abort if the Python version is too old."""

    # Use Python tuple comparison for version numbers
    # https://stackoverflow.com/a/1093331/315168
    if sys.version_info < _MIN_VERSION:
        raise RuntimeError(f"web3-ethereum-defi needs Python {_MIN_VERSION[0]}.{_MIN_VERSION[1]} or later")


_check_python_version()
