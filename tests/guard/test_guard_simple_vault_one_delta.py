"""Check guard against 1delta trades.

- Check 1delta access rights

- Check general access rights on vaults and guards
"""

import logging
import os
import shutil

import flaky
import pytest
from eth_typing import HexAddress, HexStr
from web3 import Web3
from web3._utils.events import EventLogErrorFlags
from web3.contract import Contract

from eth_defi.aave_v3.constants import MAX_AMOUNT, AaveV3InterestRateMode
from eth_defi.aave_v3.deployment import AaveV3Deployment
from eth_defi.aave_v3.deployment import fetch_deployment as fetch_aave_deployment
from eth_defi.abi import get_contract, get_deployed_contract, get_function_selector
from eth_defi.compat import encode_abi_compat
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.one_delta.constants import Exchange, TradeOperation, TradeType
from eth_defi.one_delta.deployment import OneDeltaDeployment
from eth_defi.one_delta.deployment import fetch_deployment as fetch_1delta_deployment
from eth_defi.one_delta.lending import _build_supply_multicall
from eth_defi.one_delta.position import (
    approve,
    close_short_position,
    open_short_position,
    reduce_short_position,
)
from eth_defi.one_delta.utils import encode_path
from eth_defi.provider.anvil import fork_network_anvil, mine
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.token import create_token, fetch_erc20_details
from eth_defi.trace import (
    TransactionAssertionError,
    assert_transaction_success_with_explanation,
)

# Too flaky on Github
CI = os.environ.get("CI") == "true"

pytestmark = pytest.mark.skip(reason="These tests need to be rewritten as Polygon is no longer working here")

# pytestmark = pytest.mark.skipif(
#     (os.environ.get("JSON_RPC_POLYGON") is None) or (shutil.which("anvil") is None) or CI,
#     reason="Set JSON_RPC_POLYGON env install anvil command to run these tests",
# )

POOL_FEE_RAW = 3000


@pytest.fixture
def large_usdc_holder() -> HexAddress:
    """A random account picked from Polygon that holds a lot of USDC.

    This account is unlocked on Anvil, so you have access to good USDC stash.

    `To find large holder accounts, use <https://polygonscan.com/token/0x2791bca1f2de4661ed88a30c99a7a9449aa84174#balances>`_.
    """
    # Binance Hot Wallet 6
    return HexAddress(HexStr("0xe7804c37c13166fF0b37F5aE0BB07A3aEbb6e245"))


@pytest.fixture
def anvil_polygon_chain_fork(request, large_usdc_holder) -> str:
    """Create a testable fork of live Polygon.

    :return: JSON-RPC URL for Web3
    """
    mainnet_rpc = os.environ["JSON_RPC_POLYGON"]
    launch = fork_network_anvil(
        mainnet_rpc,
        unlocked_addresses=[large_usdc_holder],
        fork_block_number=51_000_000,
    )
    try:
        yield launch.json_rpc_url
    finally:
        # Wind down Anvil process after the test is complete
        launch.close(log_level=logging.ERROR)


@pytest.fixture
def web3(anvil_polygon_chain_fork: str):
    """Set up a Web3 provider instance with a lot of workarounds for flaky nodes."""
    web3 = create_multi_provider_web3(anvil_polygon_chain_fork)
    return web3


@pytest.fixture
def usdc(web3) -> Contract:
    """Get USDC on Polygon."""
    return fetch_erc20_details(web3, "0x2791bca1f2de4661ed88a30c99a7a9449aa84174").contract


@pytest.fixture
def ausdc(web3):
    """Get aPolUSDC on Polygon."""
    return fetch_erc20_details(web3, "0x625E7708f30cA75bfd92586e17077590C60eb4cD", contract_name="aave_v3/AToken.json").contract


@pytest.fixture
def weth(web3) -> Contract:
    """Get WETH on Polygon."""
    return fetch_erc20_details(web3, "0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619").contract


