"""Test GMX account valuation.

Tests :py:func:`eth_defi.gmx.valuation.fetch_gmx_total_equity` on an
Anvil mainnet fork at a fixed block number.

Requires ``JSON_RPC_ARBITRUM`` environment variable pointing to an archive node.
"""

import logging
import os
from decimal import Decimal

import pytest
from web3 import HTTPProvider, Web3

from eth_defi.chain import install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.gmx.valuation import fetch_gmx_total_equity
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.token import fetch_erc20_details

pytestmark = pytest.mark.skipif(
    not os.environ.get("JSON_RPC_ARBITRUM"),
    reason="JSON_RPC_ARBITRUM environment variable not set",
)

#: Arbitrum USDC (native) address
USDC_ADDRESS = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

#: A known account that holds USDC on Arbitrum at the fork block
KNOWN_USDC_HOLDER = "0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055"

#: Fixed fork block for deterministic tests
FORK_BLOCK = 401_729_535


@pytest.fixture()
def anvil_arbitrum():
    """Launch an Anvil mainnet fork of Arbitrum at a fixed block."""
    rpc_url = os.environ["JSON_RPC_ARBITRUM"]
    launch = fork_network_anvil(
        rpc_url,
        fork_block_number=FORK_BLOCK,
        test_request_timeout=100,
        launch_wait_seconds=60,
    )
    try:
        yield launch.json_rpc_url
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3(anvil_arbitrum):
    """Web3 connected to the Anvil fork."""
    web3 = Web3(HTTPProvider(anvil_arbitrum, request_kwargs={"timeout": 100}))
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    return web3


@pytest.fixture()
def usdc(web3):
    return fetch_erc20_details(web3, USDC_ADDRESS)


def test_fetch_gmx_total_equity_no_positions(web3, usdc):
    """Test equity for an account with USDC reserves and no GMX positions.

    At block 401_729_535, the known USDC holder has a deterministic balance.
    """
    equity = fetch_gmx_total_equity(
        web3=web3,
        account=KNOWN_USDC_HOLDER,
        denomination_token=usdc,
        reserve_tokens=[usdc],
    )
    assert isinstance(equity, Decimal)
    assert equity == pytest.approx(Decimal("294_462_201.855947"), rel=Decimal("0.001"))


def test_fetch_gmx_total_equity_at_block(web3, usdc):
    """Test equity at the fork block explicitly passed as block_identifier."""
    equity = fetch_gmx_total_equity(
        web3=web3,
        account=KNOWN_USDC_HOLDER,
        denomination_token=usdc,
        reserve_tokens=[usdc],
        block_identifier=FORK_BLOCK,
    )
    assert isinstance(equity, Decimal)
    assert equity == pytest.approx(Decimal("294_462_201.855947"), rel=Decimal("0.001"))


def test_fetch_gmx_total_equity_empty_account(web3, usdc):
    """Test equity for an account with no reserves and no positions returns zero."""
    empty_account = "0xdead000000000000000000000000000000000042"

    equity = fetch_gmx_total_equity(
        web3=web3,
        account=empty_account,
        denomination_token=usdc,
        reserve_tokens=[usdc],
    )
    assert equity == Decimal(0)
