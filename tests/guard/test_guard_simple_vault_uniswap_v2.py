"""Check guard against Uniswap v2 trades.

- Check Uniswap v2 access rights

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
from eth_defi.uniswap_v2.deployment import (
    FOREVER_DEADLINE,
    UniswapV2Deployment,
    deploy_trading_pair,
    deploy_uniswap_v2_like,
)
from eth_defi.uniswap_v2.pair import PairDetails, fetch_pair_details


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
    return deploy_uniswap_v2_like(web3, deployer)


@pytest.fixture()
def vault(
    web3: Web3,
    usdc: Contract,
    deployer: str,
    owner: str,
    asset_manager: str,
    uniswap_v2: UniswapV2Deployment,
) -> Contract:
    """Deploy mock Uniswap v2."""
    weth = uniswap_v2.weth
    vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)

    assert vault.functions.owner().call() == deployer
    vault.functions.initialiseOwnership(owner).transact({"from": deployer})
    assert vault.functions.owner().call() == owner
    assert vault.functions.assetManager().call() == asset_manager

    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())
    assert guard.functions.owner().call() == owner
    tx_hash = guard.functions.whitelistUniswapV2Router(uniswap_v2.router.address, "Allow Uniswap v2").transact({"from": owner})
    receipt = web3.eth.get_transaction_receipt(tx_hash)

    assert len(receipt["logs"]) == 3

    # Check Uniswap router call sites was enabled in the receipt
    call_site_events = guard.events.CallSiteApproved().process_receipt(receipt, errors=EventLogErrorFlags.Ignore)
    router_selector = get_function_selector(uniswap_v2.router.functions.swapExactTokensForTokens)
    assert call_site_events[0]["args"]["notes"] == "Allow Uniswap v2"
    assert call_site_events[0]["args"]["selector"].hex() == router_selector.hex()
    assert call_site_events[0]["args"]["target"] == uniswap_v2.router.address

    assert guard.functions.isAllowedCallSite(uniswap_v2.router.address, get_function_selector(uniswap_v2.router.functions.swapExactTokensForTokens)).call()
    guard.functions.whitelistToken(usdc.address, "Allow USDC").transact({"from": owner})
    guard.functions.whitelistToken(weth.address, "Allow WETH").transact({"from": owner})
    assert guard.functions.callSiteCount().call() == 6
    return vault


@pytest.fixture()
def guard(web3: Web3, vault: Contract, uniswap_v2) -> Contract:
    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())
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
        10 * 10**18,  # 10 ETH liquidity
        17_000 * 10**6,  # 17000 USDC liquidity
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


def test_vault_initialised(
    owner: str,
    asset_manager: str,
    vault: Contract,
    guard: Contract,
    uniswap_v2: UniswapV2Deployment,
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
    assert guard.functions.callSiteCount().call() == 6
    assert guard.functions.isAllowedApprovalDestination(uniswap_v2.router.address)
    assert guard.functions.isAllowedCallSite(uniswap_v2.router.address, get_function_selector(uniswap_v2.router.functions.swapExactTokensForTokens)).call()
    assert guard.functions.isAllowedCallSite(usdc.address, get_function_selector(usdc.functions.approve)).call()
    assert guard.functions.isAllowedCallSite(usdc.address, get_function_selector(usdc.functions.transfer)).call()
    assert guard.functions.isAllowedAsset(usdc.address).call()
    assert guard.functions.isAllowedAsset(weth.address).call()


def test_guard_can_trade_uniswap_v2(
    uniswap_v2: UniswapV2Deployment,
    weth_usdc_pair: PairDetails,
    owner: str,
    asset_manager: str,
    deployer: str,
    weth: Contract,
    usdc: Contract,
    vault: Contract,
    guard: Contract,
):
    """Asset manager can perform a swap."""
    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(vault.address, usdc_amount).transact({"from": deployer})

    path = [usdc.address, weth.address]

    approve_call = usdc.functions.approve(
        uniswap_v2.router.address,
        usdc_amount,
    )

    target, call_data = encode_simple_vault_transaction(approve_call)
    vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    trade_call = uniswap_v2.router.functions.swapExactTokensForTokens(
        usdc_amount,
        0,
        path,
        vault.address,
        FOREVER_DEADLINE,
    )

    target, call_data = encode_simple_vault_transaction(trade_call)
    vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    assert weth.functions.balanceOf(vault.address).call() == 3696700037078235076


def test_guard_can_trade_uniswap_v2_tax(
    uniswap_v2: UniswapV2Deployment,
    weth_usdc_pair: PairDetails,
    owner: str,
    asset_manager: str,
    deployer: str,
    weth: Contract,
    usdc: Contract,
    vault: Contract,
    guard: Contract,
):
    """Asset manager can perform a swap w/tax support."""
    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(vault.address, usdc_amount).transact({"from": deployer})

    path = [usdc.address, weth.address]

    approve_call = usdc.functions.approve(
        uniswap_v2.router.address,
        usdc_amount,
    )

    target, call_data = encode_simple_vault_transaction(approve_call)
    vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    trade_call = uniswap_v2.router.functions.swapExactTokensForTokensSupportingFeeOnTransferTokens(
        usdc_amount,
        0,
        path,
        vault.address,
        FOREVER_DEADLINE,
    )

    target, call_data = encode_simple_vault_transaction(trade_call)
    vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    assert weth.functions.balanceOf(vault.address).call() == 3696700037078235076


def test_guard_token_in_not_approved(
    uniswap_v2: UniswapV2Deployment,
    weth_usdc_pair: PairDetails,
    owner: str,
    asset_manager: str,
    deployer: str,
    weth: Contract,
    usdc: Contract,
    vault: Contract,
    guard: Contract,
):
    """USDC not approved for the swap."""
    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(vault.address, usdc_amount).transact({"from": deployer})

    path = [usdc.address, weth.address]

    trade_call = uniswap_v2.router.functions.swapExactTokensForTokens(
        usdc_amount,
        0,
        path,
        vault.address,
        FOREVER_DEADLINE,
    )

    with pytest.raises(TransactionFailed, match="TransferHelper: TRANSFER_FROM_FAILED"):
        target, call_data = encode_simple_vault_transaction(trade_call)
        vault.functions.performCall(target, call_data).transact({"from": asset_manager})


def test_guard_token_in_not_approved(
    uniswap_v2: UniswapV2Deployment,
    weth_usdc_pair: PairDetails,
    owner: str,
    asset_manager: str,
    deployer: str,
    weth: Contract,
    usdc: Contract,
    vault: Contract,
    guard: Contract,
):
    """USDC not approved for the swap."""
    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(vault.address, usdc_amount).transact({"from": deployer})

    path = [usdc.address, weth.address]

    trade_call = uniswap_v2.router.functions.swapExactTokensForTokens(
        usdc_amount,
        0,
        path,
        vault.address,
        FOREVER_DEADLINE,
    )

    with pytest.raises(TransactionFailed, match="TransferHelper: TRANSFER_FROM_FAILED"):
        target, call_data = encode_simple_vault_transaction(trade_call)
        vault.functions.performCall(target, call_data).transact({"from": asset_manager})


def test_guard_pair_not_approved(
    uniswap_v2: UniswapV2Deployment,
    owner: str,
    asset_manager: str,
    deployer: str,
    usdc: Contract,
    weth: Contract,
    shitcoin: Contract,
    vault: Contract,
    guard: Contract,
):
    """Don't allow trading in scam token.

    - Prevent exit scam through non-liquid token
    """

    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(vault.address, usdc_amount).transact({"from": deployer})

    approve_call = usdc.functions.approve(
        uniswap_v2.router.address,
        usdc_amount,
    )

    target, call_data = encode_simple_vault_transaction(approve_call)
    vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    # path with only 1 pool
    path = [usdc.address, shitcoin.address]
    trade_call = uniswap_v2.router.functions.swapExactTokensForTokens(
        usdc_amount,
        0,
        path,
        vault.address,
        FOREVER_DEADLINE,
    )

    with pytest.raises(TransactionFailed, match="Token not allowed"):
        target, call_data = encode_simple_vault_transaction(trade_call)
        vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    # path with 2 pools where shitcoin is the intermediate token
    path = [usdc.address, shitcoin.address, weth.address]
    trade_call = uniswap_v2.router.functions.swapExactTokensForTokens(
        usdc_amount,
        0,
        path,
        vault.address,
        FOREVER_DEADLINE,
    )

    with pytest.raises(TransactionFailed, match="Token not allowed"):
        target, call_data = encode_simple_vault_transaction(trade_call)
        vault.functions.performCall(target, call_data).transact({"from": asset_manager})


def test_owner_can_withdraw(
    owner: str,
    asset_manager: str,
    deployer: str,
    weth: Contract,
    usdc: Contract,
    vault: Contract,
    guard: Contract,
):
    """Owner can withdraw."""
    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(vault.address, usdc_amount).transact({"from": deployer})

    transfer_call = usdc.functions.transfer(
        owner,
        usdc_amount,
    )

    target, call_data = encode_simple_vault_transaction(transfer_call)
    vault.functions.performCall(target, call_data).transact({"from": asset_manager})


def test_guard_unauthorized_withdraw(
    owner: str,
    asset_manager: str,
    deployer: str,
    weth: Contract,
    usdc: Contract,
    vault: Contract,
    guard: Contract,
):
    """Others cannot withdraw."""
    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(vault.address, usdc_amount).transact({"from": deployer})

    transfer_call = usdc.functions.transfer(
        asset_manager,
        usdc_amount,
    )

    with pytest.raises(TransactionFailed, match="Receiver not whitelisted"):
        target, call_data = encode_simple_vault_transaction(transfer_call)
        vault.functions.performCall(target, call_data).transact({"from": asset_manager})


def test_guard_unauthorized_approve(
    owner: str,
    asset_manager: str,
    deployer: str,
    weth: Contract,
    usdc: Contract,
    vault: Contract,
    guard: Contract,
):
    """Cannot approve unauthorized destination."""
    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(vault.address, usdc_amount).transact({"from": deployer})

    transfer_call = usdc.functions.approve(
        asset_manager,
        usdc_amount,
    )

    with pytest.raises(TransactionFailed, match="Approve address"):
        target, call_data = encode_simple_vault_transaction(transfer_call)
        vault.functions.performCall(target, call_data).transact({"from": asset_manager})


def test_guard_third_party_trade(
    uniswap_v2: UniswapV2Deployment,
    weth_usdc_pair: PairDetails,
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

    path = [usdc.address, weth.address]

    approve_call = usdc.functions.approve(
        uniswap_v2.router.address,
        usdc_amount,
    )

    target, call_data = encode_simple_vault_transaction(approve_call)
    vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    trade_call = uniswap_v2.router.functions.swapExactTokensForTokens(
        usdc_amount,
        0,
        path,
        vault.address,
        FOREVER_DEADLINE,
    )

    with pytest.raises(TransactionFailed, match="Sender not allowed"):
        target, call_data = encode_simple_vault_transaction(trade_call)
        vault.functions.performCall(target, call_data).transact({"from": third_party})


def test_guard_can_trade_any_asset_uniswap_v2(
    web3: Web3,
    uniswap_v2: UniswapV2Deployment,
    weth_usdc_pair: PairDetails,
    owner: str,
    asset_manager: str,
    deployer: str,
    weth: Contract,
    usdc: Contract,
):
    """After whitelist removed, we can trade any asset."""
    weth = uniswap_v2.weth
    vault = deploy_contract(
        web3,
        "guard/SimpleVaultV0.json",
        deployer,
        asset_manager,
        gas=10_000_000,
        libraries=GUARD_LIBRARIES,
    )
    vault.functions.initialiseOwnership(owner).transact({"from": deployer})

    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())
    guard.functions.whitelistUniswapV2Router(uniswap_v2.router.address, "Allow Uniswap v2").transact({"from": owner})
    guard.functions.setAnyAssetAllowed(True, "Allow any asset").transact({"from": owner})

    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(vault.address, usdc_amount).transact({"from": deployer})

    path = [usdc.address, weth.address]

    #
    # Buy WETH
    #

    approve_call = usdc.functions.approve(
        uniswap_v2.router.address,
        usdc_amount,
    )

    target, call_data = encode_simple_vault_transaction(approve_call)
    vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    trade_call = uniswap_v2.router.functions.swapExactTokensForTokens(
        usdc_amount,
        0,
        path,
        vault.address,
        FOREVER_DEADLINE,
    )

    target, call_data = encode_simple_vault_transaction(trade_call)
    vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    weth_amount = 3696700037078235076
    assert weth.functions.balanceOf(vault.address).call() == weth_amount

    #
    # Sell it back
    #

    approve_call = weth.functions.approve(
        uniswap_v2.router.address,
        weth_amount,
    )
    target, call_data = encode_simple_vault_transaction(approve_call)
    vault.functions.performCall(target, call_data).transact({"from": asset_manager})

    trade_call = uniswap_v2.router.functions.swapExactTokensForTokens(
        weth_amount,
        0,
        [weth.address, usdc.address],
        vault.address,
        FOREVER_DEADLINE,
    )

    target, call_data = encode_simple_vault_transaction(trade_call)
    vault.functions.performCall(target, call_data).transact({"from": asset_manager})
