"""Check Safe TradingStrategyModuleV0 against Uniswap v2 trades.

- Check Uniswap v2 access rights

- Check we can perform swap through TradingStrategyModuleV0 on behalf of Safe users
"""

import pytest
from web3 import Web3, HTTPProvider
from web3._utils.events import EventLogErrorFlags
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract, get_function_selector
from eth_defi.deploy import deploy_contract
from eth_defi.provider.anvil import AnvilLaunch, launch_anvil
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.token import create_token
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.uniswap_v2.deployment import (
    FOREVER_DEADLINE,
    UniswapV2Deployment,
    deploy_trading_pair,
    deploy_uniswap_v2_like,
)
from eth_defi.uniswap_v2.pair import PairDetails, fetch_pair_details


@pytest.fixture()
def anvil(request) -> AnvilLaunch:
    """Create a standalone Anvil RPC backend.

    :return: JSON-RPC URL for Web3
    """
    launch = launch_anvil()
    try:
        yield launch
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture
def web3(anvil):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return Web3(HTTPProvider(anvil.json_rpc_url))


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
    """Mock USDC token.

    Note that this token has 18 decimals instead of 6 of real USDC.
    """
    token = create_token(web3, deployer, "USD Coin", "USDC", 100_000_000 * 10**6)
    return token


@pytest.fixture()
def shitcoin(web3, deployer) -> Contract:
    """Mock USDC token.

    Note that this token has 18 decimals instead of 6 of real USDC.
    """
    token = create_token(web3, deployer, "Shitcoin", "SCAM", 1_000_000_000 * 10**18)
    return token


@pytest.fixture()
def uniswap_v2(web3: Web3, usdc: Contract, deployer: str) -> UniswapV2Deployment:
    """Deploy mock Uniswap v2."""
    balance = web3.eth.get_balance(deployer)
    assert balance > 10 * 10**18
    return deploy_uniswap_v2_like(web3, deployer, give_weth=5)


@pytest.fixture()
def safe(
    web3: Web3,
    usdc: Contract,
    deployer: str,
    owner: str,
    asset_manager: str,
    uniswap_v2: UniswapV2Deployment,
) -> Contract:
    """Deploy MockSafe.

    - Has ``enableModule`` and ``module`` functions
    """
    weth = uniswap_v2.weth
    safe = deploy_contract(web3, "safe-integration/MockSafe.json", deployer)

    # The module has ten variables that must be set:
    #
    #     Owner: Address that can call setter functions
    #     Avatar: Address of the DAO (e.g a Gnosis Safe)
    #     Target: Address on which the module will call execModuleTransaction()
    guard = deploy_contract(
        web3,
        "safe-integration/TradingStrategyModuleV0.json",
        owner,
        owner,
        safe.address,
    )

    assert guard.functions.owner().call() == owner
    assert guard.functions.avatar().call() == safe.address
    assert guard.functions.target().call() == safe.address
    tx_hash = guard.functions.whitelistUniswapV2Router(uniswap_v2.router.address, "Allow Uniswap v2").transact({"from": owner})
    receipt = web3.eth.get_transaction_receipt(tx_hash)

    assert len(receipt["logs"]) == 2

    # Enable Safe module
    tx_hash = safe.functions.enableModule(guard.address).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Enable asset_manager as the whitelisted trade-executor
    tx_hash = guard.functions.allowSender(asset_manager, "Whitelist trade-executor").transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Enable safe as the receiver of tokens
    tx_hash = guard.functions.allowReceiver(safe.address, "Whitelist Safe as trade receiver").transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)

    # Check Uniswap router call sites was enabled in the receipt
    call_site_events = guard.events.CallSiteApproved().process_receipt(receipt, errors=EventLogErrorFlags.Ignore)
    router_selector = get_function_selector(uniswap_v2.router.functions.swapExactTokensForTokens)
    assert call_site_events[0]["args"]["notes"] == "Allow Uniswap v2"
    assert call_site_events[0]["args"]["selector"].hex() == router_selector.hex()
    assert call_site_events[0]["args"]["target"] == uniswap_v2.router.address

    assert guard.functions.isAllowedCallSite(uniswap_v2.router.address, get_function_selector(uniswap_v2.router.functions.swapExactTokensForTokens)).call()
    guard.functions.whitelistToken(usdc.address, "Allow USDC").transact({"from": owner})
    guard.functions.whitelistToken(weth.address, "Allow WETH").transact({"from": owner})
    assert guard.functions.callSiteCount().call() == 5
    return safe


