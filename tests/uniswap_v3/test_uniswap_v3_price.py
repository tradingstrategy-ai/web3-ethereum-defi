"""Test Uniswap v3 price calculation."""
import pytest
import secrets
from decimal import Decimal
from eth_account import Account
from eth_account.signers.local import LocalAccount
from hexbytes import HexBytes
from web3 import EthereumTesterProvider, Web3
from web3.contract import Contract
from web3._utils.transactions import fill_nonce

from eth_defi.token import create_token
from eth_defi.uniswap_v3.utils import get_default_tick_range, encode_path
from eth_defi.uniswap_v3.deployment import (
    FOREVER_DEADLINE,
    UniswapV3Deployment,
    deploy_pool,
    deploy_uniswap_v3,
    add_liquidity
)
from eth_defi.uniswap_v3.price import (
    UniswapV3PriceHelper,
    estimate_buy_price,
    estimate_buy_quantity,
    estimate_buy_received_amount_raw,
    estimate_sell_price,
    estimate_sell_received_amount_raw,
)


WETH_USDC_FEE_RAW = 3000
WETH_DAI_FEE_RAW = 3000


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
def weth_usdc_uniswap_pool(web3, uniswap_v3, weth, usdc, deployer) -> Contract:
    """Mock WETH-USDC pool."""
    
    min_tick, max_tick = get_default_tick_range(WETH_USDC_FEE_RAW)

    pool_contract = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=weth,
        token1=usdc,
        fee=WETH_USDC_FEE_RAW,
    )

    add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool_contract,
        amount0=10 * 10**18,  # 10 ETH liquidity
        amount1=17_000 * 10**18,  # 17000 USDC liquidity
        lower_tick=min_tick,
        upper_tick=max_tick,
    )

    return pool_contract.address


@pytest.fixture()
def weth_dai_uniswap_pool(web3, uniswap_v3, weth, dai, deployer) -> Contract:
    """Mock WETH-DAI pool."""
    
    min_tick, max_tick = get_default_tick_range(WETH_DAI_FEE_RAW)

    pool_contract = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=weth,
        token1=dai,
        fee=WETH_DAI_FEE_RAW,
    )

    add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool_contract,
        amount0=10 * 10**18,  # 10 ETH liquidity
        amount1=17_200 * 10**18,  # 17200 DAI liquidity
        lower_tick=min_tick,
        upper_tick=max_tick,
    )

    return pool_contract.address


def test_price_helper(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    dai: Contract,
):
    """Test price helper.

    Since the setup part is fairly slow, we test multiple input/output in the same test

    Based on: https://github.com/Uniswap/v3-sdk/blob/1a74d5f0a31040fec4aeb1f83bba01d7c03f4870/src/entities/trade.test.ts
    """
    # setup 2 pools
    fee = 3000
    pool1 = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=weth,
        token1=usdc,
        fee=fee,
    )
    pool2 = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=usdc,
        token1=dai,
        fee=fee,
    )

    # add same liquidity amount to both pools as in SDK tests
    min_tick, max_tick = get_default_tick_range(fee)
    add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool1,
        amount0=100_000,
        amount1=100_000,
        lower_tick=min_tick,
        upper_tick=max_tick,
    )
    add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool2,
        amount0=120_000,
        amount1=100_000,
        lower_tick=min_tick,
        upper_tick=max_tick,
    )

    price_helper = UniswapV3PriceHelper(uniswap_v3)

    # test get_amount_out, based on: https://github.com/Uniswap/v3-sdk/blob/1a74d5f0a31040fec4aeb1f83bba01d7c03f4870/src/entities/trade.test.ts#L394
    for slippage, expected_amount_out in [
        (0, 7004),
        (5 * 100, 6670),
        (200 * 100, 2334),
    ]:
        amount_out = price_helper.get_amount_out(
            10_000,
            [
                weth.address,
                usdc.address,
                dai.address,
            ],
            [fee, fee],
            slippage=slippage,
        )

        assert amount_out == expected_amount_out

    # test get_amount_in, based on: https://github.com/Uniswap/v3-sdk/blob/1a74d5f0a31040fec4aeb1f83bba01d7c03f4870/src/entities/trade.test.ts#L361
    for slippage, expected_amount_in in [
        (0, 15488),
        (5 * 100, 16262),
        (200 * 100, 46464),
    ]:
        amount_in = price_helper.get_amount_in(
            10_000,
            [
                weth.address,
                usdc.address,
                dai.address,
            ],
            [fee, fee],
            slippage=slippage,
        )

        assert amount_in == expected_amount_in


