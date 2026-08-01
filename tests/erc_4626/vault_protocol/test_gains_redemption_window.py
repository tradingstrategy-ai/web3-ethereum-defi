"""Exercise the Gains closed-window redemption simulation."""

import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import GainsDepositManager
from eth_defi.erc_4626.vault_protocol.gains.vault import GainsVault
from eth_defi.provider.anvil import AnvilLaunch
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.token import USDC_WHALE
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")
GTRADE_BASE_USDC_VAULT = "0xad20523a7dc37babc1cc74897e4977232b3d02e5"
# This lifecycle needs a naturally closed window, which the canonical Base
# midnight block does not provide. Keep the exceptional block warm-cached.
GTRADE_BASE_CLOSED_WINDOW_BLOCK = 49_395_951


@pytest.fixture(scope="module")
def gains_base_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Share the exceptional production-rerun fork where the request window is closed."""
    return anvil_fork_pool.get_launch(
        JSON_RPC_BASE,
        GTRADE_BASE_CLOSED_WINDOW_BLOCK,
        unlocked_addresses=[USDC_WHALE[8453]],
    )


@pytest.fixture(scope="module")
def gains_base_web3(gains_base_fork: AnvilLaunch) -> Web3:
    """Connect to the closed-window Gains Base fork."""
    return create_multi_provider_web3(gains_base_fork.json_rpc_url)


@pytest.fixture
def gains_base_snapshot(gains_base_fork: AnvilLaunch) -> Iterator[None]:
    """Restore the shared fork after each window simulation."""
    yield from evm_snapshot_revert(gains_base_fork)


@pytest.mark.skipif(JSON_RPC_BASE is None, reason="JSON_RPC_BASE needed to run this test")
@pytest.mark.xdist_group("fork:base:gains-closed-window")
def test_gains_closed_window_advances_then_completes_redemption(
    gains_base_web3: Web3,
    gains_base_snapshot: None,
) -> None:
    """Advance the closed Gains request window then complete its real lifecycle.

    1. Deposit USDC into the production-rerun fork and retain the minted shares.
    2. Prove the natural withdrawal request reports the typed EndOfEpoch refusal.
    3. Advance only the permissionless epoch transition on Anvil.
    4. Submit, settle and claim the unchanged redemption through real calls.
    """
    # 1. Deposit USDC into the production-rerun fork and retain the minted shares.
    assert gains_base_snapshot is None
    vault = create_vault_instance_autodetect(gains_base_web3, GTRADE_BASE_USDC_VAULT)
    assert isinstance(vault, GainsVault)
    manager = vault.get_deposit_manager()
    assert isinstance(manager, GainsDepositManager)
    owner = gains_base_web3.eth.accounts[0]
    amount = Decimal(1001)
    funding_hash = vault.denomination_token.transfer(owner, amount).transact({"from": USDC_WHALE[8453]})
    assert_transaction_success_with_explanation(gains_base_web3, funding_hash)
    approval_hash = vault.denomination_token.approve(vault.address, amount).transact({"from": owner})
    assert_transaction_success_with_explanation(gains_base_web3, approval_hash)
    manager.create_deposit_request(owner=owner, amount=amount).broadcast(from_=owner)
    raw_shares = vault.share_token.fetch_raw_balance_of(owner)
    assert raw_shares > 0

    # 2. Prove the natural withdrawal request reports the typed EndOfEpoch refusal.
    with pytest.raises(VaultFlowUnavailable) as exc_info:
        manager.create_redemption_request(owner=owner, raw_shares=raw_shares)
    error = exc_info.value
    assert error.decoded_error == "EndOfEpoch"
    assert error.preflight_result == "redemption_window_closed"

    # 3. Advance only the permissionless epoch transition on Anvil.
    intervention = manager.prepare_redemption_simulation(owner, raw_shares, error)
    assert intervention.kind == "time_advanced"
    assert intervention.target == vault.open_pnl_contract.address
    assert intervention.timestamp_after > intervention.timestamp_before
    assert intervention.transaction_hash is not None

    # 4. Submit, settle and claim the unchanged redemption through real calls.
    request = manager.create_redemption_request(owner=owner, raw_shares=raw_shares)
    ticket = request.broadcast(from_=owner)
    settlement = manager.force_settle(ticket)
    assert settlement.is_terminal_success()
    claim_hash = manager.finish_redemption(ticket).transact({"from": owner})
    analysis = manager.analyse_redemption(claim_hash, ticket)
    assert analysis.share_count == vault.share_token.convert_to_decimals(raw_shares)
    assert analysis.denomination_amount > 0
