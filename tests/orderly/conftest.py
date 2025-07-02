"""Orderly tests on Arbitrum Sepolia fork"""

import os

import pytest
from eth_account import Account
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.hotwallet import HotWallet
from eth_defi.orderly.vault import OrderlyVault
from eth_defi.provider.anvil import AnvilLaunch, fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, fetch_erc20_details

JSON_RPC_ARBITRUM_SEPOLIA = os.environ.get("JSON_RPC_ARBITRUM_SEPOLIA")
HOT_WALLET_PRIVATE_KEY = os.environ.get("HOT_WALLET_PRIVATE_KEY")

CI = os.environ.get("CI", None) is not None

pytestmark = pytest.mark.skipif(not JSON_RPC_ARBITRUM_SEPOLIA, reason="No JSON_RPC_ARBITRUM_SEPOLIA environment variable")


@pytest.fixture()
def anvil_base_fork(request) -> AnvilLaunch:
    """Create a testable fork of live BNB chain.

    :return: JSON-RPC URL for Web3
    """
    launch = fork_network_anvil(
        JSON_RPC_ARBITRUM_SEPOLIA,
        unlocked_addresses=[],
        fork_block_number=169570220,
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
            default_http_timeout=(3, 250.0),  # multicall slow, so allow improved timeout
        )
    assert web3.eth.chain_id == 421614
    return web3


@pytest.fixture()
def usdc(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        "0x75faf114eafb1BDbe2F0316DF893fd58CE46AA4d",
    )


@pytest.fixture()
def base_weth(web3) -> TokenDetails:
    return fetch_erc20_details(
        web3,
        "0x4200000000000000000000000000000000000006",
    )


@pytest.fixture
def hot_wallet(web3, usdc) -> HotWallet:
    """Hotwallet account."""
    hw = HotWallet(Account.from_key(HOT_WALLET_PRIVATE_KEY))
    hw.sync_nonce(web3)

    assert usdc.functions.balanceOf(hw.address).call() == pytest.approx(1008 * 10**6)

    return hw


@pytest.fixture()
def broker_id() -> str:
    return "woofi_pro"


@pytest.fixture()
def orderly_account_id() -> HexAddress:
    return "0xca47e3fb4339d0e30c639bb30cf8c2d18cbb8687a27bc39249287232f86f8d00"


@pytest.fixture
def orderly_vault(web3) -> OrderlyVault:
    """Orderly vault."""
    # https://orderly.network/docs/build-on-omnichain/addresses
    return OrderlyVault(web3, "0x0EaC556c0C2321BA25b9DC01e4e3c95aD5CDCd2f")
