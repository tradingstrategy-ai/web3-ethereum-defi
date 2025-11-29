"""GMX on Arbitrum fixtures.

- Set up GMX CCXT adapter using Arbitrum configuration.
"""

import pytest
from web3 import Web3

from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.config import GMXConfig


@pytest.fixture
def web3():
    return Web3(Web3.HTTPProvider("https://arb1.arbitrum.io/rpc"))


@pytest.fixture
def ccxt_gmx_arbitrum(arbitrum_fork_config) -> GMX:
    """Create CCXT GMX exchange on Arbitrum using fork config.

    - Uses block TODO
    - Uses mocks TODO
    """
    return GMX(config=arbitrum_fork_config)


@pytest.fixture
def gmx_arbitrum(web3) -> GMX:
    """Create GMX exchange instance connected to Arbitrum mainnet.

    Uses live Arbitrum RPC for real API calls.
    """
    config = GMXConfig(web3)
    return GMX(config)
