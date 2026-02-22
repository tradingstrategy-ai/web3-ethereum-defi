"""Check guard against Uniswap v3 trades.

- Check Uniswap v3 access rights

- Check general access rights on vaults and guards
"""

import pytest
from eth_tester.exceptions import TransactionFailed
from web3 import EthereumTesterProvider, Web3
from web3._utils.events import EventLogErrorFlags
from web3.contract import Contract

from eth_defi.abi import get_contract, get_deployed_contract, get_function_selector
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.token import create_token
from eth_defi.uniswap_v3.constants import FOREVER_DEADLINE
from eth_defi.uniswap_v3.deployment import (
    UniswapV3Deployment,
    add_liquidity,
    deploy_pool,
    deploy_uniswap_v3,
)
from eth_defi.uniswap_v3.pool import PoolDetails
from eth_defi.uniswap_v3.utils import encode_path, get_default_tick_range

POOL_FEE_RAW = 3000


@pytest.fixture
def tester_provider():
    return EthereumTesterProvider()


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
def owner(web3) -> str:
    return web3.eth.accounts[1]


@pytest.fixture()
def asset_manager(web3) -> str:
    return web3.eth.accounts[2]


@pytest.fixture()
def third_party(web3) -> str:
    return web3.eth.accounts[3]


@pytest.fixture()
def usdc(web3, deployer) -> Contract:
    """Mock USDC token."""
    token = create_token(web3, deployer, "USD Coin", "USDC", 100_000_000 * 10**6)
    return token


@pytest.fixture()
def shitcoin(web3, deployer) -> Contract:
    """Mock shitcoin with 18 decimals."""
    token = create_token(web3, deployer, "Shitcoin", "SCAM", 1_000_000_000 * 10**18)
    return token


@pytest.fixture()
def uniswap_v3(web3: Web3, deployer: str) -> UniswapV3Deployment:
    """Deploy mock Uniswap v3."""
    return deploy_uniswap_v3(web3, deployer)


@pytest.fixture()
def weth(uniswap_v3) -> Contract:
    return uniswap_v3.weth


@pytest.fixture()
def weth_usdc_pool(web3, uniswap_v3, weth, usdc, deployer) -> Contract:
    """Mock WETH-USDC pool."""

    min_tick, max_tick = get_default_tick_range(POOL_FEE_RAW)

    pool = deploy_pool(
        web3,
        deployer,
        deployment=uniswap_v3,
        token0=weth,
        token1=usdc,
        fee=POOL_FEE_RAW,
    )

    add_liquidity(
        web3,
        deployer,
        deployment=uniswap_v3,
        pool=pool,
        amount0=10 * 10**18,  # 10 ETH liquidity
        amount1=20_000 * 10**6,  # 20000 USDC liquidity
        lower_tick=min_tick,
        upper_tick=max_tick,
    )

    return pool


@pytest.fixture()
def vault(
    web3: Web3,
    usdc: Contract,
    weth: Contract,
    deployer: str,
    owner: str,
    asset_manager: str,
    uniswap_v3: UniswapV3Deployment,
) -> Contract:
    """Mock vault."""
    vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)

    assert vault.functions.owner().call() == deployer
    vault.functions.initialiseOwnership(owner).transact({"from": deployer})
    assert vault.functions.owner().call() == owner
    assert vault.functions.assetManager().call() == asset_manager

    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())
    assert guard.functions.owner().call() == owner

    router_address = uniswap_v3.swap_router.address
    tx_hash = guard.functions.whitelistUniswapV3Router(router_address, "Allow Uniswap v3 router").transact({"from": owner})
    receipt = web3.eth.get_transaction_receipt(tx_hash)

    assert len(receipt["logs"]) == 4

    # Check Uniswap router call sites was enabled in the receipt
    call_site_events = guard.events.CallSiteApproved().process_receipt(receipt, errors=EventLogErrorFlags.Ignore)
    exact_input_selector = get_function_selector(uniswap_v3.swap_router.functions.exactInput)
    exact_output_selector = get_function_selector(uniswap_v3.swap_router.functions.exactOutput)
    assert call_site_events[0]["args"]["notes"] == "Allow Uniswap v3 router"
    assert call_site_events[0]["args"]["selector"].hex() == exact_input_selector.hex()
    assert call_site_events[0]["args"]["target"] == router_address
    assert call_site_events[1]["args"]["target"] == router_address
    assert call_site_events[1]["args"]["selector"].hex() == exact_output_selector.hex()

    assert guard.functions.isAllowedCallSite(router_address, exact_input_selector).call()
    assert guard.functions.isAllowedCallSite(router_address, exact_output_selector).call()

    guard.functions.whitelistToken(usdc.address, "Allow USDC").transact({"from": owner})
    guard.functions.whitelistToken(weth.address, "Allow WETH").transact({"from": owner})
    assert guard.functions.callSiteCount().call() == 7

    return vault


@pytest.fixture()
def guard(
    web3: Web3,
    vault: Contract,
    uniswap_v3: UniswapV3Deployment,
) -> Contract:
    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())
    assert guard.functions.isAllowedCallSite(uniswap_v3.swap_router.address, get_function_selector(uniswap_v3.swap_router.functions.exactInput)).call()
    return guard


