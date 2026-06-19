"""Integration tests for async vault flow event discovery."""

import os

import hypersync
import pytest

from eth_defi.erc_4626.classification import create_vault_instance_autodetect
from eth_defi.erc_4626.vault_protocol.gains.deposit_redeem import OstiumDepositTicket, OstiumRedemptionTicket, OstiumV15DepositManager
from eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem import ERC7540DepositManager, ERC7540DepositTicket, ERC7540RedemptionTicket
from eth_defi.hypersync.server import get_hypersync_server
from eth_defi.provider.multi_provider import create_multi_provider_web3
from eth_defi.vault.flow_events import VaultFlowDirection

JSON_RPC_BASE = os.environ.get("JSON_RPC_BASE")
JSON_RPC_ARBITRUM = os.environ.get("JSON_RPC_ARBITRUM")
HYPERSYNC_API_KEY = os.environ.get("HYPERSYNC_API_KEY")

LAGOON_EXPECTED_FLOW_COUNT = 4
LAGOON_FIRST_REDEEM_REQUEST_ID = 2
LAGOON_FIRST_REDEEM_RAW_SHARES = 1_000_000_000_000_000_000
LAGOON_FIRST_DEPOSIT_REQUEST_ID = 21
LAGOON_FIRST_DEPOSIT_RAW_ASSETS = 30_000_000_000

OSTIUM_EXPECTED_FLOW_COUNT = 8
OSTIUM_FIRST_REDEEM_SETTLEMENT_ID = 78
OSTIUM_FIRST_REDEEM_RAW_SHARES = 8_530_000_000
OSTIUM_LAST_DEPOSIT_SETTLEMENT_ID = 77
OSTIUM_LAST_DEPOSIT_RAW_ASSETS = 300_000_000

pytestmark = pytest.mark.skipif(
    not HYPERSYNC_API_KEY,
    reason="HYPERSYNC_API_KEY needed",
)


def _create_hypersync_client(chain_id: int) -> hypersync.HypersyncClient:
    """Create a Hypersync client for a chain."""
    return hypersync.HypersyncClient(
        hypersync.ClientConfig(
            url=get_hypersync_server(chain_id),
            bearer_token=HYPERSYNC_API_KEY,
        )
    )


@pytest.mark.skipif(not JSON_RPC_BASE, reason="JSON_RPC_BASE needed")
def test_lagoon_fetch_vault_flow_events_from_hypersync() -> None:
    """Fetch Lagoon ERC-7540 deposit and redemption request events.

    1. Open the live Base 722 Capital Lagoon vault and its deposit manager.
    2. Fetch a small historical block range with known request events.
    3. Assert deposit and redemption flows decode to ticket-compatible data.
    """
    # 1. Open the live Base 722 Capital Lagoon vault and its deposit manager.
    web3 = create_multi_provider_web3(JSON_RPC_BASE)
    vault = create_vault_instance_autodetect(web3, "0xb09f761cb13baca8ec087ac476647361b6314f98")
    manager = vault.get_deposit_manager()
    assert isinstance(manager, ERC7540DepositManager)

    # 2. Fetch a small historical block range with known request events.
    flows = list(
        manager.fetch_vault_flow_events(
            hypersync_client=_create_hypersync_client(8453),
            start_block=30_623_046,
            end_block=30_786_520,
        )
    )

    # 3. Assert deposit and redemption flows decode to ticket-compatible data.
    assert len(flows) == LAGOON_EXPECTED_FLOW_COUNT
    assert [flow.direction for flow in flows] == [
        VaultFlowDirection.redeem,
        VaultFlowDirection.deposit,
        VaultFlowDirection.deposit,
        VaultFlowDirection.redeem,
    ]

    first_redeem = flows[0]
    assert first_redeem.request_id == LAGOON_FIRST_REDEEM_REQUEST_ID
    assert first_redeem.raw_shares == LAGOON_FIRST_REDEEM_RAW_SHARES
    assert first_redeem.raw_assets is None
    assert first_redeem.owner == "0x81AE3f0D805D1EBAb21D3B16175eE3Dfa5a18656"
    redeem_ticket = manager.reconstruct_redemption_ticket(first_redeem.ticket_data)
    assert isinstance(redeem_ticket, ERC7540RedemptionTicket)
    assert redeem_ticket.request_id == first_redeem.request_id
    assert redeem_ticket.raw_shares == first_redeem.raw_shares

    first_deposit = flows[1]
    assert first_deposit.request_id == LAGOON_FIRST_DEPOSIT_REQUEST_ID
    assert first_deposit.raw_assets == LAGOON_FIRST_DEPOSIT_RAW_ASSETS
    assert first_deposit.raw_shares is None
    assert first_deposit.owner == "0x81AE3f0D805D1EBAb21D3B16175eE3Dfa5a18656"
    deposit_ticket = manager.reconstruct_deposit_ticket(first_deposit.ticket_data)
    assert isinstance(deposit_ticket, ERC7540DepositTicket)
    assert deposit_ticket.request_id == first_deposit.request_id
    assert deposit_ticket.raw_amount == first_deposit.raw_assets


