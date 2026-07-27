"""Validate closed D2 deposit calldata through GuardV0 without broadcasting it.

The exact D2 deployment is intentionally forked during its closed funding
epoch. The test proves the new simulation-only manager method preserves the
production account-admission rule and produces GuardV0-accepted deposit
calldata. It does not validate an ERC-20 approval or approval-before-deposit
ordering: ``validateCall()`` validates one policy-relevant call at a time, and
there must be no deposit broadcast while the vault is closed.
"""

import os
from collections.abc import Iterator

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.d2.vault import D2DepositManager, D2Vault
from eth_defi.provider.anvil import AnvilLaunch, fund_erc20_on_anvil
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable, WhitelistingRequired, validate_closed_deposit_request_with_guard

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

#: D2 HYPE++ during a closed funding epoch, shared with the D2 regression test.
D2_CLOSED_DEPOSIT_FORK_BLOCK = 392_313_989
D2_HYPE_PLUS_PLUS_ADDRESS: HexAddress = "0x75288264fdfea8ce68e6d852696ab1ce2f3e5004"

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run this test"),
    pytest.mark.xdist_group("fork:arbitrum:392313989"),
]


@pytest.fixture(scope="module")
def anvil_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Return the shared fixed D2 fork used by the closed-deposit regression."""
    return anvil_fork_pool.get_launch(JSON_RPC_ARBITRUM, D2_CLOSED_DEPOSIT_FORK_BLOCK)


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the cached shared fork at D2's closed funding epoch."""
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, D2_CLOSED_DEPOSIT_FORK_BLOCK)


@pytest.fixture(autouse=True)
def _evm_snapshot(anvil_fork: AnvilLaunch) -> Iterator[None]:
    """Restore every deployment and balance mutation after this validation test."""
    yield from evm_snapshot_revert(anvil_fork)


def test_closed_d2_deposit_calldata_passes_guard_validation_without_broadcast(web3: Web3) -> None:
    """Keep closure evidence separate from GuardV0 policy validation.

    D2's token-balance admission remains enabled because it is an account
    eligibility rule, not a temporary closure. Approval validation and ordering
    remain intentionally out of scope: no approval or deposit transaction is
    sent in this test.
    """
    deployer = HexAddress(web3.eth.accounts[0])
    asset_manager = HexAddress(web3.eth.accounts[1])
    vault = create_vault_instance_autodetect(web3, D2_HYPE_PLUS_PLUS_ADDRESS)
    assert isinstance(vault, D2Vault)
    manager = vault.get_deposit_manager()
    assert isinstance(manager, D2DepositManager)

    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)
    initialise_hash = simple_vault.functions.initialiseOwnership(deployer).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, initialise_hash)
    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())

    raw_amount = vault.denomination_token.convert_to_raw(10)
    admission_balance = int(vault.vault_contract.functions.whitelistBalance().call()) + raw_amount + 1
    fund_erc20_on_anvil(web3, vault.denomination_token.address, simple_vault.address, admission_balance)
    assert vault.is_account_whitelisted(simple_vault.address) is True

    with pytest.raises(VaultFlowUnavailable) as exc_info:
        manager.create_deposit_request(owner=simple_vault.address, raw_amount=raw_amount)
    assert exc_info.value.preflight_result == "deposit_closed"
    closure = exc_info.value

    cash_before = vault.denomination_token.fetch_raw_balance_of(simple_vault.address)
    shares_before = vault.share_token.fetch_raw_balance_of(simple_vault.address)
    validation_request = manager.create_deposit_request_for_guard_validation(simple_vault.address, raw_amount)
    assert len(validation_request.funcs) == 1
    with pytest.raises(Exception, match="Target not allowed"):
        validate_closed_deposit_request_with_guard(validation_request, closure, guard, asset_manager)

    whitelist_hash = guard.functions.whitelistERC4626(vault.address, "Allow D2 HYPE++ ERC-4626 deposit").transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, whitelist_hash)
    evidence = validate_closed_deposit_request_with_guard(validation_request, closure, guard, asset_manager)

    assert evidence.vault_address == vault.address
    assert evidence.owner == simple_vault.address
    assert evidence.raw_amount == raw_amount
    assert evidence.preflight_result == "deposit_closed"
    assert evidence.closure_reason == closure.reason
    assert len(evidence.calls) == 1
    assert evidence.calls[0].target == vault.address
    assert evidence.calls[0].selector == bytes.fromhex("6e553f65")

    assert vault.denomination_token.fetch_raw_balance_of(simple_vault.address) == cash_before
    assert vault.share_token.fetch_raw_balance_of(simple_vault.address) == shares_before


def test_closed_d2_guard_validation_retains_protocol_account_admission(web3: Web3) -> None:
    """A closed D2 validation request must not bypass its balance-based admission.

    The D2 epoch closure allows only the temporary phase/capacity/balance
    bypass. The protocol's ``onlyWhitelisted`` mapping-or-balance rule remains
    a real deposit constraint even though no approval or deposit is broadcast.
    """
    vault = create_vault_instance_autodetect(web3, D2_HYPE_PLUS_PLUS_ADDRESS)
    assert isinstance(vault, D2Vault)
    manager = vault.get_deposit_manager()
    assert isinstance(manager, D2DepositManager)
    unadmitted_owner = HexAddress(web3.eth.accounts[2])
    raw_amount = vault.denomination_token.convert_to_raw(10)

    assert vault.is_account_whitelisted(unadmitted_owner) is False
    with pytest.raises(WhitelistingRequired, match="not whitelisted"):
        manager.create_deposit_request_for_guard_validation(unadmitted_owner, raw_amount)
