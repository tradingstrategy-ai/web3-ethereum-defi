"""Umami uses non-standard deposit function.

Test with Tenderly:

.. code-block:: shell

    JSON_RPC_TENDERLY=https://virtual.arbitrum.eu.rpc.tenderly.co/39c01875-63cd-4efc-8cbf-82a2426fc0e8 pytest --log-cli-level=info -k test_umami_gmusdc_deposit_withdraw

"""

import datetime
import logging
import os
from decimal import Decimal

import pytest

from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect, create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.gains.deposit_redeem import GainsDepositManager
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil, AnvilLaunch, make_anvil_custom_rpc_request
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.token import TokenDetails, USDC_WHALE, fetch_erc20_details, USDC_NATIVE_TOKEN
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.umami.vault import UmamiVault, UmamiDepositManager

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
JSON_RPC_TENDERLY = os.environ.get("JSON_RPC_TENDERLY", None)

# See broken handleDeposit() under Anvil in the commentsa
pytestmark = pytest.mark.skipif(not JSON_RPC_TENDERLY, reason="Set JSON_RPC_TENDERLY to run this test manually")


@pytest.fixture()
def anvil_arbitrum_fork_write(request) -> AnvilLaunch:
    """Reset write state between tests"""

    usdc_whale = USDC_WHALE[42161]
    # open_pnl = "0xBF55C78132ab06a2B217040b7A7F20B5cBD47982"

    launch = fork_network_anvil(
        JSON_RPC_ARBITRUM,
        # 397557485
        fork_block_number=397_557_485,
        unlocked_addresses=[usdc_whale],
        verbose=True,
    )
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close(log_level=logging.INFO)


@pytest.fixture()
def web3(anvil_arbitrum_fork_write):
    tenderly_fork_rpc = os.environ.get("JSON_RPC_TENDERLY", None)

    if tenderly_fork_rpc:
        # Use Tenderly debugger
        web3 = create_multi_provider_web3(tenderly_fork_rpc)
    else:
        web3 = create_multi_provider_web3(
            anvil_arbitrum_fork_write.json_rpc_url,
            default_http_timeout=(3, 250.0),  # multicall slow, so allow improved timeout
        )
    assert web3.eth.chain_id == 42161
    return web3


@pytest.fixture()
def vault(web3) -> UmamiVault:
    """gTrade USDC vault on Arbitrum"""
    vault_address = "0x959f3807f0aa7921e18c78b00b2819ba91e52fef"
    vault = create_vault_instance(
        web3,
        vault_address,
        features={ERC4626Feature.umami_like},
    )
    assert isinstance(vault, UmamiVault)
    return vault


@pytest.fixture()
def usdc(web3) -> TokenDetails:
    usdc = fetch_erc20_details(
        web3,
        USDC_NATIVE_TOKEN[42161],
    )
    return usdc


@pytest.fixture()
def test_user(web3, usdc):
    # account = web3.eth.accounts[0]
    wallet = HotWallet.create_for_testing(web3, register_middleware=True, eth_amount=10)
    account = wallet.address
    tx_hash = usdc.transfer(account, Decimal(10_000)).transact({"from": USDC_WHALE[42161]})
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert web3.eth.get_balance(account) > 10**18
    return account


def test_umami_gmusdc_deposit(
    web3: Web3,
    test_user,
    usdc: TokenDetails,
    vault: UmamiVault,
):
    """Umami uses non-standard deposit() function."""

    amount = Decimal(100)

    tx_hash = usdc.approve(
        vault.address,
        amount,
    ).transact({"from": test_user})
    assert_transaction_success_with_explanation(web3, tx_hash)

    deposit_manager = vault.get_deposit_manager()
    assert isinstance(deposit_manager, UmamiDepositManager)

    estimated = deposit_manager.estimate_deposit(test_user, amount)
    assert estimated == pytest.approx(Decimal("87.173345"), rel=Decimal(0.01))

    # This will call handleDeposit() which does not work under Anvil
    # https://ethereum.stackexchange.com/questions/171867/i-have-a-transaction-that-fails-in-anvil-mainnet-fork-but-succeeds-in-tenderly
    deposit_request = deposit_manager.create_deposit_request(
        test_user,
        amount=amount,
    )
    ticket = deposit_request.broadcast()

    # Something somewhere handles the deposit request later,
    # but Umami does not document
    share_token = vault.share_token
    shares = share_token.fetch_balance_of(test_user)
    assert shares == pytest.approx(Decimal(0))
