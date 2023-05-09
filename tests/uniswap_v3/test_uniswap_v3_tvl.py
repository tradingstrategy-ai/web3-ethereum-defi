"""Fetch Uniswap v3 TVL and market depths.

Performed using live Polygon mainnet archive node.
"""
import os
from decimal import Decimal

import pytest
from web3 import HTTPProvider, Web3

from eth_defi.chain import install_chain_middleware, install_retry_middleware
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.uniswap_v3.pool import PoolDetails, fetch_pool_details
from eth_defi.uniswap_v3.tvl import fetch_uniswap_v3_pool_tvl

JSON_RPC_POLYGON_ARCHIVE = os.environ.get("JSON_RPC_POLYGON_ARCHIVE")

pytestmark = pytest.mark.skipif(not JSON_RPC_POLYGON_ARCHIVE, reason="This test needs Polygon archive node via JSON_RPC_POLYGON_ARCHIVE")


@pytest.fixture()
def web3():
    """Live Polygon web3 instance."""
    assert JSON_RPC_POLYGON_ARCHIVE
    web3 = Web3(HTTPProvider(JSON_RPC_POLYGON_ARCHIVE))
    web3.middleware_onion.clear()
    install_chain_middleware(web3)
    install_retry_middleware(web3)
    return web3


@pytest.fixture()
def usdc(web3) -> PoolDetails:
    """Get USDC on Polygon."""
    return fetch_erc20_details(web3, "0x2791bca1f2de4661ed88a30c99a7a9449aa84174")


@pytest.fixture()
def matic_usdc_pool(web3) -> PoolDetails:
    """Get WMATIC-USDC pool.

    https://tradingstrategy.ai/trading-view/polygon/uniswap-v3/matic-usdc-fee-5
    """
    pool = fetch_pool_details(web3, "0xa374094527e1673a86de625aa59517c5de346d32")
    return pool


def test_fetch_current_tvl(
    matic_usdc_pool: PoolDetails,
    usdc: TokenDetails,
):
    """Fetch WMATIC-USDC TVL."""

    usdc_tvl = fetch_uniswap_v3_pool_tvl(matic_usdc_pool, usdc)
    assert usdc_tvl > 100_000, f"Hoped we have at least $100,00 USDC locked up at 5 BPS pool"


def test_fetch_historic_tvl(
    matic_usdc_pool: PoolDetails,
    usdc: TokenDetails,
):
    """Fetch WMATIC-USDC TVL."""
    usdc_tvl = fetch_uniswap_v3_pool_tvl(matic_usdc_pool, usdc, block_identifier=26_000_000)
    assert usdc_tvl == pytest.approx(Decimal("1975846.143616"))  # The exact amount of USDC locked at that block
