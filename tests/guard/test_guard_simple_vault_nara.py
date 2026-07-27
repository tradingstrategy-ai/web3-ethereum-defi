"""Exercise NaraUSD+'s cooldown redemption lifecycle through GuardV0."""

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
from eth_defi.erc_4626.vault_protocol.nara.constants import NARAUSD_PLUS_VAULT
from eth_defi.erc_4626.vault_protocol.nara.deposit_redeem import NaraDepositManager, NaraRedemptionTicket
from eth_defi.erc_4626.vault_protocol.nara.vault import NaraVault
from eth_defi.provider.anvil import AnvilLaunch, mine, set_balance, unlock_account
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.trace import TransactionAssertionError, assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus, UnsupportedVaultSimulation, VaultForcedSettlementResult

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

#: Fixed block with a fork-proven NaraUSD+ deposit and full cooldown claim.
NARA_GUARD_FORK_BLOCK = 25_575_245

#: Exact denominator amount used by Nara's existing lifecycle regression test.
NARA_DEPOSIT_AMOUNT = Decimal(10)

#: Stable future timestamp used because snapshot/revert does not restore Anvil time.
NARA_GUARD_SIMULATION_TIMESTAMP = 2_000_000_000

#: Exact NaraUSD+ shares minted by the guarded deposit at the fixed fork block.
EXPECTED_DEPOSITED_RAW_SHARES = 9_872_604_805_169_425_601

#: Exact NaraUSD returned after the seven-day cooldown at the fixed fork block.
EXPECTED_REDEEMED_RAW_AMOUNT = 9_999_999_999_999_999_999

#: NaraUSD/USDC Curve pool used only to arrange real fork funding.
NARAUSD_USDC_CURVE_POOL: HexAddress = "0xf05F1b7bC5D9f966193201e9f4F320A98aAF260C"

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    pytest.mark.xdist_group("fork:ethereum:nara-25575245"),
]


@pytest.fixture(scope="module")
def nara_anvil_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Return the shared fixed Nara fork used by guarded lifecycle tests."""
    return anvil_fork_pool.get_launch(JSON_RPC_ETHEREUM, NARA_GUARD_FORK_BLOCK)


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the shared fixed Nara fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, NARA_GUARD_FORK_BLOCK)


@pytest.fixture(autouse=True)
def _evm_snapshot(nara_anvil_fork: AnvilLaunch) -> Iterator[None]:
    """Revert every guarded Nara mutation before the following test."""
    yield from evm_snapshot_revert(nara_anvil_fork)


@pytest.fixture
def nara_vault(web3: Web3) -> NaraVault:
    """Open NaraUSD+ at the fixed cooldown lifecycle block."""
    vault = create_vault_instance_autodetect(web3, NARAUSD_PLUS_VAULT)
    assert isinstance(vault, NaraVault)
    return vault


@pytest.fixture
def guarded_simple_vault(web3: Web3, nara_vault: NaraVault) -> tuple[Contract, Contract, HexAddress]:
    """Deploy and configure a SimpleVaultV0 for Nara's call surface."""
    deployer = HexAddress(web3.eth.accounts[0])
    asset_manager = HexAddress(web3.eth.accounts[1])
    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)
    initialise_hash = simple_vault.functions.initialiseOwnership(deployer).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, initialise_hash)

    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())
    whitelist_hash = guard.functions.whitelistERC4626(nara_vault.address, "Allow NaraUSD+").transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, whitelist_hash)
    return simple_vault, guard, asset_manager


def _perform_guarded_call(
    web3: Web3,
    simple_vault: Contract,
    asset_manager: HexAddress,
    func: ContractFunction,
) -> HexBytes:
    """Execute one manager-generated call through SimpleVaultV0 and GuardV0."""
    target, call_data = encode_simple_vault_transaction(func)
    tx_hash = simple_vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return tx_hash


def _assert_cooldown_settlement(settlement: VaultForcedSettlementResult) -> None:
    """Assert the hashless Nara time-advance settlement result."""
    assert settlement.settlement_required is True
    assert settlement.status_before == AsyncVaultRequestStatus.pending
    assert settlement.status_after == AsyncVaultRequestStatus.claimable
    assert settlement.transaction_hashes == ()


