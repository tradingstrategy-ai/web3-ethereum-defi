"""Exercise Gains' asynchronous redemption lifecycle through GuardV0."""

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
from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import GainsDepositManager, GainsRedemptionTicket
from eth_defi.erc_4626.vault_protocol.gains.testing import force_next_gains_epoch
from eth_defi.erc_4626.vault_protocol.gains.vault import GainsVault
from eth_defi.provider.anvil import AnvilLaunch, fund_erc20_on_anvil, set_balance
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.trace import TransactionAssertionError, assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

#: Fixed block with a fork-proven Gains deposit, epoch settlement and redemption
#: lifecycle. This predates the canonical midnight block because the established
#: test values and epoch transition are specific to this known state.
GAINS_GUARD_FORK_BLOCK = 375_216_652

#: Gains Network gUSDC vault on Arbitrum.
GAINS_GUSDC_VAULT_ADDRESS: HexAddress = "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0"

#: Exact raw gUSDC shares minted by a 100 USDC deposit at the fixed fork block.
EXPECTED_DEPOSITED_RAW_SHARES = 81_542_030

#: Epoch advances needed to unlock this redemption at the fixed fork block.
EXPECTED_SETTLEMENT_EPOCHS = 3

#: Exact raw USDC returned by redemption after Gains' two-unit rounding fee.
EXPECTED_REDEEMED_RAW_AMOUNT = 99_999_998

#: Submit expected Guard reverts without ``eth_estimateGas`` masking the
#: revert before a receipt can be inspected.
GUARDED_REJECTION_GAS_LIMIT = 1_000_000

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run these tests"),
    pytest.mark.xdist_group("fork:arbitrum:gains-375216652"),
]