@pytest.fixture()
def guard(web3: Web3, safe: Contract, uniswap_v2) -> Contract:
    guard = get_deployed_contract(web3, "safe-integration/TradingStrategyModuleV0.json", safe.functions.module().call())
    assert guard.functions.isAllowedCallSite(uniswap_v2.router.address, get_function_selector(uniswap_v2.router.functions.swapExactTokensForTokens)).call()
    return guard


@pytest.fixture()
def weth(uniswap_v2) -> Contract:
    return uniswap_v2.weth


@pytest.fixture()
def weth_usdc_pair(web3, uniswap_v2, weth, usdc, deployer) -> PairDetails:
    pair_address = deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        weth,
        usdc,
        4 * 10**18,  # 4 ETH liquidity
        4000 * 10**6,  # 4000 USDC liquidity
    )
    return fetch_pair_details(web3, pair_address)


@pytest.fixture()
def shitcoin_usdc_pair(
    web3,
    uniswap_v2,
    shitcoin: Contract,
    usdc: Contract,
    deployer: str,
) -> PairDetails:
    pair_address = deploy_trading_pair(
        web3,
        deployer,
        uniswap_v2,
        shitcoin,
        usdc,
        5 * 10**18,
        10 * 10**6,
    )
    return fetch_pair_details(web3, pair_address)


def test_safe_module_initialised(
    owner: str,
    asset_manager: str,
    safe: Contract,
    guard: Contract,
    uniswap_v2: UniswapV2Deployment,
    usdc: Contract,
    weth: Contract,
):
    """Vault and guard are initialised for the owner."""
    assert guard.functions.owner().call() == owner
    assert guard.functions.isAllowedSender(asset_manager).call() is True
    assert guard.functions.isAllowedSender(safe.address).call() is False

    # We have accessed needed for a swap
    assert guard.functions.callSiteCount().call() == 5
    assert guard.functions.isAllowedApprovalDestination(uniswap_v2.router.address)
    assert guard.functions.isAllowedCallSite(uniswap_v2.router.address, get_function_selector(uniswap_v2.router.functions.swapExactTokensForTokens)).call()
    assert guard.functions.isAllowedCallSite(usdc.address, get_function_selector(usdc.functions.approve)).call()
    assert guard.functions.isAllowedCallSite(usdc.address, get_function_selector(usdc.functions.transfer)).call()
    assert guard.functions.isAllowedAsset(usdc.address).call()
    assert guard.functions.isAllowedAsset(weth.address).call()


@pytest.mark.skip(reason="MockSafe integration does not behave, instead use tests against real Gnosis Safe")
def test_safe_module_can_trade_uniswap_v2(
    web3: Web3,
    uniswap_v2: UniswapV2Deployment,
    weth_usdc_pair: PairDetails,
    owner: str,
    asset_manager: str,
    deployer: str,
    weth: Contract,
    usdc: Contract,
    safe: Contract,
    guard: Contract,
):
    """Asset manager can perform a swap.

    - Use TradingStrategyModuleV0 to perform a swap on behalf of Safe users
    """
    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(safe.address, usdc_amount).transact({"from": deployer})

    path = [usdc.address, weth.address]

    approve_call = usdc.functions.approve(
        uniswap_v2.router.address,
        usdc_amount,
    )

    target, call_data = encode_simple_vault_transaction(approve_call)
    tx_hash = guard.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    trade_call = uniswap_v2.router.functions.swapExactTokensForTokens(
        usdc_amount,
        0,
        path,
        safe.address,
        FOREVER_DEADLINE,
    )
    target, call_data = encode_simple_vault_transaction(trade_call)
    tx_hash = guard.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)

    assert weth.functions.balanceOf(safe.address).call() == 3696700037078235076

