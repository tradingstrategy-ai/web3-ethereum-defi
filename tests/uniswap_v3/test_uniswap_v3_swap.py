"""Test Uniswap v3 swap functions."""
import secrets

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from hexbytes import HexBytes
from web3 import EthereumTesterProvider, Web3
from web3._utils.transactions import fill_nonce
from web3.contract import Contract

from eth_defi.gas import apply_gas, estimate_gas_fees
from eth_defi.revert_reason import fetch_transaction_revert_reason
from eth_defi.token import create_token
from eth_defi.uniswap_v3.constants import FOREVER_DEADLINE
from eth_defi.uniswap_v3.deployment import (
    UniswapV3Deployment,
    add_liquidity,
    deploy_pool,
    deploy_uniswap_v3,
)
from eth_defi.uniswap_v3.price import UniswapV3PriceHelper
from eth_defi.uniswap_v3.swap import swap_with_slippage_protection
from eth_defi.uniswap_v3.utils import encode_path, get_default_tick_range


@pytest.fixture
def tester_provider():
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return EthereumTesterProvider()


@pytest.fixture
def eth_tester(tester_provider):
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return tester_provider.ethereum_tester


@pytest.fixture
def web3(tester_provider):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return Web3(tester_provider)


@pytest.fixture()
def deployer(web3) -> str:
    """Deploy account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[0]


@pytest.fixture()
def user_1(web3) -> str:
    """User account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[1]


@pytest.fixture()
def uniswap_v3(web3, deployer) -> UniswapV3Deployment:
    """Uniswap v3 deployment."""
    deployment = deploy_uniswap_v3(web3, deployer)
    return deployment


@pytest.fixture()
def usdc(web3, deployer) -> Contract:
    """Mock USDC token.

    Note that this token has 18 decimals instead of 6 of real USDC.
    """
    token = create_token(web3, deployer, "USD Coin", "USDC", 100_000_000 * 10**18)
    return token


@pytest.fixture()
def dai(web3, deployer) -> Contract:
    """Mock USDC token.

    Note that this token has 18 decimals instead of 6 of real USDC.
    """
    token = create_token(web3, deployer, "DAI", "DAI", 100_000_000 * 10**18)
    return token


@pytest.fixture()
def weth(uniswap_v3) -> Contract:
    """Mock WETH token."""
    return uniswap_v3.weth


@pytest.fixture()
def hot_wallet_private_key() -> HexBytes:
    """Generate a private key"""
    return HexBytes(secrets.token_bytes(32))


@pytest.fixture()
def hot_wallet(eth_tester, hot_wallet_private_key) -> LocalAccount:
    """User account.

    Do some account allocation for tests.
    '"""
    # also add to eth_tester so we can use transact() directly
    eth_tester.add_account(hot_wallet_private_key.hex())
    return Account.from_key(hot_wallet_private_key)


@pytest.fixture
def pool_trading_fee():
    return 3000


@pytest.fixture
def weth_usdc_pool(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    pool_trading_fee: int,
):
    pool = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=weth,
        token1=usdc,
        fee=pool_trading_fee,
    )

    min_tick, max_tick = get_default_tick_range(pool_trading_fee)
    add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool,
        amount0=10 * 10**18,
        amount1=20_000 * 10**18,
        lower_tick=min_tick,
        upper_tick=max_tick,
    )

    return pool


@pytest.fixture
def usdc_dai_pool(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    usdc: Contract,
    dai: Contract,
    pool_trading_fee: int,
):
    pool = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=usdc,
        token1=dai,
        fee=pool_trading_fee,
    )

    min_tick, max_tick = get_default_tick_range(pool_trading_fee)
    add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool,
        amount0=30_000 * 10**18,
        amount1=30_000 * 10**18,
        lower_tick=min_tick,
        upper_tick=max_tick,
    )

    return pool


