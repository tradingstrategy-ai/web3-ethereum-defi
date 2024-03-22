"""Test Aave v3 loan."""

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr
from web3 import Web3
from web3.contract import Contract

from eth_defi.aave_v3.constants import MAX_AMOUNT
from eth_defi.aave_v3.deployment import AaveV3Deployment
from eth_defi.aave_v3.loan import borrow, repay, supply, withdraw
from eth_defi.hotwallet import HotWallet
from eth_defi.trace import (
    TransactionAssertionError,
    assert_transaction_success_with_explanation,
)


@pytest.fixture()
def ausdc(web3, aave_deployment) -> Contract:
    """aToken for USDC on local testnet."""
    return aave_deployment.get_contract_at_address(web3, "AToken.json", "aUSDC")


@pytest.fixture
def aave_v3_deployment(web3, aave_deployment):
    pool = aave_deployment.get_contract_at_address(web3, "Pool.json", "PoolProxy")

    data_provider = aave_deployment.get_contract_at_address(web3, "AaveProtocolDataProvider.json", "PoolDataProvider")

    oracle = aave_deployment.get_contract_at_address(web3, "AaveOracle.json", "AaveOracle")

    return AaveV3Deployment(
        web3=web3,
        pool=pool,
        data_provider=data_provider,
        oracle=oracle,
    )


@pytest.fixture
def aave_v3_weth_reserve(
    web3,
    aave_v3_deployment,
    weth,
    faucet,
    deployer,
):
    """Seed WETH reserve with 100 WETH liquidity"""
    # give deployer some WETH
    tx_hash = faucet.functions.mint(weth.address, deployer, 100_000 * 10**18).transact()
    assert_transaction_success_with_explanation(web3, tx_hash)

    # supply to WETH reserve
    approve_fn, supply_fn = supply(
        aave_v3_deployment=aave_v3_deployment,
        wallet_address=deployer,
        token=weth,
        amount=100 * 10**18,
    )
    approve_fn.transact({"from": deployer})
    tx_hash = supply_fn.transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)


@pytest.fixture
def usdc_supply_amount() -> int:
    # 10k USDC
    return 10_000 * 10**6


