"""Gains (Gtrade) vault tests."""
import datetime
import os

import pytest

from eth_defi.erc_4626.classification import create_vault_instance, create_vault_instance_autodetect, detect_vault_features
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.gains.vault import GainsVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
pytestmark = pytest.mark.skipif(not JSON_RPC_ARBITRUM, reason="Set JSON_RPC_ARBITRUM to run this test")


@pytest.fixture(scope="module")
def anvil_arbitrum_fork(request) -> AnvilLaunch:
    launch = fork_network_anvil(JSON_RPC_ARBITRUM, fork_block_number=375_216_652)
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture(scope="module")
def web3(anvil_arbitrum_fork):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork.json_rpc_url)
    return web3


@pytest.fixture(scope="module")
def vault(web3) -> GainsVault:
    """gTrade USDC vault on Arbitrum"""
    vault_address = "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0"
    vault = create_vault_instance_autodetect(web3, vault_address)
    assert isinstance(vault, GainsVault)
    return vault


def test_gains_features(web3):
    vault_address = "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0"
    features = detect_vault_features(web3, vault_address, verbose=True)
    assert ERC4626Feature.gains_like in features, f"Got features: {features}"


def test_gains_read_data(web3, vault: GainsVault):
    assert vault.name == "Gains Network USDC"
    # https://arbiscan.io/address/0xBF55C78132ab06a2B217040b7A7F20B5cBD47982#readContract
    assert vault.gains_open_trades_pnl_feed.address == "0xBF55C78132ab06a2B217040b7A7F20B5cBD47982"
    assert vault.fetch_epoch_duration() == datetime.timedelta(seconds=21600)
    assert vault.fetch_current_epoch_start() == datetime.datetime(2025, 8, 31, 21, 53, 55)
    assert vault.fetch_withdraw_epochs_time_lock() == 3
    assert vault.estimate_withdraw_timeout() == datetime.datetime(2025, 9, 1, 15, 53, 55)