"""Get Enzyme deployment on Arbitrum.

- Use Arbitrum live RPC for testing.

"""
import os

import pytest
from web3 import Web3

from eth_defi.enzyme.deployment import EnzymeDeployment, ARBITRUM_DEPLOYMENT
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
pytestmark = pytest.mark.skipif(not JSON_RPC_ARBITRUM, reason="Set JSON_RPC_ARBITRUM to run this test")


@pytest.fixture()
def web3():
    web3 = create_multi_provider_web3(JSON_RPC_ARBITRUM)
    return web3


def test_fetch_enzyme_on_arbitrum(
    web3: Web3,
):
    """Fetch Enzyme deployment."""
    deployment = EnzymeDeployment.fetch_deployment(web3, ARBITRUM_DEPLOYMENT)
    assert deployment.mln.functions.symbol().call() == "MLN"
    assert deployment.weth.functions.symbol().call() == "WETH"
