import secrets
from decimal import Decimal

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from hexbytes import HexBytes
from web3 import EthereumTesterProvider, Web3
from web3._utils.transactions import fill_nonce
from web3.contract import Contract

from eth_defi.token import create_token, reset_default_token_cache
from eth_defi.tx import get_tx_broadcast_data
from eth_defi.uniswap_v2.deployment import (
    FOREVER_DEADLINE,
    UniswapV2Deployment,
    deploy_trading_pair,
    deploy_uniswap_v2_like,
)
from eth_defi.uniswap_v2.fees import (
    UniswapV2FeeCalculator,
    estimate_buy_price,
    estimate_buy_price_decimals,
    estimate_buy_quantity,
    estimate_buy_received_amount_raw,
    estimate_sell_price,
    estimate_sell_price_decimals,
    estimate_sell_received_amount_raw,
)


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

    # Caching will break this test
    reset_default_token_cache()

    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    yield Web3(tester_provider)


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
def user_2(web3) -> str:
    """User account.

    Do some account allocation for tests.
    """
    return web3.eth.accounts[2]


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
def uniswap_v2(web3, deployer) -> UniswapV2Deployment:
    """Uniswap v2 deployment."""
    return deploy_uniswap_v2_like(web3, deployer)


@pytest.fixture()
def usdc(web3, deployer) -> Contract:
    """Mock USDC token.

    Note that this token has 18 decimals instead of 6 of real USDC.
    """
    token = create_token(web3, deployer, "USD Coin", "USDC", 100_000_000 * 10**18)
    return token


@pytest.fixture()
def weth(uniswap_v2) -> Contract:
    """Mock WETH token."""
    return uniswap_v2.weth


@pytest.fixture()
def dai(web3, deployer) -> Contract:
    """Mock DAI token."""
    return create_token(web3, deployer, "DAI", "DAI", 100_000_000 * 10**18)


def test_get_min_amount_in_from_reserves():
    assert UniswapV2FeeCalculator.get_amount_in_from_reserves(100, 1000, 1000) == 112
    assert UniswapV2FeeCalculator.get_amount_in_from_reserves(100, 10000, 10000) == 102


def test_get_max_amount_out_from_reserves():
    assert UniswapV2FeeCalculator.get_amount_out_from_reserves(100, 1000, 1000) == 90
    assert UniswapV2FeeCalculator.get_amount_out_from_reserves(100, 10000, 10000) == 98


@pytest.mark.parametrize(
    "amount_in,reserves,slippage,expected_amount_out",
    [
        # 0% slippage
        (100, [[1000, 1000], [1200, 1000]], 0, 69),
        # 5% slippage
        (100, [[1000, 1000], [1200, 1000]], 5 * 100, 65),
        # 200% slippage
        (100, [[1000, 1000], [1200, 1000]], 200 * 100, 23),
    ],
)
def test_get_amount_out(uniswap_v2, mocker, amount_in, reserves, slippage, expected_amount_out):
    """Test get min amount out with slippage calculation.

    Based on: https://github.com/Uniswap/v2-sdk/blob/411d9709e6c41fd96cea050169c7080e5c6e11e1/src/entities/trade.test.ts#L251
    """
    # mock get_reserves() to return our intended reserves
    get_reserves_mock = mocker.patch("eth_defi.uniswap_v2.fees.UniswapV2FeeCalculator.get_reserves")
    get_reserves_mock.side_effect = reserves

    # we don't need real token address for this test as long as number of tokens is correct
    mock_path = ["0x1", "0x2", "0x3"]

    helper = UniswapV2FeeCalculator(uniswap_v2)
    amount_out = helper.get_amount_out(amount_in, mock_path, slippage=slippage)
    assert amount_out == expected_amount_out


