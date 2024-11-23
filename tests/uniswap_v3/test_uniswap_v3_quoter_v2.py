"""Base + Uniswap Quoter v2 tests.

- Uses live Base Uniswap v3 deployment
"""
import os
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS
from eth_defi.uniswap_v3.deployment import fetch_deployment
from eth_defi.uniswap_v3.price import estimate_buy_received_amount, estimate_sell_received_amount

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE", "https://mainnet.base.org")

pytestmark = pytest.mark.skipif(not JSON_RPC_BASE, reason="No JSON_RPC_BASE environment variable")


@pytest.fixture()
def web3() -> Web3:
    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    assert web3.eth.chain_id == 8453
    return web3


def test_fetch_weth_usdc_buy(web3: Web3):
    """Fetch buy price for WETH/SUDC trade

    - Uses QuoterV2
    """

    # https://docs.uniswap.org/contracts/v3/reference/deployments/base-deployments
    deployment_data = UNISWAP_V3_DEPLOYMENTS["base"]
    uniswap_v3_on_base = fetch_deployment(
        web3,
        factory_address=deployment_data["factory"],
        router_address=deployment_data["router"],
        position_manager_address=deployment_data["position_manager"],
        quoter_address=deployment_data["quoter"],
        quoter_v2=deployment_data["quoter_v2"],
    )

    # https://coinmarketcap.com/dexscan/base/0xd0b53d9277642d899df5c87a3966a349a798f224
    amount = estimate_buy_received_amount(
        uniswap=uniswap_v3_on_base,
        base_token_address="0x4200000000000000000000000000000000000006",
        quote_token_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        quantity=1 * 10**6,
        target_pair_fee=5 * 100,  # 100 units = 1 BPS
    )

    amount = Decimal(amount) / Decimal(10**18)
    usdc_price = 1 / amount
    assert 1000 < usdc_price < 10_000


def test_fetch_weth_usdc_sell(web3: Web3):
    """Fetch sell price for WETH/SUDC trade

    - Uses QuoterV2
    """

    # https://docs.uniswap.org/contracts/v3/reference/deployments/base-deployments
    deployment_data = UNISWAP_V3_DEPLOYMENTS["base"]
    uniswap_v3_on_base = fetch_deployment(
        web3,
        factory_address=deployment_data["factory"],
        router_address=deployment_data["router"],
        position_manager_address=deployment_data["position_manager"],
        quoter_address=deployment_data["quoter"],
        quoter_v2=deployment_data["quoter_v2"],
    )

    # https://coinmarketcap.com/dexscan/base/0xd0b53d9277642d899df5c87a3966a349a798f224
    amount = estimate_sell_received_amount(
        uniswap=uniswap_v3_on_base,
        base_token_address="0x4200000000000000000000000000000000000006",
        quote_token_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        quantity=1 * 10**18,  # 1 WETH
        target_pair_fee=5 * 100,  # 100 units = 1 BPS
    )

    usdc_price = Decimal(amount) / Decimal(10**6)
    assert 1000 < usdc_price < 10_000


def test_fetch_three_hop_doginme_price_buy(web3: Web3):
    """Fetch price for DogInMe token.

    - Uses QuoterV2

    - Use intermediary pair

    - USDC->WETH->DogMeIn
    """

    deployment_data = UNISWAP_V3_DEPLOYMENTS["base"]
    uniswap_v3_on_base = fetch_deployment(
        web3,
        factory_address=deployment_data["factory"],
        router_address=deployment_data["router"],
        position_manager_address=deployment_data["position_manager"],
        quoter_address=deployment_data["quoter"],
        quoter_v2=deployment_data["quoter_v2"],
    )

    # Pools https://app.uniswap.org/explore/tokens/base/0x6921b130d297cc43754afba22e5eac0fbf8db75b
    #
    amount = estimate_buy_received_amount(
        uniswap=uniswap_v3_on_base,
        base_token_address="0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",  # DogInMe
        quote_token_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC
        intermediate_token_address="0x4200000000000000000000000000000000000006",  # WETH
        quantity=200 * 10**18,
        target_pair_fee=1 * 100 * 100,  # 1% DogInMe pool, 100 units = 1 BPS
        intermediate_pair_fee=5 * 100,  # 5 BPS WETH/USDC pool
    )

    assert amount > 0


def test_fetch_three_hop_doginme_price_sell(web3: Web3):
    """Fetch price for DogInMe token, selling.

    - Uses QuoterV2

    - Use intermediary pair

    - USDC->WETH->DogMeIn
    """

    deployment_data = UNISWAP_V3_DEPLOYMENTS["base"]
    uniswap_v3_on_base = fetch_deployment(
        web3,
        factory_address=deployment_data["factory"],
        router_address=deployment_data["router"],
        position_manager_address=deployment_data["position_manager"],
        quoter_address=deployment_data["quoter"],
        quoter_v2=deployment_data["quoter_v2"],
    )

    # Pools https://app.uniswap.org/explore/tokens/base/0x6921b130d297cc43754afba22e5eac0fbf8db75b
    #
    amount = estimate_sell_received_amount(
        uniswap=uniswap_v3_on_base,
        base_token_address="0x6921B130D297cc43754afba22e5EAc0FBf8Db75b",  # DogInMe
        quote_token_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC
        intermediate_token_address="0x4200000000000000000000000000000000000006",  # WETH
        quantity=200 * 10**18,
        target_pair_fee=1 * 100 * 100,  # 1% DogInMe pool, 100 units = 1 BPS
        intermediate_pair_fee=5 * 100,  # 5 BPS WETH/USDC pool
    )

    assert amount > 0