@pytest.fixture(scope="module")
def gains_anvil_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Return the shared fixed Gains fork used by guarded lifecycle tests."""
    return anvil_fork_pool.get_launch(JSON_RPC_ARBITRUM, GAINS_GUARD_FORK_BLOCK)


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the shared fixed Gains fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, GAINS_GUARD_FORK_BLOCK)


@pytest.fixture(autouse=True)
def _evm_snapshot(gains_anvil_fork: AnvilLaunch) -> Iterator[None]:
    """Revert every guarded lifecycle mutation before the following test."""
    yield from evm_snapshot_revert(gains_anvil_fork)


@pytest.fixture
def gains_vault(web3: Web3) -> GainsVault:
    """Open the Gains deployment with a fork-proven epoch lifecycle."""
    vault = create_vault_instance_autodetect(web3, GAINS_GUSDC_VAULT_ADDRESS)
    assert isinstance(vault, GainsVault)
    return vault


@pytest.fixture
def guarded_simple_vault(web3: Web3, gains_vault: GainsVault) -> tuple[Contract, Contract, HexAddress]:
    """Deploy and configure a SimpleVaultV0 for the Gains call surface."""
    deployer = HexAddress(web3.eth.accounts[0])
    asset_manager = HexAddress(web3.eth.accounts[1])
    set_balance(web3, deployer, Web3.to_wei(10, "ether"))
    set_balance(web3, asset_manager, Web3.to_wei(10, "ether"))
    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)
    initialise_hash = simple_vault.functions.initialiseOwnership(deployer).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, initialise_hash)

    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())
    whitelist_hash = guard.functions.whitelistERC4626(gains_vault.address, "Allow Gains gUSDC").transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, whitelist_hash)
    return simple_vault, guard, asset_manager


def _perform_guarded_call(
    web3: Web3,
    simple_vault: Contract,
    asset_manager: HexAddress,
    func: ContractFunction,
) -> HexBytes:
    """Execute one manager call through SimpleVaultV0 and GuardV0."""
    target, call_data = encode_simple_vault_transaction(func)
    tx_hash = simple_vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    assert_transaction_success_with_explanation(web3, tx_hash)
    return tx_hash


def _assert_guarded_call_rejected(
    web3: Web3,
    simple_vault: Contract,
    asset_manager: HexAddress,
    func: ContractFunction,
    expected_error: str,
) -> None:
    """Assert that GuardV0 rejects a call before the Gains vault executes it."""
    target, call_data = encode_simple_vault_transaction(func)
    tx_hash = simple_vault.functions.performCall(target, call_data).transact({"from": asset_manager, "gas": GUARDED_REJECTION_GAS_LIMIT})
    with pytest.raises(TransactionAssertionError, match=expected_error):
        assert_transaction_success_with_explanation(web3, tx_hash)


def test_guarded_gains_deposit_request_settlement_and_redemption(  # noqa: PLR0914
    web3: Web3,
    gains_vault: GainsVault,
    guarded_simple_vault: tuple[Contract, Contract, HexAddress],
) -> None:
    """GuardV0 executes the complete Gains manager lifecycle."""
    simple_vault, guard, asset_manager = guarded_simple_vault
    manager = gains_vault.get_deposit_manager()
    assert isinstance(manager, GainsDepositManager)

    amount = Decimal(100)
    raw_amount = gains_vault.denomination_token.convert_to_raw(amount)
    fund_erc20_on_anvil(web3, gains_vault.denomination_token.address, simple_vault.address, raw_amount)
    assert gains_vault.denomination_token.fetch_raw_balance_of(simple_vault.address) == raw_amount

    approval_target = manager.get_deposit_approval_target()
    approval = gains_vault.denomination_token.contract.functions.approve(approval_target, raw_amount)
    _perform_guarded_call(web3, simple_vault, asset_manager, approval)
    assert gains_vault.denomination_token.contract.functions.allowance(simple_vault.address, approval_target).call() == raw_amount

    deposit_request = manager.create_deposit_request(owner=simple_vault.address, raw_amount=raw_amount)
    assert len(deposit_request.funcs) == 1
    deposit_hash = _perform_guarded_call(web3, simple_vault, asset_manager, deposit_request.funcs[0])
    deposit_ticket = deposit_request.parse_deposit_transaction([deposit_hash])
    deposit_analysis = manager.analyse_deposit(deposit_hash, deposit_ticket)
    assert deposit_analysis.denomination_amount == amount
    assert deposit_analysis.share_count == gains_vault.share_token.convert_to_decimals(EXPECTED_DEPOSITED_RAW_SHARES)

    raw_shares = gains_vault.share_token.fetch_raw_balance_of(simple_vault.address)
    assert raw_shares == EXPECTED_DEPOSITED_RAW_SHARES
    assert manager.force_settle(None).settlement_required is False
    assert manager.can_create_redemption_request(simple_vault.address) is False

    force_next_gains_epoch(gains_vault, HexAddress(web3.eth.accounts[0]))
    assert manager.can_create_redemption_request(simple_vault.address) is True

    redemption_request = manager.create_redemption_request(owner=simple_vault.address, raw_shares=raw_shares)
    assert len(redemption_request.funcs) == 1
    request_hash = _perform_guarded_call(web3, simple_vault, asset_manager, redemption_request.funcs[0])
    redemption_ticket = redemption_request.parse_redeem_transaction([request_hash])
    assert isinstance(redemption_ticket, GainsRedemptionTicket)
    assert redemption_ticket.raw_shares == raw_shares
    assert manager.get_redemption_request_status(redemption_ticket) == AsyncVaultRequestStatus.pending

    settlement = manager.force_settle(redemption_ticket)
    assert settlement.settlement_required is True
    assert settlement.status_before == AsyncVaultRequestStatus.pending
    assert settlement.status_after == AsyncVaultRequestStatus.claimable
    assert len(settlement.transaction_hashes) == EXPECTED_SETTLEMENT_EPOCHS

    redemption_hash = _perform_guarded_call(web3, simple_vault, asset_manager, manager.finish_redemption(redemption_ticket))
    redemption_analysis = manager.analyse_redemption(redemption_hash, redemption_ticket)
    assert redemption_analysis.share_count == gains_vault.share_token.convert_to_decimals(raw_shares)
    assert redemption_analysis.denomination_amount == gains_vault.denomination_token.convert_to_decimals(EXPECTED_REDEEMED_RAW_AMOUNT)
    assert gains_vault.share_token.fetch_raw_balance_of(simple_vault.address) == 0
    assert gains_vault.denomination_token.fetch_raw_balance_of(simple_vault.address) == EXPECTED_REDEEMED_RAW_AMOUNT
    assert guard.functions.isAllowedApprovalDestination(approval_target).call() is True


def test_guarded_gains_rejects_unwhitelisted_withdrawal_owner(
    web3: Web3,
    gains_vault: GainsVault,
    guarded_simple_vault: tuple[Contract, Contract, HexAddress],
) -> None:
    """GuardV0 rejects a Gains withdrawal request against an outsider's shares."""
    simple_vault, _guard, asset_manager = guarded_simple_vault
    outsider = HexAddress(web3.eth.accounts[3])
    malicious_request = gains_vault.vault_contract.functions.makeWithdrawRequest(1, outsider)
    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        asset_manager,
        malicious_request,
        "Owner not whitelisted",
    )


def test_guarded_gains_rejects_unwhitelisted_redeem_addresses(
    web3: Web3,
    gains_vault: GainsVault,
    guarded_simple_vault: tuple[Contract, Contract, HexAddress],
) -> None:
    """GuardV0 rejects Gains redemption claims with substituted addresses."""
    simple_vault, _guard, asset_manager = guarded_simple_vault
    outsider = HexAddress(web3.eth.accounts[3])

    malicious_receiver_redeem = gains_vault.vault_contract.functions.redeem(1, outsider, simple_vault.address)
    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        asset_manager,
        malicious_receiver_redeem,
        "Receiver not whitelisted",
    )

    malicious_owner_redeem = gains_vault.vault_contract.functions.redeem(1, simple_vault.address, outsider)
    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        asset_manager,
        malicious_owner_redeem,
        "Owner not whitelisted",
    )
