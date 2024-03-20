"""USDC fixtures."""

import pytest
from eth_typing import ChecksumAddress
from web3 import Web3, HTTPProvider

from eth_defi.provider.anvil import AnvilLaunch, launch_anvil
from eth_defi.chain import install_chain_middleware
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, reset_default_token_cache
from eth_defi.usdc.deployment import deploy_fiat_token


@pytest.fixture()
def anvil() -> AnvilLaunch:
    """Launch Anvil for the test backend."""
    anvil = launch_anvil()
    try:
        yield anvil
    finally:
        anvil.close()


@pytest.fixture()
def web3(anvil: AnvilLaunch) -> Web3:
    """Set up the Anvil Web3 connection.

    Also perform the Anvil state reset for each test.
    """
    reset_default_token_cache()  # Avoid race conditions in parallel tests
    web3 = create_multi_provider_web3(anvil.json_rpc_url)
    return web3


@pytest.fixture()
def deployer(web3) -> ChecksumAddress:
    """Deploy account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[0]


@pytest.fixture()
def usdc(web3, deployer: ChecksumAddress) -> TokenDetails:
    """Centre fiat token.

    Deploy real USDC code.
    """
    return deploy_fiat_token(web3, deployer)
