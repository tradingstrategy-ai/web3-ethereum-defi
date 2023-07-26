"""Anvil integration (legacy).

Anvil is a blazing-fast local testnet node implementation in Rust from Foundry project.

.. note ::

    This Python module has been moved to :py:mod:`eth_defi.provider.anvil`.
    See the updated module for full API description.

"""

import warnings

warnings.warn("eth_defi.anvil has been moved to eth_defi.provider.anvil", DeprecationWarning, stacklevel=2)

from eth_defi.provider.anvil import *
