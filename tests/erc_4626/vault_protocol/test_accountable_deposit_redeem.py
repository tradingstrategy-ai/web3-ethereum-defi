"""Unit tests for Accountable deposit and asynchronous redemption handling."""

import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import eth_abi
import pytest
from hexbytes import HexBytes

from eth_defi.erc_4626.vault_protocol.accountable import deposit_redeem as accountable_deposit_redeem
from eth_defi.erc_4626.vault_protocol.accountable.deposit_redeem import AccountableDepositManager, AccountableRedemptionTicket
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus

OWNER = "0x1111111111111111111111111111111111111111"
CONTROLLER = "0x3333333333333333333333333333333333333333"
VAULT = "0x2222222222222222222222222222222222222222"


def make_ticket(controller: str = OWNER) -> AccountableRedemptionTicket:
    """Create a persisted Accountable ticket without a live RPC connection.

    :return:
        Minimal valid Accountable ticket.
    """
    return AccountableRedemptionTicket(
        vault_address=VAULT,
        owner=OWNER,
        to=OWNER,
        raw_shares=490_000_000,
        tx_hash=HexBytes("0xc3eb98d689c6a91288231feee38048f757843ac52a8a99dc6672478323de620b"),
        request_id=159,
        controller=controller,
        block_number=84_665_686,
        block_timestamp=datetime.datetime(2026, 6, 30, 10, 50, 22),
    )


def test_accountable_redemption_ticket_is_restart_safe() -> None:
    """Serialise and reconstruct the request identity needed after a restart."""
    manager = AccountableDepositManager(Mock())
    ticket = make_ticket()

    assert manager.reconstruct_redemption_ticket(manager.serialize_redemption_ticket(ticket)) == ticket


@pytest.mark.parametrize(
    ("claimable", "pending", "expected"),
    [
        (490_000_000, 490_000_000, AsyncVaultRequestStatus.claimable),
        (0, 490_000_000, AsyncVaultRequestStatus.pending),
        (0, 0, AsyncVaultRequestStatus.none),
    ],
)
def test_accountable_redemption_status_prefers_claimable_aggregate(
    monkeypatch: pytest.MonkeyPatch,
    claimable: int,
    pending: int,
    expected: AsyncVaultRequestStatus,
) -> None:
    """Claimability wins when instant or batched settlement leaves both values visible."""
    manager = AccountableDepositManager(Mock())
    monkeypatch.setattr(manager, "_claimable_redeem_shares", lambda _owner: claimable)
    monkeypatch.setattr(manager, "_pending_redeem_shares", lambda _owner: pending)

    assert manager.get_redemption_request_status(make_ticket()) is expected


def test_accountable_finish_redemption_rejects_stale_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    """A claim call cannot be built after an external actor has consumed it."""
    manager = AccountableDepositManager(Mock())
    monkeypatch.setattr(manager, "_claimable_redeem_shares", lambda _owner: 0)

    with pytest.raises(ValueError, match="below"):
        manager.finish_redemption(make_ticket())


def test_accountable_finish_redemption_binds_exact_ticket_shares(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never claim another request's controller-level aggregate balance."""
    vault = Mock()
    manager = AccountableDepositManager(vault)
    ticket = make_ticket()
    monkeypatch.setattr(manager, "_claimable_redeem_shares", lambda controller: ticket.raw_shares)

    manager.finish_redemption(ticket)

    vault.vault_contract.functions.redeem.assert_called_once_with(ticket.raw_shares, ticket.to, OWNER)


def test_accountable_finish_redemption_never_claims_the_full_aggregate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claim only the ticket amount when self-controlled requests are aggregated."""
    vault = Mock()
    manager = AccountableDepositManager(vault)
    ticket = make_ticket()
    monkeypatch.setattr(manager, "_claimable_redeem_shares", lambda _controller: ticket.raw_shares * 2)

    manager.finish_redemption(ticket)

    vault.vault_contract.functions.redeem.assert_called_once_with(ticket.raw_shares, ticket.to, OWNER)


def test_accountable_finish_redemption_rejects_delegated_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    """Historical delegated-controller requests are not auto-claimed."""
    manager = AccountableDepositManager(Mock())
    ticket = make_ticket(controller=CONTROLLER)
    monkeypatch.setattr(manager, "_claimable_redeem_shares", lambda _controller: ticket.raw_shares)

    with pytest.raises(ValueError, match="self-controlled"):
        manager.finish_redemption(ticket)


def test_accountable_flow_discovery_preserves_delegated_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preserve the indexed ERC-7540 controller separately from share owner."""
    vault = Mock()
    vault.address = VAULT
    vault.web3.eth.chain_id = 143
    manager = AccountableDepositManager(vault)
    log = SimpleNamespace(
        topics=["0x00", "0x" + "00" * 12 + CONTROLLER[2:], "0x" + "00" * 12 + OWNER[2:], "0x" + "00" * 31 + "9f"],
        data="0x" + eth_abi.encode(["address", "uint256"], [OWNER, 490_000_000]).hex(),
        transaction_hash="0xc3eb98d689c6a91288231feee38048f757843ac52a8a99dc6672478323de620b",
        block_number=84_665_686,
        block_timestamp=datetime.datetime(2026, 6, 30, 10, 50, 22),
        log_index=3,
    )
    monkeypatch.setattr(accountable_deposit_redeem, "get_topic_signature_from_event", lambda _event: "0x00")
    monkeypatch.setattr(accountable_deposit_redeem, "fetch_vault_flow_logs_hypersync", lambda **_kwargs: [log])

    flow = next(manager.fetch_vault_flow_events(Mock(), log.block_number, log.block_number))

    assert flow.owner == OWNER
    assert flow.controller == CONTROLLER
    restored_ticket = manager.reconstruct_redemption_ticket(flow.ticket_data)
    assert restored_ticket.owner == OWNER
    assert restored_ticket.controller == CONTROLLER