def test_estimate_quantity(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    weth_usdc_uniswap_pool: str,
):
    """Estimate quantity."""

    # Estimate how much ETH we will receive for 500 USDC
    amount_eth = estimate_buy_quantity(
        uniswap_v3,
        weth.address,
        usdc.address,
        500 * 10**18,
        WETH_USDC_FEE_RAW,
    )
    assert amount_eth / 1e18 == pytest.approx(0.28488156127668085)


def test_estimate_buy_price(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    weth_usdc_uniswap_pool: str,
):
    """Estimate buy price."""

    # Estimate how much USDC we will need to buy 1 ETH
    usdc_per_eth = estimate_buy_price(
        uniswap_v3,
        weth.address,
        usdc.address,
        1 * 10**18,
        WETH_USDC_FEE_RAW,
    )
    assert usdc_per_eth / 1e18 == pytest.approx(1894.572606709)

    usdc_per_eth = estimate_buy_price(
        uniswap_v3,
        weth.address,
        usdc.address,
        1 * 10**18,
        WETH_USDC_FEE_RAW,
        slippage=500,
    )


def test_estimate_sell_price(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    weth_usdc_uniswap_pool: str,
):
    """Estimate sell price."""

    # Estimate the price of selling 1 ETH
    usdc_per_eth = estimate_sell_price(
        uniswap_v3,
        weth.address,
        usdc.address,
        1 * 10**18,
        WETH_USDC_FEE_RAW,
    )
    price_as_usd = usdc_per_eth / 1e18
    assert price_as_usd == pytest.approx(1541.2385195962538)

    # Estimate the price of selling 1 ETH with slippage 5%
    usdc_per_eth = estimate_sell_price(
        uniswap_v3,
        weth.address,
        usdc.address,
        1 * 10**18,
        WETH_USDC_FEE_RAW,
        slippage=500,
    )
    price_as_usd = usdc_per_eth / 1e18
    assert price_as_usd == pytest.approx(1467.8462091392892)


