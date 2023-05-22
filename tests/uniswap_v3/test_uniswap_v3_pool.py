"""Test Uniswap v3 liquidity pool."""

import pytest
from web3 import EthereumTesterProvider, Web3
from web3._utils.events import EventLogErrorFlags
from web3.contract import Contract

from eth_defi.token import create_token
from eth_defi.uniswap_v3.constants import DEFAULT_FEES
from eth_defi.uniswap_v3.deployment import (
    UniswapV3Deployment,
    add_liquidity,
    decrease_liquidity,
    deploy_pool,
    deploy_uniswap_v3,
    increase_liquidity,
)
from eth_defi.uniswap_v3.pool import fetch_pool_details


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
    """Uniswap v3 deployment.

    NOTE: Though Uniswap v3 later introduces 1 bps fee level, it wasn't
    included in the original contract, so we need to enable it here
    """
    deployment = deploy_uniswap_v3(web3, deployer)
    deployment.factory.functions.enableFeeAmount(100, 1).transact({"from": deployer})
    return deployment


@pytest.fixture()
def usdc(web3, deployer) -> Contract:
    """Mock USDC token.

    Note that this token has 18 decimals instead of 6 of real USDC.
    """
    token = create_token(web3, deployer, "USD Coin", "USDC", 100_000_000 * 10**6, decimals=6)
    return token


@pytest.fixture()
def weth(uniswap_v3) -> Contract:
    """Mock WETH token."""
    return uniswap_v3.weth


def test_create_pool_wrong_fee(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
):
    with pytest.raises(AssertionError) as e:
        deploy_pool(
            web3,
            deployer,
            deployment=uniswap_v3,
            token0=weth,
            token1=usdc,
            fee=10,
        )

    assert str(e.value) == "Default Uniswap v3 factory only allows 4 fee levels: 100, 500, 3000, 10000"


@pytest.mark.parametrize("fee", DEFAULT_FEES)
def test_create_pool_no_liquidity(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
    fee: int,
):
    """Deploy mock pool on Uniswap v3 without initial liquidity."""
    pool = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=weth,
        token1=usdc,
        fee=fee,
    )

    # Check the pool was successfully deployed
    assert pool.address.startswith("0x")
    assert uniswap_v3.factory.functions.getPool(weth.address, usdc.address, fee).call() == pool.address
    assert pool.functions.token0().call() == weth.address
    assert pool.functions.token1().call() == usdc.address
    assert pool.functions.fee().call() == fee

    # liquidity should be 0
    liquidity = pool.functions.liquidity().call()
    assert liquidity == 0


