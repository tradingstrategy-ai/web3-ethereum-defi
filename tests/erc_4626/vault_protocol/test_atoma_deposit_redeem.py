"""Offline Atoma persistence and receipt tests, using synthetic evidence.

These tests exercise bookkeeping and failure branches. Real contract behaviour
and Guard enforcement are covered separately in tests/guard/test_guard_atoma.py.
"""

import datetime
import json
import re
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import eth_abi
import pytest
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import ContractLogicError

from eth_defi.abi import get_deployed_contract
from eth_defi.erc_4626.vault_protocol.atoma.deposit_redeem import INSUFFICIENT_IDLE, AtomaDepositManager, AtomaFlowAnalysis, AtomaRedemptionRequest, AtomaRedemptionTicket, AtomaRequestPart
from eth_defi.erc_4626.vault_protocol.atoma.vault import ATOMA_IMPLEMENTATION, ATOMA_LEGACY_IMPLEMENTATION, ATOMA_VAULT_ADDRESS, AtomaVault
from eth_defi.token import TokenDetails
from eth_defi.vault.base import VaultSpec, WithdrawalDelayType
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus, DepositRedeemEventFailure, VaultFlowUnavailable
from eth_defi.vault.flow_events import IndexedVaultFlowLog

OWNER = Web3.to_checksum_address("0x" + "11" * 20)
OTHER = Web3.to_checksum_address("0x" + "22" * 20)
USDC = Web3.to_checksum_address("0xaf88d065e77c8cc2239327c5edb3a432268e5831")
TX = HexBytes("0x" + "aa" * 32)
BLOCK = HexBytes("0x" + "bb" * 32)


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> AtomaDepositManager:
    """Bind real ABIs to an offline provider with explicit token metadata."""
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.atoma.deposit_redeem.get_block_timestamp", lambda *args: datetime.datetime(2026, 10, 28))
    w = Web3()
    monkeypatch.setattr(w.eth, "_chain_id", lambda: 42161)
    monkeypatch.setattr(w.eth, "get_block_number", lambda: 10)
    monkeypatch.setattr(w.eth, "get_block", lambda b: {"hash": BLOCK, "timestamp": 1_000})
    vault = AtomaVault(w, VaultSpec(42161, ATOMA_VAULT_ADDRESS))
    vault.__dict__["share_token"] = TokenDetails(get_deployed_contract(w, "ERC20MockDecimals.json", vault.address), decimals=12)
    vault.__dict__["denomination_token"] = TokenDetails(get_deployed_contract(w, "ERC20MockDecimals.json", USDC), decimals=6)
    monkeypatch.setattr(vault, "fetch_implementation", lambda b: ATOMA_IMPLEMENTATION)
    return vault.get_deposit_manager()


@pytest.fixture
def ticket(manager: AtomaDepositManager) -> AtomaRedemptionTicket:
    """Represent one successful request in epoch four."""
    return AtomaRedemptionTicket(vault_address=manager.vault.address, owner=OWNER, to=OWNER, raw_shares=10**12, tx_hash=TX, chain_id=42161, request_epoch=4, settlement_epoch=5, implementation=ATOMA_IMPLEMENTATION, preflight_implementation=ATOMA_IMPLEMENTATION, parts=(AtomaRequestPart(TX, 9, BLOCK, 0, 10**12),))


def event_log(address: str, signature: str, indexed: list[bytes], types: list[str], values: list, index: int = 0) -> dict:
    """Encode source-defined events without relying on the adapter decoder.

    :param address: Actual emitter.
    :param signature: Solidity signature.
    :param indexed: ABI-encoded indexed arguments.
    :param types: Non-indexed ABI types.
    :param values: Non-indexed values.
    :param index: Receipt log index.
    :return: Web3-compatible log.
    """
    return {"address": address, "topics": [Web3.keccak(text=signature), *map(HexBytes, indexed)], "data": HexBytes(eth_abi.encode(types, values)), "logIndex": index, "transactionIndex": 0, "transactionHash": TX, "blockHash": BLOCK, "blockNumber": 9}


def request_receipt(manager: AtomaDepositManager, shares: int = 10**12, epoch: int = 4) -> dict:
    """Create one synthetic canonical request receipt.

    :param manager: Bound vault.
    :param shares: Requested shares.
    :param epoch: Actual mined request epoch.
    :return: Successful receipt.
    """
    log = event_log(manager.vault.address, "WithdrawalRequested(address,uint256,uint256,uint256)", [eth_abi.encode(["address"], [OWNER])], ["uint256"] * 3, [shares, epoch, epoch + 1])
    return {"status": 1, "transactionHash": TX, "blockNumber": 9, "blockHash": BLOCK, "logs": [log]}