@pytest.mark.parametrize(
    "amount_out,reserves,slippage,expected_amount_in",
    [
        # # 0% slippage
        (100, [[1200, 1000], [1000, 1000]], 0, 156),
        # 5% slippage
        (100, [[1200, 1000], [1000, 1000]], 5 * 100, 163),
        # 200% slippage
        (100, [[1200, 1000], [1000, 1000]], 200 * 100, 468),
    ],
)
def test_get_amount_in(uniswap_v2, mocker, amount_out, reserves, slippage, expected_amount_in):
    """Test get max amount in with slippage calculation.

    Based on: https://github.com/Uniswap/v2-sdk/blob/411d9709e6c41fd96cea050169c7080e5c6e11e1/src/entities/trade.test.ts#L221
    """
    # mock get_reserves() to return our intended reserves
    get_reserves_mock = mocker.patch("eth_defi.uniswap_v2.fees.UniswapV2FeeCalculator.get_reserves")
    get_reserves_mock.side_effect = reserves

    # we don't need real token address for this test as long as number of tokens is correct
    mock_path = ["0x1", "0x2", "0x3"]

    helper = UniswapV2FeeCalculator(uniswap_v2)
    amount_in = helper.get_amount_in(amount_out, mock_path, slippage=slippage)
    assert amount_in == expected_amount_in


def test_estimate_quantity(
    web3: Web3,
    deployer: str,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Estimate quantity."""

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        10 * 10**18,  # 10 ETH liquidity
        17_000 * 10**18,  # 17000 USDC liquidity
    )

    # Estimate how much ETH we will receive for 500 USDC
    amount_eth = estimate_buy_quantity(
        uniswap_v2,
        weth,
        usdc,
        500 * 10**18,
    )
    assert amount_eth / 1e18 == pytest.approx(0.28488156127668085)


def test_estimate_buy_price(
    web3: Web3,
    deployer: str,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Estimate buy price."""

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        10 * 10**18,  # 10 ETH liquidity
        17_000 * 10**18,  # 17000 USDC liquidity
    )

    # Estimate how much USDC we will need to buy 1 ETH
    usdc_per_eth = estimate_buy_price(
        uniswap_v2,
        weth,
        usdc,
        1 * 10**18,
    )
    assert usdc_per_eth / 1e18 == pytest.approx(1894.572606709)

    usdc_per_eth = estimate_buy_price(
        uniswap_v2,
        weth,
        usdc,
        1 * 10**18,
        slippage=500,
    )


def test_estimate_sell_price(
    web3: Web3,
    deployer: str,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Estimate sell price."""

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        1_000 * 10**18,  # 1000 ETH liquidity
        1_700_000 * 10**18,  # 1.7M USDC liquidity
    )

    # Estimate the price of selling 1 ETH
    usdc_per_eth = estimate_sell_price(
        uniswap_v2,
        weth,
        usdc,
        1 * 10**18,
    )
    price_as_usd = usdc_per_eth / 1e18
    assert price_as_usd == pytest.approx(1693.2118677678354)

    # Estimate the price of selling 1 ETH with slippage 5%
    usdc_per_eth = estimate_sell_price(
        uniswap_v2,
        weth,
        usdc,
        1 * 10**18,
        slippage=500,
    )
    price_as_usd = usdc_per_eth / 1e18
    assert price_as_usd == pytest.approx(1612.5827312074623)


def test_estimate_sell_price_decimals(
    web3: Web3,
    deployer: str,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Estimate sell price using the decimal friendly function."""

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        1_000 * 10**18,  # 1000 ETH liquidity
        1_700_000 * 10**18,  # 1.7M USDC liquidity
    )

    # Estimate the price of selling 1 ETH
    usdc_per_eth = estimate_sell_price_decimals(
        uniswap_v2,
        weth.address,
        usdc.address,
        Decimal(1.0),
    )
    assert usdc_per_eth == pytest.approx(Decimal(1693.2118677678354))

    # Estimate the price of selling 1 ETH with 5% slippage
    usdc_per_eth = estimate_sell_price_decimals(
        uniswap_v2,
        weth.address,
        usdc.address,
        Decimal(1.0),
        slippage=5 * 100,
    )
    assert usdc_per_eth == pytest.approx(Decimal(1612.5827312074623))