def test_vault_initialised(
    owner: str,
    asset_manager: str,
    vault: Contract,
    guard: Contract,
    uniswap_v3: UniswapV3Deployment,
    usdc: Contract,
    weth: Contract,
):
    """Vault and guard are initialised for the owner."""
    assert guard.functions.owner().call() == owner
    assert vault.functions.assetManager().call() == asset_manager
    assert guard.functions.isAllowedSender(asset_manager).call() is True
    assert guard.functions.isAllowedWithdrawDestination(owner).call() is True
    assert guard.functions.isAllowedWithdrawDestination(asset_manager).call() is False
    assert guard.functions.isAllowedReceiver(vault.address).call() is True

    # We have accessed needed for a swap
    assert guard.functions.callSiteCount().call() == 7
    router = uniswap_v3.swap_router
    assert guard.functions.isAllowedApprovalDestination(router.address)
    assert guard.functions.isAllowedCallSite(router.address, get_function_selector(router.functions.exactInput)).call()
    assert guard.functions.isAllowedCallSite(router.address, get_function_selector(router.functions.exactOutput)).call()
    assert guard.functions.isAllowedCallSite(usdc.address, get_function_selector(usdc.functions.approve)).call()
    assert guard.functions.isAllowedCallSite(usdc.address, get_function_selector(usdc.functions.transfer)).call()
    assert guard.functions.isAllowedAsset(usdc.address).call()
    assert guard.functions.isAllowedAsset(weth.address).call()


def test_guard_can_trade_exact_input_uniswap_v3(
    uniswap_v3: UniswapV3Deployment,
    weth_usdc_pool: PoolDetails,
    owner: str,
    asset_manager: str,
    deployer: str,
    weth: Contract,
    usdc: Contract,
    vault: Contract,
    guard: Contract,
):
    """Asset manager can perform exact input swap."""
    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(vault.address, usdc_amount).transact({"from": deployer})

    approve_call = usdc.functions.approve(
        uniswap_v3.swap_router.address,
        usdc_amount,
    )

    target, call_data = encode_simple_vault_transaction(approve_call)
    vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    encoded_path = encode_path([usdc.address, weth.address], [POOL_FEE_RAW])

    trade_call = uniswap_v3.swap_router.functions.exactInput(
        (
            encoded_path,
            vault.address,
            FOREVER_DEADLINE,
            usdc_amount,
            0,
        )
    )

    target, call_data = encode_simple_vault_transaction(trade_call)
    vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    assert weth.functions.balanceOf(vault.address).call() == 3326659993034849236


def test_guard_third_party_trade(
    uniswap_v3: UniswapV3Deployment,
    weth_usdc_pool: PoolDetails,
    owner: str,
    asset_manager: str,
    third_party: str,
    deployer: str,
    weth: Contract,
    usdc: Contract,
    vault: Contract,
    guard: Contract,
):
    """Third party cannot initiate a trade."""
    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(vault.address, usdc_amount).transact({"from": deployer})

    approve_call = usdc.functions.approve(
        uniswap_v3.swap_router.address,
        usdc_amount,
    )

    target, call_data = encode_simple_vault_transaction(approve_call)
    vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    encoded_path = encode_path([usdc.address, weth.address], [POOL_FEE_RAW])

    trade_call = uniswap_v3.swap_router.functions.exactInput(
        (
            encoded_path,
            vault.address,
            FOREVER_DEADLINE,
            usdc_amount,
            0,
        )
    )

    with pytest.raises(TransactionFailed, match="Sender not allowed"):
        target, call_data = encode_simple_vault_transaction(trade_call)
        vault.functions.performCall(target, call_data).transact({"from": third_party})


def test_guard_pair_not_approved(
    uniswap_v3: UniswapV3Deployment,
    owner: str,
    asset_manager: str,
    deployer: str,
    usdc: Contract,
    weth: Contract,
    shitcoin: Contract,
    vault: Contract,
):
    """Don't allow trading in scam token.

    - Prevent exit scam through non-liquid token
    """

    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(vault.address, usdc_amount).transact({"from": deployer})

    approve_call = usdc.functions.approve(
        uniswap_v3.swap_router.address,
        usdc_amount,
    )

    target, call_data = encode_simple_vault_transaction(approve_call)
    vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    # path with only 1 pool
    encoded_path = encode_path([usdc.address, shitcoin.address], [POOL_FEE_RAW])
    trade_call = uniswap_v3.swap_router.functions.exactInput(
        (
            encoded_path,
            vault.address,
            FOREVER_DEADLINE,
            usdc_amount,
            0,
        )
    )

    with pytest.raises(TransactionFailed, match="Token not allowed"):
        target, call_data = encode_simple_vault_transaction(trade_call)
        vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    # path with 2 pools where shitcoin is the intermediate token
    encoded_path = encode_path([usdc.address, shitcoin.address, weth.address], [POOL_FEE_RAW, POOL_FEE_RAW])
    trade_call = uniswap_v3.swap_router.functions.exactInput(
        (
            encoded_path,
            vault.address,
            FOREVER_DEADLINE,
            usdc_amount,
            0,
        )
    )

    with pytest.raises(TransactionFailed, match="Token not allowed"):
        target, call_data = encode_simple_vault_transaction(trade_call)
        vault.functions.performCall(target, call_data).transact({"from": asset_manager})