@pytest.mark.parametrize("raw", [1, 2**200])
def test_offline_ticket_roundtrip(manager: AtomaDepositManager, ticket: AtomaRedemptionTicket, raw: int) -> None:
    """Large integer and hash evidence survive JSON without network access."""
    ticket = replace(ticket, raw_shares=raw, parts=(replace(ticket.parts[0], raw_shares=raw),))
    data = manager.serialize_redemption_ticket(ticket)
    assert isinstance(data["vault_raw_amount"], str)
    assert manager.reconstruct_redemption_ticket(json.loads(json.dumps(data))) == ticket


@pytest.mark.parametrize("field,value", [("atoma_chain_id", 1), ("vault_address", OTHER), ("vault_to", OTHER), ("atoma_schema", 2), ("vault_raw_amount", "1"), ("atoma_settlement_epoch", "7"), ("atoma_reconciliation_required", "false")])
def test_corrupt_ticket(manager: AtomaDepositManager, ticket: AtomaRedemptionTicket, field: str, value: object) -> None:
    """Reject mismatched custody, schema and aggregate amounts offline."""
    data = manager.serialize_redemption_ticket(ticket)
    data[field] = value
    with pytest.raises(ValueError):
        manager.reconstruct_redemption_ticket(data)


@pytest.mark.parametrize("amount,raw", [(None, None), (Decimal(1), 1), (None, 0), (None, -1), (None, True), (Decimal("0.0000001"), None)])
def test_amount_validation(manager: AtomaDepositManager, amount: Decimal | None, raw: int | None) -> None:
    """No zero, negative, ambiguous or truncated deposit amount is accepted."""
    with pytest.raises(ValueError):
        manager.create_deposit_request(OWNER, amount=amount, raw_amount=raw)


def test_receiver_validation(manager: AtomaDepositManager) -> None:
    """Request and deposit custody cannot redirect proceeds."""
    with pytest.raises(ValueError, match="receiver"):
        manager.create_deposit_request(OWNER, to=OTHER, raw_amount=100_000_000)
    with pytest.raises(ValueError, match="receiver"):
        manager.create_redemption_request(OWNER, to=OTHER, raw_shares=1)


