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
from eth_defi.gmx.valuation import GMXEquity, fetch_gmx_total_equity
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.token import fetch_erc20_details

pytestmark = pytest.mark.skipif(
    not os.environ.get("JSON_RPC_ARBITRUM"),
    reason="JSON_RPC_ARBITRUM environment variable not set",
)

#: Arbitrum USDC (native) address
USDC_ADDRESS = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

#: A known account that holds USDC on Arbitrum at the fork block but has no GMX positions
KNOWN_USDC_HOLDER = "0xEe7aE85f2Fe2239E27D9c1E23fFFe168D63b4055"

#: A known account with open GMX positions and USDC reserves at the fork block.
#: Has 9 USDC-collateralised positions across multiple markets.
ACCOUNT_WITH_POSITIONS = "0x1640e916e10610Ba39aAC5Cd8a08acF3cCae1A4c"

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

    At block 401_729_535, the USDC whale has ~$294M USDC and zero GMX positions.
    Positions subtotal should be zero; total equals reserves.
    """
    result = fetch_gmx_total_equity(
        web3=web3,
        account=KNOWN_USDC_HOLDER,
        reserve_tokens=[usdc],
    )
    assert isinstance(result, GMXEquity)
    assert result.reserves == pytest.approx(Decimal("294_462_201.855947"), rel=Decimal("0.001"))
    assert result.positions == Decimal(0)
    assert result.get_total() == result.reserves


def test_fetch_gmx_total_equity_with_positions(web3, usdc):
    """Test equity for an account with USDC reserves AND open GMX positions.

    At block 401_729_535, this account has ~$978K USDC in wallet and
    9 USDC-collateralised perpetual positions with ~$272K total collateral.
    Position values include unrealised PnL so positions > collateral alone.

    Note: PnL uses live oracle prices, so positions value is approximate
    while reserves are deterministic.
    """
    result = fetch_gmx_total_equity(
        web3=web3,
        account=ACCOUNT_WITH_POSITIONS,
        reserve_tokens=[usdc],
    )
    assert isinstance(result, GMXEquity)

    # Reserves are deterministic at the fork block
    assert result.reserves == pytest.approx(Decimal("978_163.293624"), rel=Decimal("0.001"))

    # Positions must be positive (collateral alone is ~$272K)
    assert result.positions > Decimal("200_000")

    # Total = reserves + positions
    assert result.get_total() == result.reserves + result.positions
    assert result.get_total() > Decimal("1_100_000")


def test_fetch_gmx_total_equity_empty_account(web3, usdc):
    """Test equity for an account with no reserves and no positions returns zero."""
    empty_account = "0xdead000000000000000000000000000000000042"

    result = fetch_gmx_total_equity(
        web3=web3,
        account=empty_account,
        reserve_tokens=[usdc],
    )
    assert result.reserves == Decimal(0)
    assert result.positions == Decimal(0)
    assert result.get_total() == Decimal(0)