def test_create_pool_with_initial_liquidity(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Add liquidity to the mock pool on Uniswap v3."""
    pool = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=weth,
        token1=usdc,
        fee=3000,
    )

    initial_amount0 = 1_000_000
    initial_amount1 = 20_000_000

    tx_receipt, lower_tick, upper_tick = add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool,
        amount0=initial_amount0,
        amount1=initial_amount1,
        lower_tick=100,
        upper_tick=200,
    )

    # successful
    assert tx_receipt.status == 1

    # [6617184536, 6617184536, 0, 0, 89874, 1020847100762815390390123822295304634368, 1654638644, True]
    lower_liquid_gross, lower_liquid_net, *_, init = pool.functions.ticks(lower_tick).call()
    assert init is True

    # [6617184536, -6617184536, 0, 0, 89874, 1020847100762815390390123822295304634368, 1654638849, True]
    upper_liquid_gross, upper_liquid_net, *_, init = pool.functions.ticks(upper_tick).call()
    assert init is True
    assert upper_liquid_gross == lower_liquid_gross
    assert upper_liquid_net == -lower_liquid_net

    # other tick should not be initialized
    *_, init = pool.functions.ticks(lower_tick - 60).call()
    assert init is False


def test_create_pool_with_increase_decrease_liquidity(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Increase and decrease liquidity of a mock pool on Uniswap v3."""
    pool = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=weth,
        token1=usdc,
        fee=3000,
    )

    initial_amount0 = 1_000_000
    initial_amount1 = 20_000_000

    tx_receipt, lower_tick, upper_tick = add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool,
        amount0=initial_amount0,
        amount1=initial_amount1,
        lower_tick=100,
        upper_tick=200,
    )

    # successful
    assert tx_receipt.status == 1

    # The IncreaseLiquidity event is emitted with both mint and increaseLiquidity
    # https://github.com/Uniswap/v3-periphery/blob/main/contracts/interfaces/INonfungiblePositionManager.sol
    increase_liquidity_event = uniswap_v3.position_manager.events.IncreaseLiquidity().process_receipt(tx_receipt, EventLogErrorFlags.Discard)
    token_id = increase_liquidity_event[0].args.tokenId
    liquidity_before_increase = increase_liquidity_event[0].args.liquidity

    token0_balance_before = weth.functions.balanceOf(deployer).call()
    token1_balance_before = usdc.functions.balanceOf(deployer).call()

    pool_l_before_increase, *_ = pool.functions.ticks(lower_tick).call()

    # add more liquidity
    tx_receipt = increase_liquidity(
        web3,
        deployer,
        token_id,
        deployment=uniswap_v3,
        amount0=1_000_000,
        amount1=1_000_000,
    )

    assert tx_receipt.status == 1

    increase_liquidity_event = uniswap_v3.position_manager.events.IncreaseLiquidity().process_receipt(tx_receipt, EventLogErrorFlags.Discard)
    liquidity_added = increase_liquidity_event[0].args.liquidity
    assert liquidity_added > 0
    pool_l_after_increase, *_ = pool.functions.ticks(lower_tick).call()
    assert liquidity_added == pool_l_after_increase - pool_l_before_increase

    # get current liquidity for this token_id
    *_, current_position_liquidity, _, _, _, _ = uniswap_v3.position_manager.functions.positions(token_id).call()
    assert current_position_liquidity == liquidity_added + liquidity_before_increase

    # make sure the token balances change with liquidity increase
    token0_balance_after = weth.functions.balanceOf(deployer).call()
    token1_balance_after = usdc.functions.balanceOf(deployer).call()
    assert increase_liquidity_event[0].args.amount0 == token0_balance_before - token0_balance_after
    assert increase_liquidity_event[0].args.amount1 == token1_balance_before - token1_balance_after

    # decrease liquidity and check that token values were credited to our account
    liquidity_before_decrease = current_position_liquidity
    liquidity_withdrawl = 500_000_000

    # remove some liquidity
    tx_receipt = decrease_liquidity(
        web3,
        deployer,
        token_id,
        deployment=uniswap_v3,
        liquidity_decrease_amount=liquidity_withdrawl,
    )
    assert tx_receipt.status == 1

    decrease_liquidity_event = uniswap_v3.position_manager.events.DecreaseLiquidity().process_receipt(tx_receipt, EventLogErrorFlags.Discard)
    liquidity_reduction_amount = decrease_liquidity_event[0].args.liquidity
    token0_received = decrease_liquidity_event[0].args.amount0
    token1_received = decrease_liquidity_event[0].args.amount1

    assert liquidity_reduction_amount == liquidity_withdrawl
    assert token0_received > 0 or token1_received > 0

    *_, current_position_liquidity, _, _, _, _ = uniswap_v3.position_manager.functions.positions(token_id).call()
    assert current_position_liquidity == liquidity_before_decrease - liquidity_withdrawl

    # finally ensure we received the tokens in our position.  Token are not sent to your wallet on decreaseLiquidity,
    # instead they are stored with the position in the tokens0/1 owed.  We will verify that.
    *_, token0_owed, token1_owed = uniswap_v3.position_manager.functions.positions(token_id).call()
    assert token0_owed == token0_received
    assert token1_owed == token1_received


def test_fetch_pool_details(
    web3: Web3,
    deployer: str,
    uniswap_v3: UniswapV3Deployment,
    weth: Contract,
    usdc: Contract,
):
    """Get Uniswap v3 pool info."""
    pool = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=weth,
        token1=usdc,
        fee=3000,
    )

    details = fetch_pool_details(web3, pool.address)
    assert details.token0.symbol == "WETH"
    assert details.token1.symbol == "USDC"
    assert details.fee == pytest.approx(0.0030)
