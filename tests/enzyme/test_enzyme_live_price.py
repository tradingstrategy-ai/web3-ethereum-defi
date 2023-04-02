"""Get price using one of Enzyme's configured price feeds.

Use Polygon live RPC for testing.

"""
import os

import pytest
from web3 import Web3, HTTPProvider

from eth_defi.chain import install_chain_middleware, install_retry_middleware
from eth_defi.enzyme.deployment import EnzymeDeployment, POLYGON_DEPLOYMENT
from eth_defi.enzyme.price_feed import EnzymePriceFeed
from eth_defi.token import fetch_erc20_details

JSON_RPC_POLYGON = os.environ.get("JSON_RPC_POLYGON", "https://polygon-rpc.com")


@pytest.fixture()
def web3():
    """Live Polygon web3 instance."""
    web3 = Web3(HTTPProvider(JSON_RPC_POLYGON))
    web3.middleware_onion.clear()
    install_chain_middleware(web3)
    install_retry_middleware(web3)
    return web3


def test_fetch_onchain_price(
    web3: Web3,
):
    """Fetch linve on-chain price for ETH/USDC on Polygon."""
    deployment = EnzymeDeployment.fetch_deployment(web3, POLYGON_DEPLOYMENT)
    usdc = fetch_erc20_details(web3, POLYGON_DEPLOYMENT["usdc"])
    feed = EnzymePriceFeed.fetch_price_feed(deployment, usdc)

    price = feed.calculate_current_onchain_price(usdc)
    assert 0.9 < price < 1.1
