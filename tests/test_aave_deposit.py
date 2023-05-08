"""
    export JSON_RPC_POLYGON="https://rpc.ankr.com/polygon"
    pytest test_aave_deposit.py
"""

import logging
import os
import shutil

import flaky
import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr
from web3 import HTTPProvider, Web3
from web3._utils.transactions import fill_nonce

from eth_defi.aave_v3.loan import approve_token, deposit_in_aave
from eth_defi.anvil import fork_network_anvil
from eth_defi.chain import install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.token import fetch_erc20_details

# https://docs.pytest.org/en/latest/how-to/skipping.html#skip-all-test-functions-of-a-class-or-module
pytestmark = pytest.mark.skipif(
    (os.environ.get("JSON_RPC_POLYGON") is None) or (shutil.which("anvil") is None),
    reason="Set JSON_RPC_POLYGON env in order to run these tests",
)


# Polygon Mainnet addresses
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
AAVE_DEPOSIT_ADDRESS = "0x794a61358D6845594F94dc1DB02A252b5b4814aD"
AAVE_AUSDC_ADDRESS = "0x625E7708f30cA75bfd92586e17077590C60eb4cD"


@pytest.fixture()
def large_usdc_holder() -> HexAddress:
    """A random account picked from Polygon that holds a lot of USDC.

    `To find large USDC holder accounts, use polygoscan <https://polygonscan.com/token/0x2791bca1f2de4661ed88a30c99a7a9449aa84174#balances>`_.
    """
    # Binance Hot Wallet 6
    return HexAddress(HexStr("0x06959153B974D0D5fDfd87D561db6d8d4FA0bb0B"))


@pytest.fixture()
def hot_wallet_account() -> LocalAccount:
    """Create a test account."""
    return Account.create()


@pytest.fixture()
def anvil_polygon_chain_fork(request, large_usdc_holder) -> str:
    """Create a testable fork of live Polygon chain.

    :return: JSON-RPC URL for Web3
    """
    mainnet_rpc = os.environ["JSON_RPC_POLYGON"]
    launch = fork_network_anvil(mainnet_rpc, unlocked_addresses=[large_usdc_holder])
    try:
        yield launch.json_rpc_url
    finally:
        # Wind down Anvil process after the test is complete
        launch.close(log_level=logging.ERROR)


@pytest.fixture()
def web3(anvil_polygon_chain_fork: str):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    web3 = Web3(HTTPProvider(anvil_polygon_chain_fork))
    # Anvil needs POA middlware if parent chain needs POA middleware
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    return web3


def test_anvil_output():
    """Read anvil output from stdout."""
    # mainnet_rpc = os.environ["BNB_CHAIN_JSON_RPC"]
    # process, cmd = _launch("anvil")

    mainnet_rpc = os.environ["JSON_RPC_POLYGON"]
    launch = fork_network_anvil(mainnet_rpc)
    stdout, stderr = launch.close()
    assert b"https://github.com/foundry-rs/foundry" in stdout, f"Did not see the market string in stdout: {stdout}"


def test_anvil_forked_chain_id(web3: Web3):
    """Anvil pipes through the forked chain id."""
    assert web3.eth.chain_id == 137


@flaky.flaky()
def test_anvil_fork_deposit_aave(web3: Web3, large_usdc_holder: HexAddress, hot_wallet_account: LocalAccount):
    """Test that the deposit in Aave v3 is correctly registered and the corresponding aToken is received."""

    # Fund hot wallet & check if hot wallet starts with 1 USDC & 1 MATIC on Polygon Mainnet Anvil fork
    usdc_details = fetch_erc20_details(web3, USDC_ADDRESS)
    usdc = usdc_details.contract
    amount = 1000000

    tx = web3.eth.send_transaction({"from": large_usdc_holder, "to": hot_wallet_account.address, "value": 1 * 10**18})
    web3.eth.wait_for_transaction_receipt(tx)
    receipt = web3.eth.get_transaction_receipt(tx)
    assert receipt.status == 1
    assert Web3.from_wei(web3.eth.get_balance(hot_wallet_account.address), "ether") == 1
    tx = usdc.functions.transfer(hot_wallet_account.address, amount).transact({"from": large_usdc_holder})
    web3.eth.wait_for_transaction_receipt(tx)
    receipt = web3.eth.get_transaction_receipt(tx)
    assert receipt.status == 1, "USDC transfer reverted"
    assert usdc.functions.balanceOf(hot_wallet_account.address).call() == amount, "Insufficient USDC balance amount"

    # Check that the token approval was correctly registered
    tx = approve_token(web3=web3, token_address=USDC_ADDRESS, spender=AAVE_DEPOSIT_ADDRESS, amount=amount).build_transaction({"from": hot_wallet_account.address})
    tx = fill_nonce(web3, tx)

    signed = hot_wallet_account.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    web3.eth.wait_for_transaction_receipt(tx_hash)
    receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert receipt.status == 1, "USDC approval reverted"

    # Check that the deposit was correctly registered with the Aave reserves
    tx = deposit_in_aave(web3=web3, hot_wallet=hot_wallet_account, aave_deposit_address=AAVE_DEPOSIT_ADDRESS, token_address=USDC_ADDRESS, amount=amount).build_transaction({"from": hot_wallet_account.address})
    tx = fill_nonce(web3, tx)

    signed = hot_wallet_account.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    web3.eth.wait_for_transaction_receipt(tx_hash)
    receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert receipt.status == 1, "Aave v3 deposit reverted"

    # Check that the borrower received the corresponding Aave aUSDC token back in the wallet, with the correct amount
    aUSDC_details = fetch_erc20_details(web3, AAVE_AUSDC_ADDRESS)
    aUSDC = aUSDC_details.contract
    assert aUSDC.functions.balanceOf(hot_wallet_account.address).call() == amount, "Incorrect aUSDC balance"