@pytest.mark.skipif(not JSON_RPC_ARBITRUM, reason="JSON_RPC_ARBITRUM needed")
def test_ostium_fetch_vault_flow_events_from_hypersync() -> None:
    """Fetch Ostium V1.5 deposit and redemption request events.

    1. Open the live Arbitrum Ostium V1.5 vault and its deposit manager.
    2. Fetch a small historical block range with known request events.
    3. Assert settlement ids and raw amounts decode to ticket-compatible data.
    """
    # 1. Open the live Arbitrum Ostium V1.5 vault and its deposit manager.
    web3 = create_multi_provider_web3(JSON_RPC_ARBITRUM)
    vault = create_vault_instance_autodetect(web3, "0x20d419a8e12c45f88fda7c5760bb6923cee27f98")
    manager = vault.get_deposit_manager()
    assert isinstance(manager, OstiumV15DepositManager)

    # 2. Fetch a small historical block range with known request events.
    flows = list(
        manager.fetch_vault_flow_events(
            hypersync_client=_create_hypersync_client(42161),
            start_block=457_241_109,
            end_block=457_408_441,
        )
    )

    # 3. Assert settlement ids and raw amounts decode to ticket-compatible data.
    assert len(flows) == OSTIUM_EXPECTED_FLOW_COUNT
    assert flows[0].direction == VaultFlowDirection.redeem
    assert flows[0].settlement_id == OSTIUM_FIRST_REDEEM_SETTLEMENT_ID
    assert flows[0].raw_shares == OSTIUM_FIRST_REDEEM_RAW_SHARES
    assert flows[0].owner == "0x2EF23e69262297a6e8892603271702Ec0C3F3Aba"
    redeem_ticket = manager.reconstruct_redemption_ticket(flows[0].ticket_data)
    assert isinstance(redeem_ticket, OstiumRedemptionTicket)
    assert redeem_ticket.settlement_id == flows[0].settlement_id
    assert redeem_ticket.raw_shares == flows[0].raw_shares

    assert flows[-1].direction == VaultFlowDirection.deposit
    assert flows[-1].settlement_id == OSTIUM_LAST_DEPOSIT_SETTLEMENT_ID
    assert flows[-1].raw_assets == OSTIUM_LAST_DEPOSIT_RAW_ASSETS
    assert flows[-1].owner == "0xa150B6bBAaD01bB77fAADCc9CAD6F5619b10366A"
    deposit_ticket = manager.reconstruct_deposit_ticket(flows[-1].ticket_data)
    assert isinstance(deposit_ticket, OstiumDepositTicket)
    assert deposit_ticket.settlement_id == flows[-1].settlement_id
    assert deposit_ticket.raw_amount == flows[-1].raw_assets