def test_buy_with_slippage_when_you_know_quote_amount(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    weth_usdc_pool: Contract,
    hot_wallet: LocalAccount,
    pool_trading_fee: int,
):
    """Use local hot wallet to buy as much as possible WETH on Uniswap v3 using
    define amout of mock USDC.
    """

    router = uniswap_v3.swap_router
    hw_address = hot_wallet.address

    # Give hot wallet some USDC to buy ETH (also some ETH as well to send tx)
    web3.eth.send_transaction({"from": deployer, "to": hw_address, "value": 1 * 10**18})
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(hw_address, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": hw_address})

    # build transaction
    swap_func = swap_with_slippage_protection(
        uniswap_v3_deployment=uniswap_v3,
        recipient_address=hw_address,
        base_token=weth,
        quote_token=usdc,
        pool_fees=[pool_trading_fee],
        amount_in=usdc_amount_to_pay,
        max_slippage=50,  # 50 bps = 0.5%
    )
    tx = swap_func.build_transaction(
        {
            "from": hw_address,
            "chainId": web3.eth.chain_id,
            "gas": 350_000,  # estimate max 350k gas per swap
        }
    )
    tx = fill_nonce(web3, tx)
    gas_fees = estimate_gas_fees(web3)
    apply_gas(tx, gas_fees)

    # sign and broadcast
    signed_tx = hot_wallet.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert tx_receipt.status == 1


def test_sell_three_way_with_slippage_when_you_know_base_amount(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    dai: Contract,
    weth_usdc_pool: Contract,
    usdc_dai_pool: Contract,
    hot_wallet: LocalAccount,
    pool_trading_fee: int,
):
    """Use local hot wallet to sell a define amount of WETH on Uniswap v3 for DAI"""

    router = uniswap_v3.swap_router
    hw_address = hot_wallet.address

    # Give hot wallet some USDC to buy ETH (also some ETH as well to send tx)
    web3.eth.send_transaction({"from": deployer, "to": hw_address, "value": 1 * 10**18})
    weth_amount_to_sell = 1 * 10**18
    weth.functions.transfer(hw_address, weth_amount_to_sell).transact({"from": deployer})
    weth.functions.approve(router.address, 2 * 10**18).transact({"from": hw_address})

    # build transaction
    # note that we are selling WETH for DAI and swap direction is always
    # from quote (through intermediate) to base: WETH->USDC->DAI
    # so quote_token is WETH and base_token is DAI in this case
    swap_func = swap_with_slippage_protection(
        uniswap_v3_deployment=uniswap_v3,
        recipient_address=hw_address,
        base_token=dai,
        quote_token=weth,
        intermediate_token=usdc,
        pool_fees=[pool_trading_fee, pool_trading_fee],
        amount_in=weth_amount_to_sell,
        max_slippage=50,  # 50 bps = 0.5%
    )
    tx = swap_func.build_transaction(
        {
            "from": hw_address,
            "chainId": web3.eth.chain_id,
            "gas": 350_000,
        }
    )
    tx = fill_nonce(web3, tx)
    gas_fees = estimate_gas_fees(web3)
    apply_gas(tx, gas_fees)

    # sign and broadcast
    signed_tx = hot_wallet.sign_transaction(tx)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert tx_receipt.status == 1


def test_swap_slippage_revert(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    weth_usdc_pool: Contract,
    hot_wallet: LocalAccount,
    user_1,
    pool_trading_fee: int,
):
    """Use local hot wallet to try to buy WETH on Uniswap v3 using mock USDC with slippage protection
    Simulate the scenario when a MEV bot make a faster trade,
    then the original transaction should revert
    """

    router = uniswap_v3.swap_router
    hw_address = hot_wallet.address

    # Give hot wallet some USDC to buy ETH (also some ETH as well to send tx)
    web3.eth.send_transaction({"from": deployer, "to": hw_address, "value": 1 * 10**18})
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(hw_address, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": hw_address})

    # give user_1 some cash as well
    usdc.functions.transfer(user_1, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": user_1})

    # prepare a swap USDC->WETH
    swap_func = swap_with_slippage_protection(
        uniswap_v3_deployment=uniswap_v3,
        recipient_address=hw_address,
        base_token=weth,
        quote_token=usdc,
        pool_fees=[pool_trading_fee],
        amount_in=usdc_amount_to_pay,
        max_slippage=50,  # 50 bps = 0.5%
    )
    tx = swap_func.build_transaction(
        {
            "from": hw_address,
            "chainId": web3.eth.chain_id,
            "gas": 350_000,  # estimate max 350k gas per swap
        }
    )
    tx = fill_nonce(web3, tx)
    gas_fees = estimate_gas_fees(web3)
    apply_gas(tx, gas_fees)
    signed_tx = hot_wallet.sign_transaction(tx)

    # get current price estimation
    price_helper = UniswapV3PriceHelper(uniswap_v3)
    original_price = price_helper.get_amount_out(
        1 * 10**18,
        [weth.address, usdc.address],
        [pool_trading_fee],
        slippage=50,
    )

    # user_2 makes a faster trade which moves the price
    router.functions.exactInput(
        (
            encode_path([usdc.address, weth.address], [pool_trading_fee]),
            user_1,
            FOREVER_DEADLINE,
            110 * 10**18,
            0,
        )
    ).transact({"from": user_1})

    # the price now should be lower than when we create tx1 (we get less ETH back)
    new_price = price_helper.get_amount_out(
        1 * 10**18,
        [weth.address, usdc.address],
        [pool_trading_fee],
        slippage=50,
    )
    price_move_percent = abs(original_price * 100 / new_price - 100)
    assert 1 < price_move_percent < 1.1
    # print(f"Price moved: {price_move_percent} %")

    # now the hot wallet finally manages to broadcast the tx, it should fail
    tx_hash = web3.eth.send_raw_transaction(signed_tx.rawTransaction)
    tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)

    assert tx_receipt.status == 0  # failure

    # confirm the revert reason
    reason = fetch_transaction_revert_reason(web3, tx_hash)
    assert "Too little received" in reason
