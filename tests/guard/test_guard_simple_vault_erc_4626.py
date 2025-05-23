"""Check guard against ERC-4626 deposit and withdrawal calls.

- Check ERC-4626 access rights using our mock SimpleVaultV0 implementation

"""

import os
from typing import cast

import pytest
from eth_typing import HexAddress, HexStr
from web3 import Web3
from web3._utils.events import EventLogErrorFlags
from web3.contract import Contract

from eth_defi.abi import get_deployed_contract, get_function_selector
from eth_defi.deploy import deploy_contract
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.erc_4626.core import ERC4626Feature
from eth_defi.erc_4626.flow import approve_and_deposit_4626, approve_and_redeem_4626
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.ipor.vault import IPORVault
from eth_defi.provider.anvil import fork_network_anvil
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.token import fetch_erc20_details, TokenDetails
from eth_defi.trace import (
    assert_transaction_success_with_explanation,
)


JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

pytestmark = pytest.mark.skipif(
    JSON_RPC_BASE is None,
    reason="Set JSON_RPC_BASE env",
)


@pytest.fixture
def large_usdc_holder() -> HexAddress:
    return HexAddress(HexStr("0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A"))


@pytest.fixture
def anvil_base_chain_fork(request, large_usdc_holder) -> str:
    """Create a testable fork of live chain.

    :return: JSON-RPC URL for Web3
    """
    mainnet_rpc = os.environ["JSON_RPC_BASE"]
    launch = fork_network_anvil(
        mainnet_rpc,
        unlocked_addresses=[large_usdc_holder],
        fork_block_number=58_000_000,
    )
    try:
        yield launch.json_rpc_url
    finally:
        # Wind down Anvil process after the test is complete
        launch.close()


@pytest.fixture
def web3(anvil_base_chain_fork: str):
    """Set up a Web3 provider instance with a lot of workarounds for flaky nodes."""
    web3 = create_multi_provider_web3(anvil_base_chain_fork)
    return web3


@pytest.fixture
def usdc(web3) -> Contract:
    """Get USDC."""
    return fetch_erc20_details(web3, "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913").contract


@pytest.fixture
def erc4626_vault(web3) -> ERC4626Vault:
    """Pick a random vault which we deposit/withdraw from our own vault"""

    # Harvest USDC Autopilot on IPOR on Base
    # https://app.ipor.io/fusion/base/0x0d877dc7c8fa3ad980dfdb18b48ec9f8768359c4
    # (ChainId.base, "0x0d877Dc7C8Fa3aD980DfDb18B48eC9F8768359C4".lower()),

    vault = create_vault_instance(
        web3,
        address="0x0d877Dc7C8Fa3aD980DfDb18B48eC9F8768359C4",
        features={ERC4626Feature.ipor_like},
    )
    return cast(vault, IPORVault)


