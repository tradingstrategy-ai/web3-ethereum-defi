"""Exercise Lagoon's real ERC-7540 settlement driver through GuardV0."""

import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem import LagoonDepositManager
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion
from eth_defi.erc_7540.deposit_redeem import ERC7540DepositTicket, ERC7540RedemptionTicket
from eth_defi.provider.anvil import AnvilLaunch, fund_erc20_on_anvil
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.trace import TransactionAssertionError, assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus, UnsupportedVaultSimulation

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")

#: The 722 Capital Base vault and historical state used by the existing
#: real-settlement Lagoon tests. Its valuation manager and Safe can be
#: impersonated by LagoonDepositManager.force_settle() on an Anvil fork.
LAGOON_722_CAPITAL_USDC_VAULT: HexAddress = "0xb09f761cb13baca8ec087ac476647361b6314f98"
LAGOON_722_CAPITAL_FORK_BLOCK = 35_094_246

pytestmark = [
    pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run this test"),
    pytest.mark.xdist_group("fork:base:lagoon-722-capital"),
]


@pytest.fixture
def anvil_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Return the shared historical Base state needed by Lagoon's driver."""
    return anvil_fork_pool.get_launch(JSON_RPC_BASE, LAGOON_722_CAPITAL_FORK_BLOCK)


@pytest.fixture
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the pooled 722 Capital settlement state."""
    return anvil_fork_pool.get_web3(JSON_RPC_BASE, LAGOON_722_CAPITAL_FORK_BLOCK)


@pytest.fixture(autouse=True)
def _evm_snapshot(anvil_fork: AnvilLaunch) -> Iterator[None]:
    """Revert Guard, request, settlement, and claim mutations after each test."""
    yield from evm_snapshot_revert(anvil_fork)


@pytest.fixture
def lagoon_vault(web3: Web3) -> LagoonVault:
    """Open the legacy Lagoon vault with its real Safe settlement configuration."""
    vault = create_vault_instance_autodetect(web3, LAGOON_722_CAPITAL_USDC_VAULT)
    assert isinstance(vault, LagoonVault)
    assert vault.version is LagoonVersion.legacy
    return vault


@pytest.fixture
def guarded_simple_vault(web3: Web3, lagoon_vault: LagoonVault) -> tuple[Contract, Contract, HexAddress]:
    """Deploy a guarded vault with separate governance and asset-manager accounts."""
    deployer = HexAddress(web3.eth.accounts[0])
    asset_manager = HexAddress(web3.eth.accounts[1])
    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)
    initialise_hash = simple_vault.functions.initialiseOwnership(deployer).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, initialise_hash)
    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())
    whitelist_hash = guard.functions.whitelistERC4626(lagoon_vault.address, "Allow Lagoon ERC-7540 lifecycle").transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, whitelist_hash)
    return simple_vault, guard, asset_manager


def _perform_guarded_call(web3: Web3, simple_vault: Contract, asset_manager: HexAddress, func: ContractFunction) -> HexBytes:
    """Execute one manager-generated request, approval, or claim through GuardV0."""
    target, call_data = encode_simple_vault_transaction(func)
    tx_hash = simple_vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return HexBytes(tx_hash)


def _assert_guarded_call_rejected(
    web3: Web3,
    simple_vault: Contract,
    asset_manager: HexAddress,
    func: ContractFunction,
    expected_error: str,
) -> None:
    """Assert GuardV0 rejects a substituted ERC-7540 receiver or owner."""
    target, call_data = encode_simple_vault_transaction(func)
    tx_hash = simple_vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    with pytest.raises(TransactionAssertionError, match=expected_error):
        assert_transaction_success_with_explanation(web3, tx_hash)


def test_guarded_lagoon_request_settle_and_claim(  # noqa: PLR0914
    web3: Web3,
    lagoon_vault: LagoonVault,
    guarded_simple_vault: tuple[Contract, Contract, HexAddress],
) -> None:
    """Run Lagoon's complete request-deposit/request-redeem lifecycle via GuardV0."""
    simple_vault, guard, asset_manager = guarded_simple_vault
    manager = lagoon_vault.get_deposit_manager()
    assert isinstance(manager, LagoonDepositManager)
    assert manager.has_synchronous_deposit() is False
    assert manager.has_synchronous_redemption() is False
    assert lagoon_vault.is_whitelisted_deposit() is False
    assert lagoon_vault.is_account_whitelisted(simple_vault.address) is True

    raw_amount = lagoon_vault.denomination_token.convert_to_raw(Decimal(10))
    fund_erc20_on_anvil(web3, lagoon_vault.denomination_token.address, simple_vault.address, raw_amount)
    approval = lagoon_vault.denomination_token.contract.functions.approve(lagoon_vault.address, raw_amount)
    _perform_guarded_call(web3, simple_vault, asset_manager, approval)
    assert guard.functions.isAllowedApprovalDestination(lagoon_vault.address).call() is True

    deposit_request = manager.create_deposit_request(owner=simple_vault.address, raw_amount=raw_amount)
    deposit_hash = _perform_guarded_call(web3, simple_vault, asset_manager, deposit_request.funcs[0])
    deposit_ticket = deposit_request.parse_deposit_transaction([deposit_hash])
    assert isinstance(deposit_ticket, ERC7540DepositTicket)
    assert manager.get_deposit_request_status(deposit_ticket) is AsyncVaultRequestStatus.pending
    assert manager.can_finish_deposit(deposit_ticket) is False

    deposit_settlement = manager.force_settle(deposit_ticket)
    assert deposit_settlement.status_before is AsyncVaultRequestStatus.pending
    assert deposit_settlement.status_after is AsyncVaultRequestStatus.claimable
    assert deposit_settlement.synthetic_assets_injected_raw == 0
    assert deposit_settlement.liquidity_constraints_ignored is False
    assert manager.can_finish_deposit(deposit_ticket) is True

    deposit_claim_hash = _perform_guarded_call(web3, simple_vault, asset_manager, manager.finish_deposit(deposit_ticket))
    deposit_analysis = manager.analyse_deposit(deposit_claim_hash, deposit_ticket)
    assert deposit_analysis.denomination_amount == Decimal(10)
    assert deposit_analysis.share_count > 0
    raw_shares = lagoon_vault.share_token.fetch_raw_balance_of(simple_vault.address)
    assert raw_shares == lagoon_vault.share_token.convert_to_raw(deposit_analysis.share_count)

    share_approval = lagoon_vault.share_token.contract.functions.approve(lagoon_vault.address, raw_shares)
    _perform_guarded_call(web3, simple_vault, asset_manager, share_approval)
    redemption_request = manager.create_redemption_request(owner=simple_vault.address, raw_shares=raw_shares)
    redemption_hash = _perform_guarded_call(web3, simple_vault, asset_manager, redemption_request.funcs[0])
    redemption_ticket = redemption_request.parse_redeem_transaction([redemption_hash])
    assert isinstance(redemption_ticket, ERC7540RedemptionTicket)
    assert manager.get_redemption_request_status(redemption_ticket) is AsyncVaultRequestStatus.pending

    redemption_settlement = manager.force_settle(redemption_ticket)
    assert redemption_settlement.status_before is AsyncVaultRequestStatus.pending
    assert redemption_settlement.status_after is AsyncVaultRequestStatus.claimable
    assert redemption_settlement.synthetic_assets_injected_raw == 0
    assert redemption_settlement.liquidity_constraints_ignored is False
    assert manager.can_finish_redeem(redemption_ticket) is True

    redemption_claim_hash = _perform_guarded_call(web3, simple_vault, asset_manager, manager.finish_redemption(redemption_ticket))
    redemption_analysis = manager.analyse_redemption(redemption_claim_hash, redemption_ticket)
    assert redemption_analysis.share_count == lagoon_vault.share_token.convert_to_decimals(raw_shares)
    assert redemption_analysis.denomination_amount > 0
    assert lagoon_vault.share_token.fetch_raw_balance_of(simple_vault.address) == 0
    assert lagoon_vault.denomination_token.fetch_raw_balance_of(simple_vault.address) == lagoon_vault.denomination_token.convert_to_raw(redemption_analysis.denomination_amount)


