"""GMX on Arbitrum fixtures.

- Set up GMX CCXT adapter using Arbitrum configuration.
"""

import logging
import os
from typing import Generator, Any

import pytest
from web3 import Web3

from eth_defi.chain import install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.gmx.ccxt.exchange import GMX
from eth_defi.gmx.config import GMXConfig
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from tests.gmx.conftest import _approve_tokens_for_config
from tests.gmx.fork_helpers import setup_mock_oracle


def _create_anvil_fork(
    chain_name,
    chain_rpc_url,
    large_eth_holder,
    large_wbtc_holder,
    large_wavax_holder,
    large_usdc_holder_arbitrum,
    large_usdc_holder_avalanche,
    large_wbtc_holder_avalanche,
    large_link_holder_avalanche,
    gmx_controller_arbitrum,
    large_weth_holder_arbitrum,
    gmx_keeper_arbitrum,
    large_gm_eth_usdc_holder_arbitrum,
) -> Generator[str, Any, None]:
    """Helper to create a testable fork of the live chain using Anvil."""
    unlocked_addresses = [large_eth_holder, large_wbtc_holder]

    if chain_name == "arbitrum":
        unlocked_addresses.append(large_usdc_holder_arbitrum)
        unlocked_addresses.append(gmx_controller_arbitrum)
        unlocked_addresses.append(large_weth_holder_arbitrum)
        unlocked_addresses.append(gmx_keeper_arbitrum)
        unlocked_addresses.append(large_gm_eth_usdc_holder_arbitrum)
    elif chain_name == "avalanche":
        unlocked_addresses.append(large_wavax_holder)
        unlocked_addresses.append(large_usdc_holder_avalanche)
        unlocked_addresses.append(large_wbtc_holder_avalanche)
        unlocked_addresses.append(large_link_holder_avalanche)

    launch = fork_network_anvil(
        chain_rpc_url,
        unlocked_addresses=unlocked_addresses,
        test_request_timeout=100,
        launch_wait_seconds=60,
    )

    try:
        yield launch.json_rpc_url
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def anvil_chain_fork_ccxt_long(
    chain_name,
    chain_rpc_url,
    large_eth_holder,
    large_wbtc_holder,
    large_wavax_holder,
    large_usdc_holder_arbitrum,
    large_usdc_holder_avalanche,
    large_wbtc_holder_avalanche,
    large_link_holder_avalanche,
    gmx_controller_arbitrum,
    large_weth_holder_arbitrum,
    gmx_keeper_arbitrum,
    large_gm_eth_usdc_holder_arbitrum,
) -> Generator[str, Any, None]:
    """Create a testable fork for long position tests."""
    yield from _create_anvil_fork(
        chain_name,
        chain_rpc_url,
        large_eth_holder,
        large_wbtc_holder,
        large_wavax_holder,
        large_usdc_holder_arbitrum,
        large_usdc_holder_avalanche,
        large_wbtc_holder_avalanche,
        large_link_holder_avalanche,
        gmx_controller_arbitrum,
        large_weth_holder_arbitrum,
        gmx_keeper_arbitrum,
        large_gm_eth_usdc_holder_arbitrum,
    )


@pytest.fixture()
def anvil_chain_fork_ccxt_short(
    chain_name,
    chain_rpc_url,
    large_eth_holder,
    large_wbtc_holder,
    large_wavax_holder,
    large_usdc_holder_arbitrum,
    large_usdc_holder_avalanche,
    large_wbtc_holder_avalanche,
    large_link_holder_avalanche,
    gmx_controller_arbitrum,
    large_weth_holder_arbitrum,
    gmx_keeper_arbitrum,
    large_gm_eth_usdc_holder_arbitrum,
) -> Generator[str, Any, None]:
    """Create a testable fork for short position tests."""
    yield from _create_anvil_fork(
        chain_name,
        chain_rpc_url,
        large_eth_holder,
        large_wbtc_holder,
        large_wavax_holder,
        large_usdc_holder_arbitrum,
        large_usdc_holder_avalanche,
        large_wbtc_holder_avalanche,
        large_link_holder_avalanche,
        gmx_controller_arbitrum,
        large_weth_holder_arbitrum,
        gmx_keeper_arbitrum,
        large_gm_eth_usdc_holder_arbitrum,
    )


@pytest.fixture()
def web3_arbitrum_fork_ccxt_long(anvil_chain_fork_ccxt_long: str) -> Web3:
    """Set up web3 for long position tests."""
    web3 = create_multi_provider_web3(
        anvil_chain_fork_ccxt_long,
        default_http_timeout=(3.0, 180.0),
    )
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    return web3


@pytest.fixture()
def web3_arbitrum_fork_ccxt_short(anvil_chain_fork_ccxt_short: str) -> Web3:
    """Set up web3 for short position tests."""
    web3 = create_multi_provider_web3(
        anvil_chain_fork_ccxt_short,
        default_http_timeout=(3.0, 180.0),
    )
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    return web3


@pytest.fixture
def ccxt_gmx_fork_open_close(
    web3_arbitrum_fork_ccxt_long,
    test_wallet,
) -> GMX:
    """CCXT GMX exchange with wallet for open/close long position testing.

    Uses separate anvil fork to avoid state pollution with other tests.
    """
    setup_mock_oracle(web3_arbitrum_fork_ccxt_long)
    config = GMXConfig(web3_arbitrum_fork_ccxt_long, user_wallet_address=test_wallet)
    _approve_tokens_for_config(config, web3_arbitrum_fork_ccxt_long, test_wallet.address)
    gmx = GMX(
        params={
            "rpcUrl": web3_arbitrum_fork_ccxt_long.provider.endpoint_uri if hasattr(web3_arbitrum_fork_ccxt_long.provider, "endpoint_uri") else None,
            "wallet": test_wallet,
        }
    )
    return gmx


@pytest.fixture
def ccxt_gmx_fork_short(
    web3_arbitrum_fork_ccxt_short,
    test_wallet,
) -> GMX:
    """CCXT GMX exchange with wallet for short position testing.

    Uses separate anvil fork to avoid state pollution with other tests.
    """
    setup_mock_oracle(web3_arbitrum_fork_ccxt_short)
    config = GMXConfig(web3_arbitrum_fork_ccxt_short, user_wallet_address=test_wallet)
    _approve_tokens_for_config(config, web3_arbitrum_fork_ccxt_short, test_wallet.address)
    gmx = GMX(
        params={
            "rpcUrl": web3_arbitrum_fork_ccxt_short.provider.endpoint_uri if hasattr(web3_arbitrum_fork_ccxt_short.provider, "endpoint_uri") else None,
            "wallet": test_wallet,
        }
    )
    return gmx