@pytest.fixture
def vweth(web3) -> Contract:
    """Get vPolWETH on Polygon."""
    return fetch_erc20_details(web3, "0x0c84331e39d6658Cd6e6b9ba04736cC4c4734351", contract_name="aave_v3/VariableDebtToken.json").contract


@pytest.fixture
def wmatic(web3) -> Contract:
    """Get WMATIC on Polygon."""
    return fetch_erc20_details(web3, "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270").contract


@pytest.fixture
def vwmatic(web3) -> Contract:
    """Get vPolMATIC on Polygon."""
    return fetch_erc20_details(web3, "0x4a1c3aD6Ed28a636ee1751C69071f6be75DEb8B8", contract_name="aave_v3/VariableDebtToken.json").contract


@pytest.fixture
def aave_v3_deployment(web3) -> AaveV3Deployment:
    return fetch_aave_deployment(
        web3,
        pool_address="0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        data_provider_address="0x69FA688f1Dc47d4B5d8029D5a35FB7a548310654",
        oracle_address="0xb023e699F5a33916Ea823A16485e259257cA8Bd1",
    )


@pytest.fixture
def one_delta_deployment(web3) -> OneDeltaDeployment:
    return fetch_1delta_deployment(
        web3,
        flash_aggregator_address="0x74E95F3Ec71372756a01eB9317864e3fdde1AC53",
        broker_proxy_address="0x74E95F3Ec71372756a01eB9317864e3fdde1AC53",
        quoter_address="0x36de3876ad1ef477e8f6d98EE9a162926f00463A",
    )


@pytest.fixture()
def deployer(web3, usdc, large_usdc_holder) -> str:
    """Deploy account.

    Do some account allocation for tests.
    """
    address = web3.eth.accounts[0]

    usdc.functions.transfer(
        address,
        500_000 * 10**6,
    ).transact({"from": large_usdc_holder})

    return address


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
def vault(
    web3: Web3,
    usdc: Contract,
    ausdc: Contract,
    weth: Contract,
    vweth: Contract,
    deployer: str,
    owner: str,
    asset_manager: str,
    one_delta_deployment: OneDeltaDeployment,
    aave_v3_deployment: AaveV3Deployment,
) -> Contract:
    """Mock vault."""
    vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)

    assert vault.functions.owner().call() == deployer
    tx_hash = vault.functions.initialiseOwnership(owner).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)
    assert vault.functions.owner().call() == owner
    assert vault.functions.assetManager().call() == asset_manager

    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())
    assert guard.functions.owner().call() == owner

    broker_proxy_address = one_delta_deployment.broker_proxy.address
    aave_pool_address = aave_v3_deployment.pool.address
    note = "Allow 1delta"
    tx_hash = guard.functions.whitelistOnedelta(broker_proxy_address, aave_pool_address, note).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)
    receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert len(receipt["logs"]) == 4

    # check 1delta broker and aave pool were approved
    assert guard.functions.isAllowedApprovalDestination(broker_proxy_address).call()
    assert guard.functions.isAllowedApprovalDestination(aave_pool_address).call()

    # Check 1delta broker call sites was enabled in the receipt
    call_site_events = guard.events.CallSiteApproved().process_receipt(receipt, errors=EventLogErrorFlags.Ignore)
    multicall_selector = get_function_selector(one_delta_deployment.broker_proxy.functions.multicall)
    assert call_site_events[0]["args"]["notes"] == note
    assert call_site_events[0]["args"]["selector"].hex() == multicall_selector.hex()
    assert call_site_events[0]["args"]["target"] == broker_proxy_address
    assert guard.functions.isAllowedCallSite(broker_proxy_address, multicall_selector).call()

    guard.functions.whitelistToken(usdc.address, "Allow USDC").transact({"from": owner})
    guard.functions.whitelistToken(weth.address, "Allow WETH").transact({"from": owner})
    guard.functions.whitelistToken(ausdc.address, "Allow aUSDC").transact({"from": owner})
    guard.functions.whitelistTokenForDelegation(vweth.address, "Allow vWETH").transact({"from": owner})
    assert guard.functions.callSiteCount().call() == 8

    return vault


