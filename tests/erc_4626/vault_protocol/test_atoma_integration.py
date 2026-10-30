"""Read-only Atoma RPC and credential-backed Hypersync integration checks."""

import os
from decimal import Decimal

import pytest
from eth_typing import HexAddress

from eth_defi.abi import get_topic_signature_from_event
from eth_defi.erc_4626.vault_protocol.atoma.deposit_redeem import AtomaFlowAnalysis
from eth_defi.erc_4626.vault_protocol.atoma.vault import ATOMA_IMPLEMENTATION, ATOMA_LEGACY_IMPLEMENTATION, ATOMA_VAULT_ADDRESSES, AtomaVault
from eth_defi.hypersync.utils import configure_hypersync_from_env
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.testing.anvil_fork_pool import AnvilForkPool
from eth_defi.testing.fork_blocks import ARBITRUM_MIDNIGHT_BLOCK
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus, VaultFlowUnavailable
from eth_defi.vault.flow_events import fetch_vault_flow_logs_hypersync

JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
pytestmark = pytest.mark.skipif(JSON_RPC_ARBITRUM is None, reason="JSON_RPC_ARBITRUM required")


@pytest.mark.xdist_group("fork:arbitrum:midnight")
@pytest.mark.parametrize("address", sorted(ATOMA_VAULT_ADDRESSES))
def test_atoma_legacy_readers(anvil_fork_pool: AnvilForkPool, address: HexAddress) -> None:
    """The canonical historical fork binds the legacy ABI but refuses execution."""
    w = anvil_fork_pool.get_web3(JSON_RPC_ARBITRUM, ARBITRUM_MIDNIGHT_BLOCK)
    vault = AtomaVault(w, VaultSpec(42161, address))
    contract, implementation = vault.fetch_receipt_contract(ARBITRUM_MIDNIGHT_BLOCK)
    assert implementation == ATOMA_LEGACY_IMPLEMENTATION
    assert contract.functions.MIN_DEPOSIT().call() == 100_000_000
    assert vault.share_token.decimals == 12
    with pytest.raises(VaultFlowUnavailable, match="Unreviewed"):
        vault.get_deposit_manager().create_deposit_request(w.eth.accounts[1], amount=Decimal(100))


@pytest.mark.parametrize("address", sorted(ATOMA_VAULT_ADDRESSES))
def test_atoma_live_rpc(address: HexAddress) -> None:
    """Real authenticated archive RPC validates the pinned current implementation."""
    w = create_multi_provider_web3(JSON_RPC_ARBITRUM)
    vault = AtomaVault(w, VaultSpec(42161, address))
    block = 519_150_162
    assert w.eth.chain_id == 42161
    assert vault.fetch_implementation(block) == ATOMA_IMPLEMENTATION
    assert vault.fetch_minimum_deposit(block) == Decimal(100)
    assert vault.vault_contract.functions.epochDuration().call(block_identifier=block) == 259_200
    assert vault.vault_contract.functions.maxRedeem(vault.address).call(block_identifier=block) == 0


@pytest.mark.skipif(not os.environ.get("HYPERSYNC_API_KEY"), reason="HYPERSYNC_API_KEY required for real indexed requests")
def test_atoma_live_request_and_claim_discovery() -> None:
    """Bounded real indexed reads recover request evidence and a completed claim."""
    w = create_multi_provider_web3(JSON_RPC_ARBITRUM)
    vault = AtomaVault(w, VaultSpec(42161, "0xcc56410e1a136af0eceb7241c6ae394f4d8b581c"))
    manager = vault.get_deposit_manager()
    client = configure_hypersync_from_env(w).hypersync_client
    flows = list(manager.fetch_vault_flow_events(client, 486_733_515, 486_733_515))
    assert len(flows) == 1
    flow = flows[0]
    assert flow.status is AsyncVaultRequestStatus.pending
    filtered = list(manager.fetch_vault_flow_events(client, 486_733_515, 486_733_515, owner=flow.owner, settlement_epoch=flow.request_id))
    assert filtered == flows
    claims = fetch_vault_flow_logs_hypersync(hypersync_client=client, vault_address=vault.address, topic0_list=[get_topic_signature_from_event(vault.vault_contract.events.WithdrawalClaimed)], start_block=492_170_597, end_block=492_170_597)
    assert len(claims) == 1
    claim = claims[0]
    ticket = manager.reconstruct_redemption_ticket(flow.ticket_data)
    assert not ticket.reconciliation_required
    assert claim.topics[1][-40:].lower() == ticket.owner[2:].lower()
    assert int(claim.topics[2], 16) == ticket.settlement_epoch
    result = manager.analyse_redemption(claim.transaction_hash, ticket)
    assert isinstance(result, AtomaFlowAnalysis)
    assert result.denomination_amount == Decimal("507.202587")
    assert result.withdrawal_fee == Decimal("2.548756")
    receipt = w.eth.get_transaction_receipt(claim.transaction_hash)
    operator = vault.vault_contract.functions.operator().call(block_identifier=492_170_597)
    transfers = manager._receipt_events(vault.denomination_token.contract.events.Transfer, receipt)
    assert any(t["args"]["from"].lower() == vault.address.lower() and t["args"]["to"].lower() == operator.lower() and t["args"]["value"] == 2_548_756 for t in transfers)
    assert manager.fetch_completed_redemption_tx_hash(ticket) == result.tx_hash