@pytest.fixture
def hot_wallet(
    web3,
    usdc,
    usdc_supply_amount,
    faucet,
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

    # nothing left in USDC balance
    assert usdc.functions.balanceOf(hot_wallet.address).call() == 0

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
        # withdraw everything
        (MAX_AMOUNT, None),
        # over withdraw should fail
        # error code 32 = 'User cannot withdraw more than the available balance'
        # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/libraries/helpers/Errors.sol#L41
        (1.0001, TransactionAssertionError("32")),
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

    if withdraw_factor == MAX_AMOUNT:
        withdraw_amount = withdraw_factor
    else:
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
        with pytest.raises(type(expected_exception), match=str(expected_exception)) as e:
            assert_transaction_success_with_explanation(web3, tx_hash)

        assert str(expected_exception) in e.value.revert_reason
    else:
        # withdraw successfully
        assert_transaction_success_with_explanation(web3, tx_hash)

        # check balance after withdrawal
        if withdraw_amount == MAX_AMOUNT:
            assert usdc.functions.balanceOf(hot_wallet.address).call() == usdc_supply_amount
        else:
            assert usdc.functions.balanceOf(hot_wallet.address).call() == withdraw_amount


def test_aave_v3_reserve_configuration(
    aave_v3_deployment,
    usdc,
    weth,
):
    """Test getting correct reserve configuration in Aave v3."""
    usdc_reserve_conf = aave_v3_deployment.get_reserve_configuration_data(usdc.address)
    assert usdc_reserve_conf.decimals == 6
    assert usdc_reserve_conf.ltv == 8000  # 8000bps = 80%
    assert usdc_reserve_conf.stable_borrow_rate_enabled is True

    weth_reserve_conf = aave_v3_deployment.get_reserve_configuration_data(weth.address)
    assert weth_reserve_conf.decimals == 18
    assert weth_reserve_conf.liquidation_threshold == 8250  # 82.5%
    assert weth_reserve_conf.stable_borrow_rate_enabled is False


def test_aave_v3_oracle(
    web3: Web3,
    aave_deployment,
    aave_v3_deployment,
    usdc,
    weth,
):
    """Test Aave oracle and latest mock price."""

    usdc_agg = aave_deployment.get_contract_at_address(web3, "MockAggregator.json", "USDCAgg")
    weth_agg = aave_deployment.get_contract_at_address(web3, "MockAggregator.json", "WETHAgg")

    usdc_price = aave_v3_deployment.get_price(usdc.address)
    assert usdc_price / 1e8 == 1  # MockAggregator has hardcode decimals = 8
    assert usdc_price == usdc_agg.functions.latestAnswer().call()

    weth_price = aave_v3_deployment.get_price(weth.address)
    assert weth_price / 1e8 == 4_000
    assert weth_price == weth_agg.functions.latestAnswer().call()


@pytest.mark.parametrize(
    "borrow_token_symbol,borrow_amount,expected_exception,health_factor",
    [
        # 1 USDC
        ("usdc", 1 * 10**6, None, 8500000000000000000000),
        # borrow 8000 USDC should work as USDC reserve LTV is 80%
        ("usdc", 8_000 * 10**6, None, 1062500000000000000),
        # try to borrow 8001 USDC should fail as collateral is 10k USDC and reserve LTV is 80%
        # error code 36 = 'There is not enough collateral to cover a new borrow'
        # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/libraries/helpers/Errors.sol#L45
        ("usdc", 8_001 * 10**6, TransactionAssertionError("36"), None),
        # TODO: more test case for borrowing ETH
        # 1 WETH = 4000 USDC
        ("weth", 1 * 10**18, None, 2125000000000000000),
        # 2.1 WETH (8400 USDC) should fail
        ("weth", int(2.1 * 10**18), TransactionAssertionError("36"), None),
    ],
)
def test_aave_v3_borrow(
    web3: Web3,
    aave_v3_deployment,
    hot_wallet: LocalAccount,
    usdc,
    ausdc,
    weth,
    aave_v3_weth_reserve,
    usdc_supply_amount: int,
    borrow_token_symbol: str,
    borrow_amount: int,
    expected_exception: Exception | None,
    health_factor: int | None,
):
    """Test borrow in Aave v3 with different amount threshold."""
    _test_supply(
        web3,
        aave_v3_deployment,
        hot_wallet,
        usdc,
        ausdc,
        usdc_supply_amount,
    )

    borrow_asset = {
        "usdc": usdc,
        "weth": weth,
    }[borrow_token_symbol]

    # try to borrow ETH
    borrow_fn = borrow(
        aave_v3_deployment=aave_v3_deployment,
        wallet_address=hot_wallet.address,
        token=borrow_asset,
        amount=borrow_amount,
    )
    tx = borrow_fn.build_transaction(
        {
            "from": hot_wallet.address,
            "gas": 350_000,
        }
    )
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)

    if isinstance(expected_exception, Exception):
        with pytest.raises(type(expected_exception), match=str(expected_exception)) as e:
            assert_transaction_success_with_explanation(web3, tx_hash)

        assert str(expected_exception) in e.value.revert_reason
    else:
        # borrow successfully
        assert_transaction_success_with_explanation(web3, tx_hash)

        # check balance after borrow
        assert borrow_asset.functions.balanceOf(hot_wallet.address).call() == borrow_amount

        # check user current data
        user_data = aave_v3_deployment.get_user_data(hot_wallet.address)

        # (total_collateral_base=1000000000000, total_debt_base=100000000, available_borrows_base=799900000000, current_liquidation_threshold=8500, ltv=8000, health_factor=8500000000000000000000)
        assert user_data.total_collateral_base / 1e8 == usdc_supply_amount / 1e6

        if borrow_token_symbol == "usdc":
            assert user_data.total_debt_base / 1e8 == borrow_amount / 1e6
        assert user_data.health_factor == health_factor