@pytest.fixture()
def guard(
    web3: Web3,
    vault: Contract,
) -> Contract:
    return get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())


@flaky.flaky
def test_vault_initialised(
    owner: str,
    asset_manager: str,
    vault: Contract,
    guard: Contract,
    usdc: Contract,
    ausdc: Contract,
    weth: Contract,
    vweth: Contract,
    one_delta_deployment: OneDeltaDeployment,
    aave_v3_deployment: AaveV3Deployment,
):
    """Vault and guard are initialised for the owner."""
    assert guard.functions.owner().call() == owner
    assert vault.functions.assetManager().call() == asset_manager
    assert guard.functions.isAllowedSender(asset_manager).call() is True
    assert guard.functions.isAllowedWithdrawDestination(owner).call() is True
    assert guard.functions.isAllowedWithdrawDestination(asset_manager).call() is False
    assert guard.functions.isAllowedReceiver(vault.address).call() is True

    # We have accessed needed for a swap
    broker = one_delta_deployment.broker_proxy
    assert guard.functions.callSiteCount().call() == 8
    assert guard.functions.isAllowedApprovalDestination(broker.address)
    assert guard.functions.isAllowedApprovalDestination(aave_v3_deployment.pool.address)
    assert guard.functions.isAllowedCallSite(broker.address, get_function_selector(broker.functions.multicall)).call()
    assert guard.functions.isAllowedCallSite(usdc.address, get_function_selector(usdc.functions.approve)).call()
    assert guard.functions.isAllowedCallSite(usdc.address, get_function_selector(usdc.functions.transfer)).call()
    assert guard.functions.isAllowedCallSite(ausdc.address, get_function_selector(ausdc.functions.approve)).call()
    assert guard.functions.isAllowedAsset(usdc.address).call()
    assert guard.functions.isAllowedAsset(ausdc.address).call()
    assert guard.functions.isAllowedAsset(weth.address).call()