@pytest.fixture
def malicious_vault(web3) -> ERC4626Vault:
    """Pick a random vault which we do not allow deposit/withdraw"""

    # maxAPY USDC base
    # https://app.maxapy.io/vaults/super/usdc
    # (ChainId.base, "0x7a63e8fc1d0a5e9be52f05817e8c49d9e2d6efae".lower()),

    vault = create_vault_instance(
        web3,
        address="0x7a63e8fc1d0a5e9be52f05817e8c49d9e2d6efae",
        features={ERC4626Feature.ipor_like},
    )
    return cast(vault, IPORVault)


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
    erc4626_vault: IPORVault,
) -> Contract:
    """Create SimpleVaultV0 mock to test the guard interface."""
    vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager)

    assert vault.functions.owner().call() == deployer
    vault.functions.initialiseOwnership(owner).transact({"from": deployer})
    assert vault.functions.owner().call() == owner
    assert vault.functions.assetManager().call() == asset_manager

    guard = get_deployed_contract(web3, "guard/GuardV0.json", vault.functions.guard().call())
    assert guard.functions.owner().call() == owner

    vault_address = erc4626_vault.vault_address
    note = f"Allow {erc4626_vault.name}"
    tx_hash = guard.functions.whitelistERC4626(vault_address, note).transact({"from": owner})
    receipt = web3.eth.get_transaction_receipt(tx_hash)
    assert len(receipt["logs"]) == 3

    # check Aave pool was approved
    assert guard.functions.isAllowedApprovalDestination(vault_address).call()

    # Check Aave pool call sites was enabled in the receipt
    call_site_events = guard.events.CallSiteApproved().process_receipt(receipt, errors=EventLogErrorFlags.Ignore)
    supply_selector = get_function_selector(erc4626_vault.vault_contract.functions.deposit)
    withdraw_selector = get_function_selector(erc4626_vault.vault_contract.functions.withdraw)

    assert call_site_events[0]["args"]["notes"] == note
    assert call_site_events[0]["args"]["selector"].hex() == supply_selector.hex()
    assert call_site_events[0]["args"]["target"] == vault_address

    assert call_site_events[1]["args"]["notes"] == note
    assert call_site_events[1]["args"]["selector"].hex() == withdraw_selector.hex()
    assert call_site_events[1]["args"]["target"] == vault_address

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
    erc4626_vault: IPORVault,
):
    """Vault and guard are initialised for the owner."""
    assert guard.functions.owner().call() == owner
    assert vault.functions.assetManager().call() == asset_manager
    assert guard.functions.isAllowedSender(asset_manager).call() is True
    assert guard.functions.isAllowedWithdrawDestination(owner).call() is True
    assert guard.functions.isAllowedWithdrawDestination(asset_manager).call() is False
    assert guard.functions.isAllowedReceiver(vault.address).call() is True

    # We have accessed needed for ERC-4626 vault
    vault_address = erc4626_vault.vault_address
    share_token = erc4626_vault.share_token.address
    denomination_token = erc4626_vault.denomination_token.address
    supply_selector = get_function_selector(erc4626_vault.vault_contract.functions.deposit)
    withdraw_selector = get_function_selector(erc4626_vault.vault_contract.functions.withdraw)
    assert guard.functions.isAllowedCallSite(vault_address, supply_selector).call()
    assert guard.functions.isAllowedCallSite(vault_address, withdraw_selector).call()
    assert guard.functions.isAllowedAsset(erc4626_vault.denomination_token.address)
    assert guard.functions.isAllowedAsset(erc4626_vault.share_token.address)

    assert guard.functions.callSiteCount().call() == 6
    assert guard.functions.isAllowedApprovalDestination(vault_address)
    assert guard.functions.isAllowedCallSite(share_token, get_function_selector(usdc.functions.approve)).call()
    assert guard.functions.isAllowedCallSite(share_token, get_function_selector(usdc.functions.transfer)).call()
    assert guard.functions.isAllowedCallSite(denomination_token, get_function_selector(ausdc.functions.approve)).call()
    assert guard.functions.isAllowedAsset(share_token).call()
    assert guard.functions.isAllowedAsset(denomination_token).call()


def test_guard_can_do_erc_4626_deposit(
    web3: Web3,
    erc4626_vault: IPORVault,
    asset_manager: str,
    deployer: str,
    vault: Contract,
    guard: Contract,
    usdc: TokenDetails,
):
    """Test deposit to the vault we want to trade"""
    assert usdc.fetch_balance_of(erc4626_vault.vault_address) == pytest.approx(40_000)
    tx_hash = usdc.transfer(vault.address, 50_000).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)
    usdc_amount = 10_000 * 10**6
    fn_calls = approve_and_deposit_4626(
        vault=erc4626_vault,
        amount=usdc_amount,
        from_=vault.address,
    )
    for fn_call in fn_calls:
        target, call_data = encode_simple_vault_transaction(fn_call)
        tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)
    assert usdc.fetch_balance_of(erc4626_vault.vault_address) == pytest.approx(40_000)
    assert erc4626_vault.share_token.fetch_balance_of(vault.address) == pytest.approx(usdc_amount)


