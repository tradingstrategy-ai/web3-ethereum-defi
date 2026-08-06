"""Exercise Goat's overloaded ERC-4626 Deposit event through GuardV0."""

import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction
from web3.logs import DISCARD

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.goat.deposit_redeem import GoatDepositManager
from eth_defi.erc_4626.vault_protocol.goat.vault import GoatVault
from eth_defi.provider.anvil import AnvilLaunch, set_balance, unlock_account
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK
from eth_defi.trace import TransactionAssertionError, assert_transaction_success_with_explanation

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

GOAT_VAULT: HexAddress = "0x8a1eF3066553275829d1c0F64EE8D5871D5ce9d3"
RAW_DEPOSIT_AMOUNT = 1_000_000

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]


@pytest.fixture(scope="module")
def anvil_arbitrum(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Return the shared fixed Arbitrum fork used for Goat characterisation."""
    return anvil_fork_pool.get_launch(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the shared fixed Arbitrum fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(autouse=True)
def _evm_snapshot(anvil_arbitrum: AnvilLaunch) -> Iterator[None]:
    """Restore the local backend after each guarded Goat test."""
    yield from evm_snapshot_revert(anvil_arbitrum)


@pytest.fixture
def goat_vault(web3: Web3) -> GoatVault:
    """Open the Goat Multistrategy deployment with overloaded Deposit events."""
    vault = create_vault_instance_autodetect(web3, GOAT_VAULT)
    assert isinstance(vault, GoatVault)
    return vault


@pytest.fixture
def guarded_simple_vault(web3: Web3, goat_vault: GoatVault) -> tuple[Contract, HexAddress]:
    """Deploy a GuardV0-controlled caller and allow the Goat vault."""
    deployer = HexAddress(web3.eth.accounts[0])
    asset_manager = HexAddress(web3.eth.accounts[1])
    set_balance(web3, deployer, Web3.to_wei(1, "ether"))
    set_balance(web3, asset_manager, Web3.to_wei(1, "ether"))
    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)
    assert_transaction_success_with_explanation(web3, simple_vault.functions.initialiseOwnership(deployer).transact({"from": deployer}))

    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())
    assert_transaction_success_with_explanation(
        web3,
        guard.functions.whitelistERC4626(goat_vault.address, "Allow Goat Multistrategy").transact({"from": deployer}),
    )
    return simple_vault, asset_manager


def _perform_guarded_call(
    web3: Web3,
    simple_vault: Contract,
    asset_manager: HexAddress,
    func: ContractFunction,
) -> HexBytes:
    """Execute one manager-generated call through SimpleVaultV0 and GuardV0."""
    target, call_data = encode_simple_vault_transaction(func)
    tx_hash = simple_vault.functions.performCall(target, call_data).transact({"from": asset_manager, "gas": 2_000_000})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return HexBytes(tx_hash)


def _assert_guarded_call_rejected(
    web3: Web3,
    simple_vault: Contract,
    asset_manager: HexAddress,
    func: ContractFunction,
    expected_error: str,
) -> None:
    """Assert that GuardV0 rejects a Goat call before target execution."""
    target, call_data = encode_simple_vault_transaction(func)
    tx_hash = simple_vault.functions.performCall(target, call_data).transact({"from": asset_manager, "gas": 2_000_000})
    with pytest.raises(TransactionAssertionError, match=expected_error):
        assert_transaction_success_with_explanation(web3, tx_hash)


def _assert_guarded_redemption(
    web3: Web3,
    goat_vault: GoatVault,
    manager: GoatDepositManager,
    guarded_simple_vault: tuple[Contract, HexAddress],
    raw_shares: int,
) -> None:
    """Redeem the exact shares and verify the explicit ERC-4626 Withdraw event."""
    simple_vault, asset_manager = guarded_simple_vault
    redemption_request = manager.create_redemption_request(owner=simple_vault.address, raw_shares=raw_shares)
    redemption_hash = _perform_guarded_call(web3, simple_vault, asset_manager, redemption_request.funcs[0])
    redemption_ticket = redemption_request.parse_redeem_transaction([redemption_hash])

    redemption_receipt = web3.eth.get_transaction_receipt(redemption_hash)
    assert not goat_vault.vault_contract.events.Withdraw().process_receipt(redemption_receipt, errors=DISCARD)
    standard_withdraw_events = goat_vault.vault_contract.events["Withdraw(address,address,address,uint256,uint256)"]().process_receipt(
        redemption_receipt,
        errors=DISCARD,
    )
    assert len(standard_withdraw_events) == 1
    withdrawn_raw_assets = standard_withdraw_events[0]["args"]["assets"]
    assert standard_withdraw_events[0]["args"]["shares"] == raw_shares

    redemption_analysis = manager.analyse_redemption(redemption_hash, redemption_ticket)
    assert goat_vault.share_token.convert_to_raw(redemption_analysis.share_count) == raw_shares
    assert goat_vault.denomination_token.convert_to_raw(redemption_analysis.denomination_amount) == withdrawn_raw_assets
    assert goat_vault.share_token.fetch_raw_balance_of(simple_vault.address) == 0
    assert goat_vault.denomination_token.fetch_raw_balance_of(simple_vault.address) == withdrawn_raw_assets


def test_guarded_goat_deposit_uses_erc4626_event_signature(
    web3: Web3,
    goat_vault: GoatVault,
    guarded_simple_vault: tuple[Contract, HexAddress],
) -> None:
    """Analyse a successful guarded Goat deposit despite the overloaded event name."""
    simple_vault, asset_manager = guarded_simple_vault
    manager = goat_vault.get_deposit_manager()
    assert isinstance(manager, GoatDepositManager)

    # The vault itself holds enough USDC.e at the fixed fork state. Impersonate
    # it only to seed the local Guard-controlled caller; snapshot/revert keeps
    # this test's state mutation isolated from the shared fork.
    donor_balance = goat_vault.denomination_token.fetch_raw_balance_of(goat_vault.address)
    assert donor_balance >= RAW_DEPOSIT_AMOUNT
    unlock_account(web3, goat_vault.address)
    set_balance(web3, goat_vault.address, Web3.to_wei(1, "ether"))
    transfer_hash = goat_vault.denomination_token.contract.functions.transfer(simple_vault.address, RAW_DEPOSIT_AMOUNT).transact({"from": goat_vault.address})
    assert_transaction_success_with_explanation(web3, transfer_hash)

    _perform_guarded_call(
        web3,
        simple_vault,
        asset_manager,
        goat_vault.denomination_token.contract.functions.approve(goat_vault.address, RAW_DEPOSIT_AMOUNT),
    )
    deposit_request = manager.create_deposit_request(owner=simple_vault.address, raw_amount=RAW_DEPOSIT_AMOUNT)
    deposit_hash = _perform_guarded_call(web3, simple_vault, asset_manager, deposit_request.funcs[0])
    deposit_ticket = deposit_request.parse_deposit_transaction([deposit_hash])

    # Web3 resolves the bare event name to Goat's unrelated
    # Deposit(uint256,address) overload. The manager must select the canonical
    # ERC-4626 Deposit(address,address,uint256,uint256) topic instead.
    receipt = web3.eth.get_transaction_receipt(deposit_hash)
    assert not goat_vault.vault_contract.events.Deposit().process_receipt(receipt, errors=DISCARD)
    standard_events = goat_vault.vault_contract.events["Deposit(address,address,uint256,uint256)"]().process_receipt(receipt, errors=DISCARD)
    assert len(standard_events) == 1
    assert standard_events[0]["args"]["assets"] == RAW_DEPOSIT_AMOUNT

    analysis = manager.analyse_deposit(deposit_hash, deposit_ticket)
    assert analysis.denomination_amount == Decimal(1)
    assert analysis.share_count > 0

    raw_shares = goat_vault.share_token.fetch_raw_balance_of(simple_vault.address)
    assert raw_shares == goat_vault.share_token.convert_to_raw(analysis.share_count)
    _assert_guarded_redemption(web3, goat_vault, manager, guarded_simple_vault, raw_shares)


def test_guarded_goat_rejects_substituted_redemption_addresses(
    web3: Web3,
    goat_vault: GoatVault,
    guarded_simple_vault: tuple[Contract, HexAddress],
) -> None:
    """Reject Goat redemption receiver and owner substitutions before execution."""
    simple_vault, asset_manager = guarded_simple_vault
    outsider = HexAddress(web3.eth.accounts[3])

    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        asset_manager,
        goat_vault.vault_contract.functions.redeem(1, outsider, simple_vault.address),
        "Receiver not whitelisted",
    )
    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        asset_manager,
        goat_vault.vault_contract.functions.redeem(1, simple_vault.address, outsider),
        "Owner not whitelisted",
    )
