"""Test Aave v3 loan."""

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexAddress, HexStr
from web3 import HTTPProvider, Web3
from web3.contract import Contract

from eth_defi.aave_v3.constants import MAX_AMOUNT
from eth_defi.aave_v3.deployment import AaveV3Deployment
from eth_defi.aave_v3.loan import borrow, repay, supply, withdraw
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
        "PoolProxy",
    )

    data_provider = aave_deployment_snapshot.get_contract_at_address(
        web3,
        "core-v3/contracts/misc/AaveProtocolDataProvider.sol/AaveProtocolDataProvider.json",
        "PoolDataProvider",
    )

    oracle = aave_deployment_snapshot.get_contract_at_address(
        web3,
        "core-v3/contracts/misc/AaveOracle.sol/AaveOracle.json",
        "AaveOracle",
    )

    return AaveV3Deployment(
        web3=web3,
        pool=pool,
        data_provider=data_provider,
        oracle=oracle,
    )


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
        with pytest.raises(type(expected_exception)) as e:
            assert_transaction_success_with_explanation(web3, tx_hash)

        assert str(expected_exception) == e.value.revert_reason

        # revert reason should be in message as well
        assert str(expected_exception) in str(e.value)
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
    usdc_reserve_conf = aave_v3_deployment.get_configuration_data(usdc.address)
    assert usdc_reserve_conf.decimals == 6
    assert usdc_reserve_conf.ltv == 8000  # 8000bps = 80%
    assert usdc_reserve_conf.stable_borrow_rate_enabled is True

    weth_reserve_conf = aave_v3_deployment.get_configuration_data(weth.address)
    assert weth_reserve_conf.decimals == 18
    assert weth_reserve_conf.liquidation_threshold == 8250  # 82.5%
    assert weth_reserve_conf.stable_borrow_rate_enabled is False


def test_aave_v3_oracle(
    web3: Web3,
    aave_deployment_snapshot,
    aave_v3_deployment,
    usdc,
    weth,
):
    """Test borrow in Aave v3."""

    usdc_agg = aave_deployment_snapshot.get_contract_at_address(
        web3,
        "core-v3/contracts/mocks/oracle/CLAggregators/MockAggregator.sol/MockAggregator.json",
        "USDCAgg",
    )
    weth_agg = aave_deployment_snapshot.get_contract_at_address(
        web3,
        "core-v3/contracts/mocks/oracle/CLAggregators/MockAggregator.sol/MockAggregator.json",
        "WETHAgg",
    )

    usdc_price = aave_v3_deployment.get_price(usdc.address)
    assert usdc_price / 1e8 == 1  # MockAggregator has hardcode decimals = 8
    assert usdc_price == usdc_agg.functions.latestAnswer().call()

    weth_price = aave_v3_deployment.get_price(weth.address)
    assert weth_price / 1e8 == 4_000
    assert weth_price == weth_agg.functions.latestAnswer().call()


@pytest.mark.parametrize(
    "borrow_token_symbol,borrow_amount,expected_exception",
    [
        # 1 USDC
        ("usdc", 1 * 10**6, None),
        # borrow 8000 USDC should work as USDC reserve LTV is 80%
        ("usdc", 8_000 * 10**6, None),
        # try to borrow 8001 USDC should fail as collateral is 10k USDC and reserve LTV is 80%
        # error code 36 = 'There is not enough collateral to cover a new borrow'
        # https://github.com/aave/aave-v3-core/blob/e0bfed13240adeb7f05cb6cbe5e7ce78657f0621/contracts/protocol/libraries/helpers/Errors.sol#L45
        ("usdc", 8_001 * 10**6, TransactionAssertionError("execution reverted: 36")),
        # TODO: more test case for borrowing ETH
        # 2 WETH = 4000 USDC
        # ("weth", 1 * 10**18, None),
        # 2.1 WETH should fail
        # ("weth", int(2.1 * 10**18), TransactionAssertionError("execution reverted: 36")),
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
    borrow_token_symbol: str,
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
        with pytest.raises(type(expected_exception)) as e:
            assert_transaction_success_with_explanation(web3, tx_hash)

        assert str(expected_exception) == e.value.revert_reason

        # revert reason should be in message as well
        assert str(expected_exception) in str(e.value)
    else:
        # borrow successfully
        assert_transaction_success_with_explanation(web3, tx_hash)

        # check balance after borrow
        assert borrow_asset.functions.balanceOf(hot_wallet.address).call() == borrow_amount


@pytest.mark.parametrize(
    "borrow_token_symbol,borrow_amount,repay_amount,topup_amount,expected_exception",
    [
        # borrow 8k USDC then repay same amount
        ("usdc", 8_000 * 10**6, 8_000 * 10**6, 0, None),
        # partial repay
        ("usdc", 8_000 * 10**6, 4_000 * 10**6, 0, None),
        # repay everything: capital + interest
        ("usdc", 8_000 * 10**6, MAX_AMOUNT, 1_000 * 10**6, None),
        # repay everything: capital + interest
        # currently set to fail since hot wallet doesn't have enough to repay interest
        ("usdc", 8_000 * 10**6, MAX_AMOUNT, 0, TransactionAssertionError("execution reverted: ERC20: transfer amount exceeds balance")),
    ],
)
def test_aave_v3_repay(
    web3: Web3,
    aave_v3_deployment,
    hot_wallet: LocalAccount,
    usdc,
    ausdc,
    weth,
    faucet,
    usdc_supply_amount: int,
    borrow_token_symbol: str,
    borrow_amount: int,
    repay_amount: int,
    topup_amount: int,
    expected_exception: Exception | None,
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
        with pytest.raises(type(expected_exception)) as e:
            assert_transaction_success_with_explanation(web3, tx_hash)

        assert str(expected_exception) == e.value.revert_reason

        # revert reason should be in message as well
        assert str(expected_exception) in str(e.value)
    else:
        # repay successfully
        assert_transaction_success_with_explanation(web3, tx_hash)

        # TODO: check amount of remaining debt
        # assert borrow_asset.functions.balanceOf(hot_wallet.address).call() == borrow_amount