# FAILED tests/guard/test_guard_simple_vault_one_delta.py::test_guard_can_short - assert 2000000006343538809 == 1000000000000000000 ± 1.0e+12
# @flaky.flaky
@pytest.mark.skip(reason="Very unreliable test, 50% times fails on CI - investigate why")
def test_guard_can_short(
    web3: Web3,
    one_delta_deployment: OneDeltaDeployment,
    aave_v3_deployment: AaveV3Deployment,
    asset_manager: str,
    deployer: str,
    vault: Contract,
    guard: Contract,
    usdc: Contract,
    ausdc: Contract,
    weth: Contract,
    vweth: Contract,
):
    """Asset manager can perform all major operations on a short position:

    - approve tokens
    - open position
    - increase position size
    - reduce position size
    - close position
    """
    weth_amount_to_short = 1 * 10**18
    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(vault.address, 50_000 * 10**6).transact({"from": deployer})

    approve_calls = approve(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc,
        borrow_token=weth,
        atoken=ausdc,
        vtoken=vweth,
        aave_v3_deployment=aave_v3_deployment,
        collateral_amount=MAX_AMOUNT,  # approve everything to keep this simple
    )
    for approve_call in approve_calls:
        target, call_data = encode_simple_vault_transaction(approve_call)
        tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)

    # open position
    trade_call = open_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc,
        borrow_token=weth,
        pool_fee=POOL_FEE_RAW,
        collateral_amount=usdc_amount,
        borrow_amount=weth_amount_to_short,
        wallet_address=vault.address,
    )

    target, call_data = encode_simple_vault_transaction(trade_call)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)

    mine(web3)

    # we should have some vWETH in the vault
    assert vweth.functions.balanceOf(vault.address).call() == pytest.approx(weth_amount_to_short)
    assert ausdc.functions.balanceOf(vault.address).call() == pytest.approx(12233287803)

    mine(web3)

    # increase position: double the size
    trade_call = open_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc,
        borrow_token=weth,
        pool_fee=POOL_FEE_RAW,
        collateral_amount=usdc_amount,
        borrow_amount=weth_amount_to_short,
        wallet_address=vault.address,
    )

    target, call_data = encode_simple_vault_transaction(trade_call)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)

    mine(web3)

    # check new position size
    assert vweth.functions.balanceOf(vault.address).call() == pytest.approx(weth_amount_to_short * 2)
    assert ausdc.functions.balanceOf(vault.address).call() == pytest.approx(24462991693)

    mine(web3)

    # reduce position back to original size, keep collateral in Aave
    trade_call = reduce_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc,
        borrow_token=weth,
        atoken=ausdc,
        pool_fee=POOL_FEE_RAW,
        reduce_borrow_amount=weth_amount_to_short,
        max_collateral_amount_in=MAX_AMOUNT,
        withdraw_collateral_amount=0,
        wallet_address=vault.address,
    )

    target, call_data = encode_simple_vault_transaction(trade_call)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)

    mine(web3)

    # check new position size
    assert vweth.functions.balanceOf(vault.address).call() == pytest.approx(weth_amount_to_short)
    assert ausdc.functions.balanceOf(vault.address).call() == pytest.approx(22219843923)

    mine(web3)

    # close position
    trade_call = close_short_position(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc,
        borrow_token=weth,
        atoken=ausdc,
        pool_fee=POOL_FEE_RAW,
        wallet_address=vault.address,
    )

    target, call_data = encode_simple_vault_transaction(trade_call)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)

    # we shouldn't have any aave tokens in the vault anymore
    assert vweth.functions.balanceOf(vault.address).call() == 0
    assert ausdc.functions.balanceOf(vault.address).call() == 0


def test_guard_unallowed_tokens(
    web3: Web3,
    one_delta_deployment: OneDeltaDeployment,
    aave_v3_deployment: AaveV3Deployment,
    asset_manager: str,
    deployer: str,
    vault: Contract,
    guard: Contract,
    usdc: Contract,
    ausdc: Contract,
    weth: Contract,
    vweth: Contract,
    wmatic: Contract,
):
    """Don't allow trading in unapproved token."""
    weth_amount_to_short = 1 * 10**18
    usdc_amount = 10_000 * 10**6
    usdc.functions.transfer(vault.address, usdc_amount).transact({"from": deployer})

    approve_calls = approve(
        one_delta_deployment=one_delta_deployment,
        collateral_token=usdc,
        borrow_token=weth,
        atoken=ausdc,
        vtoken=vweth,
        aave_v3_deployment=aave_v3_deployment,
        collateral_amount=usdc_amount,
    )
    for approve_call in approve_calls:
        target, call_data = encode_simple_vault_transaction(approve_call)
        tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)

    path = encode_path(
        path=[
            weth.address,
            wmatic.address,
            usdc.address,
        ],
        fees=[3000, 3000],
        exchanges=[Exchange.UNISWAP_V3, Exchange.UNISWAP_V3],
        operation=TradeOperation.OPEN,
        interest_mode=AaveV3InterestRateMode.VARIABLE,
    )

    call_swap = encode_abi_compat(
        contract=one_delta_deployment.flash_aggregator,
        fn_name="flashSwapExactIn",
        args=[
            weth_amount_to_short,
            1,
            path,
        ],
    )

    calls = _build_supply_multicall(
        one_delta_deployment=one_delta_deployment,
        token=usdc,
        amount=usdc_amount,
        wallet_address=vault.address,
    ) + [call_swap]

    trade_call = one_delta_deployment.broker_proxy.functions.multicall(calls)

    # matic isn't whitelisted so this should fail
    with pytest.raises(TransactionAssertionError, match="Token not allowed"):
        target, call_data = encode_simple_vault_transaction(trade_call)
        tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)
