"""Get price using one of Enzyme's configured price feeds.

Use Polygon live RPC for testing.

"""
import os

import pytest
from web3 import Web3, HTTPProvider

from eth_defi.chain import install_chain_middleware, install_retry_middleware
from eth_defi.enzyme.deployment import EnzymeDeployment, POLYGON_DEPLOYMENT


JSON_RPC_POLYGON = os.environ.get("JSON_RPC_POLYGON", "https://polygon-rpc.com")


@pytest.fixture()
def web3():
    """Live Polygon web3 instance."""
    web3 = Web3(HTTPProvider(JSON_RPC_POLYGON))
    web3.middleware_onion.clear()
    install_chain_middleware(web3)
    install_retry_middleware(web3)
    return web3


def test_resolve_price_feed(
    web3: Web3,
):
    """Resolve Enzyme price feed."""
    deployment = EnzymeDeployment.fetch_deployment(web3, POLYGON_DEPLOYMENT)
    weth_address = POLYGON_DEPLOYMENT["weth"]
    deployment.resolve_usd_price_feed(weth_address)


def test_fetch_price(
    web3: Web3,
):
    """Fetch Enzyme price for ETH on Polygon."""
    deployment = EnzymeDeployment.fetch_deployment(web3, POLYGON_DEPLOYMENT)
    deployment.fetch_usd_price(POLYGON_DEPLOYMENT["weth"])



