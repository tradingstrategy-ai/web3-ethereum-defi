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
from eth_defi.trace import (
    TransactionAssertionError,
    assert_transaction_success_with_explanation,
)

pytestmark = pytest.mark.skipif(
    (os.environ.get("JSON_RPC_POLYGON") is None) or (shutil.which("anvil") is None),
    reason="Set JSON_RPC_POLYGON env in order to run these tests",
)


@pytest.fixture(scope="module")
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


@pytest.fixture(scope="module")
def web3(anvil_polygon_chain_fork: str):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    web3 = Web3(HTTPProvider(anvil_polygon_chain_fork))
    # Anvil needs POA middlware if parent chain needs POA middleware
    install_chain_middleware(web3)
    web3.eth.set_gas_price_strategy(node_default_gas_price_strategy)
    return web3


@pytest.fixture(scope="module")
def large_usdc_holder() -> HexAddress:
    """A random account picked from Polygon that holds a lot of USDC.
    `To find large USDC holder accounts, use polygoscan <https://polygonscan.com/token/0x2791bca1f2de4661ed88a30c99a7a9449aa84174#balances>`_.
    """
    # Binance Hot Wallet 6
    return HexAddress(HexStr("0x06959153B974D0D5fDfd87D561db6d8d4FA0bb0B"))


@pytest.fixture(scope="module")
def usdc(web3):
    """Get USDC on Polygon."""
    return fetch_erc20_details(web3, "0x2791bca1f2de4661ed88a30c99a7a9449aa84174")


@pytest.fixture(scope="module")
def aave_v3_polygon_deployment(web3):
    Pool = get_contract(web3, "aave_v3/Pool.json", bytecode="")
    pool = Pool(address=AAVE_V3_NETWORKS["polygon"].pool_address)

    return AaveV3Deployment(web3=web3, pool=pool)


@pytest.fixture(scope="module")
def aave_v3_usdc_reserve() -> AaveToken:
    return AAVE_V3_NETWORKS["polygon"].token_contracts["USDC"]


@pytest.fixture
def usdc_supply_amount() -> int:
    # 1000 USDC
    return 1000 * 10**6


@pytest.fixture
def hot_wallet(
    web3,
    large_usdc_holder,
    usdc,
    usdc_supply_amount,
) -> HotWallet:
    """Hotwallet account."""
    hw = HotWallet(Account.create())
    hw.sync_nonce(web3)

    # give hot wallet some MATIC
    web3.eth.send_transaction(
        {
            "from": large_usdc_holder,
            "to": hw.address,
            "value": 100 * 10**18,
        }
    )

    # and USDC
    usdc.contract.functions.transfer(
        hw.address,
        usdc_supply_amount,
    ).transact({"from": large_usdc_holder})

    return hw


def _test_supply(
    web3,
    aave_v3_polygon_deployment,
    hot_wallet,
    usdc,
    aave_v3_usdc_reserve,
    usdc_supply_amount,
):
    # supply USDC to Aave
    approve_fn, supply_fn = supply(
        aave_v3_deployment=aave_v3_polygon_deployment,
        wallet_address=hot_wallet.address,
        token=usdc.contract,
        amount=usdc_supply_amount,
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
    assert ausdc_details.contract.functions.balanceOf(hot_wallet.address).call() == usdc_supply_amount


def test_aave_v3_supply(
    web3: Web3,
    aave_v3_polygon_deployment,
    usdc,
    hot_wallet: LocalAccount,
    aave_v3_usdc_reserve,
    usdc_supply_amount: int,
):
    """Test that the deposit in Aave v3 is correctly registered
    and the corresponding aToken is received.
    """
    _test_supply(
        web3,
        aave_v3_polygon_deployment,
        hot_wallet,
        usdc,
        aave_v3_usdc_reserve,
        usdc_supply_amount,
    )


@pytest.mark.parametrize(
    "withdraw_factor,expected_exception",
    [
        # full withdraw
        (1, None),
        # partial withdraw
        (0.5, None),
        # over withdraw should fail
        # error code 32 = 'User cannot withdraw more than the available balance'
        # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/libraries/helpers/Errors.sol#L41
        (1.0001, TransactionAssertionError("execution reverted: 32")),
    ],
)
def test_aave_v3_withdraw(
    web3: Web3,
    aave_v3_polygon_deployment,
    usdc,
    hot_wallet: LocalAccount,
    usdc_supply_amount: int,
    aave_v3_usdc_reserve,
    withdraw_factor: float,
    expected_exception: Exception | None,
):
    """Test that the withdraw in Aave v3 is correctly registered and the corresponding aToken is received."""
    _test_supply(
        web3,
        aave_v3_polygon_deployment,
        hot_wallet,
        usdc,
        aave_v3_usdc_reserve,
        usdc_supply_amount,
    )

    # withdraw
    withdraw_amount = int(usdc_supply_amount * withdraw_factor)
    withdraw_fn = withdraw(
        aave_v3_deployment=aave_v3_polygon_deployment,
        wallet_address=hot_wallet.address,
        token=usdc.contract,
        amount=withdraw_amount,
    )

    tx = withdraw_fn.build_transaction(
        {
            "from": hot_wallet.address,
            "gas": 350_000,
        }
    )
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)

    if isinstance(expected_exception, Exception):
        with pytest.raises(type(expected_exception)) as e:
            assert_transaction_success_with_explanation(web3, tx_hash)

        assert str(expected_exception) == e.value.revert_reason

        # revert reason should be in message as well
        assert str(expected_exception) in str(e.value)
    else:
        # withdraw successfully
        assert_transaction_success_with_explanation(web3, tx_hash)

        # check balance after withdrawal
        assert usdc.contract.functions.balanceOf(hot_wallet.address).call() == withdraw_amount
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
