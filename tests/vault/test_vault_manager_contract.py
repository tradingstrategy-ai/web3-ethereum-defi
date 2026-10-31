"""Regression coverage for shared concrete managers and temporary recovery delays."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from hexbytes import HexBytes

from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.atoma.deposit_redeem import AtomaDepositManager
from eth_defi.erc_4626.vault_protocol.atoma.vault import AtomaVault
from eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem import LagoonDepositManager
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.erc_4626.vault_protocol.upshift.deposit_redeem import UpshiftMultiAssetDepositManager, UpshiftQueuedRedemptionTicket
from eth_defi.erc_4626.vault_protocol.upshift.vault import UpshiftVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable


@pytest.mark.parametrize("vault_type,manager_type", [(ERC4626Vault, ERC4626DepositManager), (LagoonVault, LagoonDepositManager), (AtomaVault, AtomaDepositManager)])
def test_shared_managers_remain_concrete(vault_type: type, manager_type: type) -> None:
    """Instantiate real managers so optional base hooks cannot make them abstract.

    1. Construct the actual adapter type without performing network reads.
    2. Instantiate its actual manager and verify the shared hook is optional.
    """
    # 1. Construct the actual adapter type without performing network reads.
    vault = object.__new__(vault_type)
    # 2. Instantiate its actual manager and verify the shared hook is optional.
    manager = manager_type(vault)
    assert manager.vault is vault
    assert not manager_type.__abstractmethods__


def test_consumed_upshift_request_defers_for_indexer_lag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a consumed ticket unresolved when payout discovery is not indexed yet.

    1. Bind the real manager to mocked RPC/indexer transport boundaries.
    2. Refuse stale index height with a temporary typed flow error.
    3. Defer consumed-ticket valuation without marking an aggregate or zero payout.
    """
    # 1. Bind the real manager to mocked RPC/indexer transport boundaries.
    vault = object.__new__(UpshiftVault)
    vault.spec = VaultSpec(1, "0x74aD2F789Ed583DBd141bbdafC673fE1F033718b")
    vault.web3 = MagicMock()
    vault.web3.eth.block_number = 100
    vault.web3.eth.get_transaction_receipt.return_value = {"blockNumber": 100}
    vault.__dict__["vault_contract"] = MagicMock()
    vault.vault_contract.functions.getBurnableAmountByReceiver.return_value.call.return_value = 0
    manager = UpshiftMultiAssetDepositManager(vault)
    owner = "0x1111111111111111111111111111111111111111"
    ticket = UpshiftQueuedRedemptionTicket(vault_address=vault.address, owner=owner, to=owner, raw_shares=10, tx_hash=HexBytes("0x" + "12" * 32), year=2026, month=7, day=27, claimable_epoch=1785110400)
    # Only transport observations are substituted; actual manager refusal logic runs.
    monkeypatch.setattr("eth_defi.hypersync.utils.configure_hypersync_from_env", lambda web3: SimpleNamespace(hypersync_client=MagicMock()))
    monkeypatch.setattr("eth_defi.erc_4626.vault_protocol.upshift.deposit_redeem.run_vault_flow_log_fetch", lambda awaitable: 100)
    # 2. Refuse stale index height with a temporary typed flow error.
    with pytest.raises(VaultFlowUnavailable, match="indexer is behind"):
        manager.fetch_completed_redemption_tx_hash(ticket)
    # 3. Defer consumed-ticket valuation without marking an aggregate or zero payout.
    with pytest.raises(VaultFlowUnavailable, match="payout receipt recovery"):
        manager.fetch_pending_redemption_value(ticket)
