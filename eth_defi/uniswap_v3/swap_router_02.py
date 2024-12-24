"""Uniswap legacy compatibility SwapRouter02"""
import os

from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.uniswap_v2.constants import UNISWAP_V2_DEPLOYMENTS
from eth_defi.uniswap_v3.constants import UNISWAP_V3_DEPLOYMENTS
from eth_defi.uniswap_v3.deployment import fetch_deployment

JSON_RPC_BASE = os.environ["JSON_RPC_BASE"]


def deploy_swap_router_02():
    """Deploy SwapRouter02 on base.

    - Because Uniswap did not do it themselves

    - Allows us to run the legacy code
    """

    web3 = create_multi_provider_web3(JSON_RPC_BASE)

    uniswap_v2 = fetch_deployment(
        web3,
        factory_address=UNISWAP_V2_DEPLOYMENTS["base"]["factory"],
        router_address=UNISWAP_V2_DEPLOYMENTS["base"]["router"],
        init_code_hash=UNISWAP_V2_DEPLOYMENTS["base"]["init_code_hash"],
    )

    uniswap_v3_factory = UNISWAP_V3_DEPLOYMENTS["base"]["factory"]
    uniswap_v3_position_manager = UNISWAP_V3_DEPLOYMENTS["base"]["position_manager"]