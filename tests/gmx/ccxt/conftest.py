"""GMX on Arbitrum fixtures.

- Set up GMX CCXT adapter using Arbitrum configuration.
"""

import pytest

from eth_defi.gmx.api import GMXAPI
from eth_defi.gmx.ccxt.exchange import GMX


@pytest.fixture
def ccxt_gmx_arbitrum(arbitrum_fork_config) -> GMX:
    """Create CCXT GMX exchange on Arbitrum.

    - Uses block TODO
    - Uses mocks TODO
    """
    api = GMXAPI(config=arbitrum_fork_config)
    return GMX(api=api)
