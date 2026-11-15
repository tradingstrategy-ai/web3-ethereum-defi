"""Guarded public recovery for the two studied Morpho exit-route shortfalls.

Block 26402309 is an intentional lifecycle exception to the canonical midnight
fork: it is the state used to establish the arithmetic failure, position sizes
and public recovery in research PR25. Generic Guard policy refusals live in the
Solidity test and need no extra historical fork. Initial custody funding uses
Anvil storage manipulation; recovery and payouts use only existing vault assets.
"""

import json
import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction

from eth_defi.abi import get_deployed_contract
from eth_defi.deploy import GUARD_LIBRARIES, deploy_contract
from eth_defi.erc_4626.vault_protocol.morpho.recovery import MorphoRecoveryRequest
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.provider.anvil import fund_erc20_on_anvil, make_anvil_custom_rpc_request, mine
from eth_defi.simple_vault.transact import encode_simple_vault_transaction
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

RPC = os.environ.get("JSON_RPC_ETHEREUM")
BLOCK = 26_402_309
pytestmark = [pytest.mark.skipif(RPC is None, reason="JSON_RPC_ETHEREUM required"), pytest.mark.xdist_group("fork:ethereum:morpho-recovery")]


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Use the shared, fixed lifecycle fork and its persistent archive cache."""
    return anvil_fork_pool.get_web3(RPC, BLOCK)


@pytest.fixture(autouse=True)
def recovery_snapshot(anvil_fork_pool: AnvilForkPool) -> Iterator[None]:
    """Restore the shared fork after each independently funded custody scenario."""
    launch = anvil_fork_pool.get_launch(RPC, BLOCK)
    connection = anvil_fork_pool.get_web3(RPC, BLOCK)
    snapshots = evm_snapshot_revert(launch)
    next(snapshots)
    try:
        # Fix mining times as well as fork state: integer interest is time-sensitive.
        make_anvil_custom_rpc_request(connection, "anvil_setBlockTimestampInterval", [1])
        mine(connection, timestamp=connection.eth.get_block(BLOCK)["timestamp"] + 1)
        yield
    finally:
        make_anvil_custom_rpc_request(connection, "anvil_removeBlockTimestampInterval", [])
        next(snapshots, None)


def _send(web3: Web3, function: ContractFunction, sender: str):
    """Check the actual receipt, including calls sent through guarded custody."""
    tx = function.transact({"from": sender, "gas": 8_000_000})
    assert_transaction_success_with_explanation(web3, tx)
    return tx


def _guarded(web3: Web3, custody: Contract, function: ContractFunction, manager: str):
    """Execute ABI-encoded adapter calls as a distinct non-governance manager."""
    return _send(web3, custody.functions.performCall(*encode_simple_vault_transaction(function)), manager)


@pytest.mark.parametrize(
    "address, expected_net, expected_fee, expected_shares",
    [
        ("0x36cfe1568461e499391ef0a555300f1ae2da2439", "1001.150276", "0.100126", 935384177444906134165),
        ("0xc92a37fd0250f4eecf092960a2f70a1334217528", "1001.325527", "0.100143", 983596236748528249994),
    ],
    ids=["api3", "purinta"],
)
def test_guarded_morpho_recovery_restart(web3: Web3, address: str, expected_net: str, expected_fee: str, expected_shares: int):
    """Recover real existing assets and resume with exact remaining shares.

    1. Deposit 1,001 USDC through a Guard with a distinct asset manager.
    2. After 24 hours prove ordinary redemption fails and a zero fee cap refuses.
    3. Enable 1 bp recovery, persist its preflight and parse the actual fee burn.
    4. Recreate the manager/ticket, redeem the remainder and reconcile all shares
       and net proceeds. A second ordinary exit has no shares left to sell.
    """
    # 1. Fund custody for its deposit, never the vault or the recovery payout.
    governance, operator = web3.eth.accounts[:2]
    vault = MorphoV2Vault(web3, VaultSpec(1, address))
    library = deploy_contract(web3, "guard/MorphoLib.json", governance)
    custody = deploy_contract(web3, "guard/SimpleVaultV0.json", governance, operator, libraries={**GUARD_LIBRARIES, "MorphoLib": library.address})
    _send(web3, custody.functions.initialiseOwnership(governance), governance)
    guard = get_deployed_contract(web3, "guard/GuardV0.json", custody.functions.guard().call())
    _send(web3, guard.functions.whitelistERC4626(vault.address, "ordinary"), governance)
    _send(web3, guard.functions.whitelistMorphoV2Recovery(vault.address, 1, "reviewed"), governance)
    raw_amount = vault.denomination_token.convert_to_raw(Decimal(1001))
    fund_erc20_on_anvil(web3, vault.denomination_token.address, custody.address, raw_amount)
    _guarded(web3, custody, vault.denomination_token.contract.functions.approve(vault.address, raw_amount), operator)
    manager = vault.get_deposit_manager()
    deposit = manager.create_deposit_request(custody.address, raw_amount=raw_amount)
    for function in deposit.funcs:
        _guarded(web3, custody, function, operator)
    original = vault.share_token.fetch_raw_balance_of(custody.address)
    assert original == expected_shares
    vault.redemption_recovery_max_fee_bps = 1
    assert not manager.create_redemption_request(custody.address, raw_shares=original).requires_settlement
    vault.redemption_recovery_max_fee_bps = None

    # 2. Direct exits remain the default, with the original typed panic.
    mine(web3, increase_timestamp=86400)
    with pytest.raises(VaultFlowUnavailable, match="arithmetic"):
        manager.create_redemption_request(custody.address, raw_shares=original)
    vault.redemption_recovery_max_fee_bps = 0
    with pytest.raises(VaultFlowUnavailable, match="penalty exceeds"):
        manager.create_redemption_request(custody.address, raw_shares=original)

    # 3. The first mined receipt, not a balance heuristic, establishes the fee.
    vault.redemption_recovery_max_fee_bps = 1
    request = manager.create_redemption_request(custody.address, raw_shares=original)
    assert isinstance(request, MorphoRecoveryRequest) and request.requires_settlement
    saved_preflight = json.loads(json.dumps(request.get_preflight_data()))
    recovery_tx = _guarded(web3, custody, request.funcs[0], operator)
    parser = manager.create_redemption_request_for_analysis(custody.address, original)
    parser.restore_preflight_data(saved_preflight)
    ticket = parser.parse_redeem_transaction([recovery_tx])
    assert 0 < ticket.penalty_shares < original
    assert ticket.penalty_assets > 0
    assert vault.share_token.fetch_raw_balance_of(custody.address) == ticket.remaining_shares
    persisted = json.loads(json.dumps(manager.serialize_redemption_ticket(ticket)))

    # 4. A fresh adapter has recovery disabled, but still resumes its paid ticket.
    resumed = MorphoV2Vault(web3, VaultSpec(1, address)).get_deposit_manager()
    ticket = resumed.reconstruct_redemption_ticket(persisted)
    assert resumed.fetch_pending_redemption_value(ticket) < vault.denomination_token.convert_to_decimals(vault.vault_contract.functions.previewRedeem(original).call())
    before = vault.denomination_token.fetch_raw_balance_of(custody.address)
    final_tx = _guarded(web3, custody, resumed.finish_redemption(ticket), operator)
    analysis = resumed.analyse_redemption(final_tx, ticket)
    assert analysis.share_count == vault.share_token.convert_to_decimals(original)
    assert analysis.withdrawal_fee == vault.denomination_token.convert_to_decimals(ticket.penalty_assets)
    assert analysis.denomination_amount == vault.denomination_token.convert_to_decimals(vault.denomination_token.fetch_raw_balance_of(custody.address) - before)
    assert vault.share_token.fetch_raw_balance_of(custody.address) == 0
    assert analysis.denomination_amount == Decimal(expected_net)
    assert analysis.withdrawal_fee == Decimal(expected_fee)
    with pytest.raises(VaultFlowUnavailable, match="does not have enough shares"):
        resumed.finish_redemption(ticket)
