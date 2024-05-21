import logging
import os
import shutil

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr

from eth_defi.aave_v3.constants import MAX_AMOUNT
from eth_defi.aave_v3.deployment import fetch_deployment as fetch_aave_deployment
from eth_defi.aave_v3.loan import supply, withdraw
from eth_defi.hotwallet import HotWallet
from eth_defi.one_delta.deployment import OneDeltaDeployment
from eth_defi.one_delta.deployment import fetch_deployment as fetch_1delta_deployment
from eth_defi.one_delta.position import (
    approve,
    close_short_position,
    open_short_position,
)
from eth_defi.provider.anvil import fork_network_anvil, mine
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import fetch_erc20_details


@pytest.fixture(scope="module")
def large_usdc_holder() -> HexAddress:
    """A random account picked from Polygon that holds a lot of USDC.

    This account is unlocked on Anvil, so you have access to good USDC stash.

    `To find large holder accounts, use <https://polygonscan.com/token/0x2791bca1f2de4661ed88a30c99a7a9449aa84174#balances>`_.
    """
    # Binance Hot Wallet 6
    return HexAddress(HexStr("0xe7804c37c13166fF0b37F5aE0BB07A3aEbb6e245"))


@pytest.fixture
def anvil_polygon_chain_fork(request, large_usdc_holder) -> str:
    """Create a testable fork of live Polygon.

    :return: JSON-RPC URL for Web3
    """
    mainnet_rpc = os.environ["JSON_RPC_POLYGON"]
    launch = fork_network_anvil(
        mainnet_rpc,
        unlocked_addresses=[large_usdc_holder],
        fork_block_number=49_000_000,
    )
    try:
        yield launch.json_rpc_url
    finally:
        # Wind down Anvil process after the test is complete
        launch.close(log_level=logging.ERROR)


@pytest.fixture
def web3(anvil_polygon_chain_fork: str):
    """Set up a Web3 provider instance with a lot of workarounds for flaky nodes."""
    web3 = create_multi_provider_web3(anvil_polygon_chain_fork)
    return web3


@pytest.fixture
def usdc(web3):
    """Get USDC on Polygon."""
    return fetch_erc20_details(web3, "0x2791bca1f2de4661ed88a30c99a7a9449aa84174")


@pytest.fixture
def ausdc(web3):
    """Get aPolUSDC on Polygon."""
    return fetch_erc20_details(web3, "0x625E7708f30cA75bfd92586e17077590C60eb4cD", contract_name="aave_v3/AToken.json")


@pytest.fixture
def weth(web3):
    """Get WETH on Polygon."""
    return fetch_erc20_details(web3, "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619")


@pytest.fixture
def vweth(web3):
    """Get vPolWETH on Polygon."""
    return fetch_erc20_details(web3, "0x0c84331e39d6658Cd6e6b9ba04736cC4c4734351", contract_name="aave_v3/VariableDebtToken.json")


@pytest.fixture
def wmatic(web3):
    """Get WMATIC on Polygon."""
    return fetch_erc20_details(web3, "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270")


@pytest.fixture
def vwmatic(web3):
    """Get vPolMATIC on Polygon."""
    return fetch_erc20_details(web3, "0x4a1c3aD6Ed28a636ee1751C69071f6be75DEb8B8", contract_name="aave_v3/VariableDebtToken.json")


@pytest.fixture
def aave_v3_deployment(web3):
    return fetch_aave_deployment(
        web3,
        pool_address="0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        data_provider_address="0x69FA688f1Dc47d4B5d8029D5a35FB7a548310654",
        oracle_address="0xb023e699F5a33916Ea823A16485e259257cA8Bd1",
    )


@pytest.fixture
def one_delta_deployment(web3) -> OneDeltaDeployment:
    return fetch_1delta_deployment(
        web3,
        flash_aggregator_address="0x74E95F3Ec71372756a01eB9317864e3fdde1AC53",
        broker_proxy_address="0x74E95F3Ec71372756a01eB9317864e3fdde1AC53",
        quoter_address="0x36de3876ad1ef477e8f6d98EE9a162926f00463A",
    )


@pytest.fixture
def user_1() -> LocalAccount:
    """Create a test account."""
    return Account.create()


@pytest.fixture
def hot_wallet(web3, user_1, usdc, large_usdc_holder) -> HotWallet:
    """Hot wallet."""
    assert isinstance(user_1, LocalAccount)
    wallet = HotWallet(user_1)
    wallet.sync_nonce(web3)

    # give hot wallet some native token and USDC
    web3.eth.send_transaction(
        {
            "from": large_usdc_holder,
            "to": wallet.address,
            "value": 100 * 10**18,
        }
    )

    usdc.contract.functions.transfer(
        wallet.address,
        100_000 * 10**6,
    ).transact({"from": large_usdc_holder})

    return wallet
