"""Out of gas tests."""

import os

import pytest
from eth_account import Account
from web3 import HTTPProvider, Web3

from eth_defi.confirmation import OutOfGasFunds, wait_and_broadcast_multiple_nodes
from eth_defi.gas import apply_gas, estimate_gas_fees
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details
from eth_defi.uniswap_v2.deployment import UniswapV2Deployment, fetch_deployment

JSON_RPC_POLYGON = os.environ.get("JSON_RPC_POLYGON", "https://polygon-rpc.com")
pytestmark = pytest.mark.skipif(not JSON_RPC_POLYGON, reason="This test needs Polygon node via JSON_RPC_POLYGON")


@pytest.fixture(scope="module")
def web3():
    """Live Polygon web3 instance."""
    web3 = create_multi_provider_web3(os.environ["JSON_RPC_POLYGON"])
    return web3


@pytest.fixture(scope="module")
def quickswap(web3) -> UniswapV2Deployment:
    """Fetch live quickswap deployment.

    See https://docs.quickswap.exchange/concepts/protocol-overview/03-smart-contracts for more information
    """
    deployment = fetch_deployment(
        web3,
        "0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32",
        "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff",
        "0x96e8ac4277198ff8b6f785478aa9a39f403cb768dd02cbee326c3e7da348845f",
    )
    return deployment


@pytest.fixture(scope="module")
def usdc(web3) -> TokenDetails:
    return fetch_erc20_details(web3, "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359")


@pytest.mark.skipif(os.environ.get("JSON_RPC_POLYGON") is None, reason="JSON_RPC_POLYGON needed to run this test")
def test_broadcast_and_wait_multiple_out_of_gas(
    web3: Web3,
    quickswap: UniswapV2Deployment,
    usdc: TokenDetails,
):
    """Detect out of gas."""

    user = Account.create()
    hot_wallet = HotWallet(user)

    hot_wallet.sync_nonce(web3)

    # Do a swap that will fail
    raw_amount = 10
    approve_call = usdc.contract.functions.approve(quickswap.router.address, raw_amount)
    gas_estimation = estimate_gas_fees(web3)
    tx_gas_parameters = apply_gas({"gas": 100_000}, gas_estimation)  # approve should not take more than 100k gas
    signed_tx = hot_wallet.sign_bound_call_with_new_nonce(approve_call, tx_gas_parameters)

    with pytest.raises(OutOfGasFunds):
        wait_and_broadcast_multiple_nodes(
            web3,
            [signed_tx],
            check_nonce_validity=True,
        )