def test_estimate_buy_price_decimals(
    web3: Web3,
    deployer: str,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Estimate sell price using the decimal friendly function."""

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        1_000 * 10**18,  # 1000 ETH liquidity
        1_700_000 * 10**18,  # 1.7M USDC liquidity
    )

    # Estimate the price of buying 1 ETH
    usdc_per_eth = estimate_buy_price_decimals(
        uniswap_v2,
        weth.address,
        usdc.address,
        Decimal(1.0),
    )
    assert usdc_per_eth == pytest.approx(Decimal(1706.82216820632059904))

    # Estimate the price of buying 1 ETH with 10% slippage
    usdc_per_eth = estimate_buy_price_decimals(
        uniswap_v2,
        weth.address,
        usdc.address,
        Decimal(1.0),
        slippage=10 * 100,
    )
    assert usdc_per_eth == pytest.approx(Decimal(1877.504385026952658944))


def test_estimate_buy_price_for_amount(
    web3: Web3,
    deployer: str,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Estimate the asset Price for a given amount."""

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        1_000 * 10**18,  # 1000 ETH liquidity
        1_700_000 * 10**18,  # 1.7M USDC liquidity
    )

    # Estimate the price of buying 1 ETH
    usdc_per_eth = estimate_buy_price_decimals(
        uniswap_v2,
        weth.address,
        usdc.address,
        Decimal(1.0),
    )
    assert usdc_per_eth == pytest.approx(Decimal(1706.82216820632059904))

    # Estimate the price of buying 1 ETH with 10% slippage
    usdc_per_eth = estimate_buy_price_decimals(
        uniswap_v2,
        weth.address,
        usdc.address,
        Decimal(1.0),
        slippage=10 * 100,
    )
    assert usdc_per_eth == pytest.approx(Decimal(1877.504385026952658944))


