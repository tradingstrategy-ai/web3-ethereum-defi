"""Validate a globally closed Yearn V3 deposit through GuardV0 without broadcasting.

The representative Yearn vault has no deposit-limit module and a zero global
deposit limit at the fixed fork block. Its ``maxDeposit(address(0))`` value is
always zero and therefore unsuitable for generic ERC-4626 detection. The
Yearn-specific manager proves the global limit through its own state before
constructing deposit calldata. The test deliberately omits approval validation
and order: ``validateCall()`` assesses policy-relevant calls independently, and
neither approval nor deposit is broadcast against the closed vault.
"""

import os
from collections.abc import Iterator

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.yearn.deposit_redeem import YearnV3DepositManager
from eth_defi.erc_4626.vault_protocol.yearn.vault import YearnV3Vault
from eth_defi.provider.anvil import AnvilLaunch
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable, validate_closed_deposit_request_with_guard

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")

#: Yearn yvUSDC.e has a zero global deposit limit and no limit module here.
YEARN_CLOSED_DEPOSIT_VAULT: HexAddress = "0x9fa306b1f4a6a83fec98d8ebbabedff78c407f6b"

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM needed to run this test"),
    pytest.mark.xdist_group("fork:arbitrum:midnight"),
]


@pytest.fixture(scope="module")
def anvil_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Use the shared, fixed Arbitrum fork for a reproducible closure state."""
    return anvil_fork_pool.get_launch(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the shared Arbitrum midnight fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)


@pytest.fixture(autouse=True)
def _evm_snapshot(anvil_fork: AnvilLaunch) -> Iterator[None]:
    """Isolate the GuardV0 deployment and whitelist mutation."""
    yield from evm_snapshot_revert(anvil_fork)


def test_closed_yearn_deposit_calldata_passes_guard_validation_without_broadcast(web3: Web3) -> None:
    """Keep Yearn's live closure separate from GuardV0 call compatibility."""
    deployer = HexAddress(web3.eth.accounts[0])
    asset_manager = HexAddress(web3.eth.accounts[1])
    vault = create_vault_instance_autodetect(web3, YEARN_CLOSED_DEPOSIT_VAULT)
    assert isinstance(vault, YearnV3Vault)
    manager = vault.get_deposit_manager()
    assert isinstance(manager, YearnV3DepositManager)

    simple_vault = deploy_contract(web3, "guard/SimpleVaultV0.json", deployer, asset_manager, libraries=GUARD_LIBRARIES)
    initialise_hash = simple_vault.functions.initialiseOwnership(deployer).transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, initialise_hash)
    guard = get_deployed_contract(web3, "guard/GuardV0.json", simple_vault.functions.guard().call())

    raw_amount = vault.denomination_token.convert_to_raw(10)
    with pytest.raises(VaultFlowUnavailable) as exc_info:
        manager.create_deposit_request(owner=simple_vault.address, raw_amount=raw_amount)
    closure = exc_info.value
    assert closure.preflight_result == "deposit_closed"
    assert closure.available_raw_amount == 0

    cash_before = vault.denomination_token.fetch_raw_balance_of(simple_vault.address)
    shares_before = vault.share_token.fetch_raw_balance_of(simple_vault.address)
    validation_request = manager.create_deposit_request_for_guard_validation(simple_vault.address, raw_amount)
    whitelist_hash = guard.functions.whitelistERC4626(vault.address, "Allow closed Yearn ERC-4626 deposit").transact({"from": deployer})
    assert_transaction_success_with_explanation(web3, whitelist_hash)
    evidence = validate_closed_deposit_request_with_guard(validation_request, closure, guard, asset_manager)

    assert evidence.preflight_result == "deposit_closed"
    assert evidence.closure_reason == closure.reason
    assert len(evidence.calls) == 1
    assert evidence.calls[0].target == vault.address
    assert evidence.calls[0].selector == bytes.fromhex("6e553f65")
    assert vault.denomination_token.fetch_raw_balance_of(simple_vault.address) == cash_before
    assert vault.share_token.fetch_raw_balance_of(simple_vault.address) == shares_before
