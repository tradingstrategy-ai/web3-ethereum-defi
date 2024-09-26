"""Get Enzyme deployment on Arbitrum.

Use Arbitrum live RPC for testing.

"""
import os

import flaky
import pytest
from web3 import Web3, HTTPProvider

from eth_defi.chain import install_chain_middleware, install_retry_middleware
from eth_defi.enzyme.deployment import EnzymeDeployment, POLYGON_DEPLOYMENT


JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
pytestmark = pytest.mark.skipif(not JSON_RPC_ARBITRUM, reason="Set JSON_RPC_ARBITRUM to run this test")


@pytest.fixture()
def web3():
    web3 = Web3(HTTPProvider(JSON_RPC_ARBITRUM))
    web3.middleware_onion.clear()
    install_chain_middleware(web3)
    install_retry_middleware(web3)
    return web3


# Flaky because uses live endpoint
@flaky.flaky()
def test_fetch_enzyme_on_arbitrum(
    web3: Web3,
):
    """Fetch Enzyme deployment on Polygon."""
    deployment = EnzymeDeployment.fetch_deployment(web3, POLYGON_DEPLOYMENT)
    assert deployment.mln.functions.symbol().call() == "MLN"
    assert deployment.weth.functions.symbol().call() == "WMATIC"