def test_buy_sell_round_trip(
    web3: Web3,
    deployer: str,
    user_1,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Buys some token, then sells it.

    Does a full round trip of trade and see how much money we lost.
    """

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        10 * 10**18,  # 10 ETH liquidity
        17_000 * 10**18,  # 17000 USDC liquidity
    )

    router = uniswap_v2.router

    # Give user_1 500 USD to buy ETH
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(user_1, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": user_1})

    # Perform a swap USDC->WETH
    path = [usdc.address, weth.address]  # Path tell how the swap is routed
    router.functions.swapExactTokensForTokens(
        usdc_amount_to_pay,
        0,
        path,
        user_1,
        FOREVER_DEADLINE,
    ).transact({"from": user_1})

    all_weth_amount = weth.functions.balanceOf(user_1).call()
    weth.functions.approve(router.address, all_weth_amount).transact({"from": user_1})

    # Perform the reverse swap WETH->USDC
    reverse_path = [weth.address, usdc.address]  # Path tell how the swap is routed
    router.functions.swapExactTokensForTokens(
        all_weth_amount,
        0,
        reverse_path,
        user_1,
        FOREVER_DEADLINE,
    ).transact({"from": user_1})

    # user_1 has less than 500 USDC left to loses in the LP fees
    usdc_left = usdc.functions.balanceOf(user_1).call() / (10.0**18)
    assert usdc_left == pytest.approx(497.0895)


def test_swap_price_from_hot_wallet(
    web3: Web3,
    deployer: str,
    hot_wallet: LocalAccount,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Use local hot wallet to buy WETH on Uniswap v2 using mock USDC."""

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        10 * 10**18,  # 10 ETH liquidity
        17_000 * 10**18,  # 17000 USDC liquidity
    )

    router = uniswap_v2.router
    hw_address = hot_wallet.address

    # Give hot wallet some cash to buy ETH (also some ETH as well to sign tx)
    # and approve it on the router
    web3.eth.send_transaction({"from": deployer, "to": hw_address, "value": 1 * 10**18})
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(hw_address, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": hw_address})

    # Perform a swap USDC->WETH
    path = [usdc.address, weth.address]
    tx = router.functions.swapExactTokensForTokens(
        usdc_amount_to_pay,
        0,
        path,
        hw_address,
        FOREVER_DEADLINE,
    ).build_transaction({"from": hw_address})

    # prepare and sign tx
    tx = fill_nonce(web3, tx)
    signed = hot_wallet.sign_transaction(tx)

    # estimate the quantity before sending transaction
    amount_eth = estimate_sell_price(
        uniswap_v2,
        usdc,
        weth,
        usdc_amount_to_pay,
    )

    raw_bytes = get_tx_broadcast_data(signed)
    tx_hash = web3.eth.send_raw_transaction(raw_bytes)
    tx_receipt = web3.eth.wait_for_transaction_receipt(tx_hash)
    assert tx_receipt.status == 1  # 1=success and mined

    # check if hot wallet get the same ETH amount estimated earlier
    assert weth.functions.balanceOf(hw_address).call() == pytest.approx(amount_eth)
    # precision test
    assert weth.functions.balanceOf(hw_address).call() == amount_eth


def test_estimate_price_three_way(
    web3: Web3,
    deployer: str,
    user_1,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
    dai: Contract,
):
    """User buys DAI on Uniswap v2 using mock USDC through WETH"""

    # Create ETH/USDC pair
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        10 * 10**18,  # 10 ETH liquidity
        17_000 * 10**18,  # 17000 USDC liquidity
    )
    # Create ETH/DAI pair
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        dai,
        10 * 10**18,  # 10 ETH liquidity
        17_200 * 10**18,  # 17200 DAI liquidity
    )

    router = uniswap_v2.router

    # Give user_1 some cash to buy DAI and approve it on the router
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(user_1, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": user_1})

    # Estimate the DAI amount user will get
    dai_amount = estimate_sell_price(
        uniswap_v2,
        usdc,
        dai,
        quantity=usdc_amount_to_pay,
        intermediate_token=weth,
    )

    # Perform a swap USDC->WETH->DAI
    path = [usdc.address, weth.address, dai.address]

    # https://docs.uniswap.org/protocol/V2/reference/smart-contracts/router-02#swapexacttokensfortokens
    router.functions.swapExactTokensForTokens(
        usdc_amount_to_pay,
        0,
        path,
        user_1,
        FOREVER_DEADLINE,
    ).transact({"from": user_1})

    # Compare the amount user receives to the estimation ealier
    assert dai.functions.balanceOf(user_1).call() == pytest.approx(dai_amount)
    # precision test
    assert dai.functions.balanceOf(user_1).call() == dai_amount


def test_estimate_buy_price_for_cash(
    web3: Web3,
    deployer: str,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Estimate how much asset we receive for a given cash buy."""

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        1_000 * 10**18,  # 1000 ETH liquidity
        1_700_000 * 10**18,  # 1.7M USDC liquidity
    )

    # Estimate the price of buying 1650 USDC worth of ETH
    eth_received = estimate_buy_received_amount_raw(
        uniswap_v2,
        weth.address,
        usdc.address,
        1650 * 10**18,
    )

    assert eth_received / (10**18) == pytest.approx(0.9667409780905836)

    # Calculate price of ETH as $ for our purchase
    price = (1650 * 10**18) / eth_received
    assert price == pytest.approx(Decimal(1706.7653460381143))


def test_estimate_sell_received_cash(
    web3: Web3,
    deployer: str,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Estimate how much asset we receive for a given cash buy."""

    # Create the trading pair and add initial liquidity
    deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        1_000 * 10**18,  # 1000 ETH liquidity
        1_700_000 * 10**18,  # 1.7M USDC liquidity
    )

    # Sell 50 ETH
    usdc_received = estimate_sell_received_amount_raw(
        uniswap_v2,
        weth.address,
        usdc.address,
        50 * 10**18,
    )

    usdc_received_decimals = usdc_received / 10**18
    assert usdc_received_decimals == pytest.approx(80721.05538886508)

    # Calculate price of ETH as $ for our purchase
    price = usdc_received / (50 * 10**18)
    assert price == pytest.approx(Decimal(1614.4211077773016))
