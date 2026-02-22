"""Check guard against Aave v3 calls.

- Check Aave v3 access rights

- Check general access rights on vaults and guards
"""

import logging
import os
import shutil

import pytest
from eth_typing import HexAddress, HexStr
from flaky import flaky
from web3 import EthereumTesterProvider, Web3
from web3._utils.events import EventLogErrorFlags
from web3.contract import Contract

from eth_defi.aave_v3.constants import MAX_AMOUNT, AaveV3InterestRateMode
from eth_defi.aave_v3.deployment import AaveV3Deployment
from eth_defi.aave_v3.deployment import fetch_deployment as fetch_aave_deployment
from eth_defi.aave_v3.loan import supply, withdraw
from eth_defi.abi import get_contract, get_deployed_contract, get_function_selector
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.hotwallet import HotWallet
from eth_defi.provider.anvil import fork_network_anvil, mine
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.token import create_token, fetch_erc20_details
from eth_defi.trace import (
    TransactionAssertionError,
    assert_transaction_success_with_explanation,
)

# pytestmark = pytest.mark.skipif(
#     (os.environ.get("JSON_RPC_POLYGON") is None) or (shutil.which("anvil") is None),
#     reason="Set JSON_RPC_POLYGON env install anvil command to run these tests",
# )

pytestmark = pytest.mark.skip(reason="These tests need to be rewritten as Polygon is no longer working here")


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
        fork_block_number=58_000_000,
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
def aave_v3_deployment(web3) -> AaveV3Deployment:
    return fetch_aave_deployment(
        web3,
        pool_address="0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        data_provider_address="0x69FA688f1Dc47d4B5d8029D5a35FB7a548310654",
        oracle_address="0xb023e699F5a33916Ea823A16485e259257cA8Bd1",
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
    deployer: str,
    owner: str,
    asset_manager: str,
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

    aave_pool_address = aave_v3_deployment.pool.address
    note = "Allow Aave v3"
    tx_hash = guard.functions.whitelistAaveV3(aave_pool_address, note).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)
    receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert len(receipt["logs"]) == 3

    # check Aave pool was approved
    assert guard.functions.isAllowedApprovalDestination(aave_pool_address).call()

    # Check Aave pool call sites was enabled in the receipt
    call_site_events = guard.events.CallSiteApproved().process_receipt(receipt, errors=EventLogErrorFlags.Ignore)
    supply_selector = get_function_selector(aave_v3_deployment.pool.functions.supply)
    withdraw_selector = get_function_selector(aave_v3_deployment.pool.functions.withdraw)

    assert call_site_events[0]["args"]["notes"] == note
    assert call_site_events[0]["args"]["selector"].hex() == supply_selector.hex()
    assert call_site_events[0]["args"]["target"] == aave_pool_address

    assert call_site_events[1]["args"]["notes"] == note
    assert call_site_events[1]["args"]["selector"].hex() == withdraw_selector.hex()
    assert call_site_events[1]["args"]["target"] == aave_pool_address

    assert guard.functions.isAllowedCallSite(aave_pool_address, supply_selector).call()
    assert guard.functions.isAllowedCallSite(aave_pool_address, withdraw_selector).call()

    guard.functions.whitelistToken(usdc.address, "Allow USDC").transact({"from": owner})
    guard.functions.whitelistToken(ausdc.address, "Allow aUSDC").transact({"from": owner})
    assert guard.functions.callSiteCount().call() == 6

    return vault


@pytest.fixture()
def guard(
    web3: Web3,
    vault: Contract,
) -> Contract:
    return get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())


