"""Gains (Gtrade) vault tests."""
import datetime
import os
from decimal import Decimal

import pytest

from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect, detect_vault_features
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.flow import deposit_4626
from eth_defi.gains.vault import GainsVault
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import USDC_WHALE, fetch_erc20_details, USDC_NATIVE_TOKEN, TokenDetails
from eth_defi.trace import assert_transaction_success_with_explanation

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
pytestmark = pytest.mark.skipif(not JSON_RPC_ARBITRUM, reason="Set JSON_RPC_ARBITRUM to run this test")


@pytest.fixture()
def anvil_arbitrum_fork_write(request) -> AnvilLaunch:
    """Reset write state between tests"""

    usdc_whale = USDC_WHALE[42161]
    open_pnl = "0xBF55C78132ab06a2B217040b7A7F20B5cBD47982"

    launch = fork_network_anvil(
        JSON_RPC_ARBITRUM,
        fork_block_number=375_216_652,
        unlocked_addresses=[usdc_whale, open_pnl],
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture()
def web3_write(anvil_arbitrum_fork_write):
    web3 = create_multi_provider_web3(anvil_arbitrum_fork_write.json_rpc_url, retries=1)
    return web3


@pytest.fixture()
def usdc(web3_write) -> TokenDetails:
    web3 = web3_write
    usdc = fetch_erc20_details(
        web3,
        USDC_NATIVE_TOKEN[42161],
    )
    return usdc


@pytest.fixture()
def test_user(web3_write, usdc):
    web3 = web3_write
    account = web3.eth.accounts[0]
    tx_hash = usdc.transfer(account, Decimal(10_000)).transact({"from": USDC_WHALE[42161]})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return account


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


def test_gains_deposit_withdraw(
    web3_write: Web3,
    test_user,
    usdc: TokenDetails,
):
    """Do deposit/withdraw cycle on Gains vault.

    - Spoof OpenPnl contract to simulate passage of time
    """
    web3 = web3_write
    vault = create_vault_instance_autodetect(web3, "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0")

    amount = Decimal(100)

    tx_hash = usdc.approve(
        vault.address,
        amount,
    ).transact({"from": test_user})
    assert_transaction_success_with_explanation(web3, tx_hash)

    bound_func = deposit_4626(
        vault,
        test_user,
        amount,
    )
    tx_hash = bound_func.transact({"from": test_user})
    assert_transaction_success_with_explanation(web3, tx_hash)

    share_token = vault.share_token
    shares = share_token.fetch_balance_of(test_user)
    assert shares == pytest.approx(Decimal('81.54203'))

    redemption_request = vault.create_redemption_request(test_user, shares)
    tx_hash = redemption_request.func.transact({"from": test_user})
    assert_transaction_success_with_explanation(web3, tx_hash)

    redemption_ticket = redemption_request.parse_redeem_transaction(tx_hash)
    assert redemption_ticket.shares == pytest.approx(Decimal('81.54203'))


