"""Exercise C-Sigma's synchronous deposit and redemption through GuardV0."""

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
from eth_defi.erc_4626.vault_protocol.csigma.deposit_redeem import CsigmaDepositManager
from eth_defi.erc_4626.vault_protocol.csigma.vault import CsigmaVault
from eth_defi.provider.anvil import AnvilLaunch, fund_erc20_on_anvil
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.trace import TransactionAssertionError, assert_transaction_success_with_explanation

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

#: Historical block with a fork-proven immediate full redemption on this pool.
#: The current canonical midnight block has withdrawal-manager queue debt, so
#: this exact deployment needs a separate shared-fork key.
CSIGMA_GUARD_FORK_BLOCK = 21_900_000

#: cSigma Superior Quality Private Credit pool.
CSIGMA_SUPQPV_POOL_ADDRESS: HexAddress = "0x50d59b785df23728d9948804f8ca3543237a1495"

#: Exact raw shares minted by a 100 USDT deposit at ``CSIGMA_GUARD_FORK_BLOCK``.
EXPECTED_DEPOSITED_RAW_SHARES = 94_445_037

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    pytest.mark.xdist_group("fork:ethereum:csigma-21900000"),
]


@pytest.fixture(scope="module")
def csigma_anvil_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Return the shared fixed C-Sigma fork used by guarded lifecycle tests."""
    return anvil_fork_pool.get_launch(JSON_RPC_ETHEREUM, CSIGMA_GUARD_FORK_BLOCK)


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the shared fixed C-Sigma fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, CSIGMA_GUARD_FORK_BLOCK)


@pytest.fixture(autouse=True)
def _evm_snapshot(csigma_anvil_fork: AnvilLaunch) -> Iterator[None]:
    """Revert every guarded lifecycle mutation before the following test."""
    yield from evm_snapshot_revert(csigma_anvil_fork)


@pytest.fixture
def csigma_vault(web3: Web3) -> CsigmaVault:
    """Open the C-Sigma deployment with a full immediate redemption path."""
    vault = create_vault_instance_autodetect(web3, CSIGMA_SUPQPV_POOL_ADDRESS)
    assert isinstance(vault, CsigmaVault)
    return vault


@pytest.fixture
def guarded_simple_vault(web3: Web3, csigma_vault: CsigmaVault) -> tuple[Contract, Contract, HexAddress]:
    """Deploy and configure a SimpleVaultV0 for the exact C-Sigma pool."""
    deployer = HexAddress(web3.eth.accounts[0])
    asset_manager = HexAddress(web3.eth.accounts[1])
    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)
    initialise_hash = simple_vault.functions.initialiseOwnership(deployer).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, initialise_hash)

    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())
    whitelist_hash = guard.functions.whitelistERC4626(csigma_vault.address, "Allow C-Sigma pool").transact({"from": deployer})
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


def _assert_guarded_call_rejected(
    web3: Web3,
    simple_vault: Contract,
    asset_manager: HexAddress,
    func: ContractFunction,
    expected_error: str,
) -> None:
    """Assert that GuardV0 rejects a manager call before target execution."""
    target, call_data = encode_simple_vault_transaction(func)
    tx_hash = simple_vault.functions.performCall(target, call_data).transact({"from": asset_manager})
    with pytest.raises(TransactionAssertionError, match=expected_error):
        assert_transaction_success_with_explanation(web3, tx_hash)


def test_guarded_csigma_deposit_and_redeem(  # noqa: PLR0914
    web3: Web3,
    csigma_vault: CsigmaVault,
    guarded_simple_vault: tuple[Contract, Contract, HexAddress],
) -> None:
    """GuardV0 executes C-Sigma manager approval, deposit and redemption calls."""
    simple_vault, guard, asset_manager = guarded_simple_vault
    manager = csigma_vault.get_deposit_manager()
    assert isinstance(manager, CsigmaDepositManager)

    amount = Decimal(100)
    raw_amount = csigma_vault.denomination_token.convert_to_raw(amount)
    fund_erc20_on_anvil(web3, csigma_vault.denomination_token.address, simple_vault.address, raw_amount)
    assert csigma_vault.denomination_token.fetch_raw_balance_of(simple_vault.address) == raw_amount

    approval_target = manager.get_deposit_approval_target()
    approval = csigma_vault.denomination_token.contract.functions.approve(approval_target, raw_amount)
    _perform_guarded_call(web3, simple_vault, asset_manager, approval)
    assert csigma_vault.denomination_token.contract.functions.allowance(simple_vault.address, approval_target).call() == raw_amount

    deposit_request = manager.create_deposit_request(owner=simple_vault.address, raw_amount=raw_amount)
    assert len(deposit_request.funcs) == 1
    deposit_hash = _perform_guarded_call(web3, simple_vault, asset_manager, deposit_request.funcs[0])
    deposit_ticket = deposit_request.parse_deposit_transaction([deposit_hash])
    deposit_analysis = manager.analyse_deposit(deposit_hash, deposit_ticket)
    assert deposit_analysis.denomination_amount == amount
    assert deposit_analysis.share_count == csigma_vault.share_token.convert_to_decimals(EXPECTED_DEPOSITED_RAW_SHARES)

    raw_shares = csigma_vault.share_token.fetch_raw_balance_of(simple_vault.address)
    assert raw_shares == EXPECTED_DEPOSITED_RAW_SHARES
    assert manager.force_settle(None).settlement_required is False

    redemption_request = manager.create_redemption_request(owner=simple_vault.address, raw_shares=raw_shares)
    assert len(redemption_request.funcs) == 1
    redemption_hash = _perform_guarded_call(web3, simple_vault, asset_manager, redemption_request.funcs[0])
    redemption_ticket = redemption_request.parse_redeem_transaction([redemption_hash])
    redemption_analysis = manager.analyse_redemption(redemption_hash, redemption_ticket)
    assert redemption_analysis.share_count == csigma_vault.share_token.convert_to_decimals(raw_shares)
    assert redemption_analysis.denomination_amount == Decimal("99.999999")
    assert csigma_vault.share_token.fetch_raw_balance_of(simple_vault.address) == 0
    assert csigma_vault.denomination_token.fetch_raw_balance_of(simple_vault.address) == raw_amount - 1
    assert guard.functions.isAllowedApprovalDestination(approval_target).call() is True


def test_guarded_csigma_rejects_unwhitelisted_deposit_receiver(
    web3: Web3,
    csigma_vault: CsigmaVault,
    guarded_simple_vault: tuple[Contract, Contract, HexAddress],
) -> None:
    """GuardV0 rejects a C-Sigma deposit whose share receiver is an outsider."""
    simple_vault, _guard, asset_manager = guarded_simple_vault
    malicious_receiver = HexAddress(web3.eth.accounts[3])
    malicious_deposit = csigma_vault.vault_contract.functions.deposit(1, malicious_receiver)
    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        asset_manager,
        malicious_deposit,
        "Receiver not whitelisted",
    )


def test_guarded_csigma_rejects_unwhitelisted_redeem_addresses(
    web3: Web3,
    csigma_vault: CsigmaVault,
    guarded_simple_vault: tuple[Contract, Contract, HexAddress],
) -> None:
    """GuardV0 rejects an asset manager substituting redeem addresses."""
    simple_vault, _guard, asset_manager = guarded_simple_vault
    outsider = HexAddress(web3.eth.accounts[3])
    malicious_receiver_redeem = csigma_vault.vault_contract.functions.redeem(
        1,
        outsider,
        simple_vault.address,
    )
    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        asset_manager,
        malicious_receiver_redeem,
        "Receiver not whitelisted",
    )

    malicious_owner_redeem = csigma_vault.vault_contract.functions.redeem(
        1,
        simple_vault.address,
        outsider,
    )
    _assert_guarded_call_rejected(
        web3,
        simple_vault,
        asset_manager,
        malicious_owner_redeem,
        "Owner not whitelisted",
    )