def test_vault_initialised(
    owner: str,
    asset_manager: str,
    vault: Contract,
    guard: Contract,
    usdc: Contract,
    ausdc: Contract,
    aave_v3_deployment: AaveV3Deployment,
):
    """Vault and guard are initialised for the owner."""
    assert guard.functions.owner().call() == owner
    assert vault.functions.assetManager().call() == asset_manager
    assert guard.functions.isAllowedSender(asset_manager).call() is True
    assert guard.functions.isAllowedWithdrawDestination(owner).call() is True
    assert guard.functions.isAllowedWithdrawDestination(asset_manager).call() is False
    assert guard.functions.isAllowedReceiver(vault.address).call() is True

    # We have accessed needed for Aave v3
    pool = aave_v3_deployment.pool
    assert guard.functions.callSiteCount().call() == 6
    assert guard.functions.isAllowedApprovalDestination(pool.address)
    assert guard.functions.isAllowedApprovalDestination(aave_v3_deployment.pool.address)
    assert guard.functions.isAllowedCallSite(pool.address, get_function_selector(pool.functions.supply)).call()
    assert guard.functions.isAllowedCallSite(pool.address, get_function_selector(pool.functions.withdraw)).call()
    assert guard.functions.isAllowedCallSite(usdc.address, get_function_selector(usdc.functions.approve)).call()
    assert guard.functions.isAllowedCallSite(usdc.address, get_function_selector(usdc.functions.transfer)).call()
    assert guard.functions.isAllowedCallSite(ausdc.address, get_function_selector(ausdc.functions.approve)).call()
    assert guard.functions.isAllowedAsset(usdc.address).call()
    assert guard.functions.isAllowedAsset(ausdc.address).call()


def test_guard_can_do_aave_supply(
    web3: Web3,
    aave_v3_deployment: AaveV3Deployment,
    asset_manager: str,
    deployer: str,
    vault: Contract,
    guard: Contract,
    usdc: Contract,
    ausdc: Contract,
    weth: Contract,
):
    usdc.functions.transfer(vault.address, 50_000 * 10**6).transact({"from": deployer})
    usdc_amount = 10_000 * 10**6

    fn_calls = supply(
        aave_v3_deployment=aave_v3_deployment,
        token=usdc,
        amount=usdc_amount,
        wallet_address=vault.address,
    )
    for fn_call in fn_calls:
        target, call_data = encode_simple_vault_transaction(fn_call)
        tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)

    assert usdc.functions.balanceOf(vault.address).call() == pytest.approx(40_000 * 10**6)
    assert ausdc.functions.balanceOf(vault.address).call() == pytest.approx(usdc_amount)

    # Shouldn't allow to supply WETH
    approve_fn, supply_fn = supply(
        aave_v3_deployment=aave_v3_deployment,
        token=weth,
        amount=1 * 10**18,
        wallet_address=vault.address,
    )
    with pytest.raises(TransactionAssertionError, match="target not allowed"):
        target, call_data = encode_simple_vault_transaction(approve_fn)
        tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)
    with pytest.raises(TransactionAssertionError, match="Token not allowed"):
        target, call_data = encode_simple_vault_transaction(supply_fn)
        tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)


# FAILED tests/guard/test_guard_simple_vault_one_delta.py::test_guard_can_short - assert 2000000002537484976 == 1000000000000000000 ± 1.0e+12
#   comparison failed
#   Obtained: 2000000002537484976
#   Expected: 1000000000000000000 ± 1.0e+12
@pytest.mark.skip(reason="Aave market on Polygon dead")
def test_guard_can_do_aave_withdraw(
    web3: Web3,
    aave_v3_deployment: AaveV3Deployment,
    asset_manager: str,
    deployer: str,
    vault: Contract,
    guard: Contract,
    usdc: Contract,
    ausdc: Contract,
    weth: Contract,
):
    usdc.functions.transfer(vault.address, 50_000 * 10**6).transact({"from": deployer})
    usdc_amount = 10_000 * 10**6

    fn_calls = supply(
        aave_v3_deployment=aave_v3_deployment,
        token=usdc,
        amount=usdc_amount,
        wallet_address=vault.address,
    )
    for fn_call in fn_calls:
        target, call_data = encode_simple_vault_transaction(fn_call)
        tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)

    assert usdc.functions.balanceOf(vault.address).call() == pytest.approx(40_000 * 10**6)
    assert ausdc.functions.balanceOf(vault.address).call() == pytest.approx(usdc_amount)

    withdraw_fn = withdraw(
        aave_v3_deployment=aave_v3_deployment,
        token=usdc,
        amount=5_000 * 10**6,
        wallet_address=vault.address,
    )
    target, call_data = encode_simple_vault_transaction(withdraw_fn)
    tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)

    assert usdc.functions.balanceOf(vault.address).call() == pytest.approx(45_000 * 10**6)
    assert ausdc.functions.balanceOf(vault.address).call() == pytest.approx(5_000 * 10**6)
