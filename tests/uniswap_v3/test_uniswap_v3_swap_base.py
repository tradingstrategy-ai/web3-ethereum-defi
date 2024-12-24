"""Swap using our in-house deployed SwapRouter02 on base."""
import os

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS
from eth_defi.uniswap_v3.deployment import (
    fetch_deployment,
)

from eth_defi.uniswap_v3.swap import swap_with_slippage_protection

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

CI = os.environ.get("CI", None) is not None

pytestmark = pytest.mark.skipif(not JSON_RPC_BASE, reason="No JSON_RPC_BASE environment variable")



@pytest.fixture()
def usdc_holder() -> HexAddress:
    # https://basescan.org/token/0x833589fcd6edb6e08f4c7c32d4f71b54bda02913#balances
    return "0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A"



@pytest.fixture()
def anvil_base_fork(request, usdc_holder) -> AnvilLaunch:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    assert JSON_RPC_BASE, "JSON_RPC_BASE not set"
    launch = fork_network_anvil(
        JSON_RPC_BASE,
        unlocked_addresses=[usdc_holder],
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def web3(anvil_base_fork) -> Web3:
    """Create a web3 connector.

    - By default use Anvil forked Base

    - Eanble Tenderly testnet with `JSON_RPC_TENDERLY` to debug
      otherwise impossible to debug Gnosis Safe transactions
    """

    tenderly_fork_rpc = os.environ.get("JSON_RPC_TENDERLY", None)

    if tenderly_fork_rpc:
        web3 = create_multi_provider_web3(tenderly_fork_rpc)
    else:
        web3 = create_multi_provider_web3(
            anvil_base_fork.json_rpc_url,
        )
    assert web3.eth.chain_id == 8453
    return web3


@pytest.fixture()
def uniswap_v3(web3):
    deployment_data = UNISWAP_V3_DEPLOYMENTS["base"]
    uniswap_v3_on_base = fetch_deployment(
        web3,
        factory_address=deployment_data["factory"],
        router_address=deployment_data["router"],
        position_manager_address=deployment_data["position_manager"],
        quoter_address=deployment_data["quoter"],
        quoter_v2=deployment_data["quoter_v2"],
        router_v2=deployment_data["router_v2"],
    )
    return uniswap_v3_on_base


def test_uniswap_v3_swap_on_base(
    web3,
    uniswap_v3,
    usdc_holder,
):

    input_token = fetch_erc20_details(web3, "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")  # USDC
    output_token = fetch_erc20_details(web3, "0x4200000000000000000000000000000000000006")  # WETH

    amount = 5 * 10**6
    tx_hash = input_token.contract.functions.approve(uniswap_v3.swap_router.address, amount).transact({"from": usdc_holder})
    assert_transaction_success_with_explanation(web3, tx_hash)

    bound_call = swap_with_slippage_protection(
        uniswap_v3,
        quote_token=input_token.contract,
        base_token=output_token.contract,
        pool_fees=[500],
        recipient_address=usdc_holder,
        amount_in=amount,
    )

    tx_hash = bound_call.transact({"from": usdc_holder})
    assert_transaction_success_with_explanation(web3, tx_hash)