def test_guarded_nara_deposit_cooldown_and_unstake(
    web3: Web3,
    nara_vault: NaraVault,
    guarded_simple_vault: tuple[Contract, Contract, HexAddress],
) -> None:
    """GuardV0 executes Nara's deposit, cooldown and final unstake calls."""
    simple_vault, guard, asset_manager = guarded_simple_vault
    manager = nara_vault.get_deposit_manager()
    assert isinstance(manager, NaraDepositManager)
    mine(web3, timestamp=NARA_GUARD_SIMULATION_TIMESTAMP)

    raw_amount = nara_vault.denomination_token.convert_to_raw(NARA_DEPOSIT_AMOUNT)
    set_balance(web3, NARAUSD_USDC_CURVE_POOL, Web3.to_wei(10, "ether"))
    unlock_account(web3, NARAUSD_USDC_CURVE_POOL)
    funding_hash = nara_vault.denomination_token.contract.functions.transfer(simple_vault.address, raw_amount).transact({"from": NARAUSD_USDC_CURVE_POOL})
    assert_transaction_success_with_explanation(web3, funding_hash)
    approval = nara_vault.denomination_token.contract.functions.approve(manager.get_deposit_approval_target(), raw_amount)
    _perform_guarded_call(web3, simple_vault, asset_manager, approval)

    deposit_request = manager.create_deposit_request(owner=simple_vault.address, amount=NARA_DEPOSIT_AMOUNT)
    deposit_hash = _perform_guarded_call(web3, simple_vault, asset_manager, deposit_request.funcs[0])
    manager.analyse_deposit(deposit_hash, deposit_request.parse_deposit_transaction([deposit_hash]))
    raw_shares = nara_vault.share_token.fetch_raw_balance_of(simple_vault.address)
    assert raw_shares == EXPECTED_DEPOSITED_RAW_SHARES

    redemption_request = manager.create_redemption_request(owner=simple_vault.address, raw_shares=raw_shares)
    cooldown_hash = _perform_guarded_call(web3, simple_vault, asset_manager, redemption_request.funcs[0])
    ticket = redemption_request.parse_redeem_transaction([cooldown_hash])
    assert isinstance(ticket, NaraRedemptionTicket)
    assert manager.get_redemption_request_status(ticket) == AsyncVaultRequestStatus.pending

    with pytest.raises(UnsupportedVaultSimulation, match="must use the manager's exact vault contract"):
        manager.force_settle(ticket, mock=guard)

    _assert_cooldown_settlement(manager.force_settle(ticket, mock=nara_vault.narausd_plus_contract))
    assert manager.get_redemption_request_status(ticket) == AsyncVaultRequestStatus.claimable
    unstake_hash = _perform_guarded_call(web3, simple_vault, asset_manager, manager.finish_redemption(ticket))
    redemption_analysis = manager.analyse_redemption(unstake_hash, ticket)
    assert redemption_analysis.share_count == nara_vault.share_token.convert_to_decimals(raw_shares)
    assert redemption_analysis.denomination_amount == nara_vault.denomination_token.convert_to_decimals(EXPECTED_REDEEMED_RAW_AMOUNT)
    assert nara_vault.share_token.fetch_raw_balance_of(simple_vault.address) == 0
    assert nara_vault.denomination_token.fetch_raw_balance_of(simple_vault.address) == EXPECTED_REDEEMED_RAW_AMOUNT
    assert guard.functions.isAllowedApprovalDestination(manager.get_deposit_approval_target()).call() is True


def test_guarded_nara_rejects_unwhitelisted_unstake_receiver(
    web3: Web3,
    nara_vault: NaraVault,
    guarded_simple_vault: tuple[Contract, Contract, HexAddress],
) -> None:
    """GuardV0 rejects a Nara ``unstake`` request with an outsider receiver."""
    simple_vault, _guard, asset_manager = guarded_simple_vault
    outsider = HexAddress(web3.eth.accounts[3])
    target, call_data = encode_simple_vault_transaction(nara_vault.narausd_plus_contract.functions.unstake(outsider))
    tx_hash = simple_vault.functions.performCall(target, call_data).transact({"from": asset_manager, "gas": 1_000_000})
    with pytest.raises(TransactionAssertionError, match="Receiver not whitelisted"):
        assert_transaction_success_with_explanation(web3, tx_hash)
