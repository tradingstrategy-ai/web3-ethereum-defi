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
from eth_defi.uniswap_v2.deployment import (
    FOREVER_DEADLINE,
    UniswapV2Deployment,
    deploy_trading_pair,
    deploy_uniswap_v2_like,
)
from eth_defi.uniswap_v2.fees import estimate_sell_price
from eth_defi.uniswap_v2.swap import swap_with_slippage_protection


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


def test_sell_exact_with_slippage_protection(
    web3: Web3,
    deployer: str,
    hot_wallet: LocalAccount,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Use local hot wallet to buy as much as possible WETH on Uniswap v2 using
    define amout of mock USDC."""

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

    # Give hot wallet some USDC to buy ETH (also some ETH as well to send tx)
    web3.eth.send_transaction({"from": deployer, "to": hw_address, "value": 1 * 10**18})
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(hw_address, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": hw_address})

    # build transaction
    swap_func = swap_with_slippage_protection(
        uniswap_v2_deployment=uniswap_v2,
        recipient_address=hw_address,
        base_token=usdc,
        quote_token=weth,
        amount_in=usdc_amount_to_pay,
        max_slippage=50,  # 50 bps = 0.5%
    )
    tx = swap_func.buildTransaction(
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
    tx_receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert tx_receipt.status == 1


def test_buy_exact_with_slippage_protection(
    web3: Web3,
    deployer: str,
    hot_wallet: LocalAccount,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Use local hot wallet to buy a define amount of WETH on Uniswap v2 using mock USDC."""

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

    # Give hot wallet some USDC to buy ETH (also some ETH as well to send tx)
    web3.eth.send_transaction({"from": deployer, "to": hw_address, "value": 1 * 10**18})
    max_usdc_amount = 500 * 10**18
    usdc.functions.transfer(hw_address, max_usdc_amount).transact({"from": deployer})
    usdc.functions.approve(router.address, max_usdc_amount).transact({"from": hw_address})

    # expect to get 0.1 ETH
    eth_amount_expected = int(0.1 * 10**18)

    # build transaction
    swap_func = swap_with_slippage_protection(
        uniswap_v2_deployment=uniswap_v2,
        recipient_address=hw_address,
        base_token=usdc,
        quote_token=weth,
        amount_out=eth_amount_expected,
        max_slippage=50,  # 50 bps = 0.5%
    )

    tx = swap_func.buildTransaction(
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
    tx_receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert tx_receipt.status == 1

    # confirm we get expected amount of ETH
    assert weth.functions.balanceOf(hw_address).call() == eth_amount_expected


def test_swap_revert_with_slippage_protection(
    web3: Web3,
    deployer: str,
    hot_wallet: LocalAccount,
    user_2: str,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Use local hot wallet to try to buy WETH on Uniswap v2 using mock USDC with slippage protection
    Simulate the scenario when a MEV bot make a faster trade,
    then the original transaction should revert
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
    hw_address = hot_wallet.address

    # Give hot wallet some USDC to buy ETH (also some ETH as well to send tx)
    web3.eth.send_transaction({"from": deployer, "to": hw_address, "value": 1 * 10**18})
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(hw_address, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": hw_address})

    # give user_2 some cash as well
    usdc.functions.transfer(user_2, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": user_2})

    original_price = estimate_sell_price(
        uniswap_v2,
        usdc,
        weth,
        1 * 10**18,
    )

    swap_func = swap_with_slippage_protection(
        uniswap_v2_deployment=uniswap_v2,
        recipient_address=hw_address,
        base_token=usdc,
        quote_token=weth,
        amount_in=usdc_amount_to_pay,
        max_slippage=100,  # 100 bps = 1%
    )

    # prepare a swap USDC->WETH
    tx1 = swap_func.buildTransaction(
        {
            "from": hw_address,
            "chainId": web3.eth.chain_id,
            "gas": 350_000,  # estimate max 350k gas per swap
        }
    )
    tx1 = fill_nonce(web3, tx1)
    gas_fees = estimate_gas_fees(web3)
    apply_gas(tx1, gas_fees)
    signed_tx1 = hot_wallet.sign_transaction(tx1)

    # user_2 makes a faster trade which moves the price
    router.functions.swapExactTokensForTokens(
        85 * 10**18,
        0,
        [usdc.address, weth.address],
        user_2,
        FOREVER_DEADLINE,
    ).transact({"from": user_2})

    # the price now should be lower than when we create tx1 (we get less ETH back)
    new_price = estimate_sell_price(
        uniswap_v2,
        usdc,
        weth,
        1 * 10**18,
    )
    price_move_percent = original_price * 100 / new_price - 100
    assert 1 < price_move_percent < 1.1

    # print(f"Price moved: {price_move_percent} %")

    # now the hot wallet finally manages to send the tx, it should fail
    tx1_hash = web3.eth.send_raw_transaction(signed_tx1.rawTransaction)
    tx1_receipt = web3.eth.get_transaction_receipt(tx1_hash)

    assert tx1_receipt.status == 0  # failure

    # confirm the revert reason
    reason = fetch_transaction_revert_reason(web3, tx1_hash)
    assert "UniswapV2Router: INSUFFICIENT_OUTPUT_AMOUNT" in reason


def test_sell_three_way_with_slippage_protection(
    web3: Web3,
    deployer: str,
    hot_wallet: LocalAccount,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
    dai: Contract,
):
    """Use local hot wallet to buy as much as possible DAI on Uniswap v2 using
    define amout of mock USDC, through WETH pools"""

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
    hw_address = hot_wallet.address

    # Give hot wallet some USDC to buy ETH (also some ETH as well to send tx)
    web3.eth.send_transaction({"from": deployer, "to": hw_address, "value": 1 * 10**18})
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(hw_address, usdc_amount_to_pay * 2).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay * 2).transact({"from": hw_address})

    # build transaction with defautl slippage (0.1 bps)
    swap_func = swap_with_slippage_protection(
        uniswap_v2_deployment=uniswap_v2,
        recipient_address=hw_address,
        base_token=usdc,
        quote_token=dai,
        amount_in=usdc_amount_to_pay,
        intermediate_token=weth,
    )
    tx = swap_func.buildTransaction(
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
    tx_receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert tx_receipt.status == 1

    # precision test with slippage = 0
    with pytest.warns(UserWarning, match=r"The `max_slippage` has be set to 0(.*)$"):
        swap_func = swap_with_slippage_protection(
            uniswap_v2_deployment=uniswap_v2,
            recipient_address=hw_address,
            base_token=usdc,
            quote_token=dai,
            amount_in=usdc_amount_to_pay,
            intermediate_token=weth,
            max_slippage=0,
        )
    tx = swap_func.buildTransaction(
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
    tx_receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert tx_receipt.status == 1


def test_swap_three_way_revert(
    web3: Web3,
    deployer: str,
    hot_wallet: LocalAccount,
    user_2: str,
    uniswap_v2: UniswapV2Deployment,
    weth: Contract,
    usdc: Contract,
    dai: Contract,
):
    """Use local hot wallet to try to buy WETH on Uniswap v2 using mock USDC with slippage protection
    Simulate the scenario when a MEV bot make a faster trade,
    then the original transaction should revert
    """

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
    hw_address = hot_wallet.address

    # Give hot wallet some USDC to buy ETH (also some ETH as well to send tx)
    web3.eth.send_transaction({"from": deployer, "to": hw_address, "value": 1 * 10**18})
    usdc_amount_to_pay = 500 * 10**18
    usdc.functions.transfer(hw_address, usdc_amount_to_pay).transact({"from": deployer})
    usdc.functions.approve(router.address, usdc_amount_to_pay).transact({"from": hw_address})

    # give user_2 some cash as well
    weth_amount = 1 * 10**18
    weth.functions.transfer(user_2, weth_amount).transact({"from": deployer})
    weth.functions.approve(router.address, weth_amount).transact({"from": user_2})

    original_price = estimate_sell_price(
        uniswap_v2,
        usdc,
        dai,
        1 * 10**18,
        intermediate_token=weth,
    )

    # prepare a swap USDC->WETH->DAI
    swap_func = swap_with_slippage_protection(
        uniswap_v2_deployment=uniswap_v2,
        recipient_address=hw_address,
        base_token=usdc,
        quote_token=dai,
        intermediate_token=weth,
        amount_in=usdc_amount_to_pay,
        max_slippage=100,  # 100 bps = 1%
    )

    tx1 = swap_func.buildTransaction(
        {
            "from": hw_address,
            "chainId": web3.eth.chain_id,
            "gas": 350_000,  # estimate max 350k gas per swap
        }
    )
    tx1 = fill_nonce(web3, tx1)
    gas_fees = estimate_gas_fees(web3)
    apply_gas(tx1, gas_fees)
    signed_tx1 = hot_wallet.sign_transaction(tx1)

    # user_2 makes a faster trade to buy DAI which moves the price
    router.functions.swapExactTokensForTokens(
        int(0.05 * 10**18),
        0,
        [weth.address, dai.address],
        user_2,
        FOREVER_DEADLINE,
    ).transact({"from": user_2})

    # the sell price now should be lower than when we create tx1 (we get less DAI back)
    new_price = estimate_sell_price(
        uniswap_v2,
        usdc,
        dai,
        1 * 10**18,
        intermediate_token=weth,
    )

    price_move_percent = original_price * 100 / new_price - 100
    assert 1 < price_move_percent < 1.1

    # print(f"Price moved: {price_move_percent} %")

    # now the hot wallet finally manages to send the tx, it should fail
    tx1_hash = web3.eth.send_raw_transaction(signed_tx1.rawTransaction)
    tx1_receipt = web3.eth.get_transaction_receipt(tx1_hash)
    assert tx1_receipt.status == 0  # failure

    # confirm the revert reason
    reason = fetch_transaction_revert_reason(web3, tx1_hash)
    assert "UniswapV2Router: INSUFFICIENT_OUTPUT_AMOUNT" in reason
