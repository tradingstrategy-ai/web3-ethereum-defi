"""Focused regressions for the 54-vault simulation follow-up."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from eth_defi.erc_4626.vault_protocol.accountable.deposit_redeem import AccountableDepositManager
from eth_defi.erc_4626.vault_protocol.accountable.vault import AccountableVault
from eth_defi.erc_4626.vault_protocol.morpho.vault_v2 import MorphoV2Vault
from eth_defi.vault.deposit_redeem import VaultFlowUnavailable
from eth_defi.erc_4626.deposit_redeem import ERC4626DepositManager
from eth_defi.erc_4626.vault_protocol.lagoon.deposit_redeem import LagoonDepositManager
from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault, LagoonVersion
from eth_defi.erc_4626.vault_protocol.lagoon.testing import force_lagoon_settle
from eth_defi.erc_4626.vault_protocol.fluid.vault import FluidVault
from eth_defi.erc_4626.vault_protocol.maat.vault import MaatVault
from eth_defi.erc_4626.core import ERC4626Feature, get_vault_protocol_name
from eth_defi.erc_4626.classification import create_vault_instance
from eth_defi.provider.fallback import ExtraValueError
from eth_defi.vault_protocols.migration import discover_migrations
from eth_defi.abi import ZERO_ADDRESS_STR


def test_accountable_mined_request_reconstruction() -> None:
    """Keep new-request admission while reconstructing an already mined request.

    1. Model pending Accountable state without requiring a provider.
    2. Confirm a new request is refused.
    3. Reconstruct the original exact raw request without querying admission.
    """
    # 1. Model post-request state; only the request builder is under test.
    owner = "0x1111111111111111111111111111111111111111"
    token = SimpleNamespace(convert_to_decimals=lambda raw: Decimal(raw) / Decimal(10**18))
    funcs = Mock()
    vault = Mock(spec=AccountableVault)
    vault.share_token = token
    vault.fetch_minimum_redemption.return_value = None
    vault.vault_contract = SimpleNamespace(functions=funcs)
    vault.address = owner
    manager = AccountableDepositManager(vault)
    # 2. New submissions must still check the pending request.
    with patch.object(manager, "is_redemption_in_progress", return_value=True):
        with pytest.raises(VaultFlowUnavailable, match="pending"):
            manager.create_redemption_request(owner, raw_shares=10**18 + 1)
    # 3. Parsing must not call mutable admission or round the stored quantity.
    with patch.object(manager, "is_redemption_in_progress", side_effect=AssertionError("admission rerun")):
        request = manager.create_redemption_request_for_analysis(owner, 10**18 + 1)
    assert request.raw_shares == 10**18 + 1
    assert request.to == owner
    assert len(request.funcs) == 1
    funcs.requestRedeem.assert_called_once_with(10**18 + 1, owner, owner)


def test_morpho_owner_gate_predicates() -> None:
    """Distinguish canonical owner admission, refusal and unreadable results.

    1. Supply a provider-shaped stub to check decoding without RPC.
    2. Verify admitted owner checks both predicates.
    3. Verify denied and unreadable predicates stay distinct.
    """
    # 1. No external provider is needed to check bool decoding and call shape.
    owner = "0x1111111111111111111111111111111111111111"
    vault = object.__new__(MorphoV2Vault)
    provider = Mock()
    with patch.object(MorphoV2Vault, "web3", provider, create=True), patch.object(MorphoV2Vault, "address", owner), patch.object(MorphoV2Vault, "_get_block_identifier", return_value="latest"):
        # 2. Both predicates must admit the custody account.
        provider.eth.call.side_effect = [(1).to_bytes(32), (1).to_bytes(32)]
        assert vault.is_account_whitelisted(owner) is True
        assert provider.eth.call.call_count == 2
        # 3. Refusal is false; malformed output remains unknown.
        provider.eth.call.side_effect = [(0).to_bytes(32)]
        assert vault.is_account_whitelisted(owner) is False
        provider.eth.call.side_effect = [b""]
        with pytest.raises(NotImplementedError, match="Unreadable"):
            vault.is_account_whitelisted(owner)


def test_lagoon_sync_receipt_keeps_original_mode() -> None:
    """Parse DepositSync even after the NAV expires following submission.

    1. Model a valid v0.6 sync deposit with provider-free contract stubs.
    2. Build its original call and change the live NAV mode.
    3. Confirm receipt parsing selects DepositSync without rebuilding a request.
    """
    # 1. Model only the mode and call shape; real execution is in the fork matrix.
    owner = "0x1111111111111111111111111111111111111111"
    vault = Mock(spec=LagoonVault)
    vault.version = LagoonVersion.v_0_6_0
    vault.vault_contract.functions.isTotalAssetsValid.return_value.call.return_value = True
    vault.vault_contract.functions.syncMode.return_value.call.return_value = 0
    vault.denomination_token.convert_to_decimals.return_value = Decimal(1)
    manager = LagoonDepositManager(vault)
    # 2. A later validity change cannot alter the original receipt's event type.
    with patch.object(manager, "_assert_deposit_request_available"):
        request = manager.create_deposit_request(owner, raw_amount=1000000)
    assert request.raw_amount == 1000000
    vault.vault_contract.functions.syncDeposit.assert_called_once_with(1000000, owner, "0x0000000000000000000000000000000000000000")
    vault.vault_contract.functions.isTotalAssetsValid.return_value.call.return_value = False
    # 3. Reuse the shared event analyser with the actual synchronous event.
    with patch.object(ERC4626DepositManager, "_analyse_deposit") as analyse:
        manager.analyse_deposit("0x1234", None)
    analyse.assert_called_once_with("0x1234", None, deposit_event_signature="DepositSync(address,address,uint256,uint256)", hot_wallet=False)


@pytest.mark.parametrize("version,valid,mode,expected", [(LagoonVersion.v_0_5_0, True, 3, True), (LagoonVersion.v_0_5_0, False, 0, False), (LagoonVersion.v_0_6_0, True, 0, True), (LagoonVersion.v_0_6_0, True, 2, False)])
def test_lagoon_deposit_modes(version, valid, mode, expected) -> None:
    """Respect version-specific mode reads and keep claim entitlement out of capacity.

    1. Supply the observed version and mode.
    2. Read synchronous support and capacity through the manager.
    3. Check mode and capacity semantics independently.
    """
    # 1. Pure mode selection needs no provider.
    vault = Mock(spec=LagoonVault)
    vault.version = version
    vault.vault_contract.functions.isTotalAssetsValid.return_value.call.return_value = valid
    vault.vault_contract.functions.syncMode.return_value.call.return_value = mode
    manager = LagoonDepositManager(vault)
    # 2. Capacity must not read a claimable async balance as new funding room.
    assert manager.has_synchronous_deposit() is expected
    assert manager.fetch_depositable_raw_assets(ZERO_ADDRESS_STR) is None
    # 3. v0.5 lacks the v0.6 mode enum.
    if version is LagoonVersion.v_0_5_0:
        vault.vault_contract.functions.syncMode.assert_not_called()


@pytest.mark.parametrize("version,valid,signature", [(LagoonVersion.legacy, False, "settleDeposit()"), (LagoonVersion.v_0_4_0, False, "settleDeposit(uint256)"), (LagoonVersion.v_0_5_0, True, "settleDeposit(uint256)")])
def test_lagoon_settlement_selector_and_expiry(version, valid, signature) -> None:
    """Keep legacy/v0.4 calls compatible and expire valid NAV from the Safe.

    1. Stub only transaction transport; the helper constructs real call metadata.
    2. Invoke the existing forced-settlement helper.
    3. Verify selector and version-specific expiry without provider traffic.
    """
    # 1. No broadcasts are made; transaction transport is mocked deliberately.
    vault = Mock(spec=LagoonVault)
    vault.version = version
    vault.address = "0x1111111111111111111111111111111111111111"
    vault.web3 = Mock()
    vault.web3.eth.get_balance.return_value = 10**18
    vault.web3.eth.send_transaction.return_value = b"\x03" * 32
    vault.vault_contract.functions.isTotalAssetsValid.return_value.call.return_value = valid
    vault.vault_contract.functions.updateNewTotalAssets.return_value.transact.return_value = b"\x01" * 32
    vault.vault_contract.functions.expireTotalAssets.return_value.transact.return_value = b"\x02" * 32
    safe = "0x2222222222222222222222222222222222222222"
    # 2. The real helper picks its ABI and sender.
    with patch("eth_defi.erc_4626.vault_protocol.lagoon.testing.is_anvil", return_value=True), patch("eth_defi.erc_4626.vault_protocol.lagoon.testing.assert_transaction_success_with_explanation"), patch("eth_defi.erc_4626.vault_protocol.lagoon.testing.EncodedCall.from_keccak_signature") as encode:
        force_lagoon_settle(vault, vault.address, raw_nav=123, settlement_manager=safe)
    # 3. Old versions must not call the missing validity view.
    assert encode.call_args.kwargs["function"] == signature
    if version in (LagoonVersion.legacy, LagoonVersion.v_0_4_0):
        vault.vault_contract.functions.isTotalAssetsValid.assert_not_called()
    else:
        vault.vault_contract.functions.expireTotalAssets.return_value.transact.assert_called_once_with({"from": safe, "gas": 15000000})


def test_lagoon_closed_is_not_valid_nav_refusal() -> None:
    """Distinguish terminal closure from a prohibited replacement NAV proposal.

    1. Model the source-defined Closed error.
    2. Check it becomes a typed deposit refusal.
    3. Model valid NAV and confirm it is not classified as closed.
    """
    # 1. Match the real provider's structured revert envelope.
    vault = Mock(spec=LagoonVault)
    vault.address = "0x1111111111111111111111111111111111111111"
    vault.valuation_manager = vault.address
    manager = LagoonDepositManager(vault)
    call = vault.vault_contract.functions.updateNewTotalAssets.return_value.call
    call.side_effect = ExtraValueError({"code": 3, "message": "execution reverted", "data": "0x1cdde67b"})
    # 2. Closed blocks allocations before moving any assets.
    with pytest.raises(VaultFlowUnavailable, match="closed"):
        manager._assert_vault_open(ZERO_ADDRESS_STR)
    # 3. A valid NAV merely needs the supported sync/expiry path.
    call.side_effect = ExtraValueError({"code": 3, "message": "execution reverted", "data": "0xebe435fd"})
    manager._assert_vault_open(ZERO_ADDRESS_STR)


def test_fluid_name_is_not_permission_evidence() -> None:
    """Accept the canonical Fluid getter but reject an unrecognised name match.

    1. Model a canonical liquidity getter.
    2. Confirm its public policy is recognised.
    3. Remove protocol evidence and confirm admission stays unknown.
    """
    # 1. Only interface recognition is under test, not a real provider.
    vault = object.__new__(FluidVault)
    provider = Mock()
    provider.eth.call.return_value = (1).to_bytes(32) * 2 + bytes(7 * 32)
    provider.eth.get_storage_at.return_value = bytes(32)
    with patch.object(FluidVault, "web3", provider, create=True), patch.object(FluidVault, "address", ZERO_ADDRESS_STR), patch.object(FluidVault, "_get_block_identifier", return_value="latest"):
        # 2. Canonical source-backed interface permits depositors.
        assert vault.is_whitelisted_deposit() is False
        # 3. A display name does not establish permissionless access.
        provider.eth.call.return_value = b""
        with pytest.raises(NotImplementedError, match="Unrecognised"):
            vault.is_whitelisted_deposit()


def test_maat_factory_and_migration_inventory() -> None:
    """Route MAAT independently of a Fluid name and validate the migration resource.

    1. Construct a feature-bearing descriptor without reading contract state.
    2. Confirm the restricted MAAT policy and protocol label.
    3. Validate that the numbered refresh is discoverable by the actual runner.
    """
    # 1. Factory selection needs chain identity but no RPC in this case.
    provider = Mock()
    provider.eth.chain_id = 42161
    features = {ERC4626Feature.maat_like, ERC4626Feature.fluid_like}
    vault = create_vault_instance(provider, "0x466aa7fe90e1b2797f53ab303e677379c6542e7f", features=features)
    # 2. Underlying-venue names cannot select the Fluid admission policy.
    assert isinstance(vault, MaatVault)
    assert vault.is_whitelisted_deposit() is True
    assert get_vault_protocol_name(features) == "MAAT"
    # 3. This also checks required operational headers and migration signature.
    assert any(spec.number == 70 for spec in discover_migrations())
