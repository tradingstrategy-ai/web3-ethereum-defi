"""eth_defi package root.

See :ref:`api documentation` for more details.

"""

import sys


#: Minimum required Python version to run this package
MIN_PYTHON_VERSION = (3, 10)


def _check_python_version():
    """Try early abort if the Python version is too old."""

    # Use Python tuple comparison for version numbers
    # https://stackoverflow.com/a/1093331/315168
    if sys.version_info < MIN_PYTHON_VERSION:
        raise RuntimeError(f"web3-ethereum-defi needs Python {MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]} or later")


_check_python_version()