def test_reconstruct_escrowed_redemption(manager: AtomaDepositManager, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reconstruct a mined request without reopening admission for new requests.

    1. Stub the RPC boundary with all shares escrowed and an existing epoch request.
    2. Refuse a new request but reconstruct its original call with balance checks disabled.
    """
    # 1. Stub the RPC boundary with all shares escrowed and an existing epoch request.
    contract = Mock()
    contract.functions.getCurrentEpoch.return_value.call.return_value = 4
    contract.functions.userEpochShares.return_value.call.return_value = 10**12
    monkeypatch.setattr(manager.vault, "vault_contract", contract)
    monkeypatch.setattr(manager, "fetch_unlocked_shares", lambda owner, block: 0)
    # 2. Refuse a new request but reconstruct its original call with balance checks disabled.
    with pytest.raises(VaultFlowUnavailable, match="insufficient_unlocked_shares"):
        manager.create_redemption_request(OWNER, raw_shares=10**12)
    request = manager.create_redemption_request(OWNER, raw_shares=10**12, check_enough_token=False)
    assert request.raw_shares == 10**12
    assert request.implementation == ATOMA_IMPLEMENTATION
    assert len(request.funcs) == 1
    contract.functions.userEpochShares.assert_not_called()


def test_claim_quote_requires_attribution(manager: AtomaDepositManager, ticket: AtomaRedemptionTicket, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse aggregate and consumed claims rather than quoting another trade's value.

    1. Stub RPC values for a settled single-ticket entitlement and quote it.
    2. Refuse a larger owner aggregate and a consumed request, retaining zero-loss distinction.
    """
    # 1. Stub RPC values for a settled single-ticket entitlement and quote it.
    contract = Mock()
    f = contract.functions
    f.getEpoch.return_value.call.return_value = (0, 100, True)
    f.userEpochShares.return_value.call.return_value = ticket.raw_shares
    f.NAV_PRECISION.return_value.call.return_value = ticket.raw_shares
    f.epochIndexAtSettle.return_value.call.return_value = 1
    f.shortfallIndexWad.return_value.call.return_value = 1
    f.WITHDRAWAL_FEE_BPS.return_value.call.return_value = 0
    monkeypatch.setattr(manager.vault, "vault_contract", contract)
    # Canonical receipts have separate tests; this one isolates quote attribution.
    monkeypatch.setattr(manager, "_validate_ticket", lambda *args, **kwargs: None)
    assert manager.fetch_claim_amounts(ticket, block_identifier=10) == (100, 0, 100)
    # 2. Refuse a larger owner aggregate and a consumed request, retaining zero-loss distinction.
    f.userEpochShares.return_value.call.return_value = ticket.raw_shares + 1
    with pytest.raises(VaultFlowUnavailable, match="request_reconciliation_required"):
        manager.fetch_claim_amounts(ticket, block_identifier=10)
    f.userEpochShares.return_value.call.return_value = 0
    with pytest.raises(VaultFlowUnavailable, match="claim_already_completed"):
        manager.fetch_claim_amounts(ticket, block_identifier=10)


def test_unknown_implementation(manager: AtomaDepositManager, monkeypatch: pytest.MonkeyPatch) -> None:
    """An upgrade cannot silently fall back to ERC-4626 execution."""
    monkeypatch.setattr(manager.vault, "fetch_implementation", lambda b: OTHER)
    assert not manager.vault.supports_generic_deposit_manager()
    with pytest.raises(VaultFlowUnavailable, match="Unreviewed"):
        manager.create_deposit_request(OWNER, raw_amount=100_000_000)
    with pytest.raises(VaultFlowUnavailable, match="Unreviewed"):
        manager.create_redemption_request(OWNER, raw_shares=1)


def test_mined_epoch_and_upgrade_evidence(manager: AtomaDepositManager, monkeypatch: pytest.MonkeyPatch) -> None:
    """An inclusion boundary retains actual epochs, including ambiguous upgrades."""
    receipt = request_receipt(manager, epoch=8)
    monkeypatch.setattr(manager.web3.eth, "get_transaction_receipt", lambda tx: receipt)
    request = AtomaRedemptionRequest(vault=manager.vault, owner=OWNER, to=OWNER, raw_shares=10**12, shares=Decimal(1), funcs=[], implementation=ATOMA_LEGACY_IMPLEMENTATION)
    recovered = request.parse_redeem_transaction([TX])
    assert recovered.request_epoch == 8
    assert recovered.settlement_epoch == 9
    assert recovered.reconciliation_required
    assert recovered.parts[0].tx_hash == TX
    with pytest.raises(VaultFlowUnavailable, match="implementation_reconciliation"):
        manager.acknowledge_redemption_reconciliation(recovered, {p.identity for p in recovered.parts})


def test_canonical_and_attribution_checks(manager: AtomaDepositManager, ticket: AtomaRedemptionTicket, monkeypatch: pytest.MonkeyPatch) -> None:
    """Canonical blocks alone cannot validate fabricated request contributions."""
    monkeypatch.setattr(manager.web3.eth, "get_transaction_receipt", lambda tx: request_receipt(manager))
    manager._validate_ticket(ticket, canonical=True)
    with pytest.raises(VaultFlowUnavailable, match="request_evidence_mismatch"):
        manager._validate_ticket(replace(ticket, owner=OTHER, to=OTHER), canonical=True)
    monkeypatch.setattr(manager.web3.eth, "get_block", lambda b: {"hash": HexBytes("0x" + "cc" * 32)})
    with pytest.raises(VaultFlowUnavailable, match="request_reorganisation"):
        manager._validate_ticket(ticket, canonical=True)


@pytest.mark.parametrize("net,fee,implementation", [(99_500_000, 500_000, ATOMA_IMPLEMENTATION), (0, 0, ATOMA_IMPLEMENTATION), (1, 0, ATOMA_IMPLEMENTATION), (99_500_000, 500_000, ATOMA_LEGACY_IMPLEMENTATION)])
def test_actual_net_receipt(manager: AtomaDepositManager, ticket: AtomaRedemptionTicket, monkeypatch: pytest.MonkeyPatch, net: int, fee: int, implementation: str) -> None:
    """Current and legacy claim events report net payout, including total losses."""
    claim_tx = HexBytes("0x" + "dd" * 32)
    logs = [event_log(manager.vault.address, "WithdrawalClaimed(address,uint256,uint256,uint256,uint256)", [eth_abi.encode(["address"], [OWNER]), eth_abi.encode(["uint256"], [5])], ["uint256"] * 3, [ticket.raw_shares, net, fee]), event_log(USDC, "Transfer(address,address,uint256)", [eth_abi.encode(["address"], [manager.vault.address]), eth_abi.encode(["address"], [OWNER])], ["uint256"], [net], 1)]
    receipt = {"status": 1, "transactionHash": claim_tx, "blockNumber": 10, "blockHash": BLOCK, "logs": logs}
    monkeypatch.setattr(manager.web3.eth, "get_transaction_receipt", lambda tx: request_receipt(manager) if HexBytes(tx) == TX else receipt)
    monkeypatch.setattr(manager.vault, "fetch_implementation", lambda b: implementation)
    result = manager.analyse_redemption(claim_tx, ticket)
    assert isinstance(result, AtomaFlowAnalysis)
    assert result.denomination_amount == Decimal(net) / 10**6
    assert result.withdrawal_fee == Decimal(fee) / 10**6
    assert result.gross_assets == Decimal(net + fee) / 10**6
    assert result.is_success()
    # The same selector from another emitter cannot establish a payout.
    logs[0]["address"] = OTHER
    assert isinstance(manager.analyse_redemption(claim_tx, ticket), DepositRedeemEventFailure)


@pytest.mark.parametrize("error,expected", [(ContractLogicError("liquidity", data=HexBytes(INSUFFICIENT_IDLE).to_0x_hex()), False), (ContractLogicError("unknown", data="0x12345678"), None), (ConnectionError("transport"), None)])
def test_claim_simulation_errors(manager: AtomaDepositManager, ticket: AtomaRedemptionTicket, monkeypatch: pytest.MonkeyPatch, error: Exception, expected: bool | None) -> None:
    """Only the exact known liquidity error becomes temporary unavailability."""
    monkeypatch.setattr(manager, "get_redemption_request_status", lambda t: AsyncVaultRequestStatus.claimable)
    call = Mock()
    call.call.side_effect = error
    monkeypatch.setattr(manager, "_claim_call", lambda t: call)
    if expected is None:
        with pytest.raises(type(error)):
            manager.can_finish_redeem(ticket)
    else:
        assert manager.can_finish_redeem(ticket) is expected


def test_reconciliation_coverage(manager: AtomaDepositManager, ticket: AtomaRedemptionTicket, monkeypatch: pytest.MonkeyPatch) -> None:
    """Indexer lag and incomplete external contributions defer aggregate claims."""
    monkeypatch.setattr(manager, "_validate_ticket", lambda *a, **k: None)
    client = SimpleNamespace(get_height=AsyncMock(return_value=9))
    with pytest.raises(VaultFlowUnavailable, match="indexer_behind"):
        manager.reconcile_redemption_ticket(ticket, client)
    client.get_height.return_value = 11
    second = replace(ticket, tx_hash=HexBytes("0x" + "cc" * 32), parts=(replace(ticket.parts[0], tx_hash=HexBytes("0x" + "cc" * 32)),))
    flows = [SimpleNamespace(owner=OWNER, request_id=5, ticket_data=manager.serialize_redemption_ticket(t)) for t in (ticket, second)]
    monkeypatch.setattr(manager, "fetch_vault_flow_events", lambda *args, **kwargs: iter(flows))
    contract = Mock()
    contract.functions.userEpochShares.return_value.call.return_value = 3 * ticket.raw_shares
    manager.vault.__dict__["vault_contract"] = contract
    with pytest.raises(VaultFlowUnavailable, match="request_history_mismatch"):
        manager.reconcile_redemption_ticket(ticket, client)
    contract.functions.userEpochShares.return_value.call.return_value = 2 * ticket.raw_shares
    aggregate = manager.reconcile_redemption_ticket(ticket, client)
    assert aggregate.raw_shares == 2 * ticket.raw_shares
    assert aggregate.reconciliation_required
    with pytest.raises(ValueError, match="All Atoma"):
        manager.acknowledge_redemption_reconciliation(aggregate, {ticket.parts[0].identity})
    monkeypatch.setattr(manager.vault, "fetch_receipt_contract", lambda b: (contract, ATOMA_IMPLEMENTATION))
    acknowledged = manager.acknowledge_redemption_reconciliation(aggregate, {p.identity for p in aggregate.parts})
    assert not acknowledged.reconciliation_required
    assert len(acknowledged.parts) == 2


@pytest.mark.parametrize("minimum,capacity,balance,raw,reason", [(100_000_000, 10**9, 10**9, 99_999_999, "below_minimum"), (100_000_000, 0, 10**9, 100_000_000, "deposit_capacity"), (100_000_000, 10**9, 0, 100_000_000, "insufficient_assets")])
def test_deposit_preflight(manager: AtomaDepositManager, minimum: int, capacity: int, balance: int, raw: int, reason: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Minimum, paused/capped capacity and balance checks precede calldata."""
    contract = Mock()
    contract.functions.MIN_DEPOSIT.return_value.call.return_value = minimum
    contract.functions.maxDeposit.return_value.call.return_value = capacity
    manager.vault.__dict__["vault_contract"] = contract
    monkeypatch.setattr(manager.vault.denomination_token, "fetch_raw_balance_of", lambda *a, **kw: balance)
    with pytest.raises(VaultFlowUnavailable, match=reason):
        manager.create_deposit_request(OWNER, raw_amount=raw, check_max_deposit=False)
    contract.functions.deposit.assert_not_called()


@pytest.mark.parametrize("mutation", ["owner", "epoch", "shares", "transfer", "failed", "upgrade"])
def test_claim_receipt_mismatch(manager: AtomaDepositManager, ticket: AtomaRedemptionTicket, mutation: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mined but unmatched cash movements remain structured accounting failures."""
    tx = HexBytes("0x" + "dd" * 32)
    owner = OTHER if mutation == "owner" else OWNER
    epoch = 6 if mutation == "epoch" else 5
    shares = ticket.raw_shares + (1 if mutation == "shares" else 0)
    logs = [event_log(manager.vault.address, "WithdrawalClaimed(address,uint256,uint256,uint256,uint256)", [eth_abi.encode(["address"], [owner]), eth_abi.encode(["uint256"], [epoch])], ["uint256"] * 3, [shares, 99_500_000, 500_000]), event_log(OTHER if mutation == "transfer" else USDC, "Transfer(address,address,uint256)", [eth_abi.encode(["address"], [manager.vault.address]), eth_abi.encode(["address"], [owner])], ["uint256"], [99_500_000], 1)]
    receipt = {"status": 0 if mutation == "failed" else 1, "transactionHash": tx, "blockNumber": 10, "blockHash": BLOCK, "logs": logs}
    monkeypatch.setattr(manager.web3.eth, "get_transaction_receipt", lambda h: request_receipt(manager) if HexBytes(h) == TX else receipt)
    if mutation == "upgrade":
        monkeypatch.setattr(manager.vault, "fetch_implementation", lambda b: OTHER if b == 10 else ATOMA_IMPLEMENTATION)
    failure = manager.analyse_redemption(tx, ticket)
    assert isinstance(failure, DepositRedeemEventFailure)
    assert failure.tx_hash == tx
    assert failure.receipt_status == receipt["status"]


def test_wrong_provider_chain(manager: AtomaDepositManager, monkeypatch: pytest.MonkeyPatch) -> None:
    """A matching vault spec cannot authorise a transaction on another chain."""
    monkeypatch.setattr(manager.web3.eth, "_chain_id", lambda: 1)
    with pytest.raises(VaultFlowUnavailable, match="Unreviewed"):
        manager.create_deposit_request(OWNER, raw_amount=100_000_000)


def test_owner_wide_scan_failure(manager: AtomaDepositManager, monkeypatch: pytest.MonkeyPatch) -> None:
    """An incomplete owner scan cannot report that no request is outstanding."""
    contract = Mock()
    contract.functions.getCurrentEpoch.return_value.call.return_value = 4
    real = manager.vault.vault_contract.functions.userEpochShares
    contract.functions.userEpochShares = real
    manager.vault.__dict__["vault_contract"] = contract
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.atoma.deposit_redeem.read_multicall_chunked", lambda **kw: iter([SimpleNamespace(success=False, result=b"")]))
    with pytest.raises(VaultFlowUnavailable, match="epoch_scan_incomplete"):
        manager.is_redemption_in_progress(OWNER)


def test_guard_selector_collisions() -> None:
    """The two new forwarded selectors must not alias existing Guard branches."""
    source = (Path(__file__).resolve().parents[3] / "contracts/guard/src/GuardV0Base.sol").read_text()
    definitions = re.findall(r"(SEL_\w+)\s*=\s*(0x[0-9a-fA-F]{8})", source)
    for name, signature in [("SEL_ATOMA_REQUEST", "requestWithdrawal(uint256)"), ("SEL_ATOMA_CLAIM", "claimWithdrawal(uint256)")]:
        selector = Web3.keccak(text=signature)[:4].to_0x_hex()
        assert [n for n, value in definitions if value.lower() == selector] == [name]


def test_optional_hypersync_imports() -> None:
    """Core deployment and classification imports work without the optional extra."""
    result = subprocess.run([sys.executable, "-c", "import sys; sys.modules['hypersync'] = None; import eth_defi.erc_4626.vault_protocol.atoma.vault; import eth_defi.erc_4626.vault_protocol.lagoon.deployment; import eth_defi.erc_4626.deposit_probe"], capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr


def test_reconciliation_filters_before_receipts(manager: AtomaDepositManager, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unrelated historical requests cannot break or inflate recovery RPC work."""
    matching = IndexedVaultFlowLog(manager.vault.address, [Web3.keccak(text="WithdrawalRequested(address,uint256,uint256,uint256)").to_0x_hex(), HexBytes(eth_abi.encode(["address"], [OWNER])).to_0x_hex()], HexBytes(eth_abi.encode(["uint256"] * 3, [10**12, 4, 5])).to_0x_hex(), 9, None, TX.to_0x_hex(), 0)
    outsider = replace(matching, topics=[matching.topics[0], HexBytes(eth_abi.encode(["address"], [OTHER])).to_0x_hex()], data="0x")
    other_epoch = replace(matching, data=HexBytes(eth_abi.encode(["uint256"] * 3, [10**12, 1, 2])).to_0x_hex())
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.atoma.deposit_redeem.fetch_vault_flow_logs_hypersync", lambda **kw: [outsider, other_epoch, matching])
    fetch = Mock(return_value=request_receipt(manager))
    monkeypatch.setattr(manager.web3.eth, "get_transaction_receipt", fetch)
    flows = list(manager.fetch_vault_flow_events(object(), 0, 10, owner=OWNER, settlement_epoch=5))
    assert len(flows) == 1
    assert flows[0].raw_shares == 10**12
    fetch.assert_called_once_with(TX)


def test_withdrawal_metadata_has_no_promised_schedule(manager: AtomaDepositManager) -> None:
    """Unspecified metadata bounds defer to the actual ticket's scheduled end."""
    period = manager.vault.get_withdrawal_period()
    assert period.min_period is None
    assert period.max_period is None
    assert period.delay_type is WithdrawalDelayType.epoch


def test_completed_claim_lookup_defers_indexer_lag(manager: AtomaDepositManager, ticket: AtomaRedemptionTicket, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bounded indexed search cannot report absence beyond its covered head."""
    monkeypatch.setattr(manager, "_validate_ticket", lambda *a, **kw: None)
    client = SimpleNamespace(get_height=AsyncMock(return_value=10))
    # Replace only the optional configuration module; no real indexer is called.
    monkeypatch.setitem(sys.modules, "eth_defi.hypersync.utils", SimpleNamespace(configure_hypersync_from_env=lambda w: SimpleNamespace(hypersync_client=client)))
    fetch = Mock(return_value=[])
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.atoma.deposit_redeem.fetch_vault_flow_logs_hypersync", fetch)
    with pytest.raises(VaultFlowUnavailable, match="indexer_behind_chain"):
        manager.fetch_completed_redemption_tx_hash(ticket)
    assert fetch.call_args.kwargs["end_block"] == 9
    client.get_height.return_value = 11
    assert manager.fetch_completed_redemption_tx_hash(ticket) is None
    assert fetch.call_args.kwargs["end_block"] == 10


def test_claim_recovery_without_hypersync(manager: AtomaDepositManager, ticket: AtomaRedemptionTicket, monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling optional recovery without the extra produces a typed refusal."""
    monkeypatch.delitem(sys.modules, "eth_defi.hypersync.utils", raising=False)
    monkeypatch.setitem(sys.modules, "hypersync", None)
    with pytest.raises(VaultFlowUnavailable, match="hypersync_required"):
        manager.fetch_completed_redemption_tx_hash(ticket)
