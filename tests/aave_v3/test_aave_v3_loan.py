"""Test Aave v3 loan."""

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr
from web3 import HTTPProvider, Web3
from web3.contract import Contract

from eth_defi.aave_v3.deployment import AaveV3Deployment
from eth_defi.aave_v3.loan import borrow, supply, withdraw
from eth_defi.hotwallet import HotWallet
from eth_defi.trace import (
    TransactionAssertionError,
    assert_transaction_success_with_explanation,
)


@pytest.fixture(scope="module")
def usdc(web3, aave_deployment_snapshot) -> Contract:
    """USDC on Polygon."""
    return aave_deployment_snapshot.get_contract_at_address(
        web3,
        "core-v3/contracts/mocks/tokens/MintableERC20.sol/MintableERC20.json",
        "USDC",
    )


@pytest.fixture(scope="module")
def weth(web3, aave_deployment_snapshot) -> Contract:
    """WETH on Polygon."""
    return aave_deployment_snapshot.get_contract_at_address(
        web3,
        "core-v3/contracts/mocks/tokens/WETH9Mocked.sol/WETH9Mocked.json",
        "WETH",
    )


@pytest.fixture(scope="module")
def aave_v3_deployment(web3, aave_deployment_snapshot):
    pool = aave_deployment_snapshot.get_contract_at_address(
        web3,
        "core-v3/contracts/protocol/pool/Pool.sol/Pool.json",
        "Pool",
    )

    return AaveV3Deployment(web3=web3, pool=pool)


@pytest.fixture(scope="module")
def ausdc(web3, aave_deployment_snapshot) -> Contract:
    """aToken for USDC"""
    return aave_deployment_snapshot.get_contract_at_address(
        web3,
        "core-v3/contracts/protocol/tokenization/AToken.sol/AToken.json",
        "aUSDC",
    )


@pytest.fixture
def usdc_supply_amount() -> int:
    # 10k USDC
    return 10_000 * 10**6


@pytest.fixture
def hot_wallet(
    web3,
    usdc,
    usdc_supply_amount,
    aave_deployment_snapshot,
) -> HotWallet:
    """Hotwallet account."""
    hw = HotWallet(Account.create())
    hw.sync_nonce(web3)

    # give hot wallet some native token
    web3.eth.send_transaction(
        {
            "from": web3.eth.accounts[9],
            "to": hw.address,
            "value": 1 * 10**18,
        }
    )

    # and USDC
    faucet = aave_deployment_snapshot.get_contract_at_address(
        web3,
        "periphery-v3/contracts/mocks/testnet-helpers/Faucet.sol/Faucet.json",
        "Faucet",
    )
    tx_hash = faucet.functions.mint(usdc.address, hw.address, usdc_supply_amount).transact()
    assert_transaction_success_with_explanation(web3, tx_hash)

    return hw


def _test_supply(
    web3,
    aave_v3_deployment,
    hot_wallet,
    usdc,
    ausdc,
    usdc_supply_amount,
):
    # supply USDC to Aave
    approve_fn, supply_fn = supply(
        aave_v3_deployment=aave_v3_deployment,
        wallet_address=hot_wallet.address,
        token=usdc,
        amount=usdc_supply_amount,
    )

    tx = approve_fn.build_transaction({"from": hot_wallet.address, "gas": 200_000})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    tx = supply_fn.build_transaction({"from": hot_wallet.address, "gas": 350_000})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # verify aUSDC token amount in hot wallet
    assert ausdc.functions.balanceOf(hot_wallet.address).call() == usdc_supply_amount


def test_aave_v3_supply(
    web3: Web3,
    aave_v3_deployment,
    usdc,
    hot_wallet: LocalAccount,
    ausdc,
    usdc_supply_amount: int,
):
    """Test that the deposit in Aave v3 is correctly registered
    and the corresponding aToken is received.
    """
    _test_supply(
        web3,
        aave_v3_deployment,
        hot_wallet,
        usdc,
        ausdc,
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
    aave_v3_deployment,
    usdc,
    hot_wallet: LocalAccount,
    usdc_supply_amount: int,
    ausdc,
    withdraw_factor: float,
    expected_exception: Exception | None,
):
    """Test withdraw in Aave v3 with different amount threshold."""
    _test_supply(
        web3,
        aave_v3_deployment,
        hot_wallet,
        usdc,
        ausdc,
        usdc_supply_amount,
    )

    # withdraw
    withdraw_amount = int(usdc_supply_amount * withdraw_factor)
    withdraw_fn = withdraw(
        aave_v3_deployment=aave_v3_deployment,
        wallet_address=hot_wallet.address,
        token=usdc,
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
        assert usdc.functions.balanceOf(hot_wallet.address).call() == withdraw_amount


@pytest.mark.parametrize(
    "borrow_amount,expected_exception",
    [
        # 1 ETH
        (1 * 10**6, None),
        # try to borrow 1000 ETH should fail as collateral is only 10k USDC
        # error code 36 = 'There is not enough collateral to cover a new borrow'
        # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/libraries/helpers/Errors.sol#L45
        (1000 * 10**18, TransactionAssertionError("execution reverted: 36")),
    ],
)
def test_aave_v3_borrow(
    web3: Web3,
    aave_v3_deployment,
    hot_wallet: LocalAccount,
    usdc,
    ausdc,
    weth,
    usdc_supply_amount: int,
    borrow_amount: int,
    expected_exception: Exception | None,
):
    """Test borrow in Aave v3."""
    _test_supply(
        web3,
        aave_v3_deployment,
        hot_wallet,
        usdc,
        ausdc,
        usdc_supply_amount,
    )

    # try to borrow ETH
    borrow_fn = borrow(
        aave_v3_deployment=aave_v3_deployment,
        wallet_address=hot_wallet.address,
        # TODO: check how to be able to borrow WETH
        token=usdc,
        amount=borrow_amount,
    )
    tx = borrow_fn.build_transaction(
        {
            "from": hot_wallet.address,
            "gas": 400_000,
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
        assert usdc.functions.balanceOf(hot_wallet.address).call() == borrow_amount
