"""Get Enzyme deployment on polygon.

Use Polygon live RPC for testing.

"""
import os

import flaky
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


# Flaky because uses live endpoint
@flaky.flaky()
def test_fetch_enzyme_on_polygon(
    web3: Web3,
):
    """Fetch Enzyme deployment on Polygon."""
    deployment = EnzymeDeployment.fetch_deployment(web3, POLYGON_DEPLOYMENT)
    assert deployment.mln.functions.symbol().call() == "MLN"
    assert deployment.weth.functions.symbol().call() == "WMATIC"