def test_guard_no_erc_4626_deposit_unapproved_vault(
    web3: Web3,
    malicious_vault: IPORVault,
    asset_manager: str,
    deployer: str,
    vault: Contract,
    guard: Contract,
    usdc: TokenDetails,
):
    """Test deposit to the vault we want to trade"""
    erc4626_vault = malicious_vault
    assert usdc.fetch_balance_of(erc4626_vault.vault_address) == pytest.approx(40_000)
    tx_hash = usdc.transfer(vault.address, 50_000).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)
    usdc_amount = 10_000 * 10**6
    fn_calls = approve_and_deposit_4626(
        vault=erc4626_vault,
        amount=usdc_amount,
        from_=vault.address,
    )
    for fn_call in fn_calls:
        target, call_data = encode_simple_vault_transaction(fn_call)
        tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)
    assert usdc.fetch_balance_of(erc4626_vault.vault_address) == pytest.approx(40_000)
    assert erc4626_vault.share_token.fetch_balance_of(vault.address) == pytest.approx(usdc_amount)


def test_guard_can_do_erc_4626_withdraw(
    web3: Web3,
    erc4626_vault: IPORVault,
    asset_manager: str,
    deployer: str,
    vault: Contract,
    guard: Contract,
    usdc: TokenDetails,
):
    """We can withdraw / sell our shares"""
    assert usdc.fetch_balance_of(erc4626_vault.vault_address) == pytest.approx(40_000)
    tx_hash = usdc.transfer(vault.address, 50_000).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)
    usdc_amount = 10_000 * 10**6
    fn_calls = approve_and_deposit_4626(
        vault=erc4626_vault,
        amount=usdc_amount,
        from_=vault.address,
    )
    for fn_call in fn_calls:
        target, call_data = encode_simple_vault_transaction(fn_call)
        tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)

    assert usdc.fetch_balance_of(erc4626_vault.vault_address) == pytest.approx(40_000)

    # Withdraw
    share_amount = erc4626_vault.share_token.fetch_balance_of(vault.address)
    fn_calls = approve_and_redeem_4626(
        vault=erc4626_vault,
        amount=share_amount,
        from_=vault.address,
    )
    for fn_call in fn_calls:
        target, call_data = encode_simple_vault_transaction(fn_call)
        tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)

    assert usdc.fetch_balance_of(erc4626_vault.vault_address) == pytest.approx(40_000)


def test_guard_malicious_withdraw(
    web3: Web3,
    erc4626_vault: IPORVault,
    asset_manager: HexAddress,
    deployer: str,
    vault: Contract,
    guard: Contract,
    usdc: TokenDetails,
    third_party: HexAddress,
):
    """Try to withdraw to the wrong destination"""
    assert usdc.fetch_balance_of(erc4626_vault.vault_address) == pytest.approx(40_000)
    tx_hash = usdc.transfer(vault.address, 50_000).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, tx_hash)
    usdc_amount = 10_000 * 10**6
    fn_calls = approve_and_deposit_4626(
        vault=erc4626_vault,
        amount=usdc_amount,
        from_=vault.address,
    )
    for fn_call in fn_calls:
        target, call_data = encode_simple_vault_transaction(fn_call)
        tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)

    assert usdc.fetch_balance_of(erc4626_vault.vault_address) == pytest.approx(40_000)

    # Withdraw
    share_amount = erc4626_vault.share_token.fetch_balance_of(vault.address)
    fn_calls = approve_and_redeem_4626(
        vault=erc4626_vault,
        amount=share_amount,
        from_=vault.address,
    )
    fn_calls[1].args[1] = third_party
    for fn_call in fn_calls:
        target, call_data = encode_simple_vault_transaction(fn_call)
        tx_hash = vault.functions.performCall(target, call_data).transact({"from": asset_manager})
        assert_transaction_success_with_explanation(web3, tx_hash, tracing=True)

    assert usdc.fetch_balance_of(erc4626_vault.vault_address) == pytest.approx(40_000)