def test_buy_sell_round_trip(
    web3: Web3,
    deployer: str,
    user_1,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    weth_usdc_uniswap_pool: str,
):
    """Buys some token, then sells it.

    Does a full round trip of trade and see how much money we lost.
    """

    router = uniswap_v3.swap_router

    # Give user_1 500 USD to buy ETH
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(user_1, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": user_1})

    # Perform a swap USDC->WETH
    path = [usdc.address, weth.address]  # Path tell how the swap is routed
    router.functions.exactInput(
        (
            encode_path(path, [WETH_USDC_FEE_RAW]),  # path
            user_1,  # recipient
            FOREVER_DEADLINE,  # deadline
            usdc_amount_to_pay,  # amountIn
            0,  # amountOutMinimum
        )
    ).transact({"from": user_1})

    all_weth_amount = weth.functions.balanceOf(user_1).call()
    weth.functions.approve(router.address, all_weth_amount).transact({"from": user_1})

    # Perform the reverse swap WETH->USDC
    reverse_path = [weth.address, usdc.address]  # Path tell how the swap is routed
    router.functions.exactInput(
        (
            encode_path(reverse_path, [WETH_USDC_FEE_RAW]),
            user_1,
            FOREVER_DEADLINE,
            all_weth_amount,
            0,
        )
    ).transact({"from": user_1})

    # user_1 has less than 500 USDC left to loses in the LP fees
    usdc_left = usdc.functions.balanceOf(user_1).call() / (10.0**18)
    assert usdc_left == pytest.approx(497.0469798558948)


def test_swap_price_from_hot_wallet(
    web3: Web3,
    deployer: str,
    hot_wallet: LocalAccount,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    weth_usdc_uniswap_pool: str,
):
    """Use local hot wallet to buy WETH on Uniswap v2 using mock USDC."""

    router = uniswap_v3.swap_router
    hw_address = hot_wallet.address

    # Give hot wallet some cash to buy ETH (also some ETH as well to sign tx)
    # and approve it on the router
    web3.eth.send_transaction({"from": deployer, "to": hw_address, "value": 1 * 10**18})
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(hw_address, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": hw_address})

    # Perform a swap USDC->WETH
    path = [usdc.address, weth.address]
    tx = router.functions.exactInput(
        (
            encode_path(path, [WETH_USDC_FEE_RAW]),  # path
            hw_address,  # recipient
            FOREVER_DEADLINE,  # deadline
            usdc_amount_to_pay,  # amountIn
            0,  # amountOutMinimum
        )
    ).build_transaction({"from": hw_address})

    # prepare and sign tx
    tx = fill_nonce(web3, tx)
    signed = hot_wallet.sign_transaction(tx)

    # estimate the quantity before sending transaction
    amount_eth = estimate_sell_price(
        uniswap_v3,
        usdc.address,
        weth.address,
        usdc_amount_to_pay,
        WETH_USDC_FEE_RAW,
    )

    tx_hash = web3.eth.send_raw_transaction(signed.rawTransaction)
    tx_receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert tx_receipt.status == 1  # 1=success and mined

    # check if hot wallet get the same ETH amount estimated earlier
    assert weth.functions.balanceOf(hw_address).call() == pytest.approx(amount_eth)
    # precision test
    assert weth.functions.balanceOf(hw_address).call() == amount_eth


def test_estimate_price_three_way(
    deployer: str,
    user_1,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    dai: Contract,
    weth_dai_uniswap_pool: str,
    weth_usdc_uniswap_pool: str,
):
    """User buys DAI on Uniswap v3 using mock USDC through WETH"""

    router = uniswap_v3.swap_router

    # Give user_1 some cash to buy DAI and approve it on the router
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(user_1, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": user_1})

    # Estimate the DAI amount user will get
    dai_amount = estimate_sell_price(
        uniswap_v3,
        usdc.address,
        dai.address,
        quantity=usdc_amount_to_pay,
        target_pair_fee=WETH_DAI_FEE_RAW,
        intermediate_token_address=weth.address,
        intermediate_pair_fee=WETH_USDC_FEE_RAW,
    )

    # Perform a swap USDC->WETH->DAI
    path = [usdc.address, weth.address, dai.address]

    # https://docs.uniswap.org/protocol/V2/reference/smart-contracts/router-02#swapexacttokensfortokens
    router.functions.exactInput(
        (
            encode_path(path, [WETH_USDC_FEE_RAW, WETH_DAI_FEE_RAW]),  # path
            user_1,  # recipient
            FOREVER_DEADLINE,  # deadline
            usdc_amount_to_pay,  # amountIn
            0,  # amountOutMinimum
        )
    ).transact({"from": user_1})

    # Compare the amount user receives to the estimation ealier
    assert dai.functions.balanceOf(user_1).call() == pytest.approx(dai_amount)
    # precision test
    assert dai.functions.balanceOf(user_1).call() == dai_amount


def test_estimate_buy_price_for_cash(
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    weth_usdc_uniswap_pool: str,
):
    """Estimate how much asset we receive for a given cash buy."""

    # Estimate the price of buying 1650 USDC worth of ETH
    eth_received = estimate_buy_received_amount_raw(
        uniswap_v3,
        weth.address,
        usdc.address,
        1650 * 10**18,
        WETH_USDC_FEE_RAW,
    )

    assert eth_received / (10**18) == pytest.approx(0.8822985189098446)

    # Calculate price of ETH as $ for our purchase
    price = (1650 * 10**18) / eth_received
    assert price == pytest.approx(Decimal(1870.1153460381145))


def test_estimate_sell_received_cash(
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    weth_usdc_uniswap_pool: str,
):
    """Estimate how much asset we receive for a given cash buy."""

    # Sell 50 ETH
    usdc_received = estimate_sell_received_amount_raw(
        uniswap_v3,
        weth.address,
        usdc.address,
        50 * 10**18,
        WETH_USDC_FEE_RAW,
    )

    usdc_received_decimals = usdc_received / 10**18
    assert usdc_received_decimals == pytest.approx(14159.565580618213)

    # Calculate price of ETH as $ for our purchase
    # Pool only starts with 10 eth, and we are selling 50, so we should not expect to get a good price
    price = usdc_received / (50 * 10**18)
    assert price == pytest.approx(Decimal(283.19131161236425))