def test_guarded_lagoon_rejects_substituted_erc7540_addresses(
    web3: Web3,
    lagoon_vault: LagoonVault,
    guarded_simple_vault: tuple[Contract, Contract, HexAddress],
) -> None:
    """Reject substituted receiver/controller and owner values on every ERC-7540 call."""
    simple_vault, _guard, asset_manager = guarded_simple_vault
    outsider = HexAddress(web3.eth.accounts[3])

    for func, expected_error in (
        (lagoon_vault.vault_contract.functions.requestDeposit(1, outsider, simple_vault.address), "Receiver not whitelisted"),
        (lagoon_vault.vault_contract.functions.requestDeposit(1, simple_vault.address, outsider), "Owner not whitelisted"),
        (lagoon_vault.vault_contract.functions.requestRedeem(1, outsider, simple_vault.address), "Receiver not whitelisted"),
        (lagoon_vault.vault_contract.functions.requestRedeem(1, simple_vault.address, outsider), "Owner not whitelisted"),
        (lagoon_vault.vault_contract.functions.deposit(1, outsider, simple_vault.address), "Receiver not whitelisted"),
        (lagoon_vault.vault_contract.functions.deposit(1, simple_vault.address, outsider), "Owner not whitelisted"),
        (lagoon_vault.vault_contract.functions.redeem(1, outsider, simple_vault.address), "Receiver not whitelisted"),
        (lagoon_vault.vault_contract.functions.redeem(1, simple_vault.address, outsider), "Owner not whitelisted"),
    ):
        _assert_guarded_call_rejected(web3, simple_vault, asset_manager, func, expected_error)


def test_lagoon_force_settle_rejects_a_deployed_mock(
    web3: Web3,
    lagoon_vault: LagoonVault,
) -> None:
    """Keep Lagoon's inherited ``mock=`` API explicitly unsupported and typed."""
    deployer = HexAddress(web3.eth.accounts[0])
    mock_asset = deploy_contract(web3, "guard/GuardMockERC20.json", deployer, "Mock USD", "mUSD", 6)
    mock = deploy_contract(web3, "guard/MockERC7540Vault.json", deployer, mock_asset.address)
    manager = lagoon_vault.get_deposit_manager()
    assert isinstance(manager, LagoonDepositManager)

    with pytest.raises(UnsupportedVaultSimulation, match="no local mock settlement driver") as exc_info:
        manager.force_settle(object(), mock=mock)
    assert exc_info.value.unsupported_reason == "mock_settlement_driver_not_implemented"
