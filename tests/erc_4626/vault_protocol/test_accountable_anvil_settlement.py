"""Accountable fork settlement authority and request attribution boundaries."""

import datetime
from dataclasses import replace
from unittest.mock import MagicMock, Mock

import pytest
from hexbytes import HexBytes

from eth_defi.erc_4626.vault_protocol.accountable.deposit_redeem import AccountableDepositManager, AccountableRedemptionTicket
from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault
from eth_defi.vault.deposit_redeem import AsyncVaultRequestStatus, UnsupportedVaultSimulation

VAULT = "0x" + "11" * 20
OWNER = "0x" + "22" * 20
STRATEGY = "0x" + "33" * 20
TX = HexBytes("0x" + "44" * 32)


def test_accountable_fork_settlement_uses_configured_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fulfil exact pending shares, retain liquidity checks and release authority.

    1. Model a pending ticket and the vault's configured strategy.
    2. Settle through the real manager's Anvil-only driver.
    3. Check exact fulfilment, gas-only funding and cleanup on a reverted call.
    """
    # 1. Model a pending ticket and the vault's configured strategy.
    vault = MagicMock(spec=AccountableVault)
    vault.address = VAULT
    vault.web3 = MagicMock()
    vault.vault_contract.functions.strategy.return_value.call.return_value = STRATEGY
    vault.vault_contract.functions.pendingRedeemRequest.return_value.call.return_value = 100
    vault.web3.eth.get_code.return_value = b"\x60"
    vault.web3.eth.get_balance.return_value = 0
    fulfill = vault.vault_contract.functions.fulfillRedeemRequest.return_value
    fulfill.transact.return_value = TX
    manager = AccountableDepositManager(vault)
    ticket = AccountableRedemptionTicket(vault_address=VAULT, owner=OWNER, to=OWNER, raw_shares=100, tx_hash=TX, request_id=7, controller=OWNER, block_number=1, block_timestamp=datetime.datetime(2026, 11, 14))
    monkeypatch.setattr(manager, "get_redemption_request_status", Mock(side_effect=[AsyncVaultRequestStatus.pending, AsyncVaultRequestStatus.claimable]))
    pending = Mock(side_effect=[100, 0])
    claimable = Mock(return_value=100)
    monkeypatch.setattr(manager, "_pending_redeem_shares", pending)
    monkeypatch.setattr(manager, "_claimable_redeem_shares", claimable)
    rpc = Mock()
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.accountable.deposit_redeem.is_anvil", lambda _: True)
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.accountable.deposit_redeem.make_anvil_custom_rpc_request", rpc)
    receipt = Mock()
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.accountable.deposit_redeem.assert_transaction_success_with_explanation", receipt)

    # 2. Settle through the real manager's Anvil-only driver.
    result = manager.force_settle(ticket)
    assert result.is_terminal_success()
    assert result.synthetic_assets_injected_raw == 0
    assert not result.liquidity_constraints_ignored
    assert result.transaction_hashes == (TX,)

    # 3. Check exact fulfilment, gas-only funding and cleanup on a reverted call.
    vault.vault_contract.functions.fulfillRedeemRequest.assert_called_once_with(OWNER, 100)
    fulfill.transact.assert_called_once_with({"from": STRATEGY, "gas": 2_000_000})
    assert [c.args[1] for c in rpc.call_args_list] == ["anvil_impersonateAccount", "anvil_setBalance", "anvil_stopImpersonatingAccount"]
    receipt.assert_called_once_with(vault.web3, TX)
    monkeypatch.setattr(manager, "get_redemption_request_status", lambda _: AsyncVaultRequestStatus.pending)
    pending.side_effect = None
    pending.return_value = 100
    fulfill.transact.side_effect = ValueError("InsufficientLiquidity")
    rpc.reset_mock()
    with pytest.raises(ValueError, match="InsufficientLiquidity"):
        manager.force_settle(ticket)
    assert rpc.call_args.args[1] == "anvil_stopImpersonatingAccount"

    # Live providers and foreign/delegated requests must stop before any mutation.
    rpc.reset_mock()
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.accountable.deposit_redeem.is_anvil", lambda _: False)
    with pytest.raises(UnsupportedVaultSimulation):
        manager.force_settle(ticket)
    rpc.assert_not_called()
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.accountable.deposit_redeem.is_anvil", lambda _: True)
    for invalid in [replace(ticket, vault_address=STRATEGY), replace(ticket, controller=STRATEGY), replace(ticket, to=STRATEGY), replace(ticket, raw_shares=101)]:
        with pytest.raises(UnsupportedVaultSimulation):
            manager.force_settle(invalid)
    rpc.assert_not_called()

    # Already claimable requests must match exactly; partial settlements fail.
    monkeypatch.setattr(manager, "get_redemption_request_status", lambda _: AsyncVaultRequestStatus.claimable)
    pending.return_value = 0
    assert manager.force_settle(ticket).is_terminal_success()
    claimable.return_value = 99
    with pytest.raises(UnsupportedVaultSimulation, match="claimable aggregate"):
        manager.force_settle(ticket)
    monkeypatch.setattr(manager, "get_redemption_request_status", Mock(side_effect=[AsyncVaultRequestStatus.pending, AsyncVaultRequestStatus.claimable]))
    pending.side_effect = [100, 0]
    fulfill.transact.side_effect = None
    with pytest.raises(UnsupportedVaultSimulation, match="did not make the ticket claimable"):
        manager.force_settle(ticket)
    assert rpc.call_args.args[1] == "anvil_stopImpersonatingAccount"