@pytest.mark.parametrize(
    "borrow_token_symbol,borrow_amount,repay_amount,topup_amount,expected_exception,remaining_debt",
    [
        # borrow 8k USDC then repay same amount
        ("usdc", 8_000 * 10**6, 8_000 * 10**6, 0, None, 1800),
        # partial repay
        ("usdc", 8_000 * 10**6, 4_000 * 10**6, 0, None, 400000001800),
        # repay everything: capital + interest
        ("usdc", 8_000 * 10**6, MAX_AMOUNT, 1_000 * 10**6, None, 0),
        # repay everything: capital + interest
        # currently set to fail since hot wallet doesn't have enough to repay interest
        ("usdc", 8_000 * 10**6, MAX_AMOUNT, 0, TransactionAssertionError("ERC20: transfer amount exceeds balance"), None),
    ],
)
def test_aave_v3_repay(
    web3: Web3,
    aave_v3_deployment,
    hot_wallet: LocalAccount,
    usdc,
    ausdc,
    weth,
    aave_v3_weth_reserve,
    faucet,
    usdc_supply_amount: int,
    borrow_token_symbol: str,
    borrow_amount: int,
    repay_amount: int,
    topup_amount: int,
    expected_exception: Exception | None,
    remaining_debt: int,
):
    """Test repay in Aave v3."""
    _test_supply(
        web3,
        aave_v3_deployment,
        hot_wallet,
        usdc,
        ausdc,
        usdc_supply_amount,
    )

    borrow_asset = {
        "usdc": usdc,
        "weth": weth,
    }[borrow_token_symbol]

    # borrow
    borrow_fn = borrow(
        aave_v3_deployment=aave_v3_deployment,
        wallet_address=hot_wallet.address,
        token=borrow_asset,
        amount=borrow_amount,
    )
    tx = borrow_fn.build_transaction(
        {
            "from": hot_wallet.address,
            "gas": 350_000,
        }
    )
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert borrow_asset.functions.balanceOf(hot_wallet.address).call() == borrow_amount

    # top up the balance a bit to cover interests
    if topup_amount > 0:
        tx_hash = faucet.functions.mint(usdc.address, hot_wallet.address, topup_amount).transact()
        assert_transaction_success_with_explanation(web3, tx_hash)

    # try to repay
    approve_fn, repay_fn = repay(
        aave_v3_deployment=aave_v3_deployment,
        wallet_address=hot_wallet.address,
        token=borrow_asset,
        amount=repay_amount,
    )

    # approve first
    tx = approve_fn.build_transaction({"from": hot_wallet.address, "gas": 200_000})
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    assert_transaction_success_with_explanation(web3, tx_hash)

    # then repay
    tx = repay_fn.build_transaction(
        {
            "from": hot_wallet.address,
            "gas": 350_000,
        }
    )
    signed = hot_wallet.sign_transaction_with_new_nonce(tx)
    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)

    if isinstance(expected_exception, Exception):
        with pytest.raises(type(expected_exception), match=str(expected_exception)) as e:
            assert_transaction_success_with_explanation(web3, tx_hash)

        assert str(expected_exception) in e.value.revert_reason
    else:
        # repay successfully
        assert_transaction_success_with_explanation(web3, tx_hash)

        # check amount of remaining debt
        # total_collateral_base=1000000001600, total_debt_base=1800, available_borrows_base=799999999480, current_liquidation_threshold=8500, ltv=8000, health_factor=472222222977777777777777778
        user_data = aave_v3_deployment.get_user_data(hot_wallet.address)
        assert user_data.total_debt_base == remaining_debt
