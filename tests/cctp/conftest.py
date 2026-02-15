"""Shared fixtures for CCTP integration tests."""

import logging
import os

import pytest
from eth_typing import HexAddress, HexStr
from web3 import Web3
from web3.contract import Contract

from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_NATIVE_TOKEN, fetch_erc20_details

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")
JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")


#: Circle/Centre USDC deployer on Ethereum - holds large USDC balance
#: https://etherscan.io/token/0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48#balances
ETHEREUM_USDC_WHALE = HexAddress(HexStr("0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341"))


@pytest.fixture()
def ethereum_usdc_whale() -> HexAddress:
    """A large USDC holder on Ethereum mainnet."""
    return ETHEREUM_USDC_WHALE


@pytest.fixture()
def anvil_ethereum_fork(_request, ethereum_usdc_whale) -> AnvilLaunch:
    """Create a testable fork of live Ethereum mainnet."""
    launch = fork_network_anvil(
        JSON_RPC_ETHEREUM,
        unlocked_addresses=[ethereum_usdc_whale],
    )
    try:
        yield launch
    finally:
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3(anvil_ethereum_fork) -> Web3:
    """Web3 connected to Ethereum mainnet Anvil fork."""
    web3 = create_multi_provider_web3(anvil_ethereum_fork.json_rpc_url)
    assert web3.eth.chain_id == 1
    return web3


@pytest.fixture()
def usdc(web3) -> Contract:
    """USDC on Ethereum mainnet."""
    return fetch_erc20_details(web3, USDC_NATIVE_TOKEN[1]).contract
