"""
    export JSON_RPC_POLYGON=https://polygon-rpc.com/
    pytest -k test_aave_v3_loan.py
"""

import logging
import os
import shutil

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr
from web3 import HTTPProvider, Web3
from web3.exceptions import ContractLogicError

from eth_defi.aave_v3.constants import AAVE_V3_NETWORKS, AaveToken
from eth_defi.aave_v3.deployment import AaveV3Deployment
from eth_defi.aave_v3.loan import supply, withdraw
from eth_defi.abi import get_contract
from eth_defi.anvil import fork_network_anvil
from eth_defi.chain import install_chain_middleware
from eth_defi.gas import node_default_gas_price_strategy
from eth_defi.hotwallet import HotWallet
from eth_defi.token import fetch_erc20_details
from eth_defi.trace import assert_transaction_success_with_explanation

pytestmark = pytest.mark.skipif(
    (os.environ.get("JSON_RPC_POLYGON") is None) or (shutil.which("anvil") is None),
    reason="Set JSON_RPC_POLYGON env in order to run these tests",
)


@pytest.fixture()
def anvil_polygon_chain_fork(request, large_usdc_holder) -> str:
    """Create a testable fork of live Polygon chain.
    :return: JSON-RPC URL for Web3
    """
    launch = fork_network_anvil(os.environ["JSON_RPC_POLYGON"], unlocked_addresses=[large_usdc_holder])
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


@pytest.fixture()
def large_usdc_holder() -> HexAddress:
    """A random account picked from Polygon that holds a lot of USDC.
    `To find large USDC holder accounts, use polygoscan <https://polygonscan.com/token/0x2791bca1f2de4661ed88a30c99a7a9449aa84174#balances>`_.
    """
    # Binance Hot Wallet 6
    return HexAddress(HexStr("0x06959153B974D0D5fDfd87D561db6d8d4FA0bb0B"))


@pytest.fixture()
def hot_wallet(web3) -> HotWallet:
    """User account."""
    hw = HotWallet(Account.create())
    hw.sync_nonce(web3)
    return hw


@pytest.fixture()
def usdc(web3):
    """Get USDC on Polygon."""
    return fetch_erc20_details(web3, "0x2791bca1f2de4661ed88a30c99a7a9449aa84174")


@pytest.fixture()
def aave_v3_polygon_deployment(web3):
    Pool = get_contract(web3, "aave_v3/Pool.json", bytecode="")
    pool = Pool(address=AAVE_V3_NETWORKS["polygon"].pool_address)

    return AaveV3Deployment(web3=web3, pool=pool)


@pytest.fixture()
def aave_v3_usdc_reserve() -> AaveToken:
    return AAVE_V3_NETWORKS["polygon"].token_contracts["USDC"]


def test_aave_v3_supply(
    web3: Web3,
    aave_v3_polygon_deployment,
    usdc,
    large_usdc_holder: HexAddress,
    hot_wallet: LocalAccount,
    aave_v3_usdc_reserve,
):
    """Test that the deposit in Aave v3 is correctly registered and the corresponding aToken is received."""
    supply_amount = 100 * 10**6

    # give hot wallet some native token and USDC
    web3.eth.send_transaction(
        {
            "from": large_usdc_holder,
            "to": hot_wallet.address,
            "value": 100 * 10**18,
        }
    )
    usdc.contract.functions.transfer(
        hot_wallet.address,
        supply_amount * 2,
    ).transact({"from": large_usdc_holder})

    # supply USDC to Aave
    approve_fn, supply_fn = supply(
        aave_v3_deployment=aave_v3_polygon_deployment,
        wallet_address=hot_wallet.address,
        token=usdc.contract,
        amount=supply_amount,
    )

    tx = approve_fn.build_transaction({"from": hot_wallet.address})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx = supply_fn.build_transaction({"from": hot_wallet.address})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # verify aUSDC token amount in hot wallet
    ausdc_details = fetch_erc20_details(web3, aave_v3_usdc_reserve.deposit_address)
    assert ausdc_details.contract.functions.balanceOf(hot_wallet.address).call() == supply_amount


def test_aave_v3_withdraw(
    web3: Web3,
    aave_v3_polygon_deployment,
    usdc,
    large_usdc_holder: HexAddress,
    hot_wallet: LocalAccount,
    aave_v3_usdc_reserve,
):
    """Test that the deposit in Aave v3 is correctly registered and the corresponding aToken is received."""
    supply_amount = 100 * 10**6

    # give hot wallet some native token and USDC
    web3.eth.send_transaction(
        {
            "from": large_usdc_holder,
            "to": hot_wallet.address,
            "value": 100 * 10**18,
        }
    )
    usdc.contract.functions.transfer(
        hot_wallet.address,
        supply_amount * 2,
    ).transact({"from": large_usdc_holder})

    # supply USDC to Aave
    approve_fn, supply_fn = supply(
        aave_v3_deployment=aave_v3_polygon_deployment,
        wallet_address=hot_wallet.address,
        token=usdc.contract,
        amount=supply_amount,
    )

    tx = approve_fn.build_transaction({"from": hot_wallet.address})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx = supply_fn.build_transaction({"from": hot_wallet.address})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # partial withdraw should be fine
    withdraw_fn = withdraw(
        aave_v3_deployment=aave_v3_polygon_deployment,
        wallet_address=hot_wallet.address,
        token=usdc.contract,
        amount=int(supply_amount / 2),
    )
    tx = withdraw_fn.build_transaction({"from": hot_wallet.address})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    withdraw_fn = withdraw(
        aave_v3_deployment=aave_v3_polygon_deployment,
        wallet_address=hot_wallet.address,
        token=usdc.contract,
        amount=supply_amount,
    )

    with pytest.raises(ContractLogicError) as e:
        # TODO: not sure why it fails at this line
        tx = withdraw_fn.build_transaction({"from": hot_wallet.address})
        # signed = hot_wallet.sign_transaction_with_new_nonce(tx)
        # tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
        # assert_transaction_success_with_explanation(web3, tx_hash)

    # error code 32 = not enough available user balance
    # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/libraries/helpers/Errors.sol#L41
    assert str(e.value) == "execution reverted: 32"
