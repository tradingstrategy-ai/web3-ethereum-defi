"""Exercise Ember operator liquidity preflight failures."""

import os
from collections.abc import Iterator
from decimal import Decimal

import pytest
from eth_typing import HexAddress
from web3 import Web3

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.ember.deposit_redeem import EmberDepositManager
from eth_defi.erc_4626.vault_protocol.ember.vault import EmberVault
from eth_defi.provider.anvil import AnvilLaunch, fund_erc20_on_anvil
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.evm_snapshot_fixture import evm_snapshot_revert
from eth_defi.trace import assert_transaction_success_with_explanation
from eth_defi.vault.deposit_redeem import UnsupportedVaultSimulation

JSON_RPC_ETHEREUM = os.environ.get("JSON_RPC_ETHEREUM")

EMBER_INSUFFICIENT_LIQUIDITY_VAULTS = (
    HexAddress("0x9be9294722f8aad37b11a9792be2c782182cafa2"),
    HexAddress("0x0b9342c15143e8f54a83f887c280a922f4c48771"),
)
EXPECTED_PENDING_WITHDRAWAL_INDEX = 2
EMBER_OPERATOR_LIQUIDITY_FORK_BLOCK = 25_598_869

pytestmark = [
    pytest.mark.skipif(JSON_RPC_ETHEREUM is None, reason="JSON_RPC_ETHEREUM needed to run these tests"),
    pytest.mark.xdist_group("fork:ethereum:midnight"),
]


@pytest.fixture(scope="module")
def ember_fork(anvil_fork_pool: AnvilForkPool) -> AnvilLaunch:
    """Share the production-rerun fork where two Ember queues lack liquidity."""
    return anvil_fork_pool.get_launch(JSON_RPC_ETHEREUM, EMBER_OPERATOR_LIQUIDITY_FORK_BLOCK)


@pytest.fixture(scope="module")
def web3(anvil_fork_pool: AnvilForkPool) -> Web3:
    """Connect to the shared Ember liquidity fork."""
    return anvil_fork_pool.get_web3(JSON_RPC_ETHEREUM, EMBER_OPERATOR_LIQUIDITY_FORK_BLOCK)


@pytest.fixture
def ember_snapshot(ember_fork: AnvilLaunch) -> Iterator[None]:
    """Restore the shared fork after each affected Ember vault."""
    yield from evm_snapshot_revert(ember_fork)


@pytest.mark.parametrize("vault_address", EMBER_INSUFFICIENT_LIQUIDITY_VAULTS)
def test_ember_operator_liquidity_refusal_is_typed(
    web3: Web3,
    vault_address: HexAddress,
    ember_snapshot: None,
) -> None:
    """Return a stable refusal before the operator broadcasts an unfunded queue.

    1. Deposit into an affected Ember vault at the production-rerun block.
    2. Queue all newly minted shares behind the existing FIFO requests.
    3. Assert settlement reports insufficient operator liquidity without mining
       a reverted processing transaction.
    """
    del ember_snapshot

    # 1. Deposit into an affected Ember vault at the production-rerun block.
    vault = create_vault_instance_autodetect(web3, vault_address)
    assert isinstance(vault, EmberVault)
    manager = vault.get_deposit_manager()
    assert isinstance(manager, EmberDepositManager)
    owner = web3.eth.accounts[0]
    amount = Decimal(1001)
    fund_erc20_on_anvil(web3, vault.denomination_token.address, owner, vault.denomination_token.convert_to_raw(amount))
    approval_hash = vault.denomination_token.approve(vault.address, amount).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, approval_hash)
    manager.create_deposit_request(owner=owner, amount=amount).broadcast(from_=owner)

    # 2. Queue all newly minted shares behind the existing FIFO requests.
    raw_shares = vault.share_token.fetch_raw_balance_of(owner)
    ticket = manager.create_redemption_request(owner=owner, raw_shares=raw_shares).broadcast(from_=owner)
    assert manager.fetch_pending_withdrawal_index(ticket) == EXPECTED_PENDING_WITHDRAWAL_INDEX

    # 3. Refuse before broadcasting the operator transaction.
    block_before = web3.eth.block_number
    with pytest.raises(UnsupportedVaultSimulation) as exc_info:
        manager.force_settle(ticket, ignore_liquidity=False)
    assert exc_info.value.unsupported_reason == "ember_settlement_insufficient_liquidity"
    assert exc_info.value.direction == "redeem"
    assert web3.eth.block_number == block_before